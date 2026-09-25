"""What is on this machine, and where the work has actually gone -- per scroll, measured."""
from __future__ import annotations

import json
import re
import threading

from argus.core import paths

CONTRACT = "argus-scroll-effort-v1"

_lock = threading.Lock()
_cache: dict = {"key": None, "value": None}


def name_forms(scroll: str, aliases=()) -> list:
    """Every string a finding might use for `scroll`, by the rules in the module docstring."""
    forms = {scroll, *[a for a in aliases if a]}
    m = re.fullmatch(r"PHerc(\d+)([A-Za-z]?)", scroll)
    if m:
        forms.add("PHerc%d%s" % (int(m.group(1)), m.group(2)))
    else:
        n = re.fullmatch(r"PHerc([A-Za-z]+)(\d+)", scroll)
        if n:
            forms.add(n.group(1) + n.group(2))
            forms.add("%s %s" % (n.group(1), n.group(2)))
    return sorted(forms, key=len, reverse=True)


def _pattern(forms) -> re.Pattern | None:
    parts = [re.escape(f).replace(r"\ ", r"[\s_-]?") for f in forms if f]
    if not parts:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(parts), re.IGNORECASE)


def _datasets() -> dict:
    p = paths.find_artifact("registries", "datasets.json")
    if not p or not p.is_file():
        return {"present": False, "why": "the dataset registry is not on this machine"}
    d = json.loads(p.read_text(encoding="utf-8"))
    d["present"] = True
    d["_path"] = str(p)
    return d


def _findings() -> dict:
    from argus.core import findings_query as FQ
    return FQ.load()


def measure(*, datasets=None, findings=None, aliases=None) -> dict:
    ds = datasets if datasets is not None else _datasets()
    fd = findings if findings is not None else _findings()
    if aliases is None:
        from argus.core import scroll_ids as S
        aliases = S.ALIASES

    scrolls = ds.get("scrolls") or {}
    by_target: dict = {}
    for alias, target in (aliases or {}).items():
        by_target.setdefault(target, []).append(alias)

    ids = sorted(scrolls)
    forms = {s: name_forms(s, by_target.get(s, ())) for s in ids}

    collisions = []
    for s in ids:
        keep = []
        for f in forms[s]:
            clash = [o for o in ids if o != s and f.lower() == o.lower()]
            if clash:
                collisions.append({"scroll": s, "form": f, "is_the_id_of": clash})
            else:
                keep.append(f)
        forms[s] = keep

    rows_f = fd.get("findings") or {}
    rows_f = list(rows_f.values()) if isinstance(rows_f, dict) else list(rows_f)
    texts = [("%s\n%s" % (f.get("title") or "", f.get("body") or ""), f.get("n") or 0, f.get("id"))
             for f in rows_f]

    out = []
    for s in ids:
        rec = scrolls[s] or {}
        local = ((rec.get("local_state") or {}).get("value")) or {}
        pat = _pattern(forms[s])
        hits = [(n, fid) for t, n, fid in texts if pat and pat.search(t)] if fd.get("present") else []
        latest = max(hits) if hits else None
        out.append({
          "scroll": s,
          "holdings": {
            "present": bool(local.get("present")),
            "bytes": local.get("bytes") or 0,
            "files": local.get("files") or 0,
            "asset_kinds": local.get("asset_kinds") or {},
            "evidence": (rec.get("local_state") or {}).get("evidence"),
          },
          "effort": {
            "findings_mentioning": len(hits) if fd.get("present") else None,
            "latest_finding": latest[1] if latest else None,
            "latest_finding_n": latest[0] if latest else None,
            "name_forms": forms[s],
          },
        })

    held = sorted((r for r in out if r["holdings"]["present"]),
                  key=lambda r: -r["holdings"]["bytes"])
    worked = sorted((r for r in out if (r["effort"]["findings_mentioning"] or 0) > 0),
                    key=lambda r: -(r["effort"]["findings_mentioning"] or 0))
    top = worked[0] if worked else None
    runner = worked[1] if len(worked) > 1 else None

    return {
      "contract": CONTRACT,
      "holdings_source": {"route": "registries/datasets.json", "path": ds.get("_path"),
                          "generated_utc": ds.get("utc"), "present": bool(ds.get("present")),
                          "why": ds.get("why") if not ds.get("present") else None,
                          "proves": "held on the date the catalogue was built, not verified now"},
      "effort_source": {"route": "corpus/findings/findings.json", "present": bool(fd.get("present")),
                        "relation": "MENTIONS",
                        "proves": "findings that name the scroll; a proxy for where work went, "
                                  "not a measure of what the work established"},
      "rows": out,
      "held_by_bytes": [r["scroll"] for r in held],
      "worked_by_findings": [r["scroll"] for r in worked],
      "most_worked": ({"scroll": top["scroll"],
                       "findings_mentioning": top["effort"]["findings_mentioning"],
                       "lead_over_next": (top["effort"]["findings_mentioning"]
                                          - (runner["effort"]["findings_mentioning"] if runner else 0)),
                       "next": runner["scroll"] if runner else None}
                      if top else None),
      "name_form_collisions_dropped": collisions,
      "never_combined": "holdings and effort are separate rankings; no blended score is computed",
    }


