"""What must be true before a long run starts."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

NON_ANSWERS = frozenset({
  "", "UNKNOWN", "UNDECLARED", "UNRESOLVED", "STALE", "CONTRADICTED", "NONE",
  "NULL", "TBD", "PENDING", "N/A", "NA", "UNSPECIFIED", "UNVERIFIED",
})

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Purpose(Enum):
    """Why the run is being made."""

    LOCAL_RESEARCH = "LOCAL_RESEARCH"
    PUBLIC_RELEASE = "PUBLIC_RELEASE"
    PRIZE_SUBMISSION = "PRIZE_SUBMISSION"


class Exposure(Enum):
    HELD_OUT_BY_FOLD = "HELD_OUT_BY_FOLD"
    TRAINED_ON = "TRAINED_ON"
    UNSEEN_PROVEN = "UNSEEN_PROVEN"


class Compression(Enum):
    ORIGINAL_UNCOMPRESSED = "ORIGINAL_UNCOMPRESSED"
    COMPRESSED_VARIANT_DECLARED = "COMPRESSED_VARIANT_DECLARED"


class Lineage(Enum):
    HUMAN_ANNOTATION = "HUMAN_ANNOTATION"
    PSEUDO_LABEL_QUALIFIED = "PSEUDO_LABEL_QUALIFIED"


ANSWERABILITY = frozenset({"SAVED", "NEEDS_FRESH_INFERENCE"})


class LongRunRefused(RuntimeError):
    """Raised rather than starting a run that cannot produce a defensible result."""


def _enum_or_none(enum_cls, value):
    """Accept only an exact member."""
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        return None
    if value.strip().upper() in NON_ANSWERS:
        return None
    try:
        return enum_cls(value.strip().upper())
    except ValueError:
        return None


def _is_hash(v) -> bool:
    return isinstance(v, str) and bool(_HEX64.match(v.strip().lower()))


def _positive_finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) and v > 0


@dataclass
class Check:
    key: str
    state: str
    detail: str
    why_it_matters: str
    fix: str = ""

    @property
    def blocks(self) -> bool:
        return self.state in (FAIL, UNKNOWN)

    def as_record(self) -> dict:
        return {"key": self.key, "state": self.state, "detail": self.detail,
                "why_it_matters": self.why_it_matters, "fix": self.fix,
                "blocks": self.blocks}


@dataclass
class Plan:
    purpose: Purpose | str | None = None
    receipts_current: bool | None = None
    unincorporated_count: int | None = None
    acquisition_sealed: bool | None = None
    manifest_complete: bool | None = None
    exposure_declared: Exposure | str | None = None
    licence_declared: str | None = None
    pitch_um: float | None = None
    energy_kev: float | None = None
    compression_declaration: Compression | str | None = None
    label_lineage: Lineage | str | None = None
    scoring_population_frozen: bool | None = None
    scoring_population_ink_independent: bool | None = None
    aggregation_frozen: bool | None = None
    orientations: tuple = ()
    checkpoint_sha256: str | None = None
    dependency_sha256: dict = field(default_factory=dict)
    expected_dependencies: tuple = ()
    block_plan_sha256: str | None = None
    smoke: dict | None = None
    answerable_from_saved: dict | None = None


def _bool_check(v, key, ok, bad, why, fix=""):
    if not isinstance(v, bool):
        return Check(key, UNKNOWN, "not stated as a boolean", why, fix)
    return Check(key, PASS if v else FAIL, ok if v else bad, why, fix)


def evaluate(plan: Plan) -> list:
    c = []

    purpose = _enum_or_none(Purpose, plan.purpose)
    c.append(Check("purpose", PASS if purpose else UNKNOWN,
                   purpose.value if purpose else "not stated",
                   "a licence acceptable for local research is not acceptable for a public "
                   "release or a prize submission. One predicate serving all three is wrong "
                   "for two of them, so the purpose must be stated and the policy follows.",
                   "state Purpose.LOCAL_RESEARCH, PUBLIC_RELEASE or PRIZE_SUBMISSION"))

    cur = isinstance(plan.receipts_current, bool) and plan.receipts_current
    cnt = plan.unincorporated_count
    counted = isinstance(cnt, int) and not isinstance(cnt, bool) and cnt == 0
    c.append(Check("project_truth_current",
                   PASS if (cur and counted) else (FAIL if cur or counted else UNKNOWN),
                   "current, 0 unincorporated" if (cur and counted)
                   else "current=%s unincorporated=%s" % (plan.receipts_current, cnt),
                   "a receipts_current flag alone is not enough: a boolean can be true while receipts "
                   "sit unincorporated. Freshness is decided by "
                   "path + sha256 + mtime AND the count must be zero.",
                   "regenerate state and the packet until unincorporated_count is 0"))

    c.append(_bool_check(plan.acquisition_sealed, "acquisition_sealed",
                         "input manifest complete and sealed locally",
                         "acquisition is not sealed",
                         "an unsealed input can fail on the network mid-inference. Sealing first turns a "
                         "network failure into a fast one instead of an expensive one."))
    c.append(_bool_check(plan.manifest_complete, "manifest_complete",
                         "every required object present or a declared 404",
                         "manifest incomplete",
                         "only an HTTP 404 may be read as fill_value. Any other absence is a "
                         "hole in the input that will look like data."))

    exp = _enum_or_none(Exposure, plan.exposure_declared)
    c.append(Check("exposure_declared", PASS if exp else UNKNOWN,
                   exp.value if exp else "not a declared exposure (%r)"
                   % (plan.exposure_declared,),
                   "EXPOSURE_UNKNOWN is a non-answer and used to pass because it is a non-empty "
                   "string. CALIBRATION_ONLY and QUALIFIED_EVIDENCE are different claims and "
                   "an undeclared exposure cannot distinguish them.",
                   "declare HELD_OUT_BY_FOLD, TRAINED_ON or UNSEEN_PROVEN"))

    lic = plan.licence_declared
    lic_named = isinstance(lic, str) and lic.strip().upper() not in NON_ANSWERS
    if purpose is None:
        lic_check = Check("licence_declared", UNKNOWN, "purpose not stated",
                          "the licence predicate cannot be chosen without the purpose.", "")
    elif purpose is Purpose.LOCAL_RESEARCH:
        lic_check = Check(
            "licence_declared", PASS,
            (lic if lic_named else "UNDECLARED -- permitted for LOCAL_RESEARCH and RECORDED "
                                   "as a constraint that travels with any result derived "
                                   "from this run. It BLOCKS public release and prize use."),
            "local research may proceed under an undeclared licence; a public or prize use "
            "may not. The distinction is the reason purpose is a required field.", "")
    else:
        lic_check = Check("licence_declared", PASS if lic_named else FAIL,
                          lic if lic_named else "UNDECLARED blocks %s" % purpose.value,
                          "ARGUS may not publish or submit a component whose terms are "
                          "unknown. A component whose licence is undeclared therefore "
                          "cannot enter a public or prize package.",
                          "declare the licence or remove the component from the package")
    c.append(lic_check)

    comp = _enum_or_none(Compression, plan.compression_declaration)
    c.append(Check("compression_declaration", PASS if comp else UNKNOWN,
                   comp.value if comp else "not resolved (%r)"
                   % (plan.compression_declaration,),
                   "UNRESOLVED used to pass. An unresolved source compression means the run "
                   "measures the detector-plus-compression system and may not be reported as "
                   "detector transfer.",
                   "resolve the source axis from publisher metadata"))

    lin = _enum_or_none(Lineage, plan.label_lineage)
    c.append(Check("label_lineage", PASS if lin else UNKNOWN,
                   lin.value if lin else "not a declared lineage (%r)" % (plan.label_lineage,),
                   "a pseudo-label reproduces its generator's errors. Lineage is part of "
                   "admissibility, not metadata.",
                   "declare HUMAN_ANNOTATION or PSEUDO_LABEL_QUALIFIED"))

    for key, val, why in (
        ("pitch_um", plan.pitch_um,
         "a detector's reference pitch is not necessarily the scroll acquisition pitch. "
         "Normalising toward the wrong reference does not fail loudly."),
        ("energy_kev", plan.energy_kev,
         "acquisition families can differ in beam energy as well as pitch."),
    ):
        ok = _positive_finite(val)
        c.append(Check(key, PASS if ok else UNKNOWN,
                       str(val) if ok else "not a positive finite number (%r)" % (val,), why))

    frozen = isinstance(plan.scoring_population_frozen, bool) and plan.scoring_population_frozen
    indep = plan.scoring_population_ink_independent
    indep_ok = isinstance(indep, bool) and indep
    c.append(Check("scoring_population_frozen_and_ink_independent",
                   PASS if (frozen and indep_ok) else (UNKNOWN if indep is None else FAIL),
                   "frozen and independent of ink" if (frozen and indep_ok)
                   else "frozen=%s ink_independent=%s" % (plan.scoring_population_frozen, indep),
                   "a population selected around ink (for example a supervision region, where a "
                   "person chose to look) confounds the score with the selection. Freezing "
                   "such a population freezes the confound.",
                   "select the scoring population without consulting ink, labels, supervision "
                   "or detector output"))

    c.append(_bool_check(plan.aggregation_frozen, "aggregation_frozen",
                         "aggregation fixed in advance", "aggregation not frozen",
                         "the aggregation must be fixed before any result exists; choosing "
                         "it afterwards chooses the answer."))

    both = tuple(sorted(plan.orientations or ()))
    c.append(Check("both_orientations", PASS if both == ("forward", "reversed") else FAIL,
                   "orientations: %s" % (list(both) or "none"),
                   "a forward-only run can report the better half of a depth-orientation "
                   "pair and call it the result.",
                   "score and report both, separately"))

    c.append(Check("checkpoint_hashed", PASS if _is_hash(plan.checkpoint_sha256) else UNKNOWN,
                   (plan.checkpoint_sha256 or "not stated")
                   if _is_hash(plan.checkpoint_sha256)
                   else "not a 64-hex sha256 (%r)" % (plan.checkpoint_sha256,),
                   "a one-character string used to pass. A run whose model cannot be "
                   "identified cannot be reproduced or defended."))

    deps = plan.dependency_sha256 or {}
    all_hashed = bool(deps) and all(_is_hash(v) for v in deps.values())
    missing = [d for d in (plan.expected_dependencies or ()) if d not in deps]
    c.append(Check("dependencies_hashed",
                   PASS if (all_hashed and not missing) else (FAIL if deps else UNKNOWN),
                   "%d hashed, %d expected missing" % (len(deps), len(missing)),
                   "an edited dependency changes the answer while every filename stays the "
                   "same. Partial coverage is not coverage.",
                   "hash every expected dependency"))

    c.append(Check("block_plan_immutable", PASS if _is_hash(plan.block_plan_sha256) else UNKNOWN,
                   plan.block_plan_sha256 if _is_hash(plan.block_plan_sha256)
                   else "not a 64-hex sha256 (%r)" % (plan.block_plan_sha256,),
                   "crop, stride, fade, TTA, block and halo are IDENTITY, not configuration. "
                   "A replay at a different stride is a different experiment."))

    sm = plan.smoke or {}
    measured = (_positive_finite(sm.get("vram_mib")) and _positive_finite(sm.get("ram_mib"))
                and _positive_finite(sm.get("runtime_s")))
    ok_out = sm.get("output_ok") is True
    c.append(Check("representative_block_smoke",
                   PASS if (measured and ok_out) else (FAIL if sm else UNKNOWN),
                   json.dumps(sm) if sm else "no smoke block run",
                   "zero VRAM, zero runtime and output_ok=False all used to pass because they "
                   "are not None. Measured means positive, finite, and an output that was "
                   "actually well formed.",
                   "run ONE representative block and record real VRAM, RAM, runtime, and "
                   "output_ok exactly True"))

    ans = plan.answerable_from_saved or {}
    bad_vals = {k: v for k, v in ans.items() if v not in ANSWERABILITY}
    c.append(Check("answerable_from_saved_outputs",
                   PASS if (ans and not bad_vals) else (FAIL if ans else UNKNOWN),
                   "%d declared" % len(ans) if (ans and not bad_vals)
                   else ("invalid values: %s" % bad_vals if bad_vals else "not stated"),
                   "THE CLAUSE PEOPLE SKIP, and it accepted 'MAYBE_LATER'. Declaring which "
                   "analyses are answerable from saved outputs is what turns a run into a "
                   "reusable asset instead of a number -- a scorer that saves only a blended map "
                   "and not per-tile outputs forces the whole pass to be repeated for later "
                   "analyses.",
                   "every value must be SAVED or NEEDS_FRESH_INFERENCE"))

    return c


def assert_ready(plan: Plan) -> dict:
    checks = evaluate(plan)
    blocking = [x for x in checks if x.blocks]
    rec = {"preflight": "argus-long-run-preflight-v2",
           "purpose": (_enum_or_none(Purpose, plan.purpose).value
                       if _enum_or_none(Purpose, plan.purpose) else None),
           "checks": [x.as_record() for x in checks],
           "blocking": len(blocking),
           "verdict": "REFUSED" if blocking else "CLEARED",
           "fail_closed": "every field is judged by an explicit predicate against a declared "
                          "vocabulary. Unrecognised values are REFUSED, not defaulted -- a "
                          "typo, a new sentinel or a hopeful string cannot pass by being "
                          "unfamiliar.",
           "v1_was_fail_open": "the previous version used string truthiness, so UNDECLARED, "
                               "UNRESOLVED and EXPOSURE_UNKNOWN all passed, a one-character "
                               "hash passed, zero VRAM passed, and output_ok=False passed."}
    if blocking:
        raise LongRunRefused(json.dumps(
            {"verdict": "REFUSED", "blocking": len(blocking),
             "first": [x.as_record() for x in blocking[:10]]}, indent=1))
    return rec
