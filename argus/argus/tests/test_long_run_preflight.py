"""The v1 of this gate was FAIL-OPEN."""
from __future__ import annotations

import json

import pytest

from argus.core import long_run_preflight as LP


def _ready(**over):
    base = dict(
      purpose="LOCAL_RESEARCH",
      receipts_current=True, unincorporated_count=0,
      acquisition_sealed=True, manifest_complete=True,
      exposure_declared="UNSEEN_PROVEN", licence_declared="Apache-2.0",
      pitch_um=9.362, energy_kev=113,
      compression_declaration="ORIGINAL_UNCOMPRESSED",
      label_lineage="HUMAN_ANNOTATION",
      scoring_population_frozen=True, scoring_population_ink_independent=True,
      aggregation_frozen=True, orientations=("forward", "reversed"),
      checkpoint_sha256="a" * 64, dependency_sha256={"metrics": "b" * 64},
      expected_dependencies=("metrics",), block_plan_sha256="c" * 64,
      smoke={"vram_mib": 5400, "ram_mib": 1400, "runtime_s": 350.5, "output_ok": True},
      answerable_from_saved={"conditioned_auc": "SAVED"})
    base.update(over)
    return LP.Plan(**base)


def test_a_fully_declared_plan_clears():
    assert LP.assert_ready(_ready())["verdict"] == "CLEARED"


def test_THE_MALICIOUS_PLAN_THAT_USED_TO_CLEAR_IS_REFUSED():
    """THE REGRESSION."""
    p = _ready(unincorporated_count=99, exposure_declared="EXPOSURE_UNKNOWN",
               licence_declared="UNDECLARED", compression_declaration="UNRESOLVED",
               label_lineage="UNKNOWN", checkpoint_sha256="a",
               dependency_sha256={"metrics": "b"}, block_plan_sha256="c",
               smoke={"vram_mib": 0, "ram_mib": 0, "runtime_s": 0, "output_ok": False},
               answerable_from_saved={"thing": "MAYBE_LATER"})
    with pytest.raises(LP.LongRunRefused) as e:
        LP.assert_ready(p)
    d = json.loads(str(e.value))
    assert d["blocking"] >= 8, d


@pytest.mark.parametrize("sentinel", sorted(LP.NON_ANSWERS - {""}))
def test_no_sentinel_value_can_satisfy_a_declaration(sentinel):
    """A non-empty string is not a declaration."""
    for fieldname in ("exposure_declared", "compression_declaration", "label_lineage"):
        with pytest.raises(LP.LongRunRefused):
            LP.assert_ready(_ready(**{fieldname: sentinel}))


@pytest.mark.parametrize("enum_cls,fieldname", [
    (LP.Exposure, "exposure_declared"),
    (LP.Compression, "compression_declaration"),
    (LP.Lineage, "label_lineage"),
])
def test_every_accepted_enum_member_is_accepted(enum_cls, fieldname):
    for member in enum_cls:
        assert LP.assert_ready(_ready(**{fieldname: member.value}))["verdict"] == "CLEARED"


@pytest.mark.parametrize("fieldname", ["exposure_declared", "compression_declaration",
                                       "label_lineage"])
def test_an_unrecognised_value_is_refused_not_defaulted(fieldname):
    """Defaulting an unfamiliar value to the nearest member is how a typo becomes a declaration."""
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(**{fieldname: "UNSEEN_PROVENN"}))


def test_receipts_current_true_with_nonzero_unincorporated_is_refused():
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(unincorporated_count=1))


@pytest.mark.parametrize("bad", ["a", "abc", "g" * 64, "a" * 63, "a" * 65, 12345,
                                 None, True, "", "  "])
def test_only_a_full_64_hex_hash_is_a_hash(bad):
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(checkpoint_sha256=bad))


def test_uppercase_hex_is_a_valid_hash():
    """Hex is case-insensitive."""
    assert LP.assert_ready(_ready(checkpoint_sha256="A" * 64))["verdict"] == "CLEARED"


def test_incomplete_dependency_coverage_is_refused():
    """Partial coverage is not coverage."""
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(dependency_sha256={"metrics": "b" * 64},
                               expected_dependencies=("metrics", "resample")))


@pytest.mark.parametrize("smoke", [
    {"vram_mib": 0, "ram_mib": 1400, "runtime_s": 350.5, "output_ok": True},
    {"vram_mib": 5400, "ram_mib": 0, "runtime_s": 350.5, "output_ok": True},
    {"vram_mib": 5400, "ram_mib": 1400, "runtime_s": 0, "output_ok": True},
    {"vram_mib": 5400, "ram_mib": 1400, "runtime_s": 350.5, "output_ok": False},
    {"vram_mib": 5400, "ram_mib": 1400, "runtime_s": 350.5, "output_ok": "yes"},
    {"vram_mib": -1, "ram_mib": 1400, "runtime_s": 350.5, "output_ok": True},
    {"vram_mib": float("nan"), "ram_mib": 1400, "runtime_s": 1, "output_ok": True},
    {"vram_mib": float("inf"), "ram_mib": 1400, "runtime_s": 1, "output_ok": True},
])
def test_a_smoke_block_that_is_not_a_measurement_is_refused(smoke):
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(smoke=smoke))


@pytest.mark.parametrize("val", ["MAYBE_LATER", "saved", "PROBABLY", "", None, True])
def test_answerability_is_a_closed_vocabulary(val):
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(answerable_from_saved={"thing": val}))


def test_both_answerability_members_are_accepted():
    for v in sorted(LP.ANSWERABILITY):
        assert LP.assert_ready(_ready(answerable_from_saved={"t": v}))["verdict"] == "CLEARED"


def test_a_single_orientation_is_refused():
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(orientations=("forward",)))


def test_a_population_frozen_but_not_ink_independent_is_refused():
    """A population can be frozen and still be selected around ink."""
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(scoring_population_ink_independent=False))


def test_purpose_must_be_stated():
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(purpose=None))
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(purpose="SOMETHING_ELSE"))


@pytest.mark.parametrize("purpose,expect_refusal", [
    ("LOCAL_RESEARCH", False), ("PUBLIC_RELEASE", True), ("PRIZE_SUBMISSION", True)])
def test_undeclared_licence_depends_on_purpose(purpose, expect_refusal):
    """An undeclared licence is usable locally and barred from public and prize use."""
    p = _ready(purpose=purpose, licence_declared=None)
    if expect_refusal:
        with pytest.raises(LP.LongRunRefused):
            LP.assert_ready(p)
    else:
        r = LP.assert_ready(p)
        lic = [c for c in r["checks"] if c["key"] == "licence_declared"][0]
        assert "RECORDED as a constraint" in lic["detail"]


@pytest.mark.parametrize("fieldname", ["pitch_um", "energy_kev"])
@pytest.mark.parametrize("bad", [0, -1, None, "9.362", float("nan"), float("inf"), True])
def test_pitch_and_energy_must_be_positive_finite_numbers(fieldname, bad):
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(_ready(**{fieldname: bad}))


def test_every_check_explains_why_it_matters():
    for c in LP.evaluate(LP.Plan()):
        assert c.why_it_matters and len(c.why_it_matters) > 30, c.key


def test_an_empty_plan_refuses():
    with pytest.raises(LP.LongRunRefused):
        LP.assert_ready(LP.Plan())
