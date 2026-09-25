"""Official prize route boards: public requirements and eligibility only in the public ARGUS release.

The operator's build derives per-scroll readiness, route controls and target selection from private
records. Those records and that tracking are not part of the public release, so this module keeps
every public name with neutral values: the boards list the official requirements and the eligible
scrolls, readiness is never inferred, and the route boards are empty.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

CONTRACT = "argus-prize-boards-v1"
NOT_IN_PUBLIC_BUILD = "not part of the public release"

REQUIREMENTS = {
  "FIRST_LETTERS": (
    "10 legible letters inside ONE region of at most 4 cm^2", "tifxyz mesh",
    "static programmatic image", "1 cm scale bar", "representative letter dimensions",
    "visible row structure / fibre context", "false-positive controls",
    "held-out validation", "proven zero training/prediction overlap",
  ),
  "GRAND_PRIZE": (
    "complete recto surface coverage", "column-ordered tifxyz meshes",
    "programmatic per-column images", "whole-scroll banner",
    "legibility accounted per counted column", "VC3D integration",
    "reproducible pipeline", "documented human effort",
    "training-overlap controls",
  ),
  "PARIS4_TITLE": (
    "title-region spatial context", "mesh", "programmatic image", "scale bar",
    "letter dimensions", "method", "false-positive controls", "held-out validation",
    "exposure and overlap accounting",
  ),
}

READINESS = ("NOT_STARTED", "DATA_IN_HAND", "SURFACE_AVAILABLE", "RENDERED",
             "CANDIDATE_UNDER_REVIEW", "PACKAGE_READY")

PRESENT = "PRESENT"
PARTIAL = "PARTIAL"
MISSING = "MISSING"


class BoardRefusal(RuntimeError):
    """Raised when a board would claim readiness it cannot evidence."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _eligible_rows() -> list:
    """Public eligibility from the scroll catalogue; nothing else is read."""
    try:
        from argus.core import control_matrix as _m
        return list(_m.as_record().get("rows") or [])
    except Exception:
        pass
    p = Path(__file__).resolve().parent.parent / "public_target_registry.json"
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = {r.get("scroll"): r for r in d.get("targets") or []}
    out = []
    for scroll, src in rows.items():
        prizes = src.get("prizes") or []
        out.append({"scroll": scroll,
                    "eligible_first_letters": "FIRST_LETTERS" in prizes,
                    "eligible_grand_prize": "GRAND_PRIZE_2027" in prizes,
                    "published_upstream": True})
    for scroll in (d.get("special_lanes") or {}).get("PARIS4_TITLE", []):
        out.append({"scroll": scroll, "role": "PARIS4_TITLE_CONTROL",
                    "published_upstream": True})
    return out


def board(route: str) -> dict:
    if route not in REQUIREMENTS:
        raise BoardRefusal(
          "unknown SCROLL-shaped route %r. The routes are named individually (%s) because two "
          "prizes with different criteria must not share one readiness number. The progress "
          "package is DELIVERABLE-shaped -- ask `progress_board()` for it."
          % (route, ", ".join(REQUIREMENTS)))
    rows = []
    for r in _eligible_rows():
        if route == "FIRST_LETTERS" and not r.get("eligible_first_letters"):
            continue
        if route == "GRAND_PRIZE" and not r.get("eligible_grand_prize"):
            continue
        if route == "PARIS4_TITLE" and r.get("role") != "PARIS4_TITLE_CONTROL":
            continue
        rows.append({
          "scroll": r.get("scroll"),
          "readiness": "NOT_STARTED",
          "local_data": None,
          "published_upstream": r.get("published_upstream"),
          "acquisition_families": r.get("acquisition_families") or [],
          "label_authority": None,
          "operator_fence": None,
          "also_eligible_for": [k for k, v in
                                (("FIRST_LETTERS", r.get("eligible_first_letters")),
                                 ("GRAND_PRIZE", r.get("eligible_grand_prize")))
                                if v and k != route],
        })
    rows = sorted((x for x in rows if x["scroll"]), key=lambda x: x["scroll"])
    return {
      "contract": CONTRACT, "route": route,
      "requirements": list(REQUIREMENTS[route]),
      "rows": rows,
      "count": len(rows),
      "readiness_counts": {"NOT_STARTED": len(rows)} if rows else {},
      "package_ready": [],
      "generated_utc": _now(),
      "readiness_basis": "per-scroll readiness tracking is %s; every row reads NOT_STARTED"
                         % NOT_IN_PUBLIC_BUILD,
      "no_progress_number": "readiness is a WORD, not a percentage.",
      "separate_from": [k for k in REQUIREMENTS if k != route],
      "why_separate": "readiness on one route is not progress on another.",
    }


def all_boards() -> dict:
    boards = {k: board(k) for k in REQUIREMENTS}
    fl = {r["scroll"] for r in boards["FIRST_LETTERS"]["rows"]}
    gp = {r["scroll"] for r in boards["GRAND_PRIZE"]["rows"]}
    return {
      "contract": CONTRACT,
      "boards": boards,
      "counts": {k: v["count"] for k, v in boards.items()},
      "overlap": {"first_letters_and_grand_prize": sorted(fl & gp),
                  "note": "the sets may overlap and are never merged."},
      "generated_utc": _now(),
    }


def public_view(route: str) -> dict:
    """Eligibility and one readiness word."""
    b = board(route)
    return {
      "contract": CONTRACT, "route": route,
      "eligible_count": b["count"],
      "scrolls": sorted(r["scroll"] for r in b["rows"]),
      "readiness_counts": b["readiness_counts"],
      "withheld": ["per-scroll ranking", "candidate locations", "search coverage",
                   "next actions", "frozen region geometry", "exposure detail"],
      "why_withheld": "a public board that ranks targets tells a competitor where to look.",
    }


