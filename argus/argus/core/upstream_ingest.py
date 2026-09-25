"""Refresh what upstream says, automatically."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import dataclasses
import json
import pathlib
import subprocess
import time
import urllib.request

CONTRACT = "argus-upstream-ingest-v1"

GIT, HF, CATALOGUE = "git_repository", "model_repository", "data_catalogue"
KINDS = (GIT, HF, CATALOGUE)

STATE = pathlib.Path(_argus_public_path('home', 'state/upstream_pins.json'))


class IngestRefusal(RuntimeError):
    """Raised rather than promoting silently."""


@dataclasses.dataclass(frozen=True)
class Tracked:
    key: str
    kind: str
    locator: str
    stable: str
    why_stable: str


TRACKED = (
  Tracked("villa", GIT, _argus_public_path('home', 'upstream/villa'),
          "739eefd71a677bebb983186ae3d0a6bace669934",
          "the pinned Villa commit."),
  Tracked("surface_recto_verso", HF, "scrollprize/surface_recto_verso",
          "c93bb4ab0ba9d37cf7707637109cb7b729a10b24",
          "the pinned multiclass surface model."),
  Tracked("surface_recto", HF, "scrollprize/surface_recto",
          "86f026f8be537db336e2854650db0b40c004ad49", "a pinned surface model."),
  Tracked("fiber_hz_vt", HF, "scrollprize/fiber_hz_vt",
          "0905e68f14b33d1b98fd32d726f2c97607dea80c", "a pinned fiber model."),
  Tracked("fiber_ink_4class_selfdistill", HF, "scrollprize/fiber_ink_4class_selfdistill",
          "ec9bbc4dbc65a052fc4d78429f7ce5eff080aafe",
          "a pinned ink-headed model with a declared licence."),
  Tracked("open_data_catalogue", CATALOGUE,
          "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/metadata.json",
          "UNPINNED",
          "the catalogue is a live index. Its CONTENT is pinned per-resolution, not globally."),
)


def _candidate_git(locator: str) -> dict:
    def g(*a):
        try:
            r = subprocess.run(["git", "-C", locator, *a], capture_output=True, text=True,
                               timeout=300)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    g("fetch", "--no-tags", "origin", "main")
    return {"candidate": g("rev-parse", "origin/main"),
            "behind": g("rev-list", "--count", "HEAD..origin/main"),
            "local_head": g("rev-parse", "HEAD"),
            "local_modifications": len([x for x in (g("status", "--porcelain") or "").splitlines()
                                        if x.strip()]),
            "refresh_method": "fetch only. No checkout, no merge, no reset: the working tree and "
                              "its local modifications are never touched by a refresh."}


def _candidate_hf(repo_id: str) -> dict:
    try:
        d = json.loads(urllib.request.urlopen(
          "https://huggingface.co/api/models/%s" % repo_id, timeout=60).read())
        return {"candidate": d.get("sha"), "last_modified": d.get("lastModified"),
                "licence": next((t.split(":", 1)[1] for t in d.get("tags", [])
                                 if t.startswith("license:")), None)}
    except Exception as exc:
        return {"candidate": None, "why": "%s: %s" % (type(exc).__name__, exc)}


def _candidate_catalogue(url: str) -> dict:
    import hashlib
    try:
        raw = urllib.request.urlopen(url, timeout=120).read()
        return {"candidate": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "a catalogue has no commit. Its candidate IS the hash of what it "
                        "served, which is the only thing that can be compared later."}
    except Exception as exc:
        return {"candidate": None, "why": "%s: %s" % (type(exc).__name__, exc)}


def refresh(*, network: bool = True) -> dict:
    """Ask every tracked source what it currently offers."""
    rows = []
    for t in TRACKED:
        row = {"key": t.key, "kind": t.kind, "locator": t.locator,
               "stable": t.stable, "why_stable": t.why_stable}
        if not network:
            row["candidate"] = None
            row["skipped"] = "network disabled for this refresh"
        elif t.kind == GIT:
            row.update(_candidate_git(t.locator))
        elif t.kind == HF:
            row.update(_candidate_hf(t.locator))
        else:
            row.update(_candidate_catalogue(t.locator))
        cand = row.get("candidate")
        row["drifted"] = bool(cand and t.stable != "UNPINNED" and cand != t.stable)
        row["state"] = ("DRIFTED" if row["drifted"] else
                        "IN_SYNC" if cand else "UNKNOWN")
        rows.append(row)

    doc = {"contract": CONTRACT,
           "refreshed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sources": rows,
           "drifted": [r["key"] for r in rows if r["drifted"]],
           "nothing_was_promoted": "discovery, pinning, download and testing are automatic. "
                                   "Promotion is not. An experiment's interpretation must not "
                                   "change because a background task fetched something at three "
                                   "in the morning.",
           "stable_vs_candidate": "STABLE is what receipts were produced against; CANDIDATE is "
                                  "what upstream currently offers. One pin cannot answer both "
                                  "questions, and a system with one pin answers the second "
                                  "while people believe it answered the first."}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def promote(key: str, *, receipt_id: str, gate_passed: bool) -> dict:
    """Move a STABLE pin."""
    if not gate_passed:
        raise IngestRefusal(
          "refusing to promote %r: no gate passed. Automatic ingestion may discover, pin, "
          "download and test; only a receipt-derived gate may change operational state." % key)
    if not receipt_id:
        raise IngestRefusal(
          "refusing to promote %r without a receipt id. A promotion nobody can trace is a "
          "version change that appears to have happened by itself." % key)
    return {"promoted": key, "receipt": receipt_id,
            "note": "the caller must also update TRACKED, deliberately, in a reviewed change. "
                    "This function records the decision; it does not edit the pin behind your "
                    "back."}


def as_record() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"contract": CONTRACT, "refreshed_utc": None,
            "sources": [{"key": t.key, "kind": t.kind, "stable": t.stable,
                         "state": "NOT_CHECKED"} for t in TRACKED]}
