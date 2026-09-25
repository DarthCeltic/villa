"""Where the CLI keeps its own small state, and the one rule about writing it."""
from __future__ import annotations

import os
import pathlib

from argus.core import paths, receipts

STATE_SCHEMA = "argus-cli-state-v1"

ROOT_ENV = "ARGUS_CLI_ROOT"

ACCEPTANCE_FILE = "licence_acceptance.json"


def install_root() -> pathlib.Path:
    """Where acquired components live."""
    override = os.environ.get(ROOT_ENV)
    if override:
        return pathlib.Path(override)
    return pathlib.Path(paths.cache("argus-cli"))


def acceptance_path(root=None) -> pathlib.Path:
    return pathlib.Path(root or install_root()) / ACCEPTANCE_FILE


def load_acceptance(root=None) -> dict:
    """component id -> the fingerprint of the terms that were accepted."""
    import json
    p = acceptance_path(root)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    accepted = doc.get("accepted")
    return accepted if isinstance(accepted, dict) else {}


def record_acceptance(component, root=None) -> pathlib.Path:
    """Append one acceptance."""
    import datetime
    root = pathlib.Path(root or install_root())
    p = acceptance_path(root)
    paths.assert_writable(p.parent)
    current = load_acceptance(root)
    current[component.id] = component.acceptance_fingerprint()
    return receipts.write_json({
        "schema": STATE_SCHEMA,
        "accepted": current,
        "accepted_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(
            ).replace("+00:00", "Z"),
        "what_a_fingerprint_binds": "the component id, SPDX id, licence URL, source URL and "
                                    "pinned revision. Change any of them and this acceptance "
                                    "stops matching, so setup asks again instead of letting "
                                    "an old yes cover new terms.",
        "contains_no_credentials": True,
    }, p)
