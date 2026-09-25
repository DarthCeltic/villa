"""Inspect before you download, and never fetch what is already held."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOLDINGS = Path(_argus_public_path('home', 'state/holdings.json'))
MAX_INSPECT_BYTES = 1 << 20

KNOWN_LICENCES = ("MIT", "Apache-2.0", "BSD-3-Clause", "CC-BY-4.0", "CC-BY-NC-4.0",
                  "CC0-1.0", "GPL-3.0", "AGPL-3.0")


class IngestRefusal(Exception):
    pass


def _holdings() -> dict:
    try:
        return json.loads(HOLDINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"by_digest": {}, "by_source": {}}


def _save_holdings(d: dict) -> None:
    HOLDINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = HOLDINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(HOLDINGS)


def digest_file(path, cap_bytes: int | None = None) -> dict:
    """sha256 of the bytes."""
    p = Path(path)
    h = hashlib.sha256()
    n = 0
    with p.open("rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if cap_bytes and n >= cap_bytes:
                return {"sha256": None, "bytes_read": n, "complete": False,
                        "why": "larger than the cap; a partial digest is not an identity"}
    return {"sha256": h.hexdigest(), "bytes": n, "complete": True}


def inspect_remote(url: str, *, timeout: int = 30) -> dict:
    """A HEAD, and nothing more."""
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "argus-ingest-inspect")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": False, "http": exc.code,
                "why": "the server refused a HEAD"}
    except Exception as exc:
        return {"url": url, "reachable": False, "why": str(exc)[:160]}

    etag = (h.get("etag") or "").strip()
    weak = etag.startswith("W/")
    cd = h.get("content-digest") or h.get("digest")
    size = int(h["content-length"]) if h.get("content-length", "").isdigit() else None
    identity = None
    if cd:
        identity = {"kind": "content-digest", "value": cd}
    elif etag and not weak:
        identity = {"kind": "strong-etag", "value": etag.strip('"')}
    return {
        "url": url, "reachable": True, "http": status,
        "bytes": size,
        "content_type": h.get("content-type"),
        "last_modified": h.get("last-modified"),
        "identity": identity,
        "identity_verifiable_without_transfer": identity is not None,
        "why_not": (None if identity else
                    ("the server offers no Content-Digest and no strong ETag, so byte "
                     "identity cannot be established without transferring it. Recorded as "
                     "unverifiable rather than assumed new"
                     if not weak else
                     "the ETag is weak (W/), which promises semantic equivalence and not "
                     "byte equality, so it is not an identity")),
        "bytes_transferred": 0,
    }


def already_held(identity: dict | None, sha256: str | None = None) -> dict:
    """Do we hold this content already, under any name?"""
    hold = _holdings()
    if sha256 and sha256 in hold["by_digest"]:
        return {"held": True, "by": "sha256", "record": hold["by_digest"][sha256]}
    if identity:
        key = "%s:%s" % (identity["kind"], identity["value"])
        if key in hold["by_digest"]:
            return {"held": True, "by": identity["kind"], "record": hold["by_digest"][key]}
    return {"held": False,
            "note": ("not held under a content identity we can check. That is not the same "
                     "as new -- see identity_verifiable_without_transfer")}


def record_holding(*, digest_key: str, source: str, path: str, bytes_: int,
                   declared: dict | None = None) -> dict:
    hold = _holdings()
    rec = {"source": source, "path": path, "bytes": bytes_,
           "declared": declared or {},
           "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    hold["by_digest"][digest_key] = rec
    hold["by_source"].setdefault(source, []).append(digest_key)
    _save_holdings(hold)
    return rec


def validate_declared(meta: dict, *, name_hint: str | None = None) -> dict:
    """Report what a source DECLARES, and flag a name that argues with it."""
    findings = []
    pitch = meta.get("pitch_um") or meta.get("px") or meta.get("voxelsize")
    if pitch is None:
        findings.append({"field": "pitch", "state": "UNDECLARED",
                         "why": "no pitch in the source metadata"})
    else:
        findings.append({"field": "pitch", "state": "DECLARED", "value": pitch})
        if name_hint:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)\s*um", str(name_hint), re.I)
            if m:
                named = float(m.group(1))
                if abs(named - float(pitch)) / max(float(pitch), 1e-9) > 0.01:
                    findings.append({
                        "field": "pitch", "state": "NAME_DISAGREES_WITH_DECLARATION",
                        "declared": float(pitch), "name_says": named,
                        "why": ("the name and the declaration differ by more than 1%. This is "
                                "reported, not resolved: a name is not evidence, and the "
                                "PitchResolver hierarchy is what arbitrates")})
    axes = meta.get("axes")
    findings.append({"field": "axes",
                     "state": "DECLARED" if axes else "UNDECLARED", "value": axes})
    lic = (meta.get("license") or meta.get("licence") or "").strip()
    findings.append({"field": "licence",
                     "state": ("KNOWN" if lic in KNOWN_LICENCES else
                               ("STATED_UNRECOGNISED" if lic else "UNDECLARED")),
                     "value": lic or None,
                     "why": (None if lic in KNOWN_LICENCES else
                             "an unrecognised or absent licence blocks redistribution rather "
                             "than being assumed permissive")})
    return {"findings": findings,
            "usable_for_science": all(f["state"] != "UNDECLARED"
                                      for f in findings if f["field"] == "pitch"),
            "redistributable": any(f["field"] == "licence" and f["state"] == "KNOWN"
                                   for f in findings)}


def plan_import(source: str, *, meta: dict | None = None, name_hint: str | None = None,
                floors: dict | None = None) -> dict:
    """The whole decision, before a byte moves."""
    is_url = source.startswith(("http://", "https://"))
    probe = inspect_remote(source) if is_url else None
    local = None
    if not is_url:
        p = Path(source)
        if not p.exists():
            raise IngestRefusal("no such path: %s" % source)
        if p.is_file():
            local = digest_file(p, cap_bytes=None)
            local["bytes"] = local.get("bytes", p.stat().st_size)
        else:
            files = [x for x in p.rglob("*") if x.is_file()]
            local = {"sha256": None, "bytes": sum(x.stat().st_size for x in files),
                     "n_files": len(files), "complete": False,
                     "why": "a directory has no single digest; each file is identified"}
    size = (probe or {}).get("bytes") if is_url else (local or {}).get("bytes")
    held = already_held((probe or {}).get("identity"), (local or {}).get("sha256"))
    validation = validate_declared(meta or {}, name_hint=name_hint or source)

    from argus.core import storage_policy as SP
    current = SP.status()
    c_free = current["drives"]["c"]["free_gib"]
    t_free = current["drives"]["t"]["free_gib"]
    fl = {**SP.floors(), **(floors or {})}
    need_gib = (size or 0) / 2 ** 30
    fits = (c_free is not None and t_free is not None and
            (c_free - need_gib) >= fl["c"] and (t_free - need_gib) >= fl["t"])

    decision = "SKIP_ALREADY_HELD" if held["held"] else (
        "REFUSE_STORAGE_FLOOR" if not fits else "READY_TO_FETCH")
    return {
        "source": source, "kind": "url" if is_url else "path",
        "probe": probe, "local": local,
        "estimated_bytes": size,
        "estimated_gib": round(need_gib, 3),
        "already_held": held,
        "declared": validation,
        "storage": {"policy_id": SP.POLICY_ID,
                    "c_free_gib": None if c_free is None else round(c_free, 1),
                    "t_free_gib": None if t_free is None else round(t_free, 1),
                    "c_floor_gib": fl["c"], "t_floor_gib": fl["t"],
                    "c_would_leave_gib": None if c_free is None else round(c_free - need_gib, 1),
                    "t_would_leave_gib": None if t_free is None else round(t_free - need_gib, 1),
                    "fits": fits},
        "decision": decision,
        "bytes_transferred_by_this_plan": 0,
        "why_nothing_moved": ("planning and spending are different acts. The fetch is a "
                              "separate allowlisted, leased job"),
    }