def _not_in_public_build(what: str) -> dict:
    return {"state": MISSING, "evidence": {"public_build": NOT_IN_PUBLIC_BUILD},
            "note": "%s is %s." % (what, NOT_IN_PUBLIC_BUILD)}


def _probe_demonstration() -> dict:
    from argus.core import paths
    ui = paths.repo("argus", "ui")
    src = ui / "src"
    built = any((ui / d).is_dir() for d in ("dist", "build"))
    return {"state": PRESENT if (src.is_dir() and built) else
            PARTIAL if src.is_dir() else MISSING,
            "evidence": {"ui_src": src.is_dir(), "built_bundle": built}}


def _probe_readme_and_licences() -> dict:
    from argus.core import paths
    files = {n: paths.repo(n).is_file() for n in
             ("README.md", "QUICKSTART.md", "THIRD_PARTY_ACKNOWLEDGEMENTS.md", "SBOM.md",
              "LICENSE")}
    absent = sorted(k for k, v in files.items() if not v)
    return {"state": PRESENT if not absent else PARTIAL if any(files.values()) else MISSING,
            "evidence": files, "absent": absent}


PROGRESS_COMPONENTS = (
  ("product_demonstration", "polished continuous product demonstration", _probe_demonstration),
  ("architecture_and_capability_map", "architecture and capability map",
   lambda: _not_in_public_build("the capability-map probe")),
  ("real_control_run", "a real control run", lambda: _not_in_public_build("the control-run record")),
  ("provenance_drilldown", "provenance drill-down",
   lambda: _not_in_public_build("the provenance probe")),
  ("honest_failures", "failures and refusals shown honestly",
   lambda: _not_in_public_build("the failure record")),
  ("screenshots", "screenshots", lambda: _not_in_public_build("the screenshot record")),
  ("short_video", "a short video", lambda: _not_in_public_build("the video record")),
  ("readme_install_acknowledgements_licences_reproduction",
   "README, install path, acknowledgements, licences and reproduction command",
   _probe_readme_and_licences),
)


def progress_board() -> dict:
    """The progress package, DELIVERABLE-shaped."""
    rows = []
    for key, what, fn in PROGRESS_COMPONENTS:
        try:
            r = fn()
        except Exception as e:
            r = {"state": MISSING, "evidence": {"probe_error": "%s: %s" % (type(e).__name__, e)}}
        rows.append(dict(r, component=key, what=what))
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {
      "contract": CONTRACT, "route": "PROGRESS_PACKAGE",
      "shape": "DELIVERABLE",
      "rows": rows,
      "counts": counts,
      "complete": all(r["state"] == PRESENT for r in rows),
      "missing": [r["component"] for r in rows if r["state"] == MISSING],
      "partial": [r["component"] for r in rows if r["state"] == PARTIAL],
      "claim_ceiling": "the progress package may show that the machine works. It may not present "
                       "that as evidence about ink.",
      "generated_utc": _now(),
    }


ROUTE_BOARDS_CONTRACT = "argus-route-boards-v1"


def route_boards() -> dict:
    """First Letters and Grand Prize route boards: empty in the public build."""
    def _empty(route):
        return {"route": route, "count": 0, "rows": []}
    return {
      "contract": ROUTE_BOARDS_CONTRACT, "read_only": True,
      "readable": False, "why_unreadable": "route records are %s" % NOT_IN_PUBLIC_BUILD,
      "sources": [],
      "boards": {"FIRST_LETTERS": _empty("FIRST_LETTERS"), "GRAND_PRIZE": _empty("GRAND_PRIZE")},
      "controls": [],
      "claim_ceiling": "MECHANICS_ONLY: no scientific claim is made by this build.",
      "generated_utc": _now(),
    }


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    a = all_boards()
    ck("three routes, tracked separately", set(a["boards"]) == set(REQUIREMENTS))
    ck("each board states its OWN requirements",
       all(a["boards"][k]["requirements"] == list(REQUIREMENTS[k]) for k in REQUIREMENTS))
    ck("readiness is a word, never a percentage",
       all(r["readiness"] in READINESS for b in a["boards"].values() for r in b["rows"]))
    ck("nothing is PACKAGE_READY", all(not b["package_ready"] for b in a["boards"].values()))
    try:
        board("PRIZE")
        ck("an unknown route is refused", False)
    except BoardRefusal:
        ck("an unknown route is refused", True)
    pv = public_view("FIRST_LETTERS")
    ck("the public view withholds ranking and candidate locations",
       "per-scroll ranking" in pv["withheld"] and "rows" not in pv)
    pb = progress_board()
    ck("the progress package is DELIVERABLE-shaped", pb["shape"] == "DELIVERABLE")
    ck("every progress row carries evidence", all(r.get("evidence") for r in pb["rows"]))
    rb = route_boards()
    ck("the route boards are empty in the public build",
       all(not b["rows"] for b in rb["boards"].values()) and rb["controls"] == [])
    for n, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", n))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--route", choices=sorted(REQUIREMENTS))
    ap.add_argument("--progress", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if a.progress:
        print(json.dumps(progress_board(), indent=2, default=str))
        return 0
    if a.route:
        print(json.dumps(board(a.route), indent=2))
        return 0
    rec = all_boards()
    print(json.dumps({k: v for k, v in rec.items() if k != "boards"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
