"""Findings, as a reader looking at one scroll needs them: current, errata, superseded, reported."""
from __future__ import annotations

import json
import re
import threading

from argus.core import paths

CONTRACT = "argus-findings-query-v1"

CURRENT, ERRATUM, AMENDED, SUPERSEDED = "CURRENT", "ERRATUM", "AMENDED", "SUPERSEDED"
KINDS = (CURRENT, ERRATUM, AMENDED, SUPERSEDED)

MAX_LIMIT = 200
EXCERPT_CHARS = 280

_cache_lock = threading.Lock()
_cache: dict = {"key": None, "doc": None}


def corpus_path():
    return paths.repo("corpus", "findings", "findings.json")


def _ids(value) -> set:
    """`retracts` / `amends` / the top-level sets arrive as a string, a list or a dict."""
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {str(k) for k in value}
    return {str(x.get("id") if isinstance(x, dict) else x) for x in value}


def load(path=None) -> dict:
    """The corpus, cached by (path, mtime, size)."""
    p = path or corpus_path()
    try:
        st = p.stat()
    except OSError as exc:
        return {"present": False, "why": "the findings corpus is not on this machine (%s)"
                                         % type(exc).__name__, "findings": {}}
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if _cache["key"] == key:
            return _cache["doc"]
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["present"] = True
    with _cache_lock:
        _cache.update(key=key, doc=doc)
    return doc


def _rows(doc) -> list:
    f = doc.get("findings") or {}
    return list(f.values()) if isinstance(f, dict) else list(f)


def classify(finding: dict, retracted: set, amended: set) -> str:
    fid = str(finding.get("id"))
    if fid in retracted:
        return SUPERSEDED
    if _ids(finding.get("amends")) or _ids(finding.get("retracts")):
        return ERRATUM
    if fid in amended:
        return AMENDED
    return CURRENT


def mention_pattern(ids) -> re.Pattern | None:
    ids = sorted({str(i) for i in ids if i}, key=len, reverse=True)
    if not ids:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(map(re.escape, ids)),
                      re.IGNORECASE)


def _excerpt(text: str, pat: re.Pattern | None) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if pat:
        m = pat.search(t)
        if m:
            a = max(0, m.start() - 90)
            return ("..." if a else "") + t[a:a + EXCERPT_CHARS] + ("..." if a + EXCERPT_CHARS < len(t) else "")
    return t[:EXCERPT_CHARS] + ("..." if len(t) > EXCERPT_CHARS else "")


def query(*, scroll: str | None = None, aliases=(), q: str | None = None, kinds=None,
          limit: int = 50, offset: int = 0, doc=None) -> dict:
    d = doc if doc is not None else load()
    if not d.get("present"):
        return {"contract": CONTRACT, "present": False, "why": d.get("why"),
                "is_not_zero": "the corpus could not be read, which is not the same as no "
                               "findings mentioning this scroll."}

    retracted, amended = _ids(d.get("retracted")), _ids(d.get("amended"))
    pat = mention_pattern([scroll, *aliases]) if scroll else None
    needle = (q or "").strip().lower()
    want = {k for k in (kinds or KINDS) if k in KINDS}
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    counts = {k: 0 for k in KINDS}
    matched = []
    for f in _rows(d):
        text = "%s\n%s" % (f.get("title") or "", f.get("body") or "")
        if pat and not pat.search(text):
            continue
        if needle and needle not in text.lower() and needle not in str(f.get("id")).lower():
            continue
        kind = classify(f, retracted, amended)
        counts[kind] += 1
        if kind in want:
            matched.append((f, kind))

    matched.sort(key=lambda fk: -(fk[0].get("n") or 0))
    page = matched[offset:offset + limit]
    return {
      "contract": CONTRACT,
      "present": True,
      "scroll": scroll,
      "relation": ("MENTIONS" if scroll else "ALL"),
      "relation_means": ("the finding's title or body names this scroll or one of its aliases, "
                         "matched on word boundaries. It establishes a MENTION, not that the "
                         "finding is about this scroll.") if scroll else "every finding",
      "query": q or None,
      "counts_by_kind": counts,
      "total_matching_kinds": len(matched),
      "offset": offset,
      "limit": limit,
      "rows": [{
        "id": f.get("id"), "n": f.get("n"), "title": f.get("title"), "kind": kind,
        "excerpt": _excerpt(f.get("body") or "", pat),
        "ledger": f.get("ledger"), "checkable": f.get("checkable"),
        "retracts": sorted(_ids(f.get("retracts"))), "amends": sorted(_ids(f.get("amends"))),
        "cites": f.get("cites"),
      } for f, kind in page],
      "kinds_are_from_the_corpus": "SUPERSEDED means retracted by a later finding; ERRATUM means "
                                   "this finding retracts or amends another; AMENDED means a "
                                   "later finding corrected this one. Read from the corpus's own "
                                   "retracts/amends fields, never from title words.",
    }


