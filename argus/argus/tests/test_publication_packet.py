"""The publication / receipt packet: export it, reopen it, and prove what changed."""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from argus.core import actions as A
from argus.core import action_registry as R
from argus.core import interpretation_actions as IA
from argus.core import publication_packet as PP
from argus.service import app as APP
from argus.tests import interp_support as S
from argus.tests.interp_support import TARGET, run

ALPHA = "α"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def chain(tmp_path):
    """A synthetic receipt chain: root -> preparation -> checkpoint, each citing the next by {path, sha256}, exactly the shape evidence_package.walk_chain follows."""
    d = tmp_path / "receipts"
    d.mkdir()
    ckpt = d / "ckpt.bin"
    ckpt.write_bytes(b"checkpoint bytes")
    prep = d / "preparation.json"
    prep.write_text(json.dumps({"checkpoint": {"path": str(ckpt), "sha256": _sha(ckpt.read_bytes())}}))
    root = d / "root_receipt.json"
    root.write_text(json.dumps({"preparation": {"path": str(prep), "sha256": _sha(prep.read_bytes())},
                                "physical_scroll": "PHercTest"}))
    return {"root": root, "prep": prep, "ckpt": ckpt}


@pytest.fixture
def d(tmp_path, monkeypatch):
    dd = S.make_target(tmp_path, monkeypatch, extents=((0, 100, 0, 100), (0, 100, 200, 300)))
    S.settle_cell(dd, "RT-0", ALPHA)
    S.accept_region(dd, "RT-1")
    return dd


def _export(chain=None, **extra):
    params = {"target": TARGET, **extra}
    if chain is not None:
        params["receipts"] = [str(chain["root"])]
    return run("packet.export", params)


def _packet(out) -> pathlib.Path:
    return pathlib.Path(out["out_dir"])


def _rewrite(path: pathlib.Path, fn) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")


def _restamp(pkt: pathlib.Path) -> None:
    """An attacker who edits a file and repairs the manifest's hashes to match."""
    m = json.loads((pkt / PP.MANIFEST_NAME).read_text(encoding="utf-8"))
    for f in m["files"]:
        f["sha256"] = _sha((pkt / f["path"]).read_bytes())
    m["manifest_sha256"] = PP._manifest_hash(m)
    (pkt / PP.MANIFEST_NAME).write_bytes(PP._canon(m))



def test_the_plan_lists_every_file_and_its_hash_and_writes_nothing(d, chain):
    plan = R.REGISTRY["packet.export"][0]({"target": TARGET, "receipts": [str(chain["root"])]})
    names = {f["path"] for f in plan["files"]}
    assert names == set(PP.FILE_NAMES.values())
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in plan["files"])
    assert plan["never_published"] is True and plan["citations"] == 1
    assert not pathlib.Path(plan["out_dir"]).exists(), "planning wrote nothing"


def test_export_then_reopen_and_verify_round_trip(d, chain):
    plan = R.REGISTRY["packet.export"][0]({"target": TARGET, "receipts": [str(chain["root"])]})
    out = _export(chain)
    assert out["packet_id"] == plan["packet_id"]
    pkt = _packet(out)
    assert {p.name for p in pkt.iterdir()} == set(PP.FILE_NAMES.values()) | {PP.MANIFEST_NAME}
    for f in plan["files"]:
        assert _sha((pkt / f["path"]).read_bytes()) == f["sha256"]
    res = run("packet.verify", {"target": TARGET, "packet": out["packet_id"]})["verification"]
    assert res["verdict"] == "VALID" and res["valid"] is True, res["reasons"]
    assert {f["status"] for f in res["files"]} == {"OK"}
    assert res["limitations"]["status"] == "OK" and res["limitations"]["claim_limits"] == "OK"
    assert {c["status"] for c in res["citations"]} == {"OK"} and len(res["citations"]) == 2
    assert "does not mean anything in the packet is true" in res["what_valid_means"]


