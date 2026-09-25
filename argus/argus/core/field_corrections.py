"""Append-only, typed corrections to a single FIELD of an already-written record."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

CONTRACT = "argus-field-corrections-v1"

STORE = pathlib.Path(__file__).resolve().parents[2] / "corpus" / "field_corrections.json"

CORRECTABLE = ("selftest", "command", "raw")


class CorrectionRefusal(RuntimeError):
    """Raised rather than applying a correction whose target cannot be confirmed."""


def _h(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def load(path: pathlib.Path | None = None) -> dict:
    p = path or STORE
    if not p.is_file():
        return {"contract": CONTRACT, "corrections": []}
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("contract") != CONTRACT:
        raise CorrectionRefusal(
          "correction store declares contract %r, expected %r. A store whose contract is unknown "
          "is not applied." % (doc.get("contract"), CONTRACT))
    return doc


def validate(corrections: list, entries: dict) -> list:
    """Every reason a correction may not be applied."""
    problems = []
    live: dict = {}
    for i, c in enumerate(corrections):
        tag = "correction[%d] %s/%s" % (i, c.get("finding_id"), c.get("field"))

        for required in ("document", "finding_id", "field", "original_value",
                         "original_sha256", "replacement_value", "replacement_sha256",
                         "correcting_receipt", "reason", "observed_utc", "record_uid"):
            if not str(c.get(required, "")).strip():
                problems.append("%s: missing %s" % (tag, required))
        if problems and problems[-1].startswith(tag):
            continue

        if c["field"] not in CORRECTABLE:
            problems.append("%s: field %r is not correctable; %s"
                            % (tag, c["field"], list(CORRECTABLE)))
            continue

        e = entries.get(c["finding_id"])
        if e is None:
            problems.append("%s: MISSING TARGET -- no such finding in the parsed document" % tag)
            continue

        actual = e.get(c["field"], "")
        if _h(actual) != c["original_sha256"]:
            problems.append(
              "%s: CHANGED ORIGINAL -- the field now hashes to %s, the correction records %s. "
              "The correction no longer describes what is there and is refused rather than "
              "applied to whatever replaced it." % (tag, _h(actual)[:12],
                                                    c["original_sha256"][:12]))
            continue
        if c["field"] not in e:
            problems.append("%s: NONEXISTENT FIELD on the target record" % tag)
            continue
        if _h(c["replacement_value"]) != c["replacement_sha256"]:
            problems.append("%s: MALFORMED -- replacement does not match its own hash" % tag)
            continue
        if c["replacement_value"] == c["original_value"]:
            problems.append("%s: replacement is identical to the original" % tag)
            continue

        key = (c["document"], c["record_uid"], c["field"])
        if key in live:
            problems.append(
              "%s: TWO LIVE CORRECTIONS for one field. Ambiguity about which is in force is "
              "worse than no correction; supersede the earlier one explicitly." % tag)
            continue
        for other in corrections:
            if other is c:
                continue
            if (other.get("record_uid") == c["record_uid"]
                    and other.get("field") == c["field"]
                    and other.get("original_value") == c["replacement_value"]):
                problems.append("%s: CORRECTION LOOP -- its replacement is another "
                                "correction's original" % tag)
                break
        else:
            live[key] = c
    return problems


def effective(entries: dict, corrections: list) -> dict:
    """The corrected view."""
    problems = validate(corrections, entries)
    if problems:
        raise CorrectionRefusal("corrections refused:\n  - " + "\n  - ".join(problems))
    out = {k: dict(v) for k, v in entries.items()}
    applied = []
    for c in corrections:
        e = out.get(c["finding_id"])
        if e is None:
            continue
        e[c["field"]] = c["replacement_value"]
        e.setdefault("_corrected_fields", {})[c["field"]] = {
          "original_value": c["original_value"],
          "correcting_receipt": c["correcting_receipt"],
          "reason": c["reason"],
        }
        applied.append("%s/%s" % (c["finding_id"], c["field"]))
    return {"entries": out, "applied": applied,
            "raw_is_unchanged": "the historical value remains in the document and in "
                                "_corrected_fields; this view is derived, never written back."}


def make(*, document: str, finding_id: str, record_uid: str, field: str,
         original_value: str, replacement_value: str, correcting_receipt: str,
         reason: str) -> dict:
    return {
      "contract": CONTRACT,
      "document": document, "finding_id": finding_id, "record_uid": record_uid,
      "field": field,
      "original_value": original_value, "original_sha256": _h(original_value),
      "replacement_value": replacement_value, "replacement_sha256": _h(replacement_value),
      "correcting_receipt": correcting_receipt, "reason": reason,
      "observed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def append(new: list, path: pathlib.Path | None = None) -> dict:
    """Append-only: the stored list may grow and may never lose or reorder an entry."""
    p = path or STORE
    doc = load(p)
    before = list(doc["corrections"])
    doc["corrections"] = before + list(new)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    after = load(p)["corrections"]
    if after[:len(before)] != before:
        raise CorrectionRefusal("the correction store was reordered or truncated; refusing")
    return {"added": len(new), "total": len(after), "path": str(p)}


def as_record() -> dict:
    doc = load()
    return {"contract": CONTRACT, "path": str(STORE),
            "corrections": len(doc["corrections"]),
            "by_field": {f: sum(1 for c in doc["corrections"] if c.get("field") == f)
                         for f in CORRECTABLE},
            "append_only": "corrections are records, not edits. The corrected document keeps "
                           "every byte and a raw parser still sees the original value."}


def selftest() -> bool:
    ok = []
    entries = {"N-1": {"selftest": "`python -m pytest x.py`", "claim": "c"},
               "N-2": {"selftest": "none -- reason", "claim": "c"}}
    good = make(document="docs/FINDINGS.md", finding_id="N-1", record_uid="N-1@abc",
                field="selftest", original_value=entries["N-1"]["selftest"],
                replacement_value="`python argus/core/x.py --selftest`",
                correcting_receipt="N-9", reason="schema form")

    ok.append(("a well-formed correction validates", validate([good], entries) == []))
    view = effective(entries, [good])
    ok.append(("the effective view is corrected",
               view["entries"]["N-1"]["selftest"].endswith("--selftest`")))
    ok.append(("the RAW entry is untouched",
               entries["N-1"]["selftest"] == "`python -m pytest x.py`"))
    ok.append(("the original remains reachable in the corrected view",
               view["entries"]["N-1"]["_corrected_fields"]["selftest"]["original_value"]
               == "`python -m pytest x.py`"))

    def refuses(c, needle, ents=entries):
        p = validate([c], ents)
        return any(needle in x for x in p)

    miss = dict(good, finding_id="N-404")
    ok.append(("missing target refused", refuses(miss, "MISSING TARGET")))
    changed = dict(good, original_sha256=_h("something else"))
    ok.append(("changed original refused", refuses(changed, "CHANGED ORIGINAL")))
    badfield = dict(good, field="status")
    ok.append(("uncorrectable field refused", refuses(badfield, "not correctable")))
    malformed = dict(good, replacement_sha256=_h("different"))
    ok.append(("malformed replacement refused", refuses(malformed, "MALFORMED")))
    noop = dict(good, replacement_value=good["original_value"],
                replacement_sha256=good["original_sha256"])
    ok.append(("no-op replacement refused", refuses(noop, "identical")))
    incomplete = dict(good)
    incomplete["correcting_receipt"] = ""
    ok.append(("missing receipt refused", refuses(incomplete, "missing correcting_receipt")))
    two = validate([good, dict(good)], entries)
    ok.append(("two live corrections refused", any("TWO LIVE" in x for x in two)))
    loop_b = make(document="docs/FINDINGS.md", finding_id="N-1", record_uid="N-1@abc",
                  field="selftest",
                  original_value="`python argus/core/x.py --selftest`",
                  replacement_value=entries["N-1"]["selftest"],
                  correcting_receipt="N-10", reason="loop")
    ok.append(("a correction loop refused",
               any("LOOP" in x for x in validate([good, loop_b], entries))))

    for name, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS append-only field corrections")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    print(json.dumps(as_record(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
