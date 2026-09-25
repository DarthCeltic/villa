"""Registered region masks: the server's own manifest says what a mask is and what its bytes must be, and nothing is served otherwise."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from argus.core import paths

SCHEMA = "argus-registered-masks-v1"
MASK_TYPES = {
    "cavity_mask": "Cavity inspection (registered mask)",
    "sdf_mask": "Signed-distance mask inspection (registered SDF mask)",
    "between_wrap_mask": "Between-wrap inspection",
}
IDENTITY_FILE = "STORE_IDENTITY.json"
_HEX64 = set("0123456789abcdef")


class MaskRefused(RuntimeError):
    def __init__(self, code: str, why: str):
        super().__init__("%s: %s" % (code, why))
        self.code = code
        self.why = why


def registry_path() -> Path:
    env = os.environ.get("ARGUS_MASK_REGISTRY")
    return Path(env) if env else paths.repo("config", "registered_masks.json")


def load_registry(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else registry_path()
    if not p.is_file():
        return []
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MaskRefused("REGISTRY_UNREADABLE", "the mask registry cannot be read: %s" % str(e)[:120])
    if body.get("schema") != SCHEMA or not isinstance(body.get("masks"), list):
        raise MaskRefused("REGISTRY_UNREADABLE", "the mask registry is not %s" % SCHEMA)
    return body["masks"]


def _is_hex64(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and set(v) <= _HEX64


def _sha_file(p: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def merkle_root(leaves: list[bytes]) -> str:
    """Binary Merkle root over already-hashed leaves; an odd node is paired with itself."""
    if not leaves:
        return ""
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            a = level[i]
            b = level[i + 1] if i + 1 < len(level) else a
            nxt.append(hashlib.sha256(b"\x01" + a + b).digest())
        level = nxt
    return level[0].hex()


_CACHE: dict = {}


def compute_digests(store: Path, level: str) -> dict:
    """content_sha256 over the chunk bytes of `level` (path order), and a Merkle root over every file of the store."""
    store = Path(store)
    files = sorted((p for p in store.rglob("*") if p.is_file()), key=lambda p: p.relative_to(store).as_posix())
    fingerprint = tuple((p.relative_to(store).as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    key = (str(store), level)
    hit = _CACHE.get(key)
    if hit and hit[0] == fingerprint:
        return hit[1]
    leaves: list[bytes] = []
    content = hashlib.sha256()
    chunks = 0
    lvl_prefix = level.strip("/") + "/"
    for p in files:
        rel = p.relative_to(store).as_posix()
        digest, size = _sha_file(p)
        leaves.append(hashlib.sha256(b"\x00" + ("%s\0%d\0%s" % (rel, size, digest)).encode("utf-8")).digest())
        if rel.startswith(lvl_prefix) and not p.name.startswith("."):
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    content.update(b)
            chunks += 1
    out = {"content_sha256": content.hexdigest(), "merkle_root": merkle_root(leaves), "chunk_count": chunks, "file_count": len(files)}
    if len(_CACHE) > 32:
        _CACHE.clear()
    _CACHE[key] = (fingerprint, out)
    return out


def entry_for(store: Path, *, artifact_type: str, physical_scroll: str, volume_id: str, level: str, source_digest: str, producer: str, mask_id: str) -> dict:
    """What a manifest entry for these exact bytes would be."""
    d = compute_digests(store, level)
    return {"mask_id": mask_id, "store": str(store), "physical_scroll": physical_scroll, "volume_id": volume_id, "grid": "same_as_volume", "level": level,
            "artifact_type": artifact_type, "producer": producer, "source_digest": source_digest, **d}


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def find_entry(store: Path, registry: list[dict] | None = None) -> dict | None:
    for e in (registry if registry is not None else load_registry()):
        if _same_path(str(e.get("store", "")), store):
            return e
    return None


def identity_of(store: Path) -> dict:
    try:
        return json.loads((Path(store) / IDENTITY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_mask_store(store: Path) -> bool:
    kind = str(identity_of(store).get("asset_type") or "").strip().lower()
    return kind in MASK_TYPES or kind == "paired_surface"


def verify(store: Path, *, registry: list[dict] | None = None) -> dict:
    """The registered entry for this store if, and only if, every declared fact holds and every byte matches."""
    entry = find_entry(store, registry)
    if entry is None:
        raise MaskRefused("UNREGISTERED", "this mask store is not in the server's registered mask manifest, so it is not served")
    for f in ("mask_id", "physical_scroll", "volume_id", "level", "artifact_type", "source_digest", "content_sha256", "merkle_root"):
        if not str(entry.get(f) or "").strip():
            raise MaskRefused("MANIFEST_INCOMPLETE", "the manifest entry lacks %s" % f)
    if entry.get("artifact_type") not in MASK_TYPES:
        raise MaskRefused("TYPE_NOT_A_REGISTERED_MASK_TYPE", "artifact_type %r is not one of %s" % (entry.get("artifact_type"), sorted(MASK_TYPES)))
    if entry.get("grid") != "same_as_volume":
        raise MaskRefused("GRID_NOT_DECLARED", "the manifest does not declare the mask on the volume's own grid")
    for f in ("source_digest", "content_sha256", "merkle_root"):
        if not _is_hex64(entry[f]):
            raise MaskRefused("MANIFEST_INCOMPLETE", "%s is not a sha256" % f)
    ident = identity_of(store)
    for f_id, f_reg in (("physical_scroll", "physical_scroll"), ("volume_id", "volume_id"), ("asset_type", "artifact_type")):
        if str(ident.get(f_id) or "").strip() != str(entry[f_reg]).strip():
            raise MaskRefused("IDENTITY_DISAGREES_WITH_MANIFEST", "the store's own %s (%r) is not the registered %s (%r)" % (f_id, ident.get(f_id), f_reg, entry[f_reg]))
    got = compute_digests(store, str(entry["level"]))
    if got["chunk_count"] != int(entry.get("chunk_count", -1)):
        raise MaskRefused("DIGEST_MISMATCH", "the mask has %d chunk files at level %s, the manifest registered %s" % (got["chunk_count"], entry["level"], entry.get("chunk_count")))
    if got["content_sha256"] != entry["content_sha256"]:
        raise MaskRefused("DIGEST_MISMATCH", "the mask's chunk bytes are not the registered ones (content digest differs)")
    if got["merkle_root"] != entry["merkle_root"]:
        raise MaskRefused("DIGEST_MISMATCH", "the mask store has changed since it was registered (Merkle root differs)")
    return entry


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 9:
        print(__doc__)
        raise SystemExit(2)
    s = sys.argv
    print(json.dumps(entry_for(Path(s[1]), artifact_type=s[2], physical_scroll=s[3], volume_id=s[4], level=s[5], source_digest=s[6], producer=s[7], mask_id=s[8]), indent=1))
