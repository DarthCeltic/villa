"""Persistence for the update conveyor: policy, observations, updates, pins and run pins."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from argus.core import actions as A
from argus.core import update_conveyor as UC
from argus.core.safe_names import is_plain_file_name, is_safe_name

SCOPES = ("apparatus", "scientific")


def root() -> Path:
    return A.STATE / "updates"


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def digest(doc) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def save_pr_observations(obs: dict) -> None:
    _atomic_write(root() / "pull_requests.json", {"observed_utc": _utc(), "prs": obs})


def load_pr_observations() -> dict:
    return _read(root() / "pull_requests.json", {}).get("prs", {})


def load_policy() -> UC.UpdatePolicy:
    d = _read(root() / "policy.json", {})
    p = UC.UpdatePolicy(mode=d.get("mode", "UNSET"))
    p.decided_utc, p.changed_in_settings = d.get("decided_utc"), bool(d.get("changed_in_settings"))
    return p


def save_policy(policy: UC.UpdatePolicy, *, by: str) -> dict:
    doc = {"mode": policy.mode, "decided_utc": policy.decided_utc,
           "changed_in_settings": policy.changed_in_settings, "by": by}
    _atomic_write(root() / "policy.json", doc)
    return doc


def load_observations() -> dict:
    return _read(root() / "observations.json", {})


def save_observation(obs: UC.Observation) -> None:
    all_obs = load_observations()
    all_obs[obs.source_id] = dataclasses.asdict(obs)
    _atomic_write(root() / "observations.json", all_obs)


_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}")


def _update_path(source_id: str, revision: str) -> Path:
    """Both names come from a request or a fetched record: each is a plain name, never something that can leave `records/`."""
    if not is_safe_name(source_id) or not isinstance(revision, str) or not _REVISION.fullmatch(revision) or ".." in revision or not is_plain_file_name(revision):
        raise ValueError("a source id and a revision are plain names, not paths")
    safe = revision.replace(":", "_")[:72]
    return root() / "records" / source_id / (safe + ".json")


def save_update(u: UC.Update, extra: dict | None = None) -> dict:
    rec = _read(_update_path(u.source_id, u.revision), {})
    rec.update({"update": dataclasses.asdict(u), "saved_utc": _utc()})
    for k, v in (extra or {}).items():
        rec[k] = v
    rec["record_sha256"] = digest({k: v for k, v in rec.items() if k != "record_sha256"})
    _atomic_write(_update_path(u.source_id, u.revision), rec)
    return rec


def load_update(source_id: str, revision: str):
    rec = _read(_update_path(source_id, revision), None)
    if not rec:
        return None, None
    d = rec["update"]
    d["gates_passed"], d["gates_failed"] = tuple(d.get("gates_passed", ())), tuple(d.get("gates_failed", ()))
    d["impact"] = tuple(d.get("impact", ()))
    return UC.Update(**d), rec


def list_updates() -> list:
    out = []
    base = root() / "records"
    if base.is_dir():
        for p in sorted(base.glob("*/*.json")):
            rec = _read(p, None)
            if rec:
                out.append(rec)
    return out


def find_revision(source_id: str, prefix: str):
    """The stored revision named by an exact value or by a UNIQUE prefix of at least 8 hex characters."""
    prefix = (prefix or "").strip().lower()
    if len(prefix) < 8 or any(c not in "0123456789abcdef" for c in prefix):
        return None
    hits = [rec["update"]["revision"] for rec in list_updates()
            if rec["update"]["source_id"] == source_id and rec["update"]["revision"].lower().startswith(prefix)]
    return hits[0] if len(set(hits)) == 1 else None


def load_pins() -> dict:
    return _read(root() / "pins.json", {})


def active_pin(source_id: str, scope: str) -> dict | None:
    return ((load_pins().get(source_id) or {}).get(scope) or {}).get("active")


def set_active(source_id: str, scope: str, revision: str, *, by: str, receipt_sha256: str, note: str = "") -> dict:
    if scope not in SCOPES:
        raise UC.ConveyorError("unknown pin scope %r" % (scope,))
    pins = load_pins()
    slot = pins.setdefault(source_id, {}).setdefault(scope, {"active": None, "previous": []})
    if slot["active"]:
        slot["previous"].append(slot["active"])
    slot["active"] = {"revision": revision, "since_utc": _utc(), "by": by,
                      "receipt_sha256": receipt_sha256, "note": note}
    _atomic_write(root() / "pins.json", pins)
    return slot["active"]


def restore_previous(source_id: str, scope: str, *, by: str) -> dict:
    pins = load_pins()
    slot = ((pins.get(source_id) or {}).get(scope)) or {}
    if not slot.get("previous"):
        raise UC.ConveyorError("no previous pin to restore for %s/%s" % (source_id, scope))
    replaced = slot["active"]
    slot["active"] = dict(slot["previous"].pop(), restored_utc=_utc(), restored_by=by)
    slot.setdefault("rolled_back_from", []).append(replaced)
    _atomic_write(root() / "pins.json", pins)
    return slot["active"]


def clear_active(source_id: str, scope: str, *, by: str) -> dict:
    """Revert to the shipped baseline (the revision in the source registry): the activated pin is kept in `rolled_back_from`, never deleted."""
    pins = load_pins()
    slot = ((pins.get(source_id) or {}).get(scope)) or {}
    if not slot.get("active"):
        raise UC.ConveyorError("no active pin for %s/%s" % (source_id, scope))
    slot.setdefault("rolled_back_from", []).append(dict(slot["active"], rolled_back_utc=_utc(), rolled_back_by=by))
    slot["active"] = None
    _atomic_write(root() / "pins.json", pins)
    return {"reverted_to": "shipped baseline"}


def admitted_baseline(source_id: str, config_revision: str | None, scope: str = "scientific") -> str | None:
    """The revision new runs use for a source: an activated pin if one exists, else the revision written in the source registry (the runtime pin the project already ships with)."""
    a = active_pin(source_id, scope)
    return a["revision"] if a else config_revision


def pin_set(sources: list) -> dict:
    """The revision each source contributes to a run started now, per scope."""
    out = {}
    for rec in sources:
        sid = rec["source_id"]
        out[sid] = {sc: admitted_baseline(sid, rec.get("admitted_revision"), sc) for sc in SCOPES}
    return out


def record_run_pin(run_id: str, sources: list) -> dict:
    """Freeze the pins a run started with."""
    path = root() / "run_pins" / (run_id + ".json")
    existing = _read(path, None)
    if existing:
        return existing
    doc = {"run_id": run_id, "frozen_utc": _utc(), "revision_of": pin_set(sources)}
    doc["pin_set_sha256"] = digest(doc["revision_of"])
    _atomic_write(path, doc)
    return doc


def run_pin(run_id: str):
    return _read(root() / "run_pins" / (run_id + ".json"), None)
