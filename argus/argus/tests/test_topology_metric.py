from __future__ import annotations

import numpy as np
import pytest

from argus.core import topology_metric as TM


def _two_squares():
    """Two well-separated 2x2 true components, never touching even diagonally."""
    true = np.zeros((10, 10), dtype=bool)
    true[1:3, 1:3] = True
    true[1:3, 6:8] = True
    return true


def test_clean_case_identical_masks_report_no_mergers_or_splits():
    true = _two_squares()
    predicted = true.copy()
    result = TM.evaluate(predicted, true)
    assert result["n_components_true"] == 2
    assert result["n_components_predicted"] == 2
    assert result["component_count_matches"] is True
    assert result["mergers"]["count"] == 0
    assert result["splits"]["count"] == 0
    assert result["unmatched_predicted_components"] == []
    assert result["unmatched_true_components"] == []
    assert result["topology_clean"] is True
    assert result["rule_id"] == TM.RULE_ID


def test_merger_case_a_bridge_fuses_two_true_components_into_one_prediction():
    true = _two_squares()
    predicted = true.copy()
    predicted[1, 3:6] = True
    result = TM.evaluate(predicted, true)
    assert result["n_components_true"] == 2
    assert result["n_components_predicted"] == 1
    assert result["component_count_matches"] is False
    assert result["mergers"]["count"] == 1
    assert result["mergers"]["predicted_components"] == [1]
    assert result["mergers"]["detail"][1] == [1, 2]
    assert result["splits"]["count"] == 0
    assert result["topology_clean"] is False


def test_split_case_a_gap_breaks_one_true_component_into_two_predictions():
    true = np.zeros((10, 10), dtype=bool)
    true[1:3, 1:8] = True
    predicted = true.copy()
    predicted[1:3, 4] = False
    result = TM.evaluate(predicted, true)
    assert result["n_components_true"] == 1
    assert result["n_components_predicted"] == 2
    assert result["component_count_matches"] is False
    assert result["splits"]["count"] == 1
    assert result["splits"]["true_components"] == [1]
    assert result["splits"]["detail"][1] == [1, 2]
    assert result["mergers"]["count"] == 0
    assert result["topology_clean"] is False


def test_unmatched_components_are_reported_on_both_sides():
    true = np.zeros((10, 10), dtype=bool)
    true[1:3, 1:3] = True
    predicted = np.zeros((10, 10), dtype=bool)
    predicted[6:8, 6:8] = True
    result = TM.evaluate(predicted, true)
    assert result["n_components_true"] == 1
    assert result["n_components_predicted"] == 1
    assert result["mergers"]["count"] == 0
    assert result["splits"]["count"] == 0
    assert result["unmatched_predicted_components"] == [1]
    assert result["unmatched_true_components"] == [1]
    assert result["topology_clean"] is False


def test_min_overlap_voxels_filters_a_single_stray_touch_from_counting_as_a_merger():
    true = _two_squares()
    predicted = true.copy()
    predicted[1, 3] = True
    result_default = TM.evaluate(predicted, true, min_overlap_voxels=1)
    assert result_default["n_components_predicted"] == 2
    assert result_default["mergers"]["count"] == 0


def test_face_only_connectivity_can_disagree_with_full_connectivity():
    diag = np.zeros((4, 4), dtype=bool)
    diag[1, 1] = True
    diag[2, 2] = True
    n_full = TM.label_components(diag, connectivity_full=True)[1]
    n_face = TM.label_components(diag, connectivity_full=False)[1]
    assert n_full == 1
    assert n_face == 2


def test_refuses_mismatched_shapes():
    true = _two_squares()
    predicted = np.zeros((5, 5), dtype=bool)
    with pytest.raises(TM.TopologyMetricRefusal, match="different shapes"):
        TM.evaluate(predicted, true)


def test_refuses_non_binary_integer_input():
    true = _two_squares()
    predicted = np.zeros((10, 10), dtype=np.int32)
    predicted[1:3, 1:3] = 2
    with pytest.raises(TM.TopologyMetricRefusal, match="not all 0/1"):
        TM.evaluate(predicted, true)


def test_refuses_empty_input():
    empty = np.zeros((0, 0), dtype=bool)
    with pytest.raises(TM.TopologyMetricRefusal, match="empty"):
        TM.evaluate(empty, empty)


def test_refuses_min_overlap_voxels_below_one():
    true = _two_squares()
    with pytest.raises(TM.TopologyMetricRefusal, match="min_overlap_voxels"):
        TM.evaluate(true.copy(), true, min_overlap_voxels=0)


def test_works_in_3d_too():
    true = np.zeros((6, 6, 6), dtype=bool)
    true[1, 1, 1] = True
    true[4, 4, 4] = True
    result = TM.evaluate(true.copy(), true)
    assert result["n_components_true"] == 2
    assert result["n_components_predicted"] == 2
    assert result["topology_clean"] is True