def cached() -> dict:
    """Recomputed only when either source file changes."""
    from argus.core import findings_query as FQ
    dp = paths.find_artifact("registries", "datasets.json")
    fp = FQ.corpus_path()
    key = tuple((str(p), p.stat().st_mtime_ns) if p and p.is_file() else (str(p), None)
                for p in (dp, fp))
    with _lock:
        if _cache["key"] == key:
            return _cache["value"]
    v = measure()
    with _lock:
        _cache.update(key=key, value=v)
    return v


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    ck("numbered id gains its un-padded form", "PHerc172" in name_forms("PHerc0172"))
    ck("named id gains its bare and spaced forms",
       {"Paris4", "Paris 4"} <= set(name_forms("PHercParis4")))
    ds = {"present": True, "utc": "T", "scrolls": {
      "PHercParis4": {"local_state": {"value": {"present": True, "bytes": 200, "files": 3}}},
      "PHerc0175": {"local_state": {"value": {"present": False}}},
      "PHerc0175A": {"local_state": {"value": {"present": True, "bytes": 900, "files": 1}}},
      "PHerc0172": {"local_state": {"value": {"present": False}}},
    }}
    fd = {"present": True, "findings": {
      "F-1": {"id": "F-1", "n": 1, "title": "Paris 4 render", "body": ""},
      "F-2": {"id": "F-2", "n": 2, "title": "PARIS4_TITLE prize", "body": "PHercParis4 again"},
      "F-3": {"id": "F-3", "n": 3, "title": "about PHerc0175A only", "body": ""},
      "F-4": {"id": "F-4", "n": 4, "title": "PHerc172 note", "body": ""},
      "F-5": {"id": "F-5", "n": 5, "title": "Paris4", "body": ""},
    }}
    r = measure(datasets=ds, findings=fd, aliases={})
    row = {x["scroll"]: x for x in r["rows"]}
    ck("spaced, underscored and full forms all count for Paris4",
       row["PHercParis4"]["effort"]["findings_mentioning"] == 3)
    ck("word boundary: PHerc0175 does not absorb PHerc0175A",
       row["PHerc0175"]["effort"]["findings_mentioning"] == 0
       and row["PHerc0175A"]["effort"]["findings_mentioning"] == 1)
    ck("un-padded form counts", row["PHerc0172"]["effort"]["findings_mentioning"] == 1)
    ck("most worked is the top mention count", r["most_worked"]["scroll"] == "PHercParis4")
    ck("latest finding is the highest n", row["PHercParis4"]["effort"]["latest_finding"] == "F-5")
    ck("holdings rank by bytes, independently of effort",
       r["held_by_bytes"] == ["PHerc0175A", "PHercParis4"])
    ck("no blended score exists", not any("score" in k for k in r["rows"][0]))
    un = measure(datasets=ds, findings={"present": False}, aliases={})
    ck("an unreadable corpus reports None, not zero",
       all(x["effort"]["findings_mentioning"] is None for x in un["rows"]) and un["most_worked"] is None)
    col = measure(datasets={"present": True, "scrolls": {"PHerc0175": {}, "PHerc175": {}}},
                  findings=fd, aliases={})
    ck("a form equal to another scroll's id is dropped and reported",
       any(c["form"] == "PHerc175" for c in col["name_form_collisions_dropped"]))
    for n, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", n))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="scroll holdings and effort")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    r = measure()
    print("most worked:", r["most_worked"])
    for s in r["worked_by_findings"][:8]:
        x = next(y for y in r["rows"] if y["scroll"] == s)
        print("  %-14s findings %-5s held %6.1f GB" % (s, x["effort"]["findings_mentioning"],
                                                      x["holdings"]["bytes"] / 1e9))
    print("held:", [(s, round(next(y for y in r["rows"] if y["scroll"] == s)["holdings"]["bytes"] / 1e9, 1))
                    for s in r["held_by_bytes"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