def test_the_same_state_exports_the_same_packet_and_is_not_written_twice(d, chain):
    out = _export(chain)
    plan = R.REGISTRY["packet.export"][0]({"target": TARGET, "receipts": [str(chain["root"])]})
    assert plan["packet_id"] == out["packet_id"]
    assert plan["would_be_refused"] and plan["refusal"]["code"] == "PACKET_EXISTS"
    S.annotate(d, "RT-1", "alice", "OPERATOR", ALPHA)
    assert R.REGISTRY["packet.export"][0](
        {"target": TARGET, "receipts": [str(chain["root"])]})["packet_id"] != out["packet_id"]


def test_the_list_route_and_the_verify_route_reopen_a_packet(d, chain):
    out = _export(chain)
    c = TestClient(APP.app)
    listed = c.get("/api/interpretation/packets", params={"target": TARGET}).json()
    assert [p["packet_id"] for p in listed["packets"]] == [out["packet_id"]]
    assert listed["packets"][0]["published"] is False
    v = c.get("/api/interpretation/packet/verify",
              params={"target": TARGET, "packet": out["packet_id"]}).json()
    assert v["verdict"] == "VALID"
    outside = c.get("/api/interpretation/packet/verify",
                    params={"target": TARGET, "packet": str(chain["root"].parent)})
    assert outside.status_code == 403



