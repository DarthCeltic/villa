"""`argus run <public target manifest>`: the public sequencer, tested without a network or a GPU."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import io
import json
import pathlib

import numpy as np
import pytest

from argus.cli import public_pipeline as PP
from argus.core import result_class as RC
from argus.core import scroll_status as SS

MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "public_targets" / "pherc0139-w016-ink9um-control.json"


def _m():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _write(tmp_path, m, name="t.json"):
    p = tmp_path / name
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this test must not touch the network")
    monkeypatch.setattr(PP.urllib.request, "urlopen", boom)


def test_the_shipped_manifest_validates_and_names_only_public_https_sources():
    m, sha = PP.load_manifest(MANIFEST)
    assert len(sha) == 64 and m["id"] == MANIFEST.stem
    text = MANIFEST.read_text(encoding="utf-8")
    for bad in (_argus_public_path('anchor', ''), _argus_public_path('anchor', ''), _argus_public_path('anchor', ''), _argus_public_path('anchor', ''), "/t/", "/home/", "\\\\"):
        assert bad not in text, "a public manifest may not carry a local path (%r)" % bad
    urls = [m["source"]["surface_volume_url"], m["labels"]["base_url"], m["model"]["url"]]
    assert all(u.startswith("https://") for u in urls)
    assert "CC BY-NC 4.0" in m["terms"]["ct_data"] and m["terms"]["citation"]


def test_the_shipped_target_is_on_no_prize_list_and_is_labelled_public_data():
    from argus.core import official_identity as OI
    m = _m()
    survey = OI.load_survey()
    assert survey and survey["prize_sets"]
    for name, row in survey["prize_sets"].items():
        assert m["scroll"] not in row["scrolls"], "%s is on the %s list" % (m["scroll"], name)
    assert m["labels"]["arrays"] and m["labels"]["label_plane"] is not None


@pytest.mark.parametrize("mutate,code_fragment", [
    (lambda m: m.update(schema="something-else"), "schema"),
    (lambda m: m["stages"].append({"id": "publish"}), "unknown stage"),
    (lambda m: m["stages"].reverse(), "out of order"),
    (lambda m: m.update(stages=[s for s in m["stages"] if s["id"] != "inspect"]), "needs"),
    (lambda m: m.pop("terms"), "missing"),
])
def test_a_bad_manifest_is_refused_with_a_reason(tmp_path, mutate, code_fragment):
    m = _m()
    mutate(m)
    with pytest.raises(PP.StageRefusal) as e:
        PP.load_manifest(_write(tmp_path, m))
    assert e.value.code == "MANIFEST_INVALID" and code_fragment in e.value.detail


def test_every_capability_a_stage_names_is_in_the_ledger_and_callable():
    assert PP.ledger_gaps(_m()) == []
    m = _m()
    m["stages"][6]["capability"] = "vesuvius.no_such_tool"
    assert PP.ledger_gaps(m) and "absent from the ledger" in PP.ledger_gaps(m)[0]


def test_dry_run_explains_every_stage_and_makes_zero_requests():
    out = io.StringIO()
    assert PP.run_manifest(MANIFEST.stem, dry_run=True, out=out) == 0
    text = out.getvalue()
    for sid, _what, _need in PP.STAGES:
        assert sid in text
    assert "zero requests" in text and "nothing was fetched" in text
    assert "--authorize" in text and "CC BY-NC 4.0" in text
    assert "4736" in text and "acquisition_id" in text


def test_argus_run_dispatches_a_manifest_to_the_public_driver():
    from argus.cli import cmd_run
    out = io.StringIO()
    assert cmd_run.run([MANIFEST.stem, "--dry-run"], out=out) == 0
    assert "DRY RUN" in out.getvalue()
    listing = io.StringIO()
    assert cmd_run.run(["--list"], out=listing) == 0
    assert MANIFEST.stem in listing.getvalue()


def test_a_real_run_without_an_authorisation_is_refused_before_any_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(PP.paths, "artifact_write_root", lambda: tmp_path)
    monkeypatch.setattr(PP, "_find_model", lambda m: None)
    out = io.StringIO()
    assert PP.run_manifest(MANIFEST.stem, out=out, run_id="t") == 1
    assert "LAUNCH_AUTHORIZATION_V3" in out.getvalue()
    rec = json.loads(next(tmp_path.rglob("PIPELINE_RUN_RECEIPT.json")).read_text(encoding="utf-8"))
    assert rec["outcome"] == "REFUSED" and rec["refused_at"] == "launch" and rec["stages"] == []


def test_an_authorisation_the_machine_cannot_verify_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(PP.paths, "artifact_write_root", lambda: tmp_path)
    ck = tmp_path / "ck.pth"
    ck.write_bytes(b"x")
    monkeypatch.setattr(PP, "_find_model", lambda m: ck)
    out = io.StringIO()
    assert PP.run_manifest(MANIFEST.stem, authorization_id="no-such-authorisation", out=out, run_id="t") == 1
    assert "REFUSED: LAUNCH_AUTHORIZATION_V3" in out.getvalue()


def test_identify_resolves_the_surface_volume_through_the_survey_and_a_lie_is_refused():
    ctx = {}
    got = PP.s_identify(_m(), ctx)
    assert got["scroll"] == "PHerc0139" and got["volume_id"] == "20260102150214"
    assert ctx["public_name"].startswith("PHerc0139 / w016 / upstream 20250108000004-w029_2025010827")
    lie = _m()
    lie["volume_id"] = "20250728140407"
    with pytest.raises(PP.StageRefusal) as e:
        PP.s_identify(lie, {})
    assert e.value.code == "IDENTITY_MISMATCH"


def test_a_prize_scroll_is_refused_at_the_gate():
    from argus.core import official_identity as OI
    survey = OI.load_survey()
    prize_scroll = survey["prize_sets"]["FIRST_LETTERS"]["scrolls"][0]
    m = _m()
    m["scroll"] = prize_scroll
    with pytest.raises(PP.StageRefusal) as e:
        PP.s_gate_target(m, {"identity": {"prizes": []}})
    assert e.value.code == "PRIZE_TARGET"
    assert PP.s_gate_target(_m(), {"identity": {"prizes": []}})["on_a_prize_list"] is False


def test_a_surface_volume_url_carries_its_volume_id_to_the_acquisition_gate():
    from argus.core import official_identity as OI
    from argus.core import volume_id_gate as VG
    url = _m()["source"]["surface_volume_url"]
    assert OI.parse_any_volume_token(url) == "20260102150214"
    assert OI.parse_volume_store_token(url) is None
    v = VG.before_acquisition(scroll="PHerc0139", declared_volume_id="20260102150214", source_url=url)
    assert v["state"] == "VERIFIED"
    with pytest.raises(VG.VolumeIdRefusal):
        VG.before_acquisition(scroll="PHerc0139", declared_volume_id="20250728140407", source_url=url)


def test_the_acquisition_plan_is_bounded_and_needs_no_request():
    plan = PP._plan_a2(_m())
    assert plan["dry_run"] and plan["requests_made"] == 0
    assert plan["planned_objects"] == 35 and plan["planned_upper_bound_bytes"] <= _m()["source"]["byte_ceiling"]


def _fake_store(tmp_path, chunks, roi):
    store = tmp_path / "store"
    (store / "2").mkdir(parents=True)
    meta = {"zarr_format": 2, "shape": [4, 8, 8], "chunks": [4, 4, 4], "dtype": "|u1", "compressor": None,
            "fill_value": 0, "order": "C", "filters": None, "dimension_separator": "/"}
    (store / "2" / ".zarray").write_text(json.dumps(meta), encoding="utf-8")
    for (zi, yi, xi), byte in chunks.items():
        p = store / "2" / str(zi) / str(yi) / str(xi)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes([byte]) * 64)
    m = _m()
    m["source"]["roi"] = roi
    return store, m


def test_the_crop_is_rekeyed_without_touching_a_chunk_byte(tmp_path):
    store, m = _fake_store(tmp_path, {(0, 1, 1): 7, (0, 1, 0): 9},
                           {"z0": 0, "z1": 4, "y0": 4, "y1": 8, "x0": 0, "x1": 8})
    dest = tmp_path / "crop" / "2"
    got = PP._materialize_crop(store, m, dest)
    assert got == {"shape": [4, 4, 8], "chunks_copied": 2}
    assert json.loads((dest / ".zarray").read_text())["shape"] == [4, 4, 8]
    assert (dest / "0" / "0" / "1").read_bytes() == bytes([7]) * 64
    assert (dest / "0" / "0" / "0").read_bytes() == bytes([9]) * 64


def test_a_crop_off_the_chunk_grid_is_refused(tmp_path):
    store, m = _fake_store(tmp_path, {(0, 0, 0): 1}, {"z0": 0, "z1": 4, "y0": 2, "y1": 8, "x0": 0, "x1": 8})
    with pytest.raises(PP.StageRefusal) as e:
        PP._materialize_crop(store, m, tmp_path / "crop")
    assert e.value.code == "CROP_NOT_CHUNK_ALIGNED"


def test_scoring_uses_only_the_validation_mask_and_a_perfect_map_scores_one(tmp_path):
    from PIL import Image
    m = _m()
    m["source"]["roi"] = {"z0": 0, "z1": 4, "y0": 0, "y1": 4, "x0": 0, "x1": 4}
    plane = 1
    meta = {"chunks": [4, 4, 4], "shape": [4, 4, 4], "dtype": "|u1", "compressor": None, "fill_value": 0,
            "dimension_separator": "."}
    ink = np.zeros((4, 4, 4), np.uint8)
    ink[plane, :, :2] = 1
    val = np.zeros((4, 4, 4), np.uint8)
    val[plane, :, :] = 1
    val[plane, 0, :] = 0
    sup = np.zeros((4, 4, 4), np.uint8)
    for name, arr in (("ink", ink), ("validation", val), ("supervision", sup)):
        d = tmp_path / "labels" / name
        d.mkdir(parents=True)
        (d / ".zarray").write_text(json.dumps(meta), encoding="utf-8")
        (d / "0.0.0").write_bytes(arr.tobytes())
    pred = np.where(ink[plane] > 0, 250, 3).astype(np.uint8)
    pred[0, :] = 100
    Image.fromarray(pred, "L").save(str(tmp_path / "prediction.tif"), compression="tiff_adobe_deflate")
    m["labels"]["label_plane"] = plane
    got = PP.s_score(m, {"labels_dir": tmp_path / "labels", "prediction": tmp_path / "prediction.tif"})
    assert got["scored_pixels"] == 12 and got["metric"]["auc"] == 1.0
    assert got["metric"]["rule_id"].startswith("argus-metric")
    assert got["validation_pixels_also_in_supervision_mask"] == 0


def test_a_launch_binds_modules_that_exist_and_a_plan_that_does_not_move():
    from argus.core import launch_authorization_v2 as LA2
    m = _m()
    assert LA2.module_manifest(PP.BOUND_MODULES)["count"] == len(PP.BOUND_MODULES)
    a, b = PP._packet(m, "x" * 64, "ck"), PP._packet(m, "x" * 64, "ck")
    assert a == b and a["arms"] == [PP.ARM] and a["runner_rel"] == PP.RUNNER_REL


def test_a_reserved_region_control_is_a_control_and_may_never_be_called_a_discovery():
    base = dict(target="t", target_class="LABELLED_SCROLL", detector="d", detector_cross_scroll_qualified=False,
                acquisition="a", metric="AUC", score=0.77)
    held = RC.ResultClass(exposure_basis="HELD_OUT_BY_FOLD", **base)
    assert held.presentation == "KNOWN_DOMAIN_HELD_OUT_CONTROL" and not held.may_claim_discovery
    assert "proves the pipeline, not a discovery" in held.display_banner()
    with pytest.raises(RC.ResultClassError):
        held.assert_not_discovery("ink found on a scroll")
    assert RC.ResultClass(exposure_basis="TRAINED_ON", **base).presentation == "CALIBRATION_ONLY"
    assert RC.ResultClass(exposure_basis="EXPOSURE_UNKNOWN", **base).presentation == "EXPLORATORY_CANDIDATE"


def test_a_completed_ink_attempt_in_the_lineage_moves_the_route(tmp_path, monkeypatch):
    from argus.core import stage_lineage as SL
    monkeypatch.setattr(SS.process_contract, "_acquisition_receipt", lambda scroll: {"state": "PACKET_REQUIRED"})
    shelf = {"scrolls": [{"scroll": "PHerc0009B", "display": "PHerc 0009B", "shelf": "LABELLED_CONTROL", "prizes": [],
                          "local_data": "HELD", "published_upstream": True, "acquisitions": [],
                          "acquisition_families": [], "furthest_stage": "SCAN_HELD", "labelled": True,
                          "operator_fence": None, "filters": {"has_surface": False, "has_render": False}}]}

    def ink(root):
        doc = SS.status("PHerc0009B", shelf=shelf, lineage_root=root, _index={}, _effort={})
        return next(s for s in doc["steps"] if s["id"] == "ink_inference")

    assert ink(tmp_path / "lineage")["state"] == "NOT_REACHED"
    receipt = tmp_path / "PIPELINE_RUN_RECEIPT.json"
    receipt.write_text("{}", encoding="utf-8")
    SL.record_attempt("PHerc0009B", "ink_2d", "COMPLETED", receipt_path=str(receipt), detail="control",
                      root=tmp_path / "lineage")
    step = ink(tmp_path / "lineage")
    assert step["state"] == "DONE" and "not a reading" in step["why"]
    receipt2 = tmp_path / "RUN.json"
    receipt2.write_text("{}", encoding="utf-8")
    SL.record_attempt("PHerc0009B", "ink_2d", "COMPLETED", receipt_path=str(receipt2), detail="scored",
                      recorded_by={"tool": "argus run"}, root=tmp_path / "lineage")
    unlabelled = {"scrolls": [dict(shelf["scrolls"][0], labelled=False)]}
    doc = SS.status("PHerc0009B", shelf=unlabelled, lineage_root=tmp_path / "lineage", _index={}, _effort={})
    ev = next(s for s in doc["steps"] if s["id"] == "evidence_comparison")
    assert ev["state"] == "AVAILABLE" and "scored against" in ev["why"]
