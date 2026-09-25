"""Prove two arms saw the SAME thing, so a difference between them is about the arms."""
from __future__ import annotations

import hashlib
import json

CONTRACT = "argus-arm-isolation-v1"

MATCHED_FIELDS = (
  "input_array_hashes",
  "fold_membership",
  "control_suite",
  "presentation_budget",
  "preprocessing",
  "metric",
)

VARYING_FIELD = "information_class"

WHY_EACH_MATTERS = {
  "input_array_hashes": "'the same segment' is not the same bytes. A store can be re-fetched, a "
                        "mask re-derived, a crop re-cut -- all under the same name.",
  "fold_membership": "compared on PHYSICAL identity, so an alias or a second pitch cannot move "
                     "a scroll between arms while every string matches.",
  "control_suite": "an arm that ran nine of ten negative controls has a different floor under "
                   "it, and the missing one is never the easy one.",
  "presentation_budget": "a longer budget is a model-hours advantage, and it is the single "
                         "easiest thing to extend for the arm somebody hopes will win.",
  "preprocessing": "normalisation, depth window, orientation policy and pitch adapter each move "
                   "a score on their own.",
  "metric": "two implementations of one metric can disagree on the same fixture, so exactly "
            "one implementation is allowed.",
}


class IsolationRefusal(RuntimeError):
    """Raised when a comparison between arms would not be about the arms."""


def _canon(obj):
    """Canonicalise for hashing."""
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return sorted((_canon(v) for v in obj), key=lambda x: json.dumps(x, sort_keys=True,
                                                                        default=str))
    return obj


def _sha(obj) -> str:
    return hashlib.sha256(
      json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), default=str)
      .encode("utf-8")).hexdigest()


def _physical(name: str) -> str:
    from argus.core import control_matrix as CM
    return CM.physical_identity(name)


def environment_hash(env: dict) -> dict:
    """Hash each matched field separately."""
    if not isinstance(env, dict):
        raise IsolationRefusal("an arm environment must be a mapping; got %r"
                               % type(env).__name__)
    missing = [f for f in MATCHED_FIELDS if env.get(f) is None]
    if missing:
        raise IsolationRefusal(
          "arm %r does not declare %s. An undeclared field cannot be shown to have matched, and "
          "'it was probably the same' is the assumption this module exists to remove."
          % (env.get("arm", "<unnamed>"), ", ".join(missing)))
    if env.get(VARYING_FIELD) is None:
        raise IsolationRefusal(
          "arm %r does not declare its %s. Without it there is no statement of what the "
          "comparison is supposed to be measuring." % (env.get("arm", "<unnamed>"),
                                                       VARYING_FIELD))
    out = {}
    for f in MATCHED_FIELDS:
        v = env[f]
        if f == "fold_membership":
            v = {k: sorted({_physical(s) for s in (vv or [])})
                 for k, vv in sorted((v or {}).items())}
        out[f] = _sha(v)
    return out


