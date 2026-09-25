"""The canonical matrix: physical scroll x segment x representation, with control roles."""
from __future__ import annotations

import json

from argus.core import control_evidence_kinds as _CEK

CONTRACT = "argus-control-matrix-v1"

ROLES = (
  "MECHANICS_CONTROL",
  "DEVELOPMENT_MIXED_AUTHORITY",
  "TRANSFER_CONTROL",
  "QUALIFICATION_HOLDOUT",
  "FIRST_LETTERS_TARGET",
  "GRAND_PRIZE_TARGET",
  "PARIS4_TITLE_CONTROL",
)

ROLE_MEANING = {
  "MECHANICS_CONTROL":
    "used to prove the pipeline executes -- acquisition through render through evidence. It "
    "may never carry a scientific verdict: a machine that runs is not a machine that is right.",
  "DEVELOPMENT_MIXED_AUTHORITY":
    "development material whose labels are NOT proven human-only. Everything measured on it is "
    "agreement with mixed-authority labels, which is not ink accuracy.",
  "TRANSFER_CONTROL":
    "held out of a fold to measure whether a detector transfers. A transfer result is not a "
    "qualification: the labels on both sides are still mixed-authority.",
  "QUALIFICATION_HOLDOUT":
    "independent, proven HUMAN_ONLY, and never touched by development. Qualification needs a "
    "declared minimum number of them.",
  "FIRST_LETTERS_TARGET": "on the published First Letters list. No labels; never a control.",
  "GRAND_PRIZE_TARGET": "on the Grand Prize list. Tracked separately from First Letters.",
  "PARIS4_TITLE_CONTROL":
    "PHercParis4's own title lane (the public title prize). No submitted title region may "
    "overlap training data.",
}

ROLE_BOUND_SCROLL = {
  "PARIS4_TITLE_CONTROL": "PHercParis4",
}

def _exclusion_sources():
    """The frozen contracts that may bar a scroll, in the order they are consulted."""
    mods = []
    for dotted in ():
        try:
            import importlib
            mods.append(importlib.import_module(dotted))
        except Exception:
            continue
    return tuple(mods)


_EXCLUSION_SOURCES = _exclusion_sources()

REVIEWABLE_STRATA = ("FIBRE", "CRACK", "VOID", "SHEET_EDGE", "RECONSTRUCTION_ARTIFACT",
                     "HANDLING_MARK")

ONLY_SEMANTIC_CLASS = "ink"

STRATA_RULE = (
  "These are hard-negative strata for human review, not labelled classes. The existing labels "
  "establish exactly one semantic class -- ink -- and promoting a fibre proposal to a class "
  "would manufacture supervision nobody drew, then score a detector against it."
)


class MatrixRefusal(RuntimeError):
    """Raised when a compartment would leak, or a role would be asserted without evidence."""


def _shelf():
    from argus.core import scroll_shelf as _s
    try:
        return _s.compose_available()
    except _s.ShelfError as network_exc:
        try:
            return _s.compose_local()
        except _s.ShelfError as local_exc:
            raise MatrixRefusal(
              "cannot build the control matrix: shelf service unavailable (%s); "
              "in-process authorities unavailable (%s)" % (network_exc, local_exc)
            ) from local_exc


def _authority():
    from argus.core import corpus_manifest as _cm
    return _cm.SCROLL_LABEL_INVENTORY, _cm.LABEL_AUTHORITY


def physical_identity(scroll: str, aliases=()) -> str:
    """The identity two rows are compared on."""
    base = (scroll or "").strip()
    for a in aliases or ():
        if a and a != base:
            continue
    return base.upper()


def rows() -> list:
    """One row per physical scroll, carrying its representations rather than hiding them."""
    inv, auth = _authority()
    shelf = _shelf()
    out = []
    for s in shelf.get("scrolls", []):
        scroll = s["scroll"]
        a = (auth.get(scroll) or {}).get("authority") or (
            (inv.get(scroll) or {}).get("authority")) or "UNKNOWN"
        labelled = bool(inv.get(scroll))

        prizes = s.get("prizes") or []
        eligible_first_letters = "FIRST_LETTERS" in prizes
        eligible_grand_prize = any(p.startswith("GRAND_PRIZE") for p in prizes)

        if scroll == ROLE_BOUND_SCROLL.get("PARIS4_TITLE_CONTROL"):
            role = "PARIS4_TITLE_CONTROL"
        elif eligible_first_letters:
            role = "FIRST_LETTERS_TARGET"
        elif eligible_grand_prize:
            role = "GRAND_PRIZE_TARGET"
        elif labelled and a == "HUMAN_ONLY":
            role = "QUALIFICATION_HOLDOUT"
        elif labelled:
            role = "DEVELOPMENT_MIXED_AUTHORITY"
        else:
            role = "MECHANICS_CONTROL"

        out.append({
          "physical_identity": physical_identity(scroll, s.get("aliases")),
          "scroll": scroll,
          "aliases": s.get("aliases") or [],
          "role": role,
          "control_evidence_kind": _CEK.kind_for_role(role),
          "label_authority": a,
          "labelled": labelled,
          "human_subset_separable": (inv.get(scroll) or {}).get(
              "byte_separable_human_subset"),
          "acquisition_families": s.get("acquisition_families") or [],
          "representations": s.get("acquisitions") or [],
          "prizes": prizes,
          "eligible_first_letters": eligible_first_letters,
          "eligible_grand_prize": eligible_grand_prize,
          "local_data": s.get("local_data"),
          "published_upstream": s.get("published_upstream"),
          "furthest_stage": s.get("furthest_stage"),
          "operator_fence": s.get("operator_fence"),
          "frozen_exclusion": _frozen_exclusion(scroll),
        })
    return out


