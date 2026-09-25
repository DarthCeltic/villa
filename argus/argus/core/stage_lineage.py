"""One immutable, ordered lineage of process-contract stage ATTEMPTS, per physical scroll."""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core import paths, process_contract, scroll_ids
from argus.core.ledger_v2 import _Lock, record_hash

SCHEMA = "argus-stage-lineage-v1"
ZERO = "0" * 64

OUTCOMES = (
    "COMPLETED",
    "REFUSED",
    "BLOCKED",
    "HUMAN_GATED_PENDING",
)

OUTCOME_REQUIRES_RECEIPT = {"COMPLETED"}


class LineageRefusal(RuntimeError):
    """Raised instead of writing or reporting a stage-attempt record that cannot be trusted."""


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stage_ids() -> tuple:
    return tuple(s["id"] for s in process_contract.contract_stages())


def _stage_index() -> dict:
    return {s["id"]: s for s in process_contract.contract_stages()}


def lineage_path(scroll: str, *, root: Path | None = None) -> Path:
    """The one file this scroll's lineage lives in."""
    canon = scroll_ids.resolve(scroll)
    base = Path(root) if root is not None else paths.artifact_write_root()
    return base / "stage_lineage" / ("%s.jsonl" % canon)


def _resolve_receipt(receipt_path: str) -> str:
    """A cited receipt must actually exist."""
    p = Path(receipt_path)
    candidate = p if p.is_absolute() else paths.resolve_repo_relative(str(receipt_path))
    resolved = candidate.resolve()
    if not resolved.exists():
        raise LineageRefusal(
            "receipt_path %r does not resolve to an existing file (looked at %s). A lineage "
            "record may not cite a receipt that is not really there." % (receipt_path, resolved))
    return str(resolved)


def _last_record(path: Path) -> dict | None:
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data:
        return None
    if not data.endswith(b"\n"):
        raise LineageRefusal("%s ends in a partial line; refusing to chain onto a damaged file"
                             % path)
    last_line = data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    last = json.loads(last_line.decode("utf-8"))
    if record_hash(last) != last.get("this_hash"):
        raise LineageRefusal("the last record in %s does not match its own hash; refusing to "
                             "extend a chain whose head is already wrong" % path)
    return last


def record_attempt(scroll: str, stage_id: str, outcome: str, *, capability_id: str | None = None,
                   receipt_path: str | None = None, detail: str = "",
                   recorded_by: dict | None = None, root: Path | None = None) -> dict:
    """Append one immutable record: this scroll's stage_id was attempted, with this outcome."""
    canon = scroll_ids.resolve(scroll)
    known = _stage_ids()
    if stage_id in process_contract.DERIVED_STAGE_IDS:
        raise LineageRefusal("stage_id %r is a derived journey stage with no runner; its state is "
                             "read from its owning module and no attempt can be recorded against it"
                             % stage_id)
    if stage_id not in known:
        raise LineageRefusal("unknown stage_id %r; the process contract names %s"
                             % (stage_id, sorted(known)))
    if outcome not in OUTCOMES:
        raise LineageRefusal("unknown outcome %r; must be one of %s" % (outcome, OUTCOMES))
    if outcome in OUTCOME_REQUIRES_RECEIPT and not receipt_path:
        raise LineageRefusal("outcome %r requires receipt_path; a COMPLETED stage with nothing "
                             "to point at is not distinguishable from one that never ran"
                             % outcome)
    resolved_receipt = _resolve_receipt(receipt_path) if receipt_path else None

    path = lineage_path(canon, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        last = _last_record(path)
        seq = (last["seq"] + 1) if last else 0
        prev_hash = last["this_hash"] if last else ZERO
        rec = {
            "schema": SCHEMA, "seq": seq, "utc": _utc(), "prev_hash": prev_hash,
            "scroll": canon, "stage_id": stage_id, "outcome": outcome,
            "capability_id": capability_id, "receipt_path": resolved_receipt,
            "detail": detail, "recorded_by": recorded_by or {},
        }
        rec["this_hash"] = record_hash(rec)
        line = json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str) + "\n"
        with path.open("ab") as fh:
            fh.write(line.encode("utf-8"))
    return rec