def assert_isolated(arms) -> dict:
    """Refuse a comparison whose arms did not see the same thing."""
    arms = list(arms)
    if len(arms) < 2:
        raise IsolationRefusal(
          "an isolation check needs at least two arms; %d supplied. Checking one arm against "
          "itself is how a harness reports green while guaranteeing nothing." % len(arms))

    names = [a.get("arm") for a in arms]
    if any(not n for n in names):
        raise IsolationRefusal("every arm must be named; got %r" % (names,))
    if len(set(names)) != len(names):
        raise IsolationRefusal(
          "two arms share a name (%r). A comparison cannot report which arm won." % (names,))

    hashes = {a["arm"]: environment_hash(a) for a in arms}
    classes = {a["arm"]: a[VARYING_FIELD] for a in arms}

    if len(set(json.dumps(c, sort_keys=True, default=str) for c in classes.values())) == 1:
        raise IsolationRefusal(
          "every arm declares the same %s (%r). The arms are identical, so any difference "
          "between their scores is noise being read as an effect." % (VARYING_FIELD,
                                                                      list(classes.values())[0]))

    base_name = names[0]
    base = hashes[base_name]
    differences = []
    for n in names[1:]:
        for f in MATCHED_FIELDS:
            if hashes[n][f] != base[f]:
                differences.append({"field": f, "arms": [base_name, n],
                                    "why_it_matters": WHY_EACH_MATTERS[f],
                                    base_name: base[f][:16], n: hashes[n][f][:16]})

    if differences:
        lines = ["  %-22s %s vs %s\n      %s"
                 % (d["field"], d[d["arms"][0]], d[d["arms"][1]], d["why_it_matters"])
                 for d in differences]
        raise IsolationRefusal(
          "the arms did not see the same thing. %d matched field(s) differ:\n%s\n"
          "A difference between these arms would be a mixture of the arm and whatever else "
          "moved, reported as the arm. There is no correct way to retrofit a matched control "
          "onto a run that did not have one, so this is a refusal rather than a warning."
          % (len(differences), "\n".join(lines)))

    return {
      "contract": CONTRACT,
      "isolated": True,
      "arms": names,
      "matched_fields": list(MATCHED_FIELDS),
      "matched_hashes": base,
      "varying_field": VARYING_FIELD,
      "varying_values": classes,
      "what_this_licenses": "a difference between these arms' scores may be attributed to the "
                            "declared %s and to nothing else in the matched set." % VARYING_FIELD,
      "what_this_does_not_license": [
        "that the difference is real rather than noise -- that needs the uncertainty",
        "that either arm detected ink",
        "that an unmatched factor nobody declared was equal",
      ],
    }


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    def env(arm, cls, **over):
        e = {
          "arm": arm,
          "information_class": cls,
          "input_array_hashes": {"seg1": "a" * 64, "seg2": "b" * 64},
          "fold_membership": {"train": ["PHercFixture1", "PHercFixture2"], "holdout": ["PHercFixture3"]},
          "control_suite": ["permutation_null", "planted_ink", "detection_floor"],
          "presentation_budget": 1000,
          "preprocessing": {"normalisation": "global", "depth": 8},
          "metric": "argus-metric-v1",
        }
        e.update(over)
        return e

    a, b = env("A", "class_a"), env("B", "class_b")
    r = assert_isolated([a, b])
    ck("matched arms with different classes pass", r["isolated"])
    ck("it names what the pass licenses", "and to nothing else" in r["what_this_licenses"])
    ck("it names what the pass does NOT license",
       any("noise" in s for s in r["what_this_does_not_license"]))

    for field, bad in (("presentation_budget", 4000),
                       ("control_suite", ["permutation_null", "planted_ink"]),
                       ("metric", "some-other-auc"),
                       ("preprocessing", {"normalisation": "fitted", "depth": 8}),
                       ("input_array_hashes", {"seg1": "a" * 64, "seg2": "c" * 64})):
        try:
            assert_isolated([a, env("B", "class_b", **{field: bad})])
            ck("a differing %s is refused" % field, False)
        except IsolationRefusal as e:
            ck("a differing %s is refused" % field, field in str(e))

    try:
        assert_isolated([a, env("B", "class_b",
                                fold_membership={"train": ["PHercFixture1", "PHercFixture2"],
                                                 "holdout": ["PHercFixture4"]})])
        ck("a differing fold is refused", False)
    except IsolationRefusal as e:
        ck("a differing fold is refused", "fold_membership" in str(e))

    alias = env("B", "class_b",
                fold_membership={"train": ["phercfixture1", "PHERCFIXTURE2"],
                                 "holdout": ["phercfixture3"]})
    try:
        r2 = assert_isolated([a, alias])
        ck("the same folds spelled differently still MATCH (physical identity)", r2["isolated"])
    except IsolationRefusal:
        ck("the same folds spelled differently still MATCH (physical identity)", False)

    reordered = env("B", "class_b",
                    control_suite=["detection_floor", "planted_ink", "permutation_null"])
    try:
        ck("reordering the control suite does not trip the alarm",
           assert_isolated([a, reordered])["isolated"])
    except IsolationRefusal:
        ck("reordering the control suite does not trip the alarm", False)

    try:
        assert_isolated([a])
        ck("one arm is refused", False)
    except IsolationRefusal as e:
        ck("one arm is refused", "guaranteeing nothing" in str(e))

    try:
        assert_isolated([a, env("B", "class_a")])
        ck("two arms with the SAME class are refused", False)
    except IsolationRefusal as e:
        ck("two arms with the SAME class are refused", "noise being read as an effect" in str(e))

    incomplete = env("B", "class_b")
    del incomplete["control_suite"]
    try:
        assert_isolated([a, incomplete])
        ck("an undeclared matched field is refused", False)
    except IsolationRefusal as e:
        ck("an undeclared matched field is refused", "control_suite" in str(e))

    try:
        assert_isolated([a, env("A", "class_b")])
        ck("two arms sharing a name are refused", False)
    except IsolationRefusal:
        ck("two arms sharing a name are refused", True)

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
    print(json.dumps({"contract": CONTRACT, "matched_fields": list(MATCHED_FIELDS),
                      "varying_field": VARYING_FIELD, "why": WHY_EACH_MATTERS}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
