"""Where human ink labels live, and the command that proves it."""
from __future__ import annotations

import pytest

from argus.core import label_sources as ls
from argus.core import result_class as rc


def test_human_ink_labels_are_obtainable():
    got = ls.obtainable_human_label_sources()
    assert got, "the public human ink label sources must be obtainable"
    assert all(s.has_human_labels and s.obtainable for s in got)


def test_nothing_carrying_human_labels_is_marked_gated():
    for s in ls.SOURCES:
        if s.has_human_labels:
            assert s.access != "GATED_ACCEPT_TERMS", s.name


def test_four_scrolls_are_labelled_near_the_eligible_pitch():
    f = ls.cross_scroll_feasibility(min_scrolls=3)
    assert f["count"] == 4, f["scrolls_with_human_labels_near_9um"]
    assert f["feasible"] is True


def test_one_source_is_annotated_at_native_eligible_pitch():
    native = [s for s in ls.SOURCES if s.name == "ink_9um_native9"][0]
    assert native.pitch_um == 9.362
    assert native.segments == 5
    assert "PHerc0139" in native.scrolls


def test_the_whole_9um_label_set_is_small():
    f = ls.cross_scroll_feasibility()
    assert f["total_bytes"] < 64 * 2 ** 20, "32 MiB was the measured figure"


def test_open_s3_is_recorded_as_predictions_not_labels():
    s3 = [s for s in ls.SOURCES if s.name == "open_data_s3"][0]
    assert not s3.has_human_labels
    assert "MODEL_PREDICTIONS" in s3.content
    assert "prediction" in s3.note.lower()



def test_hercunet_corpus_is_registered_as_pseudolabels_not_human_labels():
    """A pseudo-label corpus is registered as model predictions, never as human labels."""
    hu = [s for s in ls.SOURCES if s.name == "hercunet_corpus_stage1_pseudolabels"][0]
    assert not hu.has_human_labels
    assert "MODEL_PREDICTIONS" in hu.content and "SUPERVISION_MASK" in hu.content
    assert hu.access == "HF_DATASET_REPO"
    assert hu not in ls.obtainable_human_label_sources()


def test_hercunet_corpus_registration_is_intake_only_and_flags_exposed_scrolls():
    hu = [s for s in ls.SOURCES if s.name == "hercunet_corpus_stage1_pseudolabels"][0]
    assert "INTAKE ONLY" in hu.note
    assert "NOT been downloaded" in hu.note
    assert "PHerc0800" in hu.scrolls and "PHerc1447" in hu.scrolls
    assert "MIT-BY-POINTER" in hu.note


def test_hercunet_corpus_does_not_change_the_human_label_feasibility_count():
    """Registering a pseudo-label source must not silently inflate the count of scrolls with real human labels -- that would be exactly the ink-vs-prediction conflation this module exists to prevent,..."""
    f = ls.cross_scroll_feasibility(min_scrolls=3)
    assert f["count"] == 4



def test_a_source_must_carry_the_command_that_lists_it():
    with pytest.raises(ls.LabelSourceError) as e:
        ls.LabelSource(name="x", uri="u", access="PUBLIC_S3", content=("HUMAN_INK_LABELS",),
                       scrolls=("a",), segments=1, bytes_total=1, pitch_um=1.0,
                       verified_utc="z", list_command="")
    assert "reproducible check" in str(e.value)


@pytest.mark.parametrize("kw", [
  {"access": "SOMEWHERE"},
  {"content": ("VIBES",)},
])
def test_unknown_access_or_content_is_refused(kw):
    base = dict(name="x", uri="u", access="PUBLIC_S3", content=("HUMAN_INK_LABELS",),
                scrolls=("a",), segments=1, bytes_total=1, pitch_um=1.0,
                verified_utc="z", list_command="ls")
    base.update(kw)
    with pytest.raises(ls.LabelSourceError):
        ls.LabelSource(**base)


def test_the_correction_is_carried_with_the_data():
    c = ls.CORRECTION
    assert "gated" in c["was"]
    assert "PUBLIC" in c["is"]
    assert "search" in c["why_it_was_believed"]



def test_bridge_gate_three_no_longer_claims_the_labels_are_gated():
    g = [x for x in rc.FIRST_LETTERS_BRIDGE
         if x["gate"] == "CROSS_SCROLL_CONTROL_AT_ELIGIBLE_ACQUISITION"][0]
    blob = (g["needs"] + " " + g["currently"]).lower()
    assert "gated" not in blob
    assert "does not exist locally" not in blob


def test_bridge_gate_three_is_still_unmet():
    assert rc.bridge_status()["gates_passed"] == 0
    g = [x for x in rc.FIRST_LETTERS_BRIDGE
         if x["gate"] == "CROSS_SCROLL_CONTROL_AT_ELIGIBLE_ACQUISITION"][0]
    assert "UNMET" in g["currently"]


def test_discovery_is_still_impossible():
    assert rc.bridge_status()["gates_passed"] == 0
    assert any("NOT OPEN" in str(v) for v in rc.bridge_status().values())


def test_feasibility_does_not_overclaim():
    f = ls.cross_scroll_feasibility()
    assert "does not mean the gate passes" in f["caveat"]
    assert any("unread scroll" in x for x in f["what_this_does_not_unblock"])
    assert any("acquisition family" in x for x in f["what_this_does_not_unblock"])
