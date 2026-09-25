r"""Verify RELEASE_MANIFEST.json: every listed file exists with its sha256, and nothing is unlisted.

Usage: python tools/verify_manifest.py   (exit 0 on a match, 1 otherwise)

Run it from the directory that holds this tools/ folder. It works the same when that directory is a
subdirectory of a larger repository (for example `argus/` inside a monorepo): paths in the manifest are
relative to this directory and the "tracked but not listed" check only sees files below it.

It also recomputes `tree_sha256`: the sha256 of the UTF-8 text made of one line per manifest row, rows
ordered by path string, each line `<path>` NUL `<file sha256>` newline. By hand:
    text = "".join("%s\0%s\n" % (r["path"], r["sha256"]) for r in sorted(rows, key=lambda r: r["path"]))
    hashlib.sha256(text.encode("utf-8")).hexdigest()
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "RELEASE_MANIFEST.json"


def tree_sha256(rows) -> str:
    text = "".join("%s\0%s\n" % (r["path"], r["sha256"]) for r in sorted(rows, key=lambda r: r["path"]))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tracked() -> set | None:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return {n for n in r.stdout.split("\n") if n}


def main() -> int:
    if not MANIFEST.is_file():
        print("no manifest: RELEASE_MANIFEST.json is absent")
        return 1
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad = []
    for row in doc["files"]:
        p = ROOT / row["path"]
        if not p.is_file():
            bad.append("missing: %s" % row["path"])
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != row["sha256"]:
            bad.append("sha256 differs: %s" % row["path"])
    recomputed = tree_sha256(doc["files"])
    if doc.get("tree_sha256") != recomputed:
        bad.append("tree_sha256 differs: manifest says %s, the listed rows give %s" % (doc.get("tree_sha256"), recomputed))
    listed = {r["path"] for r in doc["files"]} | {"RELEASE_MANIFEST.json"}
    t = tracked()
    if t is not None:
        for extra in sorted(t - listed):
            bad.append("tracked but not in the manifest: %s" % extra)
    for line in bad:
        print(line)
    print("manifest: %d files, %d problems" % (len(doc["files"]), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
