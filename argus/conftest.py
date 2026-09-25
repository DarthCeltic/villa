"""Isolate every test from any real ARGUS state on the machine.

This file is collected before any test module is imported. It points ARGUS_HOME and every other
root at a fresh temporary directory, so a test that writes a receipt, a ledger record or a job
writes under that directory and never under the home of whoever runs the suite. Subprocesses a
test starts inherit the same environment.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent
for _p in (ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_ROOT_VARS = ("ARGUS_LEGACY_ROOT", "ARGUS_CACHE_ROOT", "ARGUS_UPSTREAM_ROOT", "ARGUS_RUNTIME_ROOT",
              "ARGUS_SCIENCE_DATA_ROOT", "ARGUS_SCIENCE_CACHE_ROOT", "ARGUS_RUNS_ROOT",
              "ARGUS_MODELS_ROOT", "ARGUS_REGISTRIES_ROOT", "ARGUS_PRIVATE_SCIENCE_ROOT")
_FLAG = "ARGUS_TEST_HOME_ISOLATED"


def _isolate() -> str:
    current = os.environ.get("ARGUS_HOME")
    if current and os.environ.get(_FLAG) == current:
        return current                            # a parent pytest run already isolated this process tree
    home = tempfile.mkdtemp(prefix="argus_test_home_")
    (pathlib.Path(home) / "state").mkdir(parents=True, exist_ok=True)
    os.environ["ARGUS_HOME"] = home
    os.environ[_FLAG] = home
    for var in _ROOT_VARS:
        path = os.path.join(home, var.lower())
        os.makedirs(path, exist_ok=True)
        os.environ[var] = path
    os.environ["ARGUS_BFF_AUDIT"] = os.path.join(home, "state", "bff_audit.jsonl")
    os.environ["ARGUS_REPO"] = str(ROOT)
    return home


TEST_HOME = _isolate()


@pytest.fixture(autouse=True)
def _restore_ws_allowed_origins():
    """A test that widens the WebSocket origin allowlist must not leak that into the next test."""
    yield
    app = sys.modules.get("argus.service.app")
    if app is not None and hasattr(app, "default_ws_allowed_origins"):
        app.WS_ALLOWED_ORIGINS = app.default_ws_allowed_origins()
