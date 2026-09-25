"""argus.core.tiled_run_resume against argus-villa-provider-receipt-v1 shaped records: resume, interruption and seam-parameter safety for tiled runs."""
from __future__ import annotations

import pytest

from argus.core.tiled_run_resume import TiledRunRefusal, plan_resume


def _receipt(capability_id: str, argv: list, *, status: str = "OK") -> dict:
    return {
        "schema": "argus-villa-provider-receipt-v1",
        "capability_id": capability_id,
        "status": status,
        "argv": argv,
    }


def _predict_argv(part_id: int, num_parts: int = 4, overlap: str = "0.25") -> list:
    return ["vesuvius.predict.exe", "--model-path", "m.pt", "--input-dir", "in", "--output-dir",
           "out%d" % part_id, "--num-parts", str(num_parts), "--part-id", str(part_id),
           "--overlap", overlap, "--patch-size", "192"]


class TestCompleteAndMissing:
    def test_all_parts_done_reports_complete(self):
        receipts = [_receipt("vesuvius.predict", _predict_argv(i)) for i in range(4)]
        out = plan_resume("vesuvius.predict", 4, receipts)
        assert out["complete"] is True
        assert out["done_part_ids"] == [0, 1, 2, 3]
        assert out["missing_part_ids"] == []
        assert out["next_part_id"] is None
        assert out["seam_parameters_consistent"] is True

    def test_missing_parts_are_reported_and_next_part_id_is_the_first_missing(self):
        receipts = [_receipt("vesuvius.predict", _predict_argv(i)) for i in (0, 2)]
        out = plan_resume("vesuvius.predict", 4, receipts)
        assert out["complete"] is False
        assert out["done_part_ids"] == [0, 2]
        assert out["missing_part_ids"] == [1, 3]
        assert out["next_part_id"] == 1

    def test_a_fresh_run_with_no_receipts_reports_everything_missing(self):
        out = plan_resume("vesuvius.predict", 3, [])
        assert out["missing_part_ids"] == [0, 1, 2]
        assert out["complete"] is False
        assert out["next_part_id"] == 0

    def test_a_refused_receipt_does_not_count_as_done(self):
        receipts = [
            _receipt("vesuvius.predict", _predict_argv(0, num_parts=2)),
            _receipt("vesuvius.predict", _predict_argv(1, num_parts=2), status="REFUSED"),
        ]
        out = plan_resume("vesuvius.predict", 2, receipts)
        assert out["done_part_ids"] == [0]
        assert out["missing_part_ids"] == [1], "a REFUSED attempt must be retried, not treated as done"


class TestSeamParameterConsistency:
    def test_a_mismatched_seam_critical_parameter_is_refused(self):
        receipts = [
            _receipt("vesuvius.predict", _predict_argv(0, overlap="0.25")),
            _receipt("vesuvius.predict", _predict_argv(1, overlap="0.10")),
        ]
        with pytest.raises(TiledRunRefusal, match="real seam"):
            plan_resume("vesuvius.predict", 4, receipts)

    def test_matching_parameters_across_parts_is_fine(self):
        receipts = [_receipt("vesuvius.predict", _predict_argv(i, overlap="0.25")) for i in range(2)]
        out = plan_resume("vesuvius.predict", 4, receipts)
        assert out["seam_parameters_consistent"] is True
        assert out["reference_parameters"]["--overlap"] == "0.25"

    def test_non_seam_flags_are_allowed_to_differ(self):
        a = _predict_argv(0) + ["--num-workers", "2", "--device", "cuda:0"]
        b = _predict_argv(1) + ["--num-workers", "8", "--device", "cuda:1"]
        out = plan_resume("vesuvius.predict", 4, [_receipt("vesuvius.predict", a),
                                                   _receipt("vesuvius.predict", b)])
        assert out["seam_parameters_consistent"] is True


class TestRefusalsOnBadInput:
    def test_unknown_capability_is_refused(self):
        with pytest.raises(TiledRunRefusal, match="not one of the tiled inference capabilities"):
            plan_resume("vc_render_tifxyz", 4, [])

    def test_num_parts_must_be_a_positive_integer(self):
        with pytest.raises(TiledRunRefusal, match="positive integer"):
            plan_resume("vesuvius.predict", 0, [])
        with pytest.raises(TiledRunRefusal, match="positive integer"):
            plan_resume("vesuvius.predict", -1, [])

    def test_a_receipt_for_a_different_capability_is_refused(self):
        receipts = [_receipt("vesuvius.blend_logits", _predict_argv(0))]
        with pytest.raises(TiledRunRefusal, match="does not belong to this"):
            plan_resume("vesuvius.predict", 4, receipts)

    def test_a_receipt_declaring_a_different_num_parts_is_refused(self):
        receipts = [_receipt("vesuvius.predict", _predict_argv(0, num_parts=8))]
        with pytest.raises(TiledRunRefusal, match="different shard count"):
            plan_resume("vesuvius.predict", 4, receipts)

    def test_a_part_id_out_of_range_is_refused(self):
        argv = ["vesuvius.predict.exe", "--num-parts", "4", "--part-id", "9"]
        with pytest.raises(TiledRunRefusal, match="outside the declared"):
            plan_resume("vesuvius.predict", 4, [_receipt("vesuvius.predict", argv)])

    def test_a_receipt_missing_part_id_is_refused(self):
        argv = ["vesuvius.predict.exe", "--num-parts", "4"]
        with pytest.raises(TiledRunRefusal, match="no --part-id"):
            plan_resume("vesuvius.predict", 4, [_receipt("vesuvius.predict", argv)])

    def test_a_non_receipt_object_is_refused(self):
        with pytest.raises(TiledRunRefusal, match="real argus-villa-provider-receipt-v1"):
            plan_resume("vesuvius.predict", 4, [{"not": "a receipt"}])
