"""A block is reusable only if it is provably the same computation, committed as a transaction."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import uuid
from dataclasses import dataclass, field

import numpy as np

ACCEPT = "ACCEPT"
REFUSE = "REFUSE"

MARKER_SUFFIX = ".commit.json"


class BlockRefused(RuntimeError):
    """Raised rather than reusing a block that is not provably the same computation."""


class BlockLocked(RuntimeError):
    """Raised when another writer holds this block."""


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_path(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj) -> str:
    """Hash of the MEANING, not the formatting."""
    return sha_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                default=str).encode("utf-8"))


@dataclass(frozen=True)
class BlockKey:
    """Everything that must be identical for two blocks to be the same computation."""

    acquisition_manifest_sha256: str
    checkpoint_sha256: str
    dependency_sha256: dict
    label_identity_sha256: str
    orientation: str
    pitch_um: float
    plan: dict
    compression_declaration: str

    def plan_sha256(self) -> str:
        return canonical(self.plan)

    def sha256(self) -> str:
        return canonical({
          "acq": self.acquisition_manifest_sha256,
          "ckpt": self.checkpoint_sha256,
          "deps": dict(sorted(self.dependency_sha256.items())),
          "labels": self.label_identity_sha256,
          "orientation": self.orientation,
          "pitch_um": self.pitch_um,
          "plan": self.plan,
          "compression": self.compression_declaration,
        })

    def as_record(self) -> dict:
        return {"block_key_sha256": self.sha256(), "plan_sha256": self.plan_sha256(),
                "orientation": self.orientation, "pitch_um": self.pitch_um,
                "compression_declaration": self.compression_declaration,
                "acquisition_manifest_sha256": self.acquisition_manifest_sha256,
                "checkpoint_sha256": self.checkpoint_sha256,
                "dependency_sha256": dict(sorted(self.dependency_sha256.items())),
                "label_identity_sha256": self.label_identity_sha256,
                "plan": self.plan}


@dataclass
class Verdict:
    state: str
    reasons: list = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.state == ACCEPT

    def as_record(self) -> dict:
        return {"state": self.state, "reasons": self.reasons}


def marker_path(block_path) -> pathlib.Path:
    p = pathlib.Path(block_path)
    return p.with_name(p.name + MARKER_SUFFIX)


def validate_arrays(p_arr, c_arr, coords, *, expected_coords=None,
                    expected_shape=None, expected_dtype=None) -> list:
    """Everything that must be true of the CONTENT, not just the container."""
    bad = []
    p_arr = np.asarray(p_arr)
    c_arr = np.asarray(c_arr)

    if p_arr.ndim != 2 or c_arr.ndim != 2:
        bad.append("p and c must be 2-D (got %s and %s)" % (p_arr.ndim, c_arr.ndim))
        return bad
    if p_arr.shape != c_arr.shape:
        bad.append("p and c shapes differ: %s vs %s" % (list(p_arr.shape), list(c_arr.shape)))
    if expected_shape is not None and tuple(p_arr.shape) != tuple(expected_shape):
        bad.append("shape differs: %s vs %s" % (list(p_arr.shape), list(expected_shape)))
    if expected_dtype is not None and str(p_arr.dtype) != str(expected_dtype):
        bad.append("dtype differs: %s vs %s" % (p_arr.dtype, expected_dtype))

    if not np.issubdtype(p_arr.dtype, np.floating):
        bad.append("p must be floating point, got %s" % p_arr.dtype)
    elif not np.isfinite(p_arr).all():
        bad.append("p contains non-finite values")
    else:
        lo, hi = float(p_arr.min()), float(p_arr.max())
        if lo < 0.0 or hi > 1.0:
            bad.append("p is not a probability: range [%g, %g]" % (lo, hi))

    if c_arr.dtype != np.bool_:
        vals = np.unique(c_arr)
        if not np.isin(vals, (0, 1)).all():
            bad.append("c must be boolean or exactly 0/1, found %s" % vals[:5].tolist())
    if c_arr.size and not c_arr.any():
        bad.append("c covers no pixels; an empty block is not a completed computation")

    co = np.asarray(coords).tolist()
    if len(co) != 4:
        bad.append("coords must be [y0, y1, x0, x1], got %s" % co)
    else:
        y0, y1, x0, x1 = co
        if not (y1 > y0 and x1 > x0):
            bad.append("coords are not a positive extent: %s" % co)
        if expected_coords is not None and list(co) != list(expected_coords):
            bad.append("coordinates differ: stored %s, expected %s"
                       % (co, list(expected_coords)))
    return bad


def verify_block(path, *, key: BlockKey, expected_coords=None, expected_shape=None,
                 expected_dtype=None, recorded_sha256=None) -> Verdict:
    """Every check between a stored file and reusing it."""
    p = pathlib.Path(path)
    if not p.is_file():
        return Verdict(REFUSE, ["block absent: %s" % p])

    mk = marker_path(p)
    if not mk.is_file():
        return Verdict(REFUSE, [
            "no commit marker beside this block, so the write never completed and the block "
            "cannot be shown to be the same computation. Refusing costs one block; accepting "
            "costs a result nobody can defend."])
    try:
        marker = json.loads(mk.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Verdict(REFUSE, ["commit marker is unreadable"])

    actual = sha_path(p)
    committed = marker.get("block_sha256")
    if not committed:
        return Verdict(REFUSE, ["commit marker does not bind a block hash"])
    if actual != committed:
        return Verdict(REFUSE, [
            "block content does not match its commit marker: marker %s, file %s. A copied "
            "marker cannot validate foreign bytes." % (committed[:16], actual[:16])])
    if recorded_sha256 is not None and actual != recorded_sha256:
        return Verdict(REFUSE, ["content hash mismatch against the caller's record: %s vs %s"
                                % (recorded_sha256[:16], actual[:16])])

    reasons = []
    if marker.get("block_key_sha256") != key.sha256():
        reasons.append("block key differs -- the computation is not the same one. stored %s, "
                       "expected %s" % (str(marker.get("block_key_sha256"))[:16],
                                        key.sha256()[:16]))
    if marker.get("orientation") not in (None, key.orientation):
        reasons.append("orientation differs: stored %s, expected %s"
                       % (marker.get("orientation"), key.orientation))

    try:
        with np.load(p) as z:
            if not {"p", "c", "coords"} <= set(z.files):
                return Verdict(REFUSE, ["block is missing required arrays; has %s"
                                        % sorted(z.files)])
            reasons += validate_arrays(z["p"], z["c"], z["coords"],
                                       expected_coords=expected_coords,
                                       expected_shape=expected_shape,
                                       expected_dtype=expected_dtype)
    except Exception as e:
        return Verdict(REFUSE, ["block unreadable (%s: %s)" % (type(e).__name__, e)])

    return Verdict(REFUSE, reasons) if reasons else Verdict(ACCEPT, [])


def _fsync_path(p: pathlib.Path) -> None:
    """Flush a file to disk."""
    with open(p, "rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())


def write_block_atomic(path, *, p_arr, c_arr, coords, key: BlockKey,
                       writer_token: str | None = None) -> pathlib.Path:
    """Commit a block as a transaction: validate, promote, then mark."""
    dst = pathlib.Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    token = writer_token or uuid.uuid4().hex[:12]

    bad = validate_arrays(p_arr, c_arr, coords)
    if bad:
        raise BlockRefused("refusing to write an invalid block: %s" % bad)

    lock = dst.with_name(dst.name + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, token.encode())
        os.close(fd)
    except FileExistsError:
        raise BlockLocked("another writer holds %s" % dst.name) from None

    tmp = dst.with_name("%s.%s.partial.npz" % (dst.stem, token))
    try:
        np.savez_compressed(tmp, p=np.asarray(p_arr), c=np.asarray(c_arr),
                            coords=np.asarray(coords))
        _fsync_path(tmp)

        os.replace(tmp, dst)
        _fsync_path(dst)

        rec = key.as_record()
        rec["block_sha256"] = sha_path(dst)
        rec["writer_token"] = token
        rec["committed"] = True
        mtmp = marker_path(dst).with_name(marker_path(dst).name + "." + token + ".tmp")
        mtmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        _fsync_path(mtmp)
        os.replace(mtmp, marker_path(dst))
        return dst
    finally:
        for leftover in (tmp,):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            lock.unlink()
        except OSError:
            pass
