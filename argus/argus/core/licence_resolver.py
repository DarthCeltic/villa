"""No component enters ARGUS without a source, a revision, a hash, a licence and a redistribution policy."""
from __future__ import annotations

import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_os.path.dirname(_HERE))
_sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or '.') != _HERE]
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import dataclasses
import json
import pathlib

CONTRACT = "argus-licence-resolver-v1"

UNDECLARED = "UNDECLARED"

KNOWN = {
  "apache-2.0": {"redistribute_weights": True, "attribution_required": True,
                 "note": "the three official ScrollPrize surface/fibre models."},
  "mit":        {"redistribute_weights": True, "attribution_required": True,
                 "note": "permissively licensed community code and models."},
  "cc-by-nc-4.0": {"redistribute_weights": False, "attribution_required": True,
                   "note": "the underlying CT. NON-COMMERCIAL: this is why a prize package "
                           "cannot simply bundle source data."},
  "gpl-3.0":    {"redistribute_weights": True, "attribution_required": True,
                 "note": "the villa upstream. Copy no implementation, tests or distinctive "
                         "structure -- the standing rule is stricter than the licence because "
                         "the risk is contamination, not infringement."},
}


class LicenceRefusal(RuntimeError):
    """Raised rather than returning a permissive default."""


@dataclasses.dataclass(frozen=True)
class Component:
    key: str
    source: str
    revision: str
    sha256: str
    licence: str
    redistribute: str
    kind: str
    notes: str = ""

    def problems(self) -> list:
        out = []
        for f in ("source", "revision", "sha256", "licence"):
            v = getattr(self, f)
            if not str(v or "").strip() or str(v).strip().upper() == UNDECLARED:
                out.append("%s is %s. Undeclared is a refusal, never a presumption." % (f,
                                                                                        UNDECLARED))
        if self.revision and self.revision.strip().lower() in ("main", "master", "latest",
                                                               "head"):
            out.append("revision %r is a moving reference. `main` moves and a receipt that names "
                       "a branch names nothing; pin the immutable commit." % self.revision)
        if str(self.redistribute).strip().upper() not in ("YES", "NO", "UNKNOWN"):
            out.append("redistribute must be YES, NO or UNKNOWN. UNKNOWN is a real answer and "
                       "means the artifact stays on this machine.")
        return out


def check(c: Component) -> dict:
    probs = c.problems()
    lic = KNOWN.get(str(c.licence).strip().lower())
    return {
      "contract": CONTRACT, "component": c.key, "ok": not probs, "problems": probs,
      "licence_known": bool(lic), "licence_terms": lic,
      "may_redistribute": may_redistribute(c),
      "declared": dataclasses.asdict(c),
    }


def may_redistribute(c: Component) -> bool:
    """Fail-closed."""
    if c.problems():
        return False
    if str(c.redistribute).strip().upper() != "YES":
        return False
    lic = KNOWN.get(str(c.licence).strip().lower())
    return bool(lic and lic.get("redistribute_weights"))


def require(c: Component) -> dict:
    r = check(c)
    if not r["ok"]:
        raise LicenceRefusal("component %s refused:\n  - %s"
                             % (c.key, "\n  - ".join(r["problems"])))
    return r


def from_official_models() -> list:
    """The three pinned models, expressed as components."""
    from argus.core import official_models as OM
    out = []
    for m in OM.MODELS:
        out.append(Component(
          key=m.repo_id.split("/")[-1], source="huggingface:%s" % m.repo_id,
          revision=m.revision, sha256=m.weight_sha256, licence=m.licence,
          redistribute=("YES" if KNOWN.get(str(m.licence).strip().lower(), {})
                        .get("redistribute_weights") else "UNKNOWN"),
          kind="model",
          notes="declared labels %s; semantic state %s" % (list(m.labels), m.semantic_state)))
    return out


def register() -> dict:
    comps = from_official_models()
    rows = [check(c) for c in comps]
    return {
      "contract": CONTRACT,
      "components": len(rows),
      "rows": rows,
      "all_declared": all(r["ok"] for r in rows),
      "redistributable": [r["component"] for r in rows if r["may_redistribute"]],
      "withheld": [r["component"] for r in rows if not r["may_redistribute"]],
      "fail_closed": "UNDECLARED behaves like the most restrictive case. A permissive licence is "
                     "a claim that must be evidenced; a restrictive one needs no evidence to be "
                     "honoured.",
    }


def selftest() -> bool:
    """Fail-closed must actually fail closed, not merely intend to."""
    ok = []

    def base(**kw):
        d = dict(key="k", source="hf:o/r", revision="a" * 40, sha256="b" * 64,
                 licence="apache-2.0", redistribute="YES", kind="model")
        d.update(kw)
        return Component(**d)

    ok.append(("declared apache redistributes", may_redistribute(base()) is True))
    ok.append(("UNDECLARED refuses", may_redistribute(base(licence=UNDECLARED)) is False))
    ok.append(("moving revision refuses", may_redistribute(base(revision="main")) is False))
    ok.append(("unknown licence refuses",
               may_redistribute(base(licence="bespoke-1.0")) is False))
    ok.append(("non-commercial refuses",
               may_redistribute(base(licence="cc-by-nc-4.0")) is False))
    ok.append(("redistribute=UNKNOWN refuses",
               may_redistribute(base(redistribute="UNKNOWN")) is False))
    r = register()
    ok.append(("every registered model is declared", r["all_declared"] is True))
    ok.append(("redistribution follows the table, not a hardcoded licence string",
               all(c.redistribute == ("YES" if KNOWN.get(str(c.licence).strip().lower(), {})
                                      .get("redistribute_weights") else "UNKNOWN")
                   for c in from_official_models())))
    for name, good in ok:
        print("  %-4s %s" % ("ok" if good else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS licence resolver")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    import json as _j
    print(_j.dumps(register(), indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
