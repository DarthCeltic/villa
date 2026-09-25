"""ONE canonical ordered-key-list hash, versioned, that still verifies every historical convention."""
from __future__ import annotations

import hashlib
import json

CANONICAL = "argus-key-list-hash-v2"

LEGACY_TERMINATED = "argus-key-list-hash-v0-newline-terminated"
LEGACY_JOINED = "argus-key-list-hash-v1-newline-joined"

VERSIONS = (CANONICAL, LEGACY_JOINED, LEGACY_TERMINATED)

WRITTEN_BY = {
  CANONICAL: "argus.core.key_list_hash (this module)",
  LEGACY_JOINED: "the legacy acquire script -> STORE_IDENTITY.json "
                 "required_key_list_sha256 (every sealed store it wrote)",
  LEGACY_TERMINATED: "argus/core/acquisition_identity.key_list_hash -> binding() "
                     "ordered_key_list_sha256 (and acquisition_id payloads)",
}


class KeyListHashRefusal(ValueError):
    """Raised instead of guessing which convention a digest was made with."""


def _keys(keys) -> list:
    out = list(keys)
    bad = [k for k in out if not isinstance(k, str)]
    if bad:
        raise KeyListHashRefusal("keys must be strings; got %r"
                                 % [type(b).__name__ for b in bad[:3]])
    return out


def digest(keys, *, version: str = CANONICAL) -> str:
    """Hex digest of the ordered key list under a NAMED convention."""
    ks = _keys(keys)
    if version == CANONICAL:
        blob = b"argus-key-list-hash-v2\0" + json.dumps(
          ks, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()
    if version == LEGACY_JOINED:
        return hashlib.sha256("\n".join(ks).encode("utf-8")).hexdigest()
    if version == LEGACY_TERMINATED:
        h = hashlib.sha256()
        for k in ks:
            h.update(k.encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()
    raise KeyListHashRefusal("unknown key-list hash version %r; known: %s" % (version, VERSIONS))


def hash_record(keys) -> dict:
    """The canonical record."""
    ks = _keys(keys)
    return {"key_list_hash_version": CANONICAL, "sha256": digest(ks), "count": len(ks)}


def tagged(keys) -> str:
    return "%s:%s" % (CANONICAL, digest(keys))


def parse(recorded) -> tuple:
    """(version or None, hex)."""
    if isinstance(recorded, dict):
        v = recorded.get("key_list_hash_version") or recorded.get("version")
        h = recorded.get("sha256")
        if v not in VERSIONS:
            raise KeyListHashRefusal("record names unknown version %r" % v)
        return v, str(h or "")
    s = str(recorded or "").strip()
    if ":" in s:
        v, h = s.rsplit(":", 1)
        if v not in VERSIONS:
            raise KeyListHashRefusal("tag names unknown version %r" % v)
        return v, h
    return None, s


def verify(keys, recorded, *, allow_legacy: bool = True) -> dict:
    """Does `recorded` describe these keys?"""
    ks = _keys(keys)
    version, hexd = parse(recorded)
    hexd = hexd.lower()
    if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
        return {"verified": False, "matched_version": None, "recorded": recorded,
                "why": "recorded value is not a sha256 hex digest", "count": len(ks),
                "canonical": hash_record(ks)}
    if version is not None:
        ok = digest(ks, version=version) == hexd
        return {"verified": ok, "matched_version": version if ok else None,
                "declared_version": version, "recorded": recorded, "count": len(ks),
                "why": ("matches under its declared version" if ok else
                        "does NOT match under its declared version %s" % version),
                "canonical": hash_record(ks)}
    if not allow_legacy:
        return {"verified": False, "matched_version": None, "recorded": recorded,
                "count": len(ks), "canonical": hash_record(ks),
                "why": "bare digest with no version, and legacy verification was not allowed"}
    matched = [v for v in VERSIONS if digest(ks, version=v) == hexd]
    if len(matched) > 1:
        return {"verified": False, "matched_version": None, "ambiguous_versions": matched,
                "recorded": recorded, "count": len(ks), "canonical": hash_record(ks),
                "why": "a bare digest matched more than one convention; refusing to choose"}
    return {"verified": bool(matched), "matched_version": matched[0] if matched else None,
            "declared_version": None, "recorded": recorded, "count": len(ks),
            "written_by": WRITTEN_BY.get(matched[0]) if matched else None,
            "why": ("bare historical digest; matched %s" % matched[0] if matched else
                    "bare digest matches NO known convention (%s)" % ", ".join(VERSIONS)),
            "canonical": hash_record(ks)}


def assert_verified(keys, recorded, **kw) -> dict:
    r = verify(keys, recorded, **kw)
    if not r["verified"]:
        raise KeyListHashRefusal("key-list hash does not verify: %s" % r["why"])
    return r
