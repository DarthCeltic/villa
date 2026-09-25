"""One live storage policy for every ARGUS write preflight."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

POLICY_ID = "argus-storage-policy-v2"
CONFIG_SCHEMA = "argus-storage-policy-config-v1"

_BUILTIN = {"c": {"hard_stop_gib": 20.0, "advisory_gib": None}, "t": {"hard_stop_gib": 20.0, "advisory_gib": 40.0}}


class StoragePolicyRefusal(RuntimeError):
    """A planned write cannot prove that both protected drives stay above their hard-stop floors."""


class StoragePolicyConfigError(ValueError):
    pass


def config_path() -> Path:
    env = os.environ.get("ARGUS_STORAGE_POLICY")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config" / "storage_policy.json"


def _validate(levels: dict) -> dict:
    out = {}
    for drive in ("c", "t"):
        row = levels.get(drive)
        if not isinstance(row, dict):
            raise StoragePolicyConfigError("storage policy has no levels for drive %s" % drive.upper())
        hard = row.get("hard_stop_gib")
        adv = row.get("advisory_gib")
        if not isinstance(hard, (int, float)) or isinstance(hard, bool) or hard <= 0:
            raise StoragePolicyConfigError("%s: hard_stop_gib must be a positive number" % drive.upper())
        if adv is not None and (not isinstance(adv, (int, float)) or isinstance(adv, bool) or adv < hard):
            raise StoragePolicyConfigError("%s: advisory_gib must be null or at least hard_stop_gib" % drive.upper())
        out[drive] = {"hard_stop_gib": float(hard), "advisory_gib": None if adv is None else float(adv)}
    return out


def load_levels(path: Path | None = None) -> tuple[dict, str]:
    p = Path(path) if path else config_path()
    if not p.is_file():
        return _validate(_BUILTIN), "built-in defaults (config file absent)"
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("schema") != CONFIG_SCHEMA:
        raise StoragePolicyConfigError("storage policy config has schema %r, expected %r" % (doc.get("schema"), CONFIG_SCHEMA))
    return _validate(doc["drives"]), str(p.name)


_LEVELS, _SOURCE = load_levels()
C_FLOOR_GIB = _LEVELS["c"]["hard_stop_gib"]
T_FLOOR_GIB = _LEVELS["t"]["hard_stop_gib"]


@dataclass(frozen=True)
class DriveStatus:
    name: str
    root: str
    free_gib: float | None
    hard_stop_gib: float
    advisory_gib: float | None

    @property
    def after_floor_ok(self) -> bool:
        return self.free_gib is not None and self.free_gib >= self.hard_stop_gib

    @property
    def below_advisory(self) -> bool:
        return self.advisory_gib is not None and self.free_gib is not None and self.free_gib < self.advisory_gib

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "root": self.root,
            "free_gib": None if self.free_gib is None else round(self.free_gib, 2),
            "hard_stop_gib": self.hard_stop_gib,
            "floor_gib": self.hard_stop_gib,
            "advisory_gib": self.advisory_gib,
            "above_floor": self.after_floor_ok,
            "below_advisory": self.below_advisory,
        }


def floors() -> dict[str, float]:
    """The hard-stop (refusal) floors."""
    return {"c": C_FLOOR_GIB, "t": T_FLOOR_GIB}


def advisories() -> dict[str, float | None]:
    return {"c": _LEVELS["c"]["advisory_gib"], "t": _LEVELS["t"]["advisory_gib"]}


def _root(drive: str) -> str:
    env = os.environ.get("ARGUS_STORAGE_ROOT_" + drive.upper())
    if env:
        return env
    literal = drive.upper() + ":/"
    if os.name == "nt" and os.path.isdir(literal):
        return literal
    return _argus_public_path("home" if drive == "c" else "cache", "")


def _free_gib(root: str) -> float | None:
    try:
        p = Path(root)
        while not p.exists() and p != p.parent:
            p = p.parent
        return shutil.disk_usage(str(p)).free / (1 << 30)
    except OSError:
        return None


def status() -> dict:
    drives = (
        DriveStatus("C", _root("c"), _free_gib(_root("c")), C_FLOOR_GIB, _LEVELS["c"]["advisory_gib"]),
        DriveStatus("T", _root("t"), _free_gib(_root("t")), T_FLOOR_GIB, _LEVELS["t"]["advisory_gib"]),
    )
    return {
        "policy_id": POLICY_ID,
        "current": True,
        "levels_source": _SOURCE,
        "drives": {d.name.lower(): d.as_dict() for d in drives},
        "above_floors": all(d.after_floor_ok for d in drives),
        "above_hard_stop": all(d.after_floor_ok for d in drives),
        "warnings": ["%s: %.2f GiB free is below the %.0f GiB advisory headroom (not a refusal)" % (d.name, d.free_gib, d.advisory_gib)
                     for d in drives if d.below_advisory],
        "rule": "hard_stop_gib refuses a write; advisory_gib only warns",
        "historical_boundaries": {
            "resource_receipts": "preserved evidence; not the live write policy",
        },
        "mutations": "none; storage conveyor owns archive, hydrate and evict",
    }


def capacity_for_write(*, c_gib: float = 0.0, t_gib: float = 0.0,
                       observed: Mapping[str, float | None] | None = None) -> dict:
    """Return a complete, two-drive post-write decision without writing anything."""
    if c_gib < 0 or t_gib < 0:
        raise ValueError("planned writes must be non-negative")
    live = observed or {"c": _free_gib(_root("c")), "t": _free_gib(_root("t"))}
    plan = {"c": (live.get("c"), c_gib), "t": (live.get("t"), t_gib)}
    after = {}
    reasons = []
    warnings = []
    for drive, (free, planned) in plan.items():
        hard, adv = _LEVELS[drive]["hard_stop_gib"], _LEVELS[drive]["advisory_gib"]
        after[drive] = None if free is None else round(free - planned, 2)
        if free is None:
            reasons.append(f"{drive.upper()}: free space unknown")
        elif free - planned < hard:
            reasons.append(f"{drive.upper()}: {free - planned:.2f} GiB after write is below the {hard:.1f} GiB hard-stop floor")
        elif adv is not None and free - planned < adv:
            warnings.append(f"{drive.upper()}: {free - planned:.2f} GiB after write is below the {adv:.1f} GiB advisory headroom (not a refusal)")
    return {
        "policy_id": POLICY_ID,
        "allowed": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "planned_write_gib": {"c": c_gib, "t": t_gib},
        "after_free_gib": after,
        "floors_gib": floors(),
        "hard_stop_gib": floors(),
        "advisory_gib": advisories(),
    }


def require_capacity(*, c_gib: float = 0.0, t_gib: float = 0.0,
                     observed: Mapping[str, float | None] | None = None) -> dict:
    decision = capacity_for_write(c_gib=c_gib, t_gib=t_gib, observed=observed)
    if not decision["allowed"]:
        raise StoragePolicyRefusal("; ".join(decision["reasons"]))
    return decision
