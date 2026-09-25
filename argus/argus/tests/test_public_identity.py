"""Guards on public segment naming."""
from __future__ import annotations

import pytest

from argus.core import public_identity as PI

SEG_A = PI.PublicSegment(
    physical_scroll="PHercFixture1", argus_alias="fixture1-w001",
    upstream_segment_id="20000101000000-w002_fixture",
    volume_id="1.000um-fixture-volume-a.zarr", pyramid_level="2")

SEG_B = PI.PublicSegment(
    physical_scroll="PHercFixture2", argus_alias="fixture2-w002",
    upstream_segment_id="20000102000000-w002_fixture",
    volume_id="1.000um-fixture-volume-b.zarr", pyramid_level="2")


def test_the_collision_is_real_and_the_names_separate_it():
    """A token inside one segment's upstream id can equal another scroll's alias."""
    assert "w002" in SEG_A.upstream_segment_id
    assert "w002" in SEG_B.argus_alias
    assert SEG_A.physical_scroll != SEG_B.physical_scroll
    assert SEG_A.public_name() != SEG_B.public_name()


def test_a_public_name_carries_all_four_parts():
    n = SEG_A.public_name()
    for part in ("PHercFixture1", "fixture1-w001", "20000101000000-w002_fixture",
                 "1.000um-fixture-volume-a.zarr"):
        assert part in n


def test_a_bare_alias_in_public_text_is_refused():
    with pytest.raises(PI.AmbiguousPublicName):
        PI.assert_public_safe("We scored w002 and found ink.", [SEG_A, SEG_B], where="README")


def test_a_fully_qualified_mention_passes():
    text = "Scored %s successfully." % SEG_B.public_name()
    assert PI.assert_public_safe(text, [SEG_A, SEG_B], where="README")["safe"]


def test_text_with_no_segments_at_all_passes():
    assert PI.assert_public_safe("ARGUS is an instrument.", [], where="README")["safe"]


def test_the_scan_does_not_fire_on_ordinary_words():
    """'window', 'workflow', 'w3c' must not be mistaken for a segment alias."""
    assert PI.assert_public_safe(
        "Open the window in the workflow; see w3c specs.", [], where="doc")["safe"]


def test_from_control_builds_a_resolvable_name():
    seg = PI.from_control(
        key="fixture1-w001",
        pub="PHercFixture1/segments/20000101000000-w002_fixture",
        vol="1.000um-fixture-volume-a.zarr",
        scroll="PHercFixture1", s3="https://example.invalid")
    assert seg.upstream_segment_id == "20000101000000-w002_fixture"
    assert seg.source_url.endswith("/2")
    assert "PHercFixture1" in seg.public_name()
