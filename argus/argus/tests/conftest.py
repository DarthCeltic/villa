"""Test kit fixtures: a disposable operator key, and no GPU context at collection time.

Isolation of ARGUS_HOME happens in the repository-level conftest.py. Two things are specific to
this directory:

* transport tests get an explicit disposable operator key, never a production default;
* a module that imports torch at module level would create a CUDA context merely by being
  collected, so such modules are left out of collection and the run says so. Set
  ARGUS_GPU_TESTS=1 to collect them anyway.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

HERE = pathlib.Path(__file__).parent
ENV = "ARGUS_GPU_TESTS"


@pytest.fixture(autouse=True)
def _bff_operator_access_key(tmp_path_factory, monkeypatch):
    try:
        from argus.service import bff
    except ImportError:
        yield
        return
    key_path = tmp_path_factory.mktemp("bff-access") / "ui_access_key"
    key_path.write_text("TEST-UI-ACCESS-KEY", encoding="utf-8")
    monkeypatch.setattr(bff, "UI_ACCESS_KEY_PATH", key_path)
    bff._SESSIONS.clear()
    yield


def _imports_torch_at_module_level(p: pathlib.Path) -> bool:
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return True
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "torch" for a in node.names):
                return True
            continue
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "torch":
                return True
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in ("importorskip", "import_module") and sub.args:
                    a0 = sub.args[0]
                    if isinstance(a0, ast.Constant) and str(a0.value).split(".")[0] == "torch":
                        return True
    return False


collect_ignore: list = []
if os.environ.get(ENV) != "1":
    for _p in sorted(HERE.glob("test_*.py")):
        if _imports_torch_at_module_level(_p):
            collect_ignore.append(_p.name)

_MESSAGE = ("argus/tests: %d module(s) not collected because they import torch at module level: %s. "
            "Set %s=1 to include them." % (len(collect_ignore), ", ".join(collect_ignore) or "-", ENV))


def pytest_report_header(config):
    return _MESSAGE if collect_ignore else "argus/tests: no GPU-importing modules excluded"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if collect_ignore:
        terminalreporter.write_sep("-", "collection excluded")
        terminalreporter.write_line(_MESSAGE)