def community(scroll: str | None = None, aliases=(), key: str | None = None) -> dict:
    """Reported by others, not reproduced here."""
    from argus.core import community_intel as CI
    reg = CI.register()
    claims = reg.get("claims") or []
    pat = mention_pattern([scroll, *aliases]) if scroll else None
    rows = [c for c in claims
            if (not key or c.get("key") == key)
            and (not pat or pat.search("%s %s" % (c.get("claim", ""), c.get("would_reproduce", ""))))]
    return {"contract": CONTRACT, "scroll": scroll, "count": len(rows), "claims": rows,
            "these_are_not_findings": reg.get("these_are_not_findings"),
            "status_of_every_claim": reg.get("status_of_every_claim")}


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    doc = {
      "present": True,
      "retracted": ["N-002"],
      "amended": {"N-003": "N-005"},
      "findings": {
        "N-001": {"id": "N-001", "n": 1, "title": "A plain result", "body": "About PHercFixtureA only."},
        "N-002": {"id": "N-002", "n": 2, "title": "Old claim", "body": "PHercFixture was wrong."},
        "N-003": {"id": "N-003", "n": 3, "title": "Later corrected", "body": "PHercFixture detail."},
        "N-004": {"id": "N-004", "n": 4, "title": "Retraction", "body": "retracts it",
                  "retracts": ["N-002"]},
        "N-005": {"id": "N-005", "n": 5, "title": "Erratum mentioning the word ERRATUM",
                  "body": "PHercFixture amended", "amends": "N-003"},
        "N-006": {"id": "N-006", "n": 6, "title": "Discusses an erratum in prose",
                  "body": "this is not itself an ERRATUM and names PHercFixture"},
      },
    }
    r = query(scroll="PHercFixture", doc=doc)
    ids = {x["id"] for x in r["rows"]}
    ck("word boundary: PHercFixture does not match inside PHercFixtureA", "N-001" not in ids)
    ck("retracted finding is SUPERSEDED",
       next(x for x in r["rows"] if x["id"] == "N-002")["kind"] == SUPERSEDED)
    ck("a finding later corrected is AMENDED",
       next(x for x in r["rows"] if x["id"] == "N-003")["kind"] == AMENDED)
    ck("a finding that amends another is an ERRATUM",
       next(x for x in r["rows"] if x["id"] == "N-005")["kind"] == ERRATUM)
    ck("SABOTAGE prose containing the word ERRATUM is still CURRENT",
       next(x for x in r["rows"] if x["id"] == "N-006")["kind"] == CURRENT)
    ck("relation is MENTIONS, never ABOUT", r["relation"] == "MENTIONS")
    ck("newest first", [x["n"] for x in r["rows"]] == sorted([x["n"] for x in r["rows"]], reverse=True))
    ck("kinds filter narrows rows but counts stay complete",
       query(scroll="PHercFixture", kinds=[SUPERSEDED], doc=doc)["counts_by_kind"][CURRENT] >= 1
       and all(x["kind"] == SUPERSEDED
               for x in query(scroll="PHercFixture", kinds=[SUPERSEDED], doc=doc)["rows"]))
    ck("free-text search within a scroll", {x["id"] for x in query(
       scroll="PHercFixture", q="amended", doc=doc)["rows"]} == {"N-005"})
    ck("an unreadable corpus says so instead of reporting zero",
       query(doc={"present": False, "why": "gone"})["present"] is False)
    ck("limit is clamped", query(doc=doc, limit=10_000)["limit"] == MAX_LIMIT)
    ck("community claims are a separate list with their own status",
       "claims" in community() and "rows" not in community())
    for n, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", n))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="findings query")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scroll")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    r = query(scroll=a.scroll, limit=5)
    print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=1))
    for x in r.get("rows", []):
        print("  %-8s %-10s %s" % (x["id"], x["kind"], (x["title"] or "")[:70]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