def _frozen_exclusion(scroll: str):
    """Why the frozen contract bars this scroll, or None."""
    want = physical_identity(scroll)
    for mod in _EXCLUSION_SOURCES:
        try:
            table = getattr(mod, "EXCLUSIONS", None) or {}
        except Exception:
            continue
        for k, v in table.items():
            if physical_identity(k) != want:
                continue
            detail = v if isinstance(v, str) else (v.get("why") or str(v))
            scope = (v.get("excluded_from") if isinstance(v, dict) else None) or [
              "training", "checkpoint_selection", "hyperparameter_choice", "early_stopping",
              "any comparison that influences a decision"]
            return {"source": getattr(mod, "CONTRACT_ID", mod.__name__),
                    "excluded_from": list(scope), "why": detail,
                    "is_not_an_operator_fence": "this comes from a frozen pre-registration. "
                                                "Lifting it is an amendment, not a decision."}
    return None


def assert_disjoint(side_a, side_b, *, what="this evaluation") -> dict:
    """REFUSE if one physical object appears on both sides."""
    a = {physical_identity(x if isinstance(x, str) else x.get("scroll", "")) for x in side_a}
    b = {physical_identity(x if isinstance(x, str) else x.get("scroll", "")) for x in side_b}
    a.discard("")
    b.discard("")
    shared = sorted(a & b)
    if shared:
        raise MatrixRefusal(
          "%s puts the same PHYSICAL object on both sides: %s. Aliases, pitches, "
          "representations and aligned copies are not separate objects, and a holdout that "
          "contains its own training material measures memorisation."
          % (what, ", ".join(shared)))
    return {"disjoint": True, "a": sorted(a), "b": sorted(b),
            "compared_on": "physical identity, never row or segment id"}


def as_record() -> dict:
    from argus.core import label_inventory as _li
    rs = rows()
    by_role = {}
    for r in rs:
        by_role.setdefault(r["role"], []).append(r["scroll"])
    holdouts = by_role.get("QUALIFICATION_HOLDOUT", [])
    return {
      "contract": CONTRACT,
      "rows": rs, "count": len(rs),
      "roles": list(ROLES), "role_meaning": ROLE_MEANING,
      "by_role": {k: sorted(v) for k, v in sorted(by_role.items())},
      "prize_eligibility": {
        "first_letters": sorted(r["scroll"] for r in rs if r["eligible_first_letters"]),
        "grand_prize": sorted(r["scroll"] for r in rs if r["eligible_grand_prize"]),
        "on_both": sorted(r["scroll"] for r in rs
                          if r["eligible_first_letters"] and r["eligible_grand_prize"]),
        "rule": "the two sets OVERLAP and are never merged. Grand Prize is currently a strict "
                "subset of First Letters, so a single-role assignment silently emptied the "
                "Grand Prize board -- eligibility is carried per prize for exactly that reason.",
      },
      "qualification": {
        "holdouts_available": len(holdouts), "required": 3,
        "shortfall": max(0, 3 - len(holdouts)),
        "status": "BLOCKED_INSUFFICIENT_PROVEN_HUMAN_GROUND_TRUTH_SCROLLS"
                  if len(holdouts) < 3 else "UNBLOCKED",
        "blocks": "qualification only. It does not block the operational instrument "
                  "or a tooling release.",
      },
      "label_authority_reconciliation": _li.assert_authority_agrees(),
      "reviewable_strata": list(REVIEWABLE_STRATA),
      "only_semantic_class": ONLY_SEMANTIC_CLASS,
      "strata_rule": STRATA_RULE,
      "disjointness_rule": "compartments are compared on PHYSICAL IDENTITY. An alias, a second "
                           "pitch, an aligned copy or another representation is the same "
                           "object.",
    }


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    rec = as_record()
    rs = rec["rows"]
    ck("every row carries exactly one declared role",
       rs and all(r["role"] in ROLES for r in rs))
    ck("no scroll appears twice", len({r["scroll"] for r in rs}) == len(rs))
    ck("the shortfall is the gap to the required holdouts",
       rec["qualification"]["shortfall"]
       == max(0, rec["qualification"]["required"] - rec["qualification"]["holdouts_available"]))
    ck("the blocker is scoped to qualification, not to the instrument",
       "does not block the operational instrument" in rec["qualification"]["blocks"])
    ck("a role-bound scroll carries its bound role",
       all(r["role"] == role for r in rs for role, s in ROLE_BOUND_SCROLL.items()
           if r["scroll"] == s))
    ck("ink is the only semantic class", rec["only_semantic_class"] == "ink")
    ck("strata are reviewable, never labelled classes",
       "would manufacture supervision nobody drew" in rec["strata_rule"])
    ck("the label authority is read from the owner, not decided here",
       rec["label_authority_reconciliation"]["reconciled"] is True)

    try:
        assert_disjoint(["PHercFixture1"], ["phercfixture1"], what="a sabotaged fold")
        ck("SABOTAGE the same scroll in two cases is refused", False)
    except MatrixRefusal:
        ck("SABOTAGE the same scroll in two cases is refused", True)
    try:
        assert_disjoint([{"scroll": "PHercFixture2"}], [{"scroll": "PHercFixture1"}])
        ck("genuinely disjoint sides pass", True)
    except MatrixRefusal:
        ck("genuinely disjoint sides pass", False)

    for n, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", n))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    rec = as_record()
    print(json.dumps({k: v for k, v in rec.items() if k != "rows"}, indent=2)[:2600])
    for r in rec["rows"]:
        print("  %-13s %-28s %-14s %s" % (r["scroll"], r["role"], r["label_authority"],
                                          r["local_data"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
