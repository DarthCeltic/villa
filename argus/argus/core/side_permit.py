"""The one interface every side worker uses to ask whether it may run."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import pathlib
import time

PERMIT = pathlib.Path(_argus_public_path('home', 'state/side_work_permit.json'))

MAX_AGE_S = 90.0


def permit_state() -> dict:
    """The current permit, or a synthetic denial explaining why there is none."""
    try:
        raw = PERMIT.read_text(encoding="utf-8")
    except OSError:
        return {"allow": False, "reason": "NO_PERMIT_FILE",
                "detail": "the governor is not running; side work is denied by default so a "
                          "dead governor cannot silently authorise everything."}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {"allow": False, "reason": "PERMIT_UNPARSEABLE"}
    age = time.time() - float(doc.get("written_epoch", 0))
    if age > MAX_AGE_S:
        return {"allow": False, "reason": "PERMIT_STALE",
                "age_s": round(age, 1), "detail": doc.get("detail")}
    doc["age_s"] = round(age, 1)
    return doc


def allowed() -> bool:
    return bool(permit_state().get("allow"))


def wait_for_permit(poll_s: float = 15.0, timeout_s: float | None = None,
                    announce=None) -> bool:
    """Block until side work is permitted."""
    t0 = time.time()
    said = False
    while True:
        st = permit_state()
        if st.get("allow"):
            if said and announce:
                announce({"resumed": True, "after_s": round(time.time() - t0, 1)})
            return True
        if not said and announce:
            announce({"suspended": True, "reason": st.get("reason"),
                      "detail": st.get("detail")})
            said = True
        if timeout_s is not None and time.time() - t0 > timeout_s:
            return False
        time.sleep(poll_s)


def guard(announce=None):
    """Convenience for a loop body: wait if suspended, then proceed."""
    if not allowed():
        wait_for_permit(announce=announce)
    return True


def write_permit(doc: dict) -> None:
    """Governor-only."""
    doc = dict(doc)
    doc["written_epoch"] = time.time()
    doc["written_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    PERMIT.parent.mkdir(parents=True, exist_ok=True)
    tmp = PERMIT.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(doc, indent=1) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PERMIT)