def test_a_flipped_byte_is_a_hash_mismatch_on_that_file(d, chain):
    pkt = _packet(_export(chain))
    f = pkt / PP.FILE_NAMES["reviews"]
    b = bytearray(f.read_bytes())
    b[len(b) // 2] ^= 0x01
    f.write_bytes(bytes(b))
    res = PP.verify(pkt)
    status = {r["path"]: r["status"] for r in res["files"]}
    assert status[PP.FILE_NAMES["reviews"]] == "HASH_MISMATCH"
    assert [v for k, v in status.items() if k != PP.FILE_NAMES["reviews"]] == ["OK"] * 5
    assert res["verdict"] == "INVALID" and res["valid"] is False


def test_a_deleted_file_is_reported_missing(d, chain):
    pkt = _packet(_export(chain))
    (pkt / PP.FILE_NAMES["translation"]).unlink()
    res = PP.verify(pkt)
    assert {r["path"]: r["status"] for r in res["files"]}[PP.FILE_NAMES["translation"]] == "MISSING"
    assert res["verdict"] == "INVALID"


def test_an_edited_manifest_is_noticed(d, chain):
    pkt = _packet(_export(chain))
    _rewrite(pkt / PP.MANIFEST_NAME, lambda m: m.update(scroll="somewhere else"))
    res = PP.verify(pkt)
    assert res["manifest"]["self_hash"] == "HASH_MISMATCH" and res["verdict"] == "INVALID"


def test_an_unlisted_extra_file_is_noticed(d, chain):
    pkt = _packet(_export(chain))
    (pkt / "SMUGGLED.json").write_text("{}")
    res = PP.verify(pkt)
    assert res["unlisted_files"] == ["SMUGGLED.json"] and res["verdict"] == "INVALID"


def test_a_missing_manifest_is_refused_not_guessed(d, chain):
    pkt = _packet(_export(chain))
    (pkt / PP.MANIFEST_NAME).unlink()
    res = PP.verify(pkt)
    assert res["verdict"] == "REFUSED" and res["valid"] is False and res["reasons"]



def test_stripping_the_limitations_block_is_refused_even_with_the_hashes_repaired(d, chain):
    pkt = _packet(_export(chain))
    _rewrite(pkt / PP.LIMITS_NAME, lambda doc: doc.pop("limitations"))
    _restamp(pkt)
    res = PP.verify(pkt)
    assert all(f["status"] == "OK" for f in res["files"]), "the attacker repaired the hashes"
    assert res["limitations"]["status"] == "MISSING"
    assert res["verdict"] == "INVALID" and any("limitations block is missing" in r for r in res["reasons"])


def test_a_weakened_limitation_is_altered_not_valid(d, chain):
    pkt = _packet(_export(chain))

    def weaken(doc):
        doc["limitations"][0]["text"] = "This packet reads the scroll."

    _rewrite(pkt / PP.LIMITS_NAME, weaken)
    _restamp(pkt)
    res = PP.verify(pkt)
    assert res["limitations"]["status"] == "ALTERED" and res["verdict"] == "INVALID"


def test_a_claim_limit_flipped_true_is_refused(d, chain):
    pkt = _packet(_export(chain))
    _rewrite(pkt / PP.LIMITS_NAME, lambda doc: doc["claim_limits"].update(unread_scroll_reading_claimed=True))
    _restamp(pkt)
    res = PP.verify(pkt)
    assert res["limitations"]["claim_limits"] == "ALTERED" and res["verdict"] == "INVALID"


def test_a_missing_claim_limit_is_refused(d, chain):
    pkt = _packet(_export(chain))
    _rewrite(pkt / PP.LIMITS_NAME, lambda doc: doc["claim_limits"].pop("prize_eligibility_claimed"))
    _restamp(pkt)
    assert PP.verify(pkt)["limitations"]["claim_limits"] == "ALTERED"


def test_removing_the_limits_file_and_its_manifest_entry_is_refused(d, chain):
    pkt = _packet(_export(chain))
    (pkt / PP.LIMITS_NAME).unlink()
    m = json.loads((pkt / PP.MANIFEST_NAME).read_text(encoding="utf-8"))
    m["files"] = [f for f in m["files"] if f["path"] != PP.LIMITS_NAME]
    m["manifest_sha256"] = PP._manifest_hash(m)
    (pkt / PP.MANIFEST_NAME).write_bytes(PP._canon(m))
    res = PP.verify(pkt)
    assert res["limitations"]["status"] == "MISSING" and res["verdict"] == "INVALID"


def test_a_manifest_that_claims_publication_or_a_reading_is_refused(d, chain):
    pkt = _packet(_export(chain))

    def boast(m):
        m["published"] = True
        m["claim_limits"]["unread_scroll_reading_claimed"] = True
        m["manifest_sha256"] = PP._manifest_hash(dict(m))

    _rewrite(pkt / PP.MANIFEST_NAME, boast)
    res = PP.verify(pkt)
    assert res["verdict"] == "INVALID"
    assert any("claim the packet may not make" in r for r in res["reasons"])


def test_an_unknown_limitations_version_cannot_be_checked_and_is_invalid(d, chain):
    pkt = _packet(_export(chain))

    def bump(m):
        m["limitations_version"] = 99
        m["manifest_sha256"] = PP._manifest_hash(dict(m))

    _rewrite(pkt / PP.MANIFEST_NAME, bump)
    assert PP.verify(pkt)["limitations"]["status"] == "UNKNOWN_VERSION"



def test_a_changed_cited_receipt_is_a_hash_mismatch(d, chain):
    out = _export(chain)
    chain["ckpt"].write_bytes(b"a different checkpoint")
    res = PP.verify(_packet(out))
    assert "HASH_MISMATCH" in {c["status"] for c in res["citations"]}
    assert res["verdict"] == "INVALID"


def test_a_deleted_cited_receipt_is_reported_missing(d, chain):
    out = _export(chain)
    chain["prep"].unlink()
    res = PP.verify(_packet(out))
    assert "MISSING" in {c["status"] for c in res["citations"]} | {r["status"] for r in res["roots"]}
    assert res["verdict"] == "INVALID"


def test_a_deleted_root_receipt_is_reported_missing(d, chain):
    out = _export(chain)
    chain["root"].unlink()
    res = PP.verify(_packet(out))
    assert res["roots"][0]["status"] == "MISSING" and res["verdict"] == "INVALID"


def test_the_chain_file_records_the_re_hashed_citations(d, chain):
    pkt = _packet(_export(chain))
    doc = json.loads((pkt / PP.FILE_NAMES["chain"]).read_text(encoding="utf-8"))
    assert doc["roots"][0]["sha256"] == _sha(chain["root"].read_bytes())
    assert {c["status"] for c in doc["citations"]} == {"OK"}



def test_the_packet_carries_evidence_roles_and_keeps_model_answers_apart(d, chain):
    run("htr.propose", {"target": TARGET, "task_id": "RT-1", "answer": "INK",
                        "source_id": "ocr-x", "source_version": "2"}, actor="agent:ocr")
    pkt = _packet(_export(chain))
    reviews = json.loads((pkt / PP.FILE_NAMES["reviews"]).read_text(encoding="utf-8"))
    assert reviews["role_counts"]["HUMAN_JUDGMENT"] > 0
    assert reviews["role_counts"]["PREDICTION_DISCOVERY_EVIDENCE"] == 1
    for rec in reviews["records"]:
        for a in rec["answers"]:
            assert a["evidence_role"] == ("PREDICTION_DISCOVERY_EVIDENCE"
                                          if a["reviewer_class"] == "AI_AGENT" else "HUMAN_JUDGMENT")
    m = json.loads((pkt / PP.MANIFEST_NAME).read_text(encoding="utf-8"))
    roles = {f["path"]: f["evidence_roles"] for f in m["files"]}
    assert "HUMAN_JUDGMENT" in roles[PP.FILE_NAMES["reviews"]]
    assert "PREDICTION_DISCOVERY_EVIDENCE" in roles[PP.FILE_NAMES["reviews"]]
    assert "GROUND_TRUTH" not in sum(roles.values(), [])


def test_relabelling_a_model_answer_as_a_person_s_is_refused(d, chain):
    run("htr.propose", {"target": TARGET, "task_id": "RT-1", "answer": "INK",
                        "source_id": "ocr-x", "source_version": "2"}, actor="agent:ocr")
    pkt = _packet(_export(chain))

    def relabel(doc):
        for rec in doc["records"]:
            for a in rec["answers"]:
                if a["reviewer_class"] == "AI_AGENT":
                    a["evidence_role"] = "HUMAN_JUDGMENT"

    _rewrite(pkt / PP.FILE_NAMES["reviews"], relabel)
    _restamp(pkt)
    res = PP.verify(pkt)
    assert res["verdict"] == "INVALID" and any("relabelled" in r for r in res["reasons"])


def test_an_unclaimed_packet_contains_no_reading_claim_for_an_unread_scroll(d, chain):
    pkt = _packet(_export(chain))
    text = "".join((pkt / n).read_text(encoding="utf-8") for n in PP.FILE_NAMES.values())
    board = json.loads((pkt / PP.FILE_NAMES["board"]).read_text(encoding="utf-8"))
    assert board["claim"]["state"] == "NONE" and board["board"]["class"] == "READING_BOARD"
    assert '"class": "TRANSCRIPTION"' not in text
    lim = json.loads((pkt / PP.LIMITS_NAME).read_text(encoding="utf-8"))
    assert set(lim["claim_limits"].values()) == {False}
    assert lim["prize_posture"]["unread_scroll_reading_claimed"] is False
    assert lim["prize_posture"]["prize_eligibility_claimed"] is False
    assert lim["prize_posture"]["publication"]["may_publish"] is False
    ids = {x["id"] for x in lim["limitations"]}
    assert {"NO_UNREAD_SCROLL_READING", "TRANSLATIONS_ARE_PROPOSALS",
            "NOT_PUBLISHED", "VALID_MEANS_UNCHANGED_ONLY"} <= ids
    tr = json.loads((pkt / PP.FILE_NAMES["transcription"]).read_text(encoding="utf-8"))
    assert tr["proposed_reading"]["gaps"] >= 1, "a cell nobody lettered is a gap, not a guess"
    assert "a reading of an unread scroll" in tr["proposed_reading"]["what_this_is_not"]


def test_a_claimed_packet_says_so_and_still_makes_no_unread_scroll_claim(d, chain):
    S.annotate(d, "RT-1", "alice", "OPERATOR", ALPHA)
    S.annotate(d, "RT-1", "bob", "COMMUNITY", ALPHA)
    from argus.core import review_store as RS
    S.validate(d, RS.glyph_task_id("RT-1"), "carol", ALPHA)
    run("transcription.claim", {"target": TARGET, "claimed_by": "dana", "claimed_by_class": "OPERATOR"})
    pkt = _packet(_export(chain))
    board = json.loads((pkt / PP.FILE_NAMES["board"]).read_text(encoding="utf-8"))
    assert board["claim"]["state"] == "ACTIVE" and board["claim"]["claim"]["claimed_by"] == "dana"
    lim = json.loads((pkt / PP.LIMITS_NAME).read_text(encoding="utf-8"))
    assert set(lim["claim_limits"].values()) == {False}
    assert PP.verify(pkt)["verdict"] == "VALID"


def test_translation_proposals_travel_labelled_as_proposals(d, chain):
    S.annotate(d, "RT-1", "alice", "OPERATOR", ALPHA)
    S.annotate(d, "RT-1", "bob", "COMMUNITY", ALPHA)
    from argus.core import review_store as RS
    S.validate(d, RS.glyph_task_id("RT-1"), "carol", ALPHA)
    run("transcription.claim", {"target": TARGET, "claimed_by": "dana", "claimed_by_class": "OPERATOR"})
    run("language.write", {"target": TARGET, "language": "Greek", "declared_by": "erin"})
    tok = [c["cell_id"] for c in IA._context(d)["board"]["cells"]][:1]
    run("translation.propose", {"target": TARGET, "source_token_ids": tok, "text": "a phrase",
                                "proposed_by": "erin", "proposed_by_class": "SPECIALIST"})
    pkt = _packet(_export(chain))
    tr = json.loads((pkt / PP.FILE_NAMES["translation"]).read_text(encoding="utf-8"))
    assert tr["label"] == "PROPOSAL" and len(tr["candidates"]) == 1
    assert tr["candidates"][0]["label"] == "PROPOSAL"
    assert tr["candidates"][0]["is_a_reading_of_an_unread_scroll"] is False


def test_a_packet_never_publishes_or_uploads(d, chain, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    pkt = _packet(_export(chain))
    m = json.loads((pkt / PP.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert m["published"] is False and m["publication"]["visibility"] == "PRIVATE"
    assert "gh release" not in json.dumps(m)


def test_a_packet_without_a_cited_receipt_says_so(d):
    pkt = _packet(_export())
    doc = json.loads((pkt / PP.FILE_NAMES["chain"]).read_text(encoding="utf-8"))
    assert doc["roots"] == [] and "no receipt was cited" in doc["note"]
    assert PP.verify(pkt)["verdict"] == "VALID"


def test_a_receipt_outside_the_declared_artifact_roots_cannot_be_cited(d, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "secret.json"
    elsewhere.write_text("{}", encoding="utf-8")
    params = {"target": TARGET, "receipts": [str(elsewhere)]}
    plan = R.REGISTRY["packet.export"][0](params)
    assert plan["would_be_refused"] and plan["refusal"]["code"] == "RECEIPT_OUTSIDE_ROOTS"
    with pytest.raises(A.Refused) as e:
        run("packet.export", params)
    assert e.value.code == "RECEIPT_OUTSIDE_ROOTS"


def test_verifying_an_id_or_path_outside_the_packets_root_is_refused(d, chain):
    out = _export(chain)
    with pytest.raises(A.Refused) as e:
        IA.resolve_packet(TARGET, str(chain["root"].parent))
    assert e.value.code == "OUTSIDE_PACKETS_ROOT"
    assert IA.resolve_packet(TARGET, out["packet_id"]).is_dir()
    assert IA.resolve_packet(TARGET, out["out_dir"]).is_dir()
