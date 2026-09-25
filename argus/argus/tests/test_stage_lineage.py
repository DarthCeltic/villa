"""One immutable, ordered lineage of process-contract stage attempts, per scroll."""
from __future__ import annotations

import json

import pytest

from argus.core import process_contract as PC
from argus.core import stage_lineage as SL
from argus.core.scroll_ids import CANONICAL

# Any two registered ids serve: nothing below depends on which scroll they are.
S, S2 = CANONICAL[0], CANONICAL[1]


def _n_stages():
    return len(SL._stage_ids())


def test_unregistered_scroll_refuses(tmp_path):
    with pytest.raises(KeyError):
        SL.record_attempt("NotARealScroll", "raw_ct", "REFUSED", root=tmp_path)


def test_unknown_stage_id_refuses(tmp_path):
    with pytest.raises(SL.LineageRefusal, match="unknown stage_id"):
        SL.record_attempt(S, "not_a_real_stage", "REFUSED", root=tmp_path)


def test_unknown_outcome_refuses(tmp_path):
    with pytest.raises(SL.LineageRefusal, match="unknown outcome"):
        SL.record_attempt(S, "raw_ct", "MOSTLY_DONE", root=tmp_path)


def test_completed_without_receipt_refuses(tmp_path):
    with pytest.raises(SL.LineageRefusal, match="requires receipt_path"):
        SL.record_attempt(S, "flatten", "COMPLETED", root=tmp_path)


def test_completed_with_nonexistent_receipt_refuses(tmp_path):
    with pytest.raises(SL.LineageRefusal, match="does not resolve to an existing file"):
        SL.record_attempt(S, "flatten", "COMPLETED",
                          receipt_path=str(tmp_path / "nope.json"), root=tmp_path)


def test_stage_vocabulary_is_read_from_the_process_contract_not_duplicated():
    ids = SL._stage_ids()
    assert ids == tuple(s["id"] for s in PC.derive()["stages"])
    assert len(ids) == len(set(ids)) > 0


def test_records_chain_in_order_with_correct_prev_hash(tmp_path):
    receipt = tmp_path / "r.json"
    receipt.write_text("{}", encoding="utf-8")

    r0 = SL.record_attempt(S, "raw_ct", "REFUSED", detail="no packet", root=tmp_path)
    r1 = SL.record_attempt(S, "flatten", "COMPLETED", receipt_path=str(receipt),
                           root=tmp_path)
    r2 = SL.record_attempt(S, "review", "HUMAN_GATED_PENDING", root=tmp_path)

    assert [r0["seq"], r1["seq"], r2["seq"]] == [0, 1, 2]
    assert r0["prev_hash"] == SL.ZERO
    assert r1["prev_hash"] == r0["this_hash"]
    assert r2["prev_hash"] == r1["this_hash"]
    assert r0["scroll"] == S


def test_read_reports_the_full_verified_ordered_chain(tmp_path):
    receipt = tmp_path / "r.json"
    receipt.write_text("{}", encoding="utf-8")
    SL.record_attempt(S, "raw_ct", "BLOCKED", root=tmp_path)
    SL.record_attempt(S, "flatten", "COMPLETED", receipt_path=str(receipt),
                      root=tmp_path)

    chain = SL.read(S, root=tmp_path)
    assert chain["chain_state"] == "VERIFIED"
    assert [r["stage_id"] for r in chain["records"]] == ["raw_ct", "flatten"]
    assert chain["records"][0]["seq"] == 0 and chain["records"][1]["seq"] == 1


def test_read_of_never_attempted_scroll_is_empty_not_absent(tmp_path):
    chain = SL.read(S2, root=tmp_path)
    assert chain["exists"] is False
    assert chain["chain_state"] == "EMPTY"
    assert chain["records"] == []


def test_tampering_with_a_record_is_detected(tmp_path):
    SL.record_attempt(S, "raw_ct", "REFUSED", detail="original", root=tmp_path)
    path = SL.lineage_path(S, root=tmp_path)
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    lines[0]["detail"] = "TAMPERED, this_hash no longer matches"
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")

    chain = SL.read(S, root=tmp_path)
    assert chain["chain_state"] == "BROKEN"
    assert chain["broken_at"] == 0
    assert "does not match its own hash" in chain["why"]


