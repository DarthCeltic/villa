from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence_gate.pipeline import REFUSAL, check_saved_mesh, evaluate_payload, write_receipt

pytest.importorskip("argus.core.contracts", reason="measured-mode tests need the argus package")
pytest.importorskip("tifffile", reason="measured-mode tests need tifffile")

from evidence_gate.measure import measure_mesh
from evidence_gate_fixtures.build_measured import build as build_measured


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "evidence_gate_fixtures"


def test_fixture_corpus_matches_expected_verdicts() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["fixtures"]:
        payload = json.loads((FIXTURES / f"{item['name']}.json").read_text(encoding="utf-8"))
        assert evaluate_payload(payload).status == item["expected"]


def test_remote_volume_is_refused_without_network() -> None:
    report = check_saved_mesh(FIXTURES / "clean.json", "s3://private/volume.zarr")
    assert report.status == REFUSAL
    assert report.network_used is False
    assert report.gates[0].name == "local_evidence"


def test_missing_measurement_refuses_instead_of_guessing() -> None:
    report = evaluate_payload({"mesh_bbox": [0, 0, 0], "volume_bounds": [1, 1, 1], "mesh_contained": True})
    assert report.status == REFUSAL
    assert any(g.status == REFUSAL for g in report.gates)


def test_receipt_records_command_and_redacts_credentials(tmp_path: Path) -> None:
    report = evaluate_payload(
        json.loads((FIXTURES / "clean.json").read_text(encoding="utf-8")),
        command="evidence-gate check",
        argv=["evidence-gate", "check", "--api-key", "do-not-write-this"],
    )
    receipt = tmp_path / "receipt.json"
    write_receipt(report, receipt)
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["command"] == "evidence-gate check"
    assert "do-not-write-this" not in receipt.read_text(encoding="utf-8")
    assert saved["argv"][-1] == "<redacted>"



def test_measured_clean_mesh_passes_every_gate_including_chunk_identity(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["clean"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          volume_url=m["volume_url"], chunk_shape=tuple(m["chunk_shape"]),
                          orientation_provenance=m["orientation_provenance"])
    assert report.status == "PASS"
    names = {g.name: g.status for g in report.gates}
    assert names["frame_handshake"] == "PASS"
    assert names["topology"] == "PASS"
    assert names["chunk_identity"] == "PASS"
    assert report.network_used is False


def test_measured_mesh_outside_volume_fails_citing_1660(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["mesh_outside_volume"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          orientation_provenance=m["orientation_provenance"])
    assert report.status == "FAIL"
    assert any("#1660" in g.reason for g in report.gates)


def test_measured_seam_across_sheet_fails_citing_1675(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["seam_across_sheet"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          orientation_provenance=m["orientation_provenance"])
    assert report.status == "FAIL"
    assert any("#1675" in g.reason for g in report.gates)


def test_measured_all_fill_fails_citing_1674(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["all_fill"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          volume_url=m["volume_url"], chunk_shape=tuple(m["chunk_shape"]),
                          orientation_provenance=m["orientation_provenance"])
    assert report.status == "FAIL"
    assert any("#1674" in g.reason for g in report.gates)


def test_measured_remote_volume_is_not_read_without_allow_network(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["clean"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          volume_url="https://example.invalid/volume.zarr",
                          orientation_provenance=m["orientation_provenance"])
    assert report.network_used is False
    names = {g.name: g.status for g in report.gates}
    assert names["frame_handshake"] == "PASS"
    assert names["chunk_identity"] == "NOT_MEASURED"



def test_volume_only_probe_passes_on_held_signal_and_fails_on_unwritten_chunks(tmp_path: Path) -> None:
    from evidence_gate.measure import parse_probe_box, probe_volume
    manifest = build_measured(tmp_path)
    clean = manifest["cases"]["clean"]["manifest"]
    box = parse_probe_box("0:8:0:16:0:16")
    ok = probe_volume(clean["volume_url"], box)
    assert ok.status == "PASS" and [g.name for g in ok.gates] == ["chunk_identity"], ok
    assert ok.network_used is False
    empty = manifest["cases"]["all_fill"]["manifest"]
    bad = probe_volume(empty["volume_url"], box)
    assert bad.status == "FAIL" and "never written" in bad.gates[0].reason


def test_volume_only_probe_reports_a_partly_held_box_and_refuses_outside_boxes(tmp_path: Path) -> None:
    from evidence_gate.measure import probe_volume
    manifest = build_measured(tmp_path)
    url = manifest["cases"]["clean"]["manifest"]["volume_url"]
    root = Path(__import__("urllib.request").request.url2pathname(url[len("file://"):]))
    victim = next(p for p in sorted((root / "0").rglob("*")) if p.is_file() and p.name != ".zarray")
    victim.unlink()
    partial = probe_volume(url, (0, 16, 0, 16, 0, 16))
    assert partial.status == REFUSAL and "partly held" in partial.gates[0].reason
    outside = probe_volume(url, (0, 10**6, 0, 8, 0, 8))
    assert outside.status == REFUSAL and "outside" in outside.gates[0].reason


def test_volume_only_probe_rejects_a_malformed_box() -> None:
    from evidence_gate.measure import parse_probe_box
    for bad in ("1:2:3", "4:2:0:1:0:1", "-1:2:0:1:0:1", "a:b:c:d:e:f"):
        with pytest.raises(ValueError):
            parse_probe_box(bad)


def test_chunk_identity_verdict_is_not_hidden_by_a_later_refusal(tmp_path: Path) -> None:
    manifest = build_measured(tmp_path)
    m = manifest["cases"]["clean"]["manifest"]
    report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"], volume_shape=m["volume_shape"],
                          volume_url=m["volume_url"], chunk_shape=tuple(m["chunk_shape"]),
                          orientation_provenance=None)
    names = {g.name: g for g in report.gates}
    assert report.status == REFUSAL, report.status
    assert names["chunk_identity"].status == "PASS", report.gates


def test_cli_volume_only_needs_a_box_and_prints_the_verdict(tmp_path: Path, capsys) -> None:
    from evidence_gate.cli import main
    manifest = build_measured(tmp_path)
    mp = tmp_path / "vol.json"
    mp.write_text(json.dumps({"volume_url": manifest["cases"]["clean"]["manifest"]["volume_url"]}))
    assert main(["measure", "--manifest", str(mp), "--volume-only", "--probe-box", "0:8:0:8:0:8"]) == 0
    assert json.loads(capsys.readouterr().out)["gates"][0]["status"] == "PASS"
    with pytest.raises(SystemExit):
        main(["measure", "--manifest", str(mp), "--volume-only"])
