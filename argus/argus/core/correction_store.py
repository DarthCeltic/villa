"""Durable, append-only events for `argus.core.surface_corrections`, and their replay onto whatever the current proposal geometry actually is."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

from argus.core import surface_corrections as SC

CORRECTIONS_FILENAME = "CORRECTIONS.jsonl"

_AI_CLASS = "AI_AGENT"


class CorrectionStoreError(ValueError):
    pass


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def new_event_id(seed: str) -> str:
    return "CX-" + _sha(seed + str(time.time()))[:12]


def events_path(mesh_dir: Path) -> Path:
    return Path(mesh_dir) / CORRECTIONS_FILENAME


def _append(mesh_dir: Path, event: dict) -> None:
    p = events_path(mesh_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, default=str) + "\n"
    fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def load_events(mesh_dir: Path) -> list:
    """Every event, in the order recorded."""
    p = events_path(mesh_dir)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _event_ids(events: list) -> set:
    return {e["event_id"] for e in events if "event_id" in e}


def _transform_from_dict(d: dict) -> SC.CoordinateTransform:
    return SC.CoordinateTransform(
        volume_id=d["volume_id"], array_path=d["array_path"], crop_row0=int(d["crop_row0"]),
        crop_col0=int(d["crop_col0"]), depth_offset=int(d["depth_offset"]),
        depth_planes=int(d["depth_planes"]), voxel_um=float(d["voxel_um"]),
    )


def record_correction(mesh_dir: Path, *, proposal_id: str, proposal_sha256: str, action: str,
                      payload: dict, transform: dict, view_hash: str = "",
                      reviewer_id: str = "operator", reviewer_class: str = "OPERATOR",
                      event_id: str | None = None) -> dict:
    """Validate (via the real `SC.Correction`/`to_constraint`, never a restatement of their rules) and durably append one correction."""
    events = load_events(mesh_dir)
    eid = event_id or new_event_id(proposal_id + action)
    if eid in _event_ids(events):
        raise CorrectionStoreError("event id %r already recorded -- duplicate events are "
                                   "refused, not silently merged" % eid)
    tf = _transform_from_dict(transform)
    corr = SC.Correction(
        correction_id=eid, proposal_id=proposal_id, proposal_sha256=proposal_sha256,
        action=action, payload=payload, reviewer_id=reviewer_id, reviewer_class=reviewer_class,
        transform=tf, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        view_hash=view_hash,
    )
    constraint = SC.to_constraint(corr)
    event = {
        "kind": "correction", "event_id": eid, "proposal_id": proposal_id,
        "proposal_sha256": proposal_sha256, "action": action, "payload": payload,
        "reviewer_id": reviewer_id, "reviewer_class": reviewer_class,
        "transform": dataclasses.asdict(tf), "utc": corr.utc, "view_hash": view_hash,
    }
    _append(mesh_dir, event)
    return constraint


def record_undo(mesh_dir: Path, correction_event_id: str, *,
                reviewer_id: str = "operator") -> dict:
    """A compensating event."""
    events = load_events(mesh_dir)
    corrections = {e["event_id"]: e for e in events if e.get("kind") == "correction"}
    if correction_event_id not in corrections:
        raise CorrectionStoreError("no correction %r to undo" % correction_event_id)
    already = {e["undoes"] for e in events if e.get("kind") == "undo"}
    if correction_event_id in already:
        raise CorrectionStoreError("correction %r is already undone" % correction_event_id)
    eid = new_event_id("undo:" + correction_event_id)
    event = {
        "kind": "undo", "event_id": eid, "undoes": correction_event_id,
        "reviewer_id": reviewer_id, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append(mesh_dir, event)
    return event


def record_decision(mesh_dir: Path, proposal_id: str, decision: str, *,
                    why: str | None = None, reviewer_id: str = "operator",
                    reviewer_class: str = "OPERATOR") -> dict:
    """ACCEPTED or REJECTED for a whole proposal -- see this module's header for why this is not a `Correction`."""
    if decision not in ("ACCEPTED", "REJECTED"):
        raise CorrectionStoreError("decision must be ACCEPTED or REJECTED, got %r" % decision)
    eid = new_event_id("decision:" + proposal_id)
    event = {
        "kind": "decision", "event_id": eid, "proposal_id": proposal_id, "decision": decision,
        "why": why, "reviewer_id": reviewer_id, "reviewer_class": reviewer_class,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append(mesh_dir, event)
    return event


def _distinct_human_reviewers(events: list, proposal_id: str) -> set:
    out = set()
    for e in events:
        if e.get("proposal_id") != proposal_id:
            continue
        if e.get("kind") not in ("correction", "decision"):
            continue
        if e.get("reviewer_class") == _AI_CLASS:
            continue
        rid = e.get("reviewer_id")
        if rid:
            out.add(rid)
    return out


def current_promotion_state(events: list, proposal_id: str) -> str:
    state = "PROPOSED"
    for e in events:
        if e.get("kind") == "promotion" and e.get("proposal_id") == proposal_id:
            state = e["to_state"]
    return state


def record_promotion(mesh_dir: Path, proposal_id: str, to_state: str, *,
                     reviewer_id: str = "operator") -> dict:
    """Advance one step, gated entirely by `SC.assert_promotable`."""
    events = load_events(mesh_dir)
    from_state = current_promotion_state(events, proposal_id)
    reviewers = _distinct_human_reviewers(events, proposal_id)
    human_validated = len(reviewers) > 0
    new_state = SC.assert_promotable(
        from_state, to_state, human_validated=human_validated,
        independent_validators=len(reviewers),
    )
    eid = new_event_id("promotion:" + proposal_id + to_state)
    event = {
        "kind": "promotion", "event_id": eid, "proposal_id": proposal_id,
        "from_state": from_state, "to_state": new_state,
        "independent_validators": len(reviewers), "human_validated": human_validated,
        "reviewer_id": reviewer_id, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append(mesh_dir, event)
    return event


def merge_corrections(mesh_dir: Path, *, current_proposal_sha256: dict | None = None) -> dict:
    """Replay every event onto the current state."""
    events = load_events(mesh_dir)
    undone = {e["undoes"] for e in events if e.get("kind") == "undo"}
    by_proposal: dict = {}
    for e in events:
        if e.get("kind") != "correction":
            continue
        pid = e["proposal_id"]
        row = by_proposal.setdefault(pid, {
            "proposal_id": pid, "constraints": [], "decision": None, "state": "PROPOSED",
        })
        tf = _transform_from_dict(e["transform"])
        corr = SC.Correction(
            correction_id=e["event_id"], proposal_id=pid, proposal_sha256=e["proposal_sha256"],
            action=e["action"], payload=e["payload"], reviewer_id=e["reviewer_id"],
            reviewer_class=e["reviewer_class"], transform=tf, utc=e["utc"],
            view_hash=e.get("view_hash", ""),
        )
        c = SC.to_constraint(corr)
        c["event_id"] = e["event_id"]
        c["undone"] = e["event_id"] in undone
        expected = (current_proposal_sha256 or {}).get(pid)
        c["stale"] = bool(expected and expected != e["proposal_sha256"])
        row["constraints"].append(c)

    for e in events:
        if e.get("kind") == "decision":
            pid = e["proposal_id"]
            row = by_proposal.setdefault(pid, {
                "proposal_id": pid, "constraints": [], "decision": None, "state": "PROPOSED",
            })
            row["decision"] = {"value": e["decision"], "why": e.get("why"), "utc": e["utc"]}

    for pid, row in by_proposal.items():
        row["state"] = current_promotion_state(events, pid)
        row["active_constraints"] = [c for c in row["constraints"] if not c["undone"]]

    return {"contract": "argus-correction-store-v1", "proposals": by_proposal}
