"""Hash-verified science import register: public stub.

The operator's science imports and the manifests they verify are not part of the public release.
This stub keeps the names the service uses and always returns an empty register: nothing is
imported, verified, mounted or promoted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argus.core import paths

LOCK_SCHEMA = "argus-science-intake-lock-v1"
REGISTER_SCHEMA = "argus-science-intake-register-v2"
NO_QUALIFIED_DETECTOR = "NO_QUALIFIED_DETECTOR"
PUBLIC_NOTE = "science imports are not part of the public release"


class ScienceIntakeRefusal(ValueError):
    pass


def lock_path() -> Path:
    return paths.repo("config", "science_intake_lock.json")


def load_lock(path: Path | None = None) -> dict:
    p = Path(path) if path else lock_path()
    try:
        lock = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": LOCK_SCHEMA, "imports": []}
    if lock.get("schema") != LOCK_SCHEMA:
        raise ScienceIntakeRefusal("science intake lock has schema %r, expected %r" % (lock.get("schema"), LOCK_SCHEMA))
    return lock


def imported_science_register(*, root: Path | None = None, lock: dict | None = None) -> dict:
    lk = {"schema": LOCK_SCHEMA, "imports": []}
    return {
        "schema": REGISTER_SCHEMA,
        "imports": [],
        "all_verified": True,
        "lock": {"path": "config/science_intake_lock.json",
                 "sha256": hashlib.sha256(json.dumps(lk, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
        "detector_qualification": NO_QUALIFIED_DETECTOR,
        "private_arrays_mounted": False,
        "promotion": "import by hash and review; never auto-promote",
        "note": PUBLIC_NOTE,
    }


def assert_science_boundary(register: dict) -> dict:
    if register.get("detector_qualification") != NO_QUALIFIED_DETECTOR:
        raise ScienceIntakeRefusal("science intake cannot clear detector qualification")
    if register.get("private_arrays_mounted"):
        raise ScienceIntakeRefusal("science intake must not mount private arrays")
    unverified = [i.get("id") for i in register.get("imports", []) if not i.get("verified")]
    if unverified:
        raise ScienceIntakeRefusal("unverified science imports: %s" % ", ".join(map(str, unverified)))
    return register
