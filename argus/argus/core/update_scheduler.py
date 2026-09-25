"""Small, bounded scheduler for the AUTOMATIC provider-update policy."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from argus.core import provider_updates as PU
from argus.core import update_store as S
from argus.core import upstream_discovery as D

_START_LOCK = threading.Lock()
_STARTED = False


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def check_interval_s(cfg: dict) -> int:
    """The shortest declared interval, with a one-minute safety floor."""
    values = [
        int(row.get("check_interval_s", 21600))
        for row in cfg.get("sources", [])
        if int(row.get("check_interval_s", 21600)) > 0
    ]
    return max(60, min(values, default=21600))


def check_is_due(cfg: dict, *, now: float | None = None) -> bool:
    observations = S.load_observations()
    checked = [
        _epoch(row.get("checked_utc"))
        for row in observations.values()
        if isinstance(row, dict)
    ]
    checked = [value for value in checked if value is not None]
    if not checked:
        return True
    return (time.time() if now is None else now) - max(checked) >= check_interval_s(cfg)


def run_cycle(*, now: float | None = None) -> dict:
    policy = PU.policy()
    if policy.mode != "AUTOMATIC":
        return {"status": "IDLE", "why": "update policy is not AUTOMATIC"}
    cfg = D.load_config()
    if not check_is_due(cfg, now=now):
        return {"status": "IDLE", "why": "the configured check interval has not elapsed"}
    result = PU.check(cfg)
    return {"status": "CHECKED", "result": result}


def _loop() -> None:
    poll_s = max(1.0, float(os.environ.get("ARGUS_UPDATE_SCHEDULER_POLL_S", "5")))
    while True:
        try:
            run_cycle()
        except Exception as exc:
            print("provider update scheduler: %s" % type(exc).__name__)
        time.sleep(poll_s)


def start() -> bool:
    """Start once per command-service process."""
    global _STARTED
    if os.environ.get("ARGUS_UPDATE_SCHEDULER_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return False
    with _START_LOCK:
        if _STARTED:
            return True
        threading.Thread(target=_loop, name="argus-provider-update-scheduler", daemon=True).start()
        _STARTED = True
    return True
