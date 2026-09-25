"""The canonical evaluation envelope: arrays are saved BEFORE the summary is written."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

import numpy as np

from argus.core.scoring import Population, score

ENVELOPE_ID = "argus-evaluation-envelope-v1"


class EnvelopeRefusal(ValueError):
    """Raised rather than emitting a summary that could never be replayed."""


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha_array(a: np.ndarray) -> str:
    return _sha_bytes(np.ascontiguousarray(a).tobytes())


def write_envelope(out_dir, *, scores, labels, valid, sample_ids,
                   aggregation: str,
                   checkpoint_hash: str,
                   membership_hash: str,
                   coordinate_mapping_hash: str,
                   orientation: str,
                   target: str,
                   notes: str = "") -> dict:
    """Save arrays, then score, then write the summary that references them."""
    for name, v in (("checkpoint_hash", checkpoint_hash),
                    ("membership_hash", membership_hash),
                    ("coordinate_mapping_hash", coordinate_mapping_hash),
                    ("orientation", orientation), ("target", target)):
        if not v:
            raise EnvelopeRefusal(
                "%s is required. An evaluation that cannot say which population, which "
                "weights, which coordinates or which orientation produced it is not "
                "replayable." % name)

    s = np.asarray(scores, dtype=np.float32)
    y = np.asarray(labels).astype(bool)
    v = np.asarray(valid).astype(bool)
    ids = np.asarray(sample_ids)
    if not (s.shape == y.shape == v.shape == ids.shape):
        raise EnvelopeRefusal("scores %s, labels %s, valid %s and ids %s must agree"
                              % (s.shape, y.shape, v.shape, ids.shape))

    d = pathlib.Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    arrays = d / "arrays.npz"
    tmp = arrays.with_suffix(".partial")
    with tmp.open("wb") as fh:
        np.savez_compressed(fh, scores=s, labels=y, valid=v, sample_ids=ids)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, arrays)
    payload = arrays.read_bytes()

    result = score([Population(scores=s, labels=y, valid=v, group=target)],
                   aggregation=aggregation)

    doc = {
      "envelope_id": ENVELOPE_ID,
      "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "target": target, "orientation": orientation,
      "arrays": {"path": str(arrays), "format": "npz (compressed)",
                 "sha256": _sha_bytes(payload), "bytes": len(payload),
                 "members": ["scores(float32)", "labels(bool)", "valid(bool)",
                             "sample_ids"]},
      "content_hashes": {"scores": _sha_array(s), "labels": _sha_array(y),
                         "valid": _sha_array(v), "sample_ids": _sha_array(ids)},
      "identity": {"checkpoint_hash": checkpoint_hash,
                   "membership_hash": membership_hash,
                   "coordinate_mapping_hash": coordinate_mapping_hash},
      "scorer": {"service_id": result["service_id"],
                 "metric_rule_id": result["metric_rule_id"],
                 "aggregation": result["aggregation"],
                 "tie_rule": result["tie_rule"]},
      "result": {k: result[k] for k in
                 ("auc", "ap", "prevalence", "lift", "n", "n_positive", "n_negative",
                  "coverage", "ci95") if k in result},
      "replayable": True,
      "replay_command": ("np.load(%r) -> scores/labels/valid; then "
                         "argus.core.scoring.score(..., aggregation=%r)"
                         % (str(arrays), aggregation)),
      "orientation_rule": ("both depth orientations are preserved separately. Neither may be "
                           "selected from target output."),
      "notes": notes,
    }
    p = d / "EVALUATION_ENVELOPE.json"
    t2 = p.with_suffix(".tmp")
    with t2.open("w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(doc, indent=1) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(t2, p)
    return doc



DURABLE_SET_ID = "argus-durable-artifact-set-v1"

REQUIRED_KINDS = ("arrays", "labels", "masks", "coords", "tile_ids", "predictions")


def _fsync_file(p: pathlib.Path) -> None:
    """fsync through a WRITABLE handle; os.fsync on a read-only Windows handle raises EBADF."""
    with open(p, "rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_dir(d: pathlib.Path) -> bool:
    """Directory entry durability where the platform supports it (not on Windows)."""
    if os.name == "nt":
        return False
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
        return True
    finally:
        os.close(fd)


class DurableArtifactSet:
    """Arrays first, fsynced and hashed; a summary only after all of them."""

    def __init__(self, out_dir, *, required_kinds=REQUIRED_KINDS):
        self.dir = pathlib.Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.required = tuple(required_kinds)
        self.records = {}
        self.summary_written = None

    def persist(self, kind: str, name: str, array) -> dict:
        if kind not in REQUIRED_KINDS:
            raise EnvelopeRefusal("unknown artifact kind %r; kinds are %s" % (kind, REQUIRED_KINDS))
        if self.summary_written is not None:
            raise EnvelopeRefusal("a summary was already written at %s; an artifact persisted "
                                  "after it is one the summary could not have described"
                                  % self.summary_written)
        if not name or "/" in name or "\\" in name:
            raise EnvelopeRefusal("artifact name %r must be a bare file stem" % name)
        if name in self.records:
            raise EnvelopeRefusal("artifact %r already persisted; append-only" % name)
        a = np.asarray(array)
        final = self.dir / ("%s.npy" % name)
        tmp = self.dir / ("%s.partial" % name)
        with tmp.open("wb") as fh:
            np.save(fh, a, allow_pickle=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
        _fsync_file(final)
        _fsync_dir(self.dir)
        rec = {"kind": kind, "name": name, "path": str(final),
               "sha256": _sha_bytes(final.read_bytes()), "content_sha256": _sha_array(a),
               "shape": list(a.shape), "dtype": str(a.dtype), "fsynced": True}
        self.records[name] = rec
        return rec

    def missing_kinds(self) -> list:
        have = {r["kind"] for r in self.records.values()}
        return [k for k in self.required if k not in have]

    def write_summary(self, doc: dict, filename: str = "SUMMARY.json", *, references=None) -> dict:
        refs = list(references) if references is not None else list(self.records)
        problems = []
        missing = self.missing_kinds()
        if missing:
            problems.append("required artifact kinds not yet durably persisted: %s" % missing)
        unknown = [r for r in refs if r not in self.records]
        if unknown:
            problems.append("summary references artifacts never persisted by this set: %s"
                            % unknown)
        for n, rec in self.records.items():
            p = pathlib.Path(rec["path"])
            if not p.is_file():
                problems.append("%s vanished after persist" % n)
            elif _sha_bytes(p.read_bytes()) != rec["sha256"]:
                problems.append("%s changed on disk after it was synced" % n)
        if problems:
            raise EnvelopeRefusal("REFUSING TO WRITE SUMMARY: " + "; ".join(problems))
        body = dict(doc, durable_set=DURABLE_SET_ID,
                    references={r: {k: self.records[r][k] for k in
                                    ("kind", "path", "sha256", "content_sha256", "shape", "dtype",
                                     "fsynced")} for r in refs},
                    ordering="every referenced artifact was written, fsynced and hashed before "
                             "this summary existed")
        p = self.dir / filename
        t = p.with_name(p.name + ".tmp")
        with t.open("w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(body, indent=1, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(t, p)
        self.summary_written = str(p)
        return dict(body, summary_path=str(p))


def replay(envelope_path) -> dict:
    """Recompute the result from the saved arrays."""
    doc = json.loads(pathlib.Path(envelope_path).read_text(encoding="utf-8"))
    ap = pathlib.Path(doc["arrays"]["path"])
    if _sha_bytes(ap.read_bytes()) != doc["arrays"]["sha256"]:
        raise EnvelopeRefusal("array file hash differs from the envelope's record")
    z = np.load(ap, allow_pickle=False)
    r = score([Population(scores=z["scores"], labels=z["labels"], valid=z["valid"],
                          group=doc["target"])],
              aggregation=doc["scorer"]["aggregation"])
    return {"recorded": doc["result"]["auc"], "replayed": r["auc"],
            "identical": r["auc"] == doc["result"]["auc"]}
