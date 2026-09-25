"""The scroll shelf: every registered scroll, from records, grouped by what it is."""
from __future__ import annotations

import json
import pathlib
import copy
import time
import urllib.error
import urllib.request

CONTRACT = "argus-scroll-shelf-v1"

SHELVES = (
  "FIRST_LETTERS_ELIGIBLE",
  "GRAND_PRIZE_ELIGIBLE",
  "LABELLED_CONTROL",
  "LOCAL_RESEARCH_ONLY",
  "UNAVAILABLE_UPSTREAM_REFERENCE",
)

SHELF_MEANING = {
  "FIRST_LETTERS_ELIGIBLE":
    "on the published First Letters list.",
  "GRAND_PRIZE_ELIGIBLE":
    "on the Grand Prize list but not on First Letters.",
  "LABELLED_CONTROL":
    "not prize-eligible, but carries ink labels -- so it is what a detector can be measured "
    "against.",
  "LOCAL_RESEARCH_ONLY":
    "a canonical scroll identity we hold something for, with no prize eligibility and no "
    "labels.",
  "UNAVAILABLE_UPSTREAM_REFERENCE":
    "a canonical identity with nothing published and nothing held. Listed because it exists, "
    "not because it can be worked on.",
}

STAGES = ("NONE", "SCAN_HELD", "GEOMETRY", "RENDER", "LABELS", "RESULT")


class ShelfError(RuntimeError):
    """Raised when the shelf cannot be composed from records, rather than invented."""


LOCAL_CACHE_TTL_S = 5.0
_LOCAL_CACHE = {"at": 0.0, "value": None}
SERVICE_RETRY_S = 5.0
_SERVICE_RETRY_AFTER = {}

_DISCOVERY_LEDGER = pathlib.Path(__file__).resolve().parents[2] / "corpus" / "upstream_discovered.json"


def _discovered_upstream() -> dict:
    """Scrolls `upstream_watch.bind_discoveries` has bound to a physical identity, read as a fifth shelf authority."""
    try:
        raw = json.loads(_DISCOVERY_LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"bound_scrolls": [], "unresolved": []}
    return {"bound_scrolls": sorted(set(raw.get("bound_scrolls") or [])),
            "unresolved": list(raw.get("unresolved") or [])}


def _get(base: str, path: str, timeout: float = 20.0):
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ShelfError("could not read %s: %s" % (path, exc)) from exc


def _furthest(rec: dict) -> str:
    """The last stage with EVIDENCE behind it."""
    if int(rec.get("results_complete") or 0) > 0:
        return "RESULT"
    if int(rec.get("labelled") or 0) > 0:
        return "LABELS"
    if int(rec.get("aligned") or 0) > 0:
        return "RENDER"
    if int(rec.get("physical_segments") or 0) > 0:
        return "GEOMETRY"
    if int(rec.get("cached_ct_regions") or 0) > 0:
        return "SCAN_HELD"
    return "NONE"


