"""The named final-disposition wrapper for VIGILES."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path


SCHEMA = "argus-belisarius-final-disposition-v1"
AUTHORITY_ID = "belisarius"
AUTHORITY_NAME = "Final human disposition"
RUN_FILENAME = "belriouse_run.json"
DECISIONS = ("YES", "NO")


class BelisariusRefusal(ValueError):
    """A malformed or scientifically unsafe final disposition."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def output_path(state_root: Path) -> Path:
    """The one current-pointer file; callers must not choose an arbitrary output path."""
    return Path(state_root) / "belisarius" / RUN_FILENAME


def _read_vigiles(receipt: Path) -> tuple[dict, str]:
    if not receipt.is_file():
        raise BelisariusRefusal("the VIGILES receipt does not exist: %s" % receipt)
    try:
        doc = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BelisariusRefusal("the VIGILES receipt is not readable JSON: %s" % exc) from None
    if not isinstance(doc, dict) or doc.get("schema") != "argus-vigiles-decision-v1":
        raise BelisariusRefusal(
            "the wrapped file must be an argus-vigiles-decision-v1 receipt")
    if not doc.get("terminal"):
        raise BelisariusRefusal("the VIGILES receipt has no terminal decision")
    return doc, _sha256_file(receipt)


def plan(*, vigiles_receipt: str | Path, decision: str | None = None,
         state_root: str | Path) -> dict:
    """Read and validate the proposed wrapper without writing anything."""
    receipt = Path(vigiles_receipt)
    doc, digest = _read_vigiles(receipt)
    normalized = decision.upper() if isinstance(decision, str) else None
    if decision is not None and (not isinstance(decision, str) or normalized not in DECISIONS):
        raise BelisariusRefusal("decision must be YES or NO")
    terminal = doc["terminal"]
    return {
        "schema": SCHEMA,
        "authority_id": AUTHORITY_ID,
        "authority_name": AUTHORITY_NAME,
        "decision_options": list(DECISIONS),
        "decision": normalized,
        "vigiles_receipt": str(receipt),
        "vigiles_sha256": digest,
        "vigiles_terminal": terminal,
        "yes_allowed": terminal != "REFUSED",
        "output_file": str(output_path(Path(state_root))),
        "scientific_effect": "none: this wrapper cannot promote or override VIGILES",
        "ready": normalized is None or normalized == "NO" or terminal != "REFUSED",
    }


def record(*, vigiles_receipt: str | Path, decision: str, rationale: str,
           actor: str, state_root: str | Path, supersedes_sha256: str | None = None) -> dict:
    """Write the explicit human final disposition and return the receipt."""
    if not isinstance(actor, str) or not actor.startswith("human:"):
        raise BelisariusRefusal("the final disposition requires a human actor")
    normalized = decision.upper() if isinstance(decision, str) else ""
    if normalized not in DECISIONS:
        raise BelisariusRefusal("decision must be YES or NO")
    if not isinstance(rationale, str) or not rationale.strip():
        raise BelisariusRefusal("a final disposition requires a non-empty rationale")
    doc, digest = _read_vigiles(Path(vigiles_receipt))
    if normalized == "YES" and doc["terminal"] == "REFUSED":
        raise BelisariusRefusal("a final YES cannot override a REFUSED VIGILES receipt")

    destination = output_path(Path(state_root))
    previous = None
    if destination.is_file():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BelisariusRefusal(
                "the existing belriouse_run.json is unreadable; preserve it and repair it "
                "before taking another final decision") from None
        prior_hash = _sha256_file(destination)
        if supersedes_sha256 != prior_hash:
            raise BelisariusRefusal(
                "an existing final decision requires supersedes_sha256=%s" % prior_hash)

    rec = {
        "schema": SCHEMA,
        "authority": {"id": AUTHORITY_ID, "name": AUTHORITY_NAME,
                       "actor": actor, "class": "HUMAN_ONLY"},
        "decision": normalized,
        "disposition": "BELISARIUS_SAYS_%s" % normalized,
        "rationale": rationale.strip(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": "belriouse-%s" % uuid.uuid4().hex[:12],
        "vigiles": {"receipt": str(vigiles_receipt), "sha256": digest,
                     "terminal": doc["terminal"]},
        "supersedes_sha256": (_sha256_file(destination) if previous is not None else None),
        "scientific_effect": "none: this is a final human disposition, not a certification",
        "can_override_vigiles": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".json.tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, destination)
    rec["file"] = str(destination)
    rec["file_sha256"] = _sha256_file(destination)
    return rec


def current(*, state_root: str | Path) -> dict:
    """Read the current wrapper without treating absence as a decision."""
    path = output_path(Path(state_root))
    if not path.is_file():
        return {"schema": SCHEMA, "authority_id": AUTHORITY_ID,
                "authority_name": AUTHORITY_NAME, "state": "PENDING",
                "decision": None, "file": str(path), "scientific_effect": "none"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "authority_id": AUTHORITY_ID,
                "authority_name": AUTHORITY_NAME, "state": "REFUSED",
                "decision": None, "file": str(path),
                "why": "belriouse_run.json is not valid JSON"}
    doc = dict(doc)
    doc["file"] = str(path)
    doc["file_sha256"] = _sha256_file(path)
    doc["state"] = "RECORDED"
    return doc
