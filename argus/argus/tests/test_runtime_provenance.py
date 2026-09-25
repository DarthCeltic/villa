"""/api/runtime names the source a tree was built from, and a declared-optional torch is not an error."""
from __future__ import annotations

import builtins
import json
import subprocess

from fastapi.testclient import TestClient

from argus.service import app as A


def _tree(tmp_path, *, provenance: bool, torch_optional: bool):
    (tmp_path / "runtime").mkdir()
    lock = b"# test lock\nnumpy==1.0\n"
    (tmp_path / "runtime" / "requirements.lock.txt").write_bytes(lock)
    import hashlib
    (tmp_path / "runtime" / "RUNTIME_FREEZE.json").write_text(json.dumps({
        "python": {"version": "0.0.0"},
        "torch": {"version": None, "optional": torch_optional},
        "lockfile": {"sha256": hashlib.sha256(lock).hexdigest(), "n_packages": 1}}), encoding="utf-8")
    if provenance:
        (tmp_path / "SOURCE_PROVENANCE.json").write_text(json.dumps({"source_revision": "abc123def"}), encoding="utf-8")


def _runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "ROOT", tmp_path)
    A._RUNTIME_MEMO.clear()
    real_import = builtins.__import__

    def no_torch(name, *a, **k):
        if name == "torch":
            raise ModuleNotFoundError("No module named 'torch'")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_torch)
    return TestClient(A.app).get("/api/runtime").json()


def test_an_export_reports_the_source_revision_it_was_built_from(monkeypatch, tmp_path):
    _tree(tmp_path, provenance=True, torch_optional=True)
    d = _runtime(monkeypatch, tmp_path)
    assert d["service_identity"]["source_revision"] == "abc123def"
    assert d["service_identity"]["source_provenance"] == "SOURCE_PROVENANCE.json"


def test_a_declared_optional_torch_that_is_absent_is_not_an_error(monkeypatch, tmp_path):
    _tree(tmp_path, provenance=False, torch_optional=True)
    d = _runtime(monkeypatch, tmp_path)
    assert d["torch"]["optional"] is True and d["torch"]["error"] is None and d["torch"]["matches"] is True
    assert d["service_identity"]["source_provenance"] == "this tree is the source checkout"


def test_an_undeclared_missing_torch_is_still_reported(monkeypatch, tmp_path):
    _tree(tmp_path, provenance=False, torch_optional=False)
    d = _runtime(monkeypatch, tmp_path)
    assert d["torch"]["optional"] is False and "ModuleNotFoundError" in (d["torch"]["error"] or "")



