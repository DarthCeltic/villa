"""Undeclared must behave like the most restrictive case, not the most convenient one."""
from __future__ import annotations

import pytest

from argus.core import licence_resolver as LR


def _c(**kw):
    base = dict(key="k", source="huggingface:org/repo", revision="a" * 40,
                sha256="b" * 64, licence="apache-2.0", redistribute="YES", kind="model")
    base.update(kw)
    return LR.Component(**base)


def test_a_fully_declared_apache_component_may_be_redistributed():
    assert LR.may_redistribute(_c()) is True


@pytest.mark.parametrize("field", ("source", "revision", "sha256", "licence"))
def test_each_of_the_four_fields_is_separately_required(field):
    c = _c(**{field: ""})
    assert LR.may_redistribute(c) is False
    assert any(field in p for p in c.problems())
    with pytest.raises(LR.LicenceRefusal):
        LR.require(c)


@pytest.mark.parametrize("moving", ("main", "master", "latest", "HEAD"))
def test_a_moving_reference_is_not_a_revision(moving):
    """A receipt that names a branch names nothing: the branch moves and the receipt does not."""
    c = _c(revision=moving)
    assert LR.may_redistribute(c) is False
    assert any("moves" in p for p in c.problems())


def test_the_literal_string_undeclared_is_a_refusal_not_a_value():
    assert LR.may_redistribute(_c(licence=LR.UNDECLARED)) is False


def test_a_declared_but_unknown_licence_does_not_default_to_permissive():
    """Fail-closed means an unrecognised SPDX id is withheld, not waved through."""
    c = _c(licence="some-bespoke-research-licence-1.0")
    assert not c.problems()
    assert LR.may_redistribute(c) is False


def test_a_noncommercial_licence_does_not_permit_weight_redistribution():
    assert LR.may_redistribute(_c(licence="cc-by-nc-4.0")) is False


@pytest.mark.parametrize("answer", ("NO", "UNKNOWN"))
def test_redistribute_is_separate_from_the_licence(answer):
    """A permissive licence on a model says nothing about the data it was trained on."""
    assert LR.may_redistribute(_c(redistribute=answer)) is False


def test_an_unrecognised_redistribute_answer_is_a_problem_not_a_yes():
    c = _c(redistribute="probably")
    assert any("YES, NO or UNKNOWN" in p for p in c.problems())
    assert LR.may_redistribute(c) is False


def test_every_registered_official_model_arrives_fully_declared():
    """Not pinned to a count: the registry grows, and a test that pins the count fails for the right reason once and then gets its number bumped without anyone reading the row."""
    from argus.core import official_models as OM
    r = LR.register()
    assert r["components"] == len(OM.MODELS)
    assert r["all_declared"] is True, [x["problems"] for x in r["rows"]]
    assert set(r["redistributable"]) == {m.repo_id.split("/")[-1] for m in OM.MODELS}


def test_redistribution_follows_the_licence_table_not_a_hardcoded_string():
    """A declared MIT model was marked UNKNOWN and withheld because the mapping tested for apache-2.0 by string equality."""
    for c in LR.from_official_models():
        terms = LR.KNOWN.get(str(c.licence).strip().lower(), {})
        expected = "YES" if terms.get("redistribute_weights") else "UNKNOWN"
        assert c.redistribute == expected, (c.key, c.licence, c.redistribute)


def test_the_official_components_carry_immutable_revisions_and_real_hashes():
    for c in LR.from_official_models():
        assert len(c.revision) == 40 and all(ch in "0123456789abcdef" for ch in c.revision), c
        assert len(c.sha256) == 64, c
