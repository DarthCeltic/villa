"""Public release tests: the demo, the metric, the guards and the read-only service.

Every test runs with ARGUS_HOME pointed at a temporary directory (see conftest.py), so nothing
here reads or writes anything outside the test's own scratch space.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOUNDARY = ("ships no detector and makes no scientific claim", "does not read unread scrolls",
            "A rendered candidate is not a reading", "never_published")


def _run(*args, timeout=600):
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "argus"] + list(args), cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def test_demo_is_offline_and_labelled_demonstration_only(tmp_path):
    out = tmp_path / "receipt.json"
    r = _run("demo", "--no-render", "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "DEMONSTRATION_ONLY" in r.stdout
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["offline"] is True and rec["gpu_used"] is False
    assert rec["result"]["presentation"] == "DEMONSTRATION_ONLY"
    assert rec["metric"]["rule_id"] == "argus-metric-v1"


def test_demo_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert _run("demo", "--no-render", "--out", str(a)).returncode == 0
    assert _run("demo", "--no-render", "--out", str(b)).returncode == 0
    ma = json.loads(a.read_text(encoding="utf-8"))["metric"]
    mb = json.loads(b.read_text(encoding="utf-8"))["metric"]
    assert ma == mb


def test_doctor_runs_to_its_summary():
    r = _run("doctor")
    assert r.returncode in (0, 1), r.stderr
    assert "checks:" in r.stdout and "UNKNOWN is not a pass" in r.stdout


def test_metric_matches_hand_computed_values():
    from argus.core import metrics
    # scores/labels: positives at 0.9 and 0.4, negatives at 0.8, 0.3, 0.1
    s = [0.9, 0.8, 0.4, 0.3, 0.1]
    y = [1, 0, 1, 0, 0]
    # pairs (pos > neg): 0.9 beats 3, 0.4 beats 2 -> 5 of 6
    assert metrics.auc(s, y) == pytest.approx(5 / 6)
    # precision at each positive: 1/1 and 2/3 -> AP = (1 + 2/3) / 2
    assert metrics.average_precision(s, y) == pytest.approx((1 + 2 / 3) / 2)
    # ties are averaged, never ordered by luck
    assert metrics.auc([0.5, 0.5], [1, 0]) == pytest.approx(0.5)


def test_metric_refuses_a_single_class():
    from argus.core import metrics
    with pytest.raises(metrics.MetricRefusal):
        metrics.auc([0.1, 0.2, 0.3], [1, 1, 1])


def test_a_synthetic_result_cannot_be_captioned_as_a_discovery():
    from argus.core import result_class as RC
    rc = RC.ResultClass(target="fixture", target_class="SYNTHETIC",
                        exposure_basis="EXPOSURE_UNKNOWN", detector="box filter",
                        detector_cross_scroll_qualified=False, acquisition="synthetic",
                        metric="AUC", score=0.9)
    assert rc.presentation == "DEMONSTRATION_ONLY"
    with pytest.raises(RC.ResultClassError):
        rc.assert_not_discovery("ink found on the scroll")


def test_exposure_unknown_is_never_qualified_evidence():
    from argus.core import result_class as RC
    rc = RC.ResultClass(target="a scroll", target_class="UNREAD_SCROLL",
                        exposure_basis="EXPOSURE_UNKNOWN", detector="any",
                        detector_cross_scroll_qualified=True, acquisition="x",
                        metric="AUC", score=0.99)
    assert rc.presentation != "QUALIFIED_EVIDENCE"


def test_undeclared_weights_cannot_be_redistributed_or_submitted():
    from argus.core import licence_registry as LR
    rows = {r["name"]: r for r in LR.manifest()["components"]}
    weights = [r for r in rows.values() if r["kind"] == "MODEL_WEIGHTS" and r["spdx"] is None]
    assert all(not r["redistribute"] and not r["prize_submission"] for r in weights)
    ct = [r for r in rows.values() if r["kind"] == "DATA"]
    assert ct and all(not r["redistribute"] for r in ct)


def test_paths_default_under_argus_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_HOME", str(tmp_path))
    from argus import public_paths
    assert public_paths.public_path("home", "runs") == (tmp_path / "runs").as_posix()


def test_observatory_serves_health_and_refuses_writes():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from argus.service import app as A
    c = TestClient(A.app)
    h = c.get("/api/health")
    assert h.status_code == 200 and h.json().get("read_only") is True
    assert c.post("/api/observatory").status_code == 405


def test_fresh_install_routes_are_usable_without_operator_artifacts(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from argus.service import app as A

    monkeypatch.setattr(A, "ARTIFACT_ROOTS", [tmp_path])
    c = TestClient(A.app)

    targets = c.get("/api/targets")
    assert targets.status_code == 200
    assert targets.json()["available"] is True
    assert len(targets.json()["targets"]) == 23
    assert targets.json()["sets"]["GRAND_PRIZE_2027"]["count"] == 13
    boards = c.get("/api/prize-boards")
    assert boards.status_code == 200
    assert boards.json()["boards"]["FIRST_LETTERS"]["count"] == 22
    assert boards.json()["boards"]["GRAND_PRIZE"]["count"] == 13
    assert boards.json()["boards"]["PARIS4_TITLE"]["count"] == 1

    unroll = c.get("/api/unroll")
    assert unroll.status_code == 200
    assert unroll.json()["available"] is False
    assert unroll.json()["targets"] == []
    assert c.get("/api/unroll", params={"target": "missing"}).status_code == 404

    status = c.get("/api/scroll_status", params={"scroll": "PHercParis4"})
    assert status.status_code == 200
    assert status.json().get("status") != "ERROR"


def test_public_ui_keeps_optional_viewers_out_of_the_navigation_bundle():
    vite = (ROOT / "argus" / "ui" / "vite.config.ts").read_text(encoding="utf-8")
    assert "__ARGUS_UI_SOURCE_ROOT__" in vite and "__ARGUS_UI_BUILD_SHA__" in vite
    assert "manualChunks" in vite
    assert "vendor-vtk" in vite and "vendor-openseadragon" in vite
    assert vite.count('"/ui"') == 2
    assert "changeOrigin: true" not in vite


def test_release_manifest_matches_every_file():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "verify_manifest.py")],
                       cwd=str(ROOT), capture_output=True, text=True)
    if "no manifest" in r.stdout:
        pytest.skip("not a packaged release tree")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_claim_boundary_is_stated_in_the_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for c in BOUNDARY:
        assert c in text, c


def test_every_front_document_states_the_claim_boundary():
    for name in ("README.md", "QUICKSTART.md", "PUBLIC_CLAIMS_AND_LIMITS.md", "docs/LIMITATIONS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "ships no detector and makes no scientific claim" in text, name
        assert "A rendered candidate is not a reading" in text, name
        assert "never_published" in text, name


def test_the_package_declares_what_the_dockerfile_installs_and_what_the_tests_need():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = meta["project"]["optional-dependencies"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '".[service,volume-local]"' in dockerfile
    assert {"service", "volume-local", "test", "evidence-gate"} <= set(extras)
    assert any(d.startswith("zarr") for d in extras["test"])
    assert meta["tool"]["pytest"]["ini_options"]["testpaths"] == ["argus/tests", "tests"]
    assert "readme" not in meta["project"]           # the image copies no README.md, so the build must not need one
    for entry in meta["project"]["scripts"].values():
        module = entry.split(":")[0]
        assert (ROOT / (module.replace(".", "/") + ".py")).is_file(), entry


def test_the_docs_agree_with_the_dockerfile_and_ci_on_node():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "argus-portable-validation.yml").read_text(encoding="utf-8")
    assert "FROM node:20" in dockerfile and "node-version: '22'" in workflow
    assert "FROM python:3.11" in dockerfile and "'3.11'" in workflow
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Node 20+" in readme and "Node 22" in readme
    assert "Node 18" not in readme


def test_the_docs_name_the_public_repository_and_the_stack_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/Cinder-Covenant/ARGUS" in readme
    for needle in ("argus start", "argus status", "argus stop", "Start-ARGUS.cmd"):
        assert needle in readme, needle
    assert "<repository-url>" not in readme


def _pins(name):
    import re
    return dict((k.lower().replace("_", "-"), v) for k, v in re.findall(
        r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", (ROOT / name).read_text(encoding="utf-8"), re.M))


def test_uv_lock_uses_the_same_pins_as_the_portable_ci_requirements():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = meta["tool"]["uv"]["constraint-dependencies"]
    pins = _pins("runtime/requirements-portable-ci.txt")
    assert {c.split("==")[0].lower().replace("_", "-"): c.split("==")[1] for c in constraints} == pins
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {p["name"].lower().replace("_", "-"): p.get("version") for p in lock["package"]}
    assert [n for n, v in pins.items() if n in locked and locked[n] != v] == []
    root = [p for p in lock["package"] if p["name"] == meta["project"]["name"]]
    assert root and root[0]["version"] == meta["project"]["version"]
    assert lock["requires-python"] == meta["project"]["requires-python"]


def test_docker_files_are_relative_to_this_directory_so_they_work_from_a_monorepo_subdirectory():
    import re
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    contexts = [ln.split(":", 1)[1].strip() for ln in compose.splitlines() if ln.strip().startswith("context:")]
    assert contexts and all(not c.startswith(("..", "/")) for c in contexts), contexts
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        m = re.match(r"COPY\s+(?:--\S+\s+)*(.+?)\s+\S+\s*$", line)
        if m and "--from=" not in line:      # a copy out of another build stage is not a file in this tree
            for src in m.group(1).split():
                if "*" in src or src.startswith("--"):
                    continue
                assert (ROOT / src).exists(), "COPY source %s is not under this directory" % src
    assert (ROOT / "docs" / "VILLA_SUBPROJECT.md").is_file()


def test_tests_locate_the_tree_from_their_own_path_not_the_working_directory():
    import re
    for rel in ("tests/conftest.py", "conftest.py", "scripts/run_portable_ci.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert re.search(r"Path\(__file__\)\.resolve\(\)", text), rel
        assert "getcwd" not in text and "Path.cwd" not in text and "Path('.')" not in text, rel