def _g(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _w(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def _mk_repo(tmp_path, *, nested: bool):
    top = tmp_path / "mono"
    top.mkdir()
    _g(top, "init", "-q")
    root = top / "argus" if nested else top
    root.mkdir(exist_ok=True)
    _w(root / "pyproject.toml", "[project]\nname = 'argus'\n")
    _w(root / "mod.py", "x = 1\n")
    if nested:
        (top / "other").mkdir()
        _w(top / "other" / "f.txt", "a\n")
    _g(top, "add", "-A")
    _g(top, "commit", "-q", "-m", "seed")
    return top, root


def test_subdir_layout_binds_commit_subtree_and_scoped_dirty_state(tmp_path):
    from argus.core import git_state
    top, root = _mk_repo(tmp_path, nested=True)
    d = git_state.describe(root)
    assert d["layout"] == "subdir" and d["subdir"] == "argus"
    assert d["commit"] == _g(top, "rev-parse", "HEAD")
    assert d["tree"] == _g(top, "rev-parse", "HEAD:argus") != _g(top, "rev-parse", "HEAD^{tree}")
    assert git_state.binding(root)["tree_clean"] is True
    _w(top / "other" / "f.txt", "changed\n")
    _w(top / "other" / "new.txt", "n\n")
    assert git_state.status_porcelain(root) == ""
    assert git_state.binding(root)["tree_clean"] is True
    _w(root / "mod.py", "x = 2\n")
    assert git_state.binding(root)["tree_clean"] is False
    assert git_state.dirty_paths(root) == ["mod.py"]


def test_toplevel_layout_is_unchanged(tmp_path):
    from argus.core import git_state
    top, root = _mk_repo(tmp_path, nested=False)
    assert git_state.describe(root)["layout"] == "toplevel"
    _w(root / "mod.py", "x = 3\n")
    _w(root / "new.py", "y = 1\n")
    raw = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    assert git_state.status_porcelain(root) == raw
    b = git_state.binding(root)
    assert "tree_hash" not in b and "subdir" not in b and b["commit"] == _g(top, "rev-parse", "HEAD")


def test_untracked_subdir_or_no_git_falls_back(tmp_path):
    from argus.core import git_state
    top, _ = _mk_repo(tmp_path, nested=True)
    (top / "untracked_dir").mkdir()
    assert git_state.describe(top / "untracked_dir")["layout"] == "none"
    plain = tmp_path / "export"
    plain.mkdir()
    _w(plain / "SOURCE_PROVENANCE.json", json.dumps({"source_revision": "r1"}))
    assert git_state.describe(plain)["layout"] == "none"
    assert git_state.source_provenance(plain) == {"source_revision": "r1"}


def _l1():
    """The first-generation launch authorization is private-only; the public build ships v2 and v3, which take the same binding."""
    try:
        from argus.core import launch_authorization as L1
    except ImportError:
        return None
    return L1


def test_receipts_bind_to_the_subtree_and_ignore_sibling_changes(tmp_path, monkeypatch):
    from argus.core import evidence_package as EP
    L1 = _l1()
    from argus.core import launch_authorization_v2 as V2
    from argus.core import launch_authorization_v3 as V3
    top, root = _mk_repo(tmp_path, nested=True)
    monkeypatch.setenv("ARGUS_REPO", str(root))
    tree = _g(top, "rev-parse", "HEAD:argus")
    head = _g(top, "rev-parse", "HEAD")
    if L1 is not None:
        sc = L1.source_commit()
        assert sc["commit"] == head and sc["tree_hash"] == tree and sc["subdir"] == "argus"
    assert EP._git_head() == head and EP._subtree_binding()["repo_subtree_hash"] == tree
    assert V3.git_commit() == head
    base = V2.dirty_patch(["mod.py"])
    assert base["source_tree_hash"] == tree and base["dirty_path_count"] == 0
    _w(top / "other" / "f.txt", "changed\n")
    assert V2.dirty_patch(["mod.py"]) == base
    assert L1 is None or L1.source_commit()["tree_clean"] is True
    _w(root / "mod.py", "x = 9\n")
    after = V2.dirty_patch(["mod.py"])
    assert after["relevant_dirty"] == ["mod.py"] and after["dirty_patch_sha256"] != base["dirty_patch_sha256"]
    assert L1 is None or L1.source_commit()["tree_clean"] is False


def test_toplevel_receipts_gain_no_new_keys(tmp_path, monkeypatch):
    from argus.core import evidence_package as EP
    L1 = _l1()
    from argus.core import launch_authorization_v2 as V2
    _top, root = _mk_repo(tmp_path, nested=False)
    monkeypatch.setenv("ARGUS_REPO", str(root))
    assert (L1 is None or "tree_hash" not in L1.source_commit()) and EP._subtree_binding() == {}
    assert "source_tree_hash" not in V2.dirty_patch(["mod.py"])


def test_runtime_reports_the_subtree_when_nested(monkeypatch, tmp_path):
    top, root = _mk_repo(tmp_path, nested=True)
    _tree(root, provenance=False, torch_optional=True)
    d = _runtime(monkeypatch, root)
    assert d["service_identity"]["source_subtree_hash"] == _g(top, "rev-parse", "HEAD:argus")
