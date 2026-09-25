"""The licence registry decides what may ship."""
from __future__ import annotations

import pytest

from argus.core import licence_registry as L


def test_undeclared_weights_cannot_enter_a_submission():
    weights = next(c for c in L.COMPONENTS if c.kind == "MODEL_WEIGHTS")
    assert weights.family == "UNDECLARED"
    assert weights.may_enter_prize_submission is False
    assert weights.may_redistribute is False


def test_data_is_an_input_not_a_method():
    ct = next(c for c in L.COMPONENTS if c.kind == "DATA")
    assert ct.is_part_of_the_method is False
    assert ct.may_enter_prize_submission is True
    assert ct.may_redistribute is False
    assert ct.may_bundle_in_installer is False


def test_gpl_runs_externally_and_may_be_submitted_but_never_bundled():
    vc = next(c for c in L.COMPONENTS if c.family == "COPYLEFT")
    assert vc.may_run_locally is True
    assert vc.may_enter_prize_submission is True
    assert vc.may_bundle_in_installer is False


def test_only_the_checkpoint_blocks_a_submission():
    m = L.manifest()
    assert [b["name"] for b in m["prize_submission_blockers"]] == [
        "Third-party model weights (no declared licence)"]


def test_no_noncommercial_or_undeclared_component_is_bundled():
    m = L.manifest()
    for r in m["components"]:
        if r["family"] in ("NONCOMMERCIAL", "UNDECLARED"):
            assert r["bundle_in_installer"] is False, r["name"]


def test_a_licence_claim_needs_evidence():
    with pytest.raises(L.LicenceError) as e:
        L.Component(name="x", kind="CODE", family="PERMISSIVE", spdx="MIT",
                    source="s", evidence="  ")
    assert "cannot grant anything" in str(e.value)


def test_the_public_build_is_an_allowlist_not_a_deletion_pass():
    m = L.manifest()
    rule = m["release_rules"]["public_build_is_an_allowlist"]
    assert "what MAY ship" in rule
    assert "never by deleting" in rule
