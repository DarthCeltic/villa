"""Append-only corrections to committed receipts, honoured only when the bytes prove what the correction says."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "argus-erratum-v1"


def _errata(directory: Path) -> list[tuple[Path, dict]]:
    out = []
    try:
        candidates = sorted(directory.glob("ERRATA*.json"))
    except OSError:
        return out
    for p in candidates:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("schema") == SCHEMA:
            out.append((p, doc))
    return out


def line_ending_equivalent(path: Path, declared_sha256: str) -> dict | None:
    """The erratum that explains `path` hashing differently from `declared_sha256`, or None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    lf = data.replace(b"\r\n", b"\n")
    committed = hashlib.sha256(lf).hexdigest()
    forms = {hashlib.sha256(lf).hexdigest(), hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}
    if declared_sha256 not in forms:
        return None
    for erratum_path, doc in _errata(path.parent):
        for c in doc.get("corrections") or []:
            if (c.get("kind") == "line_ending_only_hash" and Path(str(c.get("path", ""))).name == path.name
                    and c.get("declared_sha256") == declared_sha256 and c.get("committed_sha256") == committed):
                return {"id": doc.get("id"), "path": erratum_path.name, "kind": c["kind"]}
    return None


def declared_output_not_committed(receipt_dir: Path, declared_path: str, manifest_sha256: str | None) -> dict | None:
    """The erratum that discloses `declared_path` was never committed, or None."""
    for erratum_path, doc in _errata(receipt_dir):
        for c in doc.get("corrections") or []:
            if c.get("kind") != "declared_output_not_committed":
                continue
            for o in c.get("outputs") or []:
                if o.get("path") == declared_path and o.get("manifest_sha256") == manifest_sha256:
                    return {"id": doc.get("id"), "path": erratum_path.name, "why": o.get("why") or c.get("why")}
    return None