def read(scroll: str, *, root: Path | None = None) -> dict:
    """The full ordered, verified chain for one scroll."""
    canon = scroll_ids.resolve(scroll)
    path = lineage_path(canon, root=root)
    if not path.is_file():
        return {"schema": SCHEMA, "scroll": canon, "path": str(path), "exists": False,
                "chain_state": "EMPTY", "records": []}
    lines = [l for l in path.read_bytes().decode("utf-8").split("\n") if l.strip()]
    prev = ZERO
    records = []
    for i, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("seq") != i:
            return {"schema": SCHEMA, "scroll": canon, "path": str(path), "exists": True,
                    "chain_state": "BROKEN", "broken_at": i,
                    "why": "seq %r out of order (expected %d)" % (rec.get("seq"), i),
                    "records": records}
        if rec.get("prev_hash") != prev:
            return {"schema": SCHEMA, "scroll": canon, "path": str(path), "exists": True,
                    "chain_state": "BROKEN", "broken_at": i, "why": "prev_hash does not chain",
                    "records": records}
        if record_hash(rec) != rec.get("this_hash"):
            return {"schema": SCHEMA, "scroll": canon, "path": str(path), "exists": True,
                    "chain_state": "BROKEN", "broken_at": i,
                    "why": "record content does not match its own hash", "records": records}
        records.append(rec)
        prev = rec["this_hash"]
    return {"schema": SCHEMA, "scroll": canon, "path": str(path), "exists": True,
            "chain_state": "VERIFIED", "head": prev, "records": records}


def coverage(scroll: str, *, root: Path | None = None) -> dict:
    """The ordered attempt history joined against the FULL fifteen-stage contract."""
    canon = scroll_ids.resolve(scroll)
    chain = read(canon, root=root)
    stage_rows = _stage_index()
    per_stage: dict = {sid: {"stage_id": sid, "label": row["label"], "attempted": False,
                             "attempts": 0, "latest": None,
                             "contract_state": row["state"], "contract_gap": row["gap"]}
                       for sid, row in stage_rows.items()}
    order = []
    for rec in chain["records"]:
        sid = rec["stage_id"]
        cell = per_stage[sid]
        cell["attempted"] = True
        cell["attempts"] += 1
        cell["latest"] = {"seq": rec["seq"], "utc": rec["utc"], "outcome": rec["outcome"],
                          "capability_id": rec["capability_id"],
                          "receipt_path": rec["receipt_path"], "detail": rec["detail"],
                          "recorded_by": rec.get("recorded_by") or {}}
        order.append({"seq": rec["seq"], "utc": rec["utc"], "stage_id": sid,
                      "outcome": rec["outcome"], "receipt_path": rec["receipt_path"]})
    attempted_n = sum(1 for c in per_stage.values() if c["attempted"])
    return {
        "schema": "argus-stage-lineage-coverage-v1", "scroll": canon,
        "chain_state": chain["chain_state"], "chain_path": chain.get("path"),
        "attempted_order": order,
        "stages": per_stage,
        "counts": {"total_stages": len(per_stage), "attempted": attempted_n,
                  "never_attempted": len(per_stage) - attempted_n},
        "no_hidden_shell_work": ("every attempted_order entry's receipt_path, where present, "
                                 "was verified to exist on disk at record time; the chain_state "
                                 "above says whether the whole ordered record still verifies"),
    }


def seed_acquisition_from_existing_receipt(scroll: str, *, root: Path | None = None) -> dict | None:
    """Record a real 'raw_ct' attempt from evidence that already exists on disk, if any."""
    canon = scroll_ids.resolve(scroll)
    acq = process_contract._acquisition_receipt(canon)
    receipt = acq.get("receipt")
    if not receipt:
        return None
    resolved_receipt = str(Path(receipt).resolve())
    already = read(canon, root=root)
    for rec in already["records"]:
        if rec["stage_id"] == "raw_ct" and rec["receipt_path"] == resolved_receipt:
            return None

    outcome = "COMPLETED" if acq["state"] == "PACKET_PASSED" else "REFUSED"
    detail = acq["detail"]
    if acq.get("failed_gates"):
        detail += " failed_gates=%s" % acq["failed_gates"]
    return record_attempt(
        canon, "raw_ct", outcome,
        capability_id="target_acquisition_packet",
        receipt_path=receipt,
        detail=detail,
        recorded_by={"seeded_by": "stage_lineage.seed_acquisition_from_existing_receipt"},
        root=root,
    )