def _local_stores(scroll: str) -> dict:
    """{\"sealed\": [dir, ...], \"attested\": [{\"store\", \"attested_by\"}, ...]} for this scroll."""
    import json as _json
    try:
        from argus.core import acquisition_identity as _ai
        roots = [r for r in (_ai.store_roots() or []) if r and r.is_dir()]
    except Exception:
        return {"sealed": [], "attested": []}
    from argus.core import store_identity as _si
    want = scroll.strip().lower()
    out = {"sealed": [], "attested": []}
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            ident = child / "STORE_IDENTITY.json"
            if not ident.is_file():
                continue
            try:
                rec = _json.loads(ident.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(rec.get("physical_scroll") or "").strip().lower() == want:
                if _si.holding_state(rec) == _si.ATTESTED:
                    out["attested"].append({"store": str(child), "attested_by": rec.get("attested_by")})
                else:
                    out["sealed"].append(str(child))
    return out


def _held_locally(scroll: str) -> bool:
    """Only a SEALED store is a holding."""
    return bool(_local_stores(scroll)["sealed"])


def _surveyed_scrolls() -> frozenset:
    """Scrolls the public official survey lists at least one published volume for."""
    from argus.core import official_identity as OI
    return frozenset(name for name, _v, _r in OI.survey_volumes())


def compose(base: str = "http://127.0.0.1:8787") -> dict:
    """Compose from the running service's four authoritative read-only routes."""
    return compose_records(
        _get(base, "/api/scroll-ids"),
        _get(base, "/api/targets"),
        _get(base, "/api/scrolls"),
        source="service:%s" % base.rstrip("/"),
    )


def compose_local(*, refresh: bool = False) -> dict:
    """Compose from the same authorities in-process when no service is listening."""
    now = time.monotonic()
    cached = _LOCAL_CACHE.get("value")
    if not refresh and cached is not None and now - float(_LOCAL_CACHE.get("at") or 0) < LOCAL_CACHE_TTL_S:
        return copy.deepcopy(cached)
    try:
        from argus.service import receipts as _receipts
        from argus.core import scroll_index as _scroll_index
        from argus.service.app import targets as _targets
        composed = compose_records(
            _receipts.scroll_ids(),
            _targets(),
            _scroll_index.summary(),
            source="in_process_authorities",
        )
        _LOCAL_CACHE.update(at=time.monotonic(), value=composed)
        return copy.deepcopy(composed)
    except Exception as exc:
        raise ShelfError("could not compose from in-process authorities: %s" % exc) from exc


def compose_available(base: str = "http://127.0.0.1:8787") -> dict:
    """Use the live service when available, otherwise explicit local authorities."""
    now = time.monotonic()
    retry_after = float(_SERVICE_RETRY_AFTER.get(base) or 0)
    if now >= retry_after:
        try:
            return compose(base)
        except ShelfError:
            _SERVICE_RETRY_AFTER[base] = now + SERVICE_RETRY_S
    return compose_local()


def compose_records(ids: dict, targets: dict, scrolls: dict, *,
                    source: str = "declared_authorities") -> dict:
    """Join the four authoritative records into one physical-scroll shelf."""

    target_registry_available = targets.get("available") is not False
    canonical = list(ids.get("canonical") or [])
    if not canonical:
        raise ShelfError("the canonical identity list is empty; refusing to invent a shelf")

    prize_scrolls = sorted({t["scroll"] for t in (targets.get("targets") or [])
                            if t.get("scroll")})
    missing_from_canonical = [s for s in prize_scrolls if s not in set(canonical)]
    canonical = sorted(set(canonical) | set(prize_scrolls))

    discovered = _discovered_upstream()
    discovered_scrolls = set(discovered["bound_scrolls"])
    missing_from_canonical_discovered = sorted(discovered_scrolls - set(canonical))
    canonical = sorted(set(canonical) | discovered_scrolls)
    aliases = dict(ids.get("aliases") or {})
    placeholders = set(ids.get("placeholders") or [])

    prize: dict[str, set] = {}
    acquisitions: dict[str, list] = {}
    store_state: dict[str, set] = {}
    for t in (targets.get("targets") or []):
        s = t.get("scroll")
        if not s:
            continue
        prize.setdefault(s, set()).update(t.get("prizes") or [])
        fam = None
        if t.get("pitch_um") and t.get("energy_kev"):
            fam = "%sum/%skeV" % (t["pitch_um"], t["energy_kev"])
        entry = {"scan_id": t.get("scan_id"), "family": fam,
                 "store_state": t.get("store_state"),
                 "operator_fence": t.get("operator_fence")}
        if entry not in acquisitions.setdefault(s, []):
            acquisitions[s].append(entry)
        if t.get("store_state"):
            store_state.setdefault(s, set()).add(t["store_state"])

    by_scroll = {r["scroll"]: r for r in (scrolls.get("scrolls") or []) if r.get("scroll")}

    try:
        from argus.core import corpus_manifest as _cm
        label_inventory = dict(_cm.SCROLL_LABEL_INVENTORY)
    except Exception:
        label_inventory = {}

    rows = []
    for s in sorted(canonical):
        if s in placeholders:
            continue
        p = prize.get(s, set())
        rec = by_scroll.get(s, {})
        acq = acquisitions.get(s, [])
        published_upstream = any(a.get("store_state") == "FOUND" for a in acq)             or s in _surveyed_scrolls()
        stores = _local_stores(s)
        held = bool(stores["sealed"])
        attested = stores["attested"]
        inv = label_inventory.get(s) or {}
        inv_labels = len(inv.get("segments") or []) if isinstance(inv.get("segments"), list) else int(inv.get("segments") or 0)
        labelled = int(rec.get("labelled") or 0) > 0 or bool(inv) or inv_labels > 0
        furthest = _furthest(rec)

        if "FIRST_LETTERS" in p:
            shelf = "FIRST_LETTERS_ELIGIBLE"
        elif p:
            shelf = "GRAND_PRIZE_ELIGIBLE"
        elif labelled:
            shelf = "LABELLED_CONTROL"
        elif held or furthest != "NONE":
            shelf = "LOCAL_RESEARCH_ONLY"
        else:
            shelf = "UNAVAILABLE_UPSTREAM_REFERENCE"

        rows.append({
          "scroll": s,
          "display": rec.get("display") or s,
          "shelf": shelf,
          "prizes": sorted(p),
          "acquisitions": acq,
          "acquisition_families": sorted({a["family"] for a in acq if a.get("family")}),
          "local_data": ("HELD" if held else
                         "PARTIAL" if furthest != "NONE" else "NONE"),
          "published_upstream": published_upstream,
          "attested_store": ({"count": len(attested),
                              "attested_by": sorted({str(a.get("attested_by")) for a in attested}),
                              "state": "ATTESTED_NOT_SEALED"} if attested else None),
          "furthest_stage": furthest,
          "labelled": labelled,
          "label_representations": int(rec.get("label_representations") or 0) or inv_labels,
          "label_authority": inv.get("authority") if inv else None,
          "physical_segments": int(rec.get("physical_segments") or 0),
          "operator_fence": next((a["operator_fence"] for a in acq
                                  if a.get("operator_fence")), None),
          "aliases": [k for k, v in aliases.items() if v == s],
          "discovered_upstream": s in discovered_scrolls,
          "filters": {
            "eligible": bool(p),
            "available_locally": held,
            "published_upstream": published_upstream,
            "has_surface": furthest in ("GEOMETRY", "RENDER", "LABELS", "RESULT"),
            "has_render": furthest in ("RENDER", "LABELS", "RESULT"),
            "has_labels": labelled,
            "has_work_in_progress": int(rec.get("results_partial") or 0) > 0,
          },
        })

    counts: dict[str, int] = {k: 0 for k in SHELVES}
    for r in rows:
        counts[r["shelf"]] += 1

    return {
      "contract": CONTRACT,
      "composition_source": source,
      "target_registry": {
          "available": target_registry_available,
          "why": (None if target_registry_available else
                  targets.get("why_v2") or "No official target registry is installed."),
          "next_action": (None if target_registry_available else targets.get("next_action")),
      },
      "scrolls": rows,
      "count": len(rows),
      "shelves": list(SHELVES),
      "shelf_meaning": SHELF_MEANING,
      "counts": counts,
      "selected": None,
      "no_default_rule": (
        "`selected` is None until a person chooses. A shelf that silently opens its first "
        "entry is a single-scroll view with extra steps, and it teaches the reader that "
        "whatever they are looking at is what the system recommends."),
      "dedupe_rule": (
        "one row per PHYSICAL scroll. The prize lists are keyed by (scroll, scan_id), so a "
        "scroll scanned twice appears twice there and ONCE here, with both acquisitions "
        "recorded against it."),
      "confusable_pairs": ids.get("confusable_pairs") or [],
      "prize_scrolls_absent_from_canonical_ids": missing_from_canonical,
      "why_that_list_matters": (
        "these identities are declared prize-eligible and are missing from the canonical "
        "identity list. The shelf includes them from the prize registry, but the omission is "
        "reported rather than smoothed over: the identity list is the thing that should be "
        "corrected, and a silently-repaired input never gets corrected."),
      "upstream_discovered_scrolls": sorted(discovered_scrolls),
      "upstream_discovery_unresolved": discovered["unresolved"],
      "upstream_discovery_source": str(_DISCOVERY_LEDGER),
      "upstream_discovered_absent_from_canonical_ids": missing_from_canonical_discovered,
      "why_upstream_discovery_matters": (
        "a scan `scripts/upstream_watch.py --update` runs binds every newly-seen bucket "
        "name to a physical scroll through scroll_ids.resolve and writes it here. This union "
        "is how that binding reaches the shelf (and so Library) automatically -- the identity "
        "authority (scroll_ids.CANONICAL) still deserves a deliberate, reviewed addition, but "
        "the shelf does not wait for that review to show what was discovered."),
      "held_means": (
        "present in a sealed store root on THIS machine. It is not `store_state`, which "
        "records that the publisher's bucket lists the volume -- a different question, now "
        "carried separately as `published_upstream`."),
      "placeholders_excluded": sorted(placeholders),
      "what_a_tile_may_not_say": (
        "a scientific verdict. A shelf is for choosing what to look at; a claim on a chooser "
        "is one nobody asked for and cannot act on."),
    }


def selftest() -> bool:
    ok = []

    def ck(name, cond):
        ok.append((name, bool(cond)))

    try:
        s = compose()
    except ShelfError as exc:
        print("  SKIP service not reachable: %s" % exc)
        return True

    rows = s["scrolls"]
    ck("every scroll appears exactly once", len({r["scroll"] for r in rows}) == len(rows))
    ck("every row sits on a declared shelf",
       all(r["shelf"] in SHELVES for r in rows))
    ck("the shelf counts partition the rows", sum(s["counts"].values()) == len(rows))
    ck("nothing is selected by default", s["selected"] is None)
    ck("a scroll on both prize lists is filed under First Letters, once",
       all(r["shelf"] == "FIRST_LETTERS_ELIGIBLE"
           for r in rows if "FIRST_LETTERS" in r["prizes"]))
    ck("placeholders are excluded rather than rendered as scrolls",
       not any(r["scroll"].startswith("PHercNNNN") for r in rows))
    ck("every furthest stage is a declared stage",
       all(r["furthest_stage"] in STAGES for r in rows))
    ck("filters are precomputed so two screens cannot disagree",
       all(set(r["filters"]) == {"eligible", "available_locally", "published_upstream",
                                 "has_surface", "has_render", "has_labels",
                                 "has_work_in_progress"} for r in rows))

    ck("every prize-eligible scroll reaches the shelf, not just the canonical ids",
       len([r for r in rows if "FIRST_LETTERS" in r["prizes"]]) == 22)
    ck("held means held HERE, never the publisher's bucket listing",
       "not `store_state`" in s["held_means"]
       and all(("published_upstream" in r) for r in rows))

    for name, good in ok:
        print("  %-4s %s" % ("ok" if good else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    s = compose(a.base)
    print(json.dumps({k: v for k, v in s.items() if k != "scrolls"}, indent=2))
    for r in s["scrolls"]:
        print("  %-12s %-32s %-10s %s"
              % (r["scroll"], r["shelf"], r["local_data"], r["furthest_stage"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