def test_appending_after_the_chain_head_is_damaged_refuses(tmp_path):
    SL.record_attempt(S, "raw_ct", "REFUSED", root=tmp_path)
    path = SL.lineage_path(S, root=tmp_path)
    with path.open("ab") as fh:
        fh.write(b'{"seq": 1, "broken": true')
    with pytest.raises(SL.LineageRefusal, match="partial line"):
        SL.record_attempt(S, "flatten", "BLOCKED", root=tmp_path)


def test_coverage_joins_attempts_against_the_full_stage_contract(tmp_path):
    n = _n_stages()
    cov = SL.coverage(S, root=tmp_path)
    assert cov["counts"]["total_stages"] == n
    assert cov["counts"]["attempted"] == 0
    assert cov["counts"]["never_attempted"] == n
    assert cov["stages"]["translation"]["contract_state"] == "HUMAN_GATED"
    assert cov["stages"]["translation"]["contract_gap"]
    assert cov["stages"]["translation"]["attempted"] is False


def test_coverage_after_real_attempts_reports_order_and_latest_outcome(tmp_path):
    receipt = tmp_path / "r.json"
    receipt.write_text("{}", encoding="utf-8")
    SL.record_attempt(S, "raw_ct", "REFUSED", root=tmp_path)
    SL.record_attempt(S, "flatten", "COMPLETED", receipt_path=str(receipt),
                      root=tmp_path)
    SL.record_attempt(S, "flatten", "BLOCKED", root=tmp_path)

    cov = SL.coverage(S, root=tmp_path)
    assert cov["counts"]["attempted"] == 2
    assert cov["counts"]["never_attempted"] == _n_stages() - 2
    assert [row["stage_id"] for row in cov["attempted_order"]] == ["raw_ct", "flatten", "flatten"]
    assert cov["stages"]["flatten"]["attempts"] == 2
    assert cov["stages"]["flatten"]["latest"]["outcome"] == "BLOCKED"


def test_seed_from_an_acquisition_receipt_records_once(tmp_path, monkeypatch):
    receipt = tmp_path / "fixture_receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(PC, "_acquisition_receipt",
                        lambda scroll: {"receipt": str(receipt), "state": "PACKET_PASSED",
                                        "detail": "fixture receipt"})

    rec = SL.seed_acquisition_from_existing_receipt(S, root=tmp_path)
    assert rec is not None
    assert rec["stage_id"] == "raw_ct"
    assert rec["outcome"] == "COMPLETED"
    assert rec["capability_id"] == "target_acquisition_packet"
    assert rec["receipt_path"]

    again = SL.seed_acquisition_from_existing_receipt(S, root=tmp_path)
    assert again is None
    assert len(SL.read(S, root=tmp_path)["records"]) == 1


def test_seed_returns_none_when_no_acquisition_evidence_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(PC, "_acquisition_receipt", lambda scroll: {"receipt": None})
    rec = SL.seed_acquisition_from_existing_receipt(S2, root=tmp_path)
    assert rec is None
    assert SL.read(S2, root=tmp_path)["records"] == []


def test_service_exposes_stage_lineage_for_a_scroll():
    from argus.service import app as service

    doc = service.stage_lineage(scroll=S)
    assert doc["schema"] == "argus-stage-lineage-coverage-v1"
    assert doc["scroll"] == S
    assert doc["counts"]["total_stages"] == _n_stages()


def test_service_refuses_an_unknown_scroll_with_404():
    from fastapi import HTTPException
    from argus.service import app as service

    with pytest.raises(HTTPException) as exc:
        service.stage_lineage(scroll="NotARealScroll")
    assert exc.value.status_code == 404


def test_lineage_path_is_one_file_per_canonical_scroll_never_shared(tmp_path):
    a = SL.lineage_path(S, root=tmp_path)
    b = SL.lineage_path(S.lower()[:5] + "_" + S[5:], root=tmp_path)
    c = SL.lineage_path(S2, root=tmp_path)
    assert a == b
    assert a != c
    assert a.name == "%s.jsonl" % S
