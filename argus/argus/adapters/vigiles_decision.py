"""Stage 4 (VIGILES) decision adapter: not part of the public ARGUS release.

The operator's build turns ink maps into a decision under a frozen decision rule and a set of
licences. That rule and its calibration are not shipped, so this stage refuses honestly: it
reports that it cannot handle any input, and running it raises a CERTIFICATION refusal. The
blinding-marker names stay here because the feed and the receipts service use them to keep
sealed experiments sealed.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.contracts import Module, Refusal, Terminal, sha256_file

BLINDING_MARKERS = ("BLINDING_RECORD.txt", "PREREG_LOCK.txt")

_UNAVAILABLE = "the stage-4 decision rule is not part of the public release"


class VigilesDecision(Module):
    name = "stage4.vigiles_decision"

    CERTIFIES = Terminal.CERTIFIED_2D

    def inspect(self, ctx: dict) -> dict:
        return {"module": self.name, "can_handle": False, "why_not": _UNAVAILABLE,
                "blinded_by": None, "rule_module": None}

    def plan(self, ctx: dict) -> dict:
        return {"module": self.name, "rule": None, "rule_sha256": None,
                "available": False, "why": _UNAVAILABLE, "writes": []}

    def run(self, ctx: dict, attempt_dir: Path) -> dict:
        raise Refusal("CERTIFICATION", _UNAVAILABLE, {"available": False})

    def verify(self, ctx: dict, attempt_dir: Path) -> dict:
        p = Path(attempt_dir) / "decision.json"
        if not p.is_file():
            return {"verdict": "REFUSED", "failures": ["no decision written"]}
        rec = json.loads(p.read_text(encoding="utf-8"))
        bad = [k for k, v in (rec.get("licences") or {}).items() if not v.get("granted")]
        return {"verdict": "PASS" if rec.get("licences") and not bad else "REFUSED",
                "denied_licences": bad, "receipt_sha256": sha256_file(p)}


def selftest() -> bool:
    import tempfile

    ck = []
    m = VigilesDecision()
    ck.append(("the public stage refuses to handle any input",
               m.inspect({})["can_handle"] is False))
    try:
        m.run({}, Path(tempfile.gettempdir()))
        refused = False
    except Refusal:
        refused = True
    ck.append(("running it is a refusal, not a result", refused))
    ck.append(("the blinding markers are still declared", len(BLINDING_MARKERS) == 2))
    ok = all(g for _, g in ck)
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
