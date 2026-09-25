"""The one place ARGUS asks git about its OWN source tree."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_TIMEOUT = 60


def argus_root() -> Path:
    """ARGUS_ROOT: the directory holding pyproject.toml and the `argus` package."""
    from argus.core import paths
    return paths.repo()


def git(root, *args: str, timeout: int = _TIMEOUT, cwd=None):
    """Run git at `root`."""
    root = Path(root)
    try:
        p = subprocess.run(["git", "-c", "safe.directory=%s" % root.as_posix(), *args],
                           cwd=str(cwd or root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", str(exc)
    return p.returncode, p.stdout or "", p.stderr or ""


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def describe(root=None) -> dict:
    """Layout and identity of ARGUS's source tree."""
    root = Path(root) if root is not None else argus_root()
    out = {"root": str(root), "layout": "none", "toplevel": None, "subdir": None,
           "commit": None, "tree": None}
    rc, top, _ = git(root, "rev-parse", "--show-toplevel")
    if rc != 0 or not top.strip():
        return out
    top_p = Path(top.strip())
    out["toplevel"] = str(top_p)
    rc, head, _ = git(root, "rev-parse", "HEAD")
    commit = head.strip() if rc == 0 else None
    if _same(top_p, root):
        out.update(layout="toplevel", commit=commit)
        return out
    try:
        sub = root.resolve().relative_to(top_p.resolve()).as_posix()
    except ValueError:
        return out
    rc, tree, _ = git(root, "rev-parse", "HEAD:" + sub)
    if rc != 0 or not commit:
        return out
    out.update(layout="subdir", subdir=sub, commit=commit, tree=tree.strip())
    return out


def head(root=None) -> str:
    """The commit, or \"\" if git cannot say."""
    return describe(root)["commit"] or ""


def status_porcelain(root=None) -> str | None:
    """`git status --porcelain` limited to ARGUS's own tree."""
    d = describe(root)
    r = Path(d["root"])
    if d["layout"] == "toplevel":
        rc, out, _ = git(r, "status", "--porcelain", timeout=120)
    elif d["layout"] == "subdir":
        rc, out, _ = git(r, "status", "--porcelain", "--", d["subdir"], timeout=120,
                        cwd=d["toplevel"])
    else:
        rc, out, _ = git(r, "status", "--porcelain", timeout=120)
    return out if rc == 0 else None


def dirty_paths(root=None) -> list[str]:
    """Dirty paths relative to ARGUS_ROOT (the subdirectory prefix is stripped)."""
    d = describe(root)
    raw = status_porcelain(root) or ""
    prefix = (d["subdir"] + "/") if d["layout"] == "subdir" else ""
    paths = []
    for line in raw.splitlines():
        if len(line) > 3:
            p = line[3:].strip().strip('"').replace("\\", "/")
            if " -> " in p:
                p = p.split(" -> ", 1)[1].strip('"')
            paths.append(p[len(prefix):] if prefix and p.startswith(prefix) else p)
    return paths


def binding(root=None) -> dict:
    """What a receipt binds to."""
    d = describe(root)
    porcelain = status_porcelain(root)
    b = {"layout": d["layout"], "commit": d["commit"] or "UNKNOWN",
         "dirty_paths": len([ln for ln in (porcelain or "").splitlines() if ln.strip()]),
         "tree_clean": porcelain == ""}
    if d["layout"] == "subdir":
        b["subdir"] = d["subdir"]
        b["tree_hash"] = d["tree"]
    return b


def git_relative(root, rel: str) -> str:
    """A `<rev>:<path>` path component for `rel` (relative to ARGUS_ROOT) that git resolves from the working directory, so it is correct in either layout."""
    return ("./" + rel) if describe(root)["layout"] == "subdir" else rel


def log_pathspec(root=None) -> list[str]:
    """Extra `git log` arguments that limit history to ARGUS's subtree (empty at top level)."""
    d = describe(root)
    return ["--", d["subdir"]] if d["layout"] == "subdir" else []


def source_provenance(root=None) -> dict:
    """SOURCE_PROVENANCE.json at ARGUS_ROOT, or {} (a source checkout is its own source)."""
    p = Path(root if root is not None else argus_root()) / "SOURCE_PROVENANCE.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}
