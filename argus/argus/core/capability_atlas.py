"""The atomic capability atlas and its evidence ladder."""
from __future__ import annotations

import dataclasses
from typing import Iterable

ATLAS_ID = "argus-capability-atlas-v1"

LEVELS = (
  "L0_DISCOVERED",
  "L1_CONTRACT_FROZEN",
  "L2_IMPLEMENTED_UNIT_TESTED",
  "L3_INTEGRATED_WITH_REAL_ENGINE",
  "L4_REAL_DATA_ROUND_TRIP_PROVEN",
  "L5_USER_JOURNEY_PROVEN",
  "L6_FRESH_INSTALL_RELEASE_PROVEN",
)
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

MATRICES = ("OFFICIAL_REQUIREMENTS_COVERAGE", "TOOL_AND_FORMAT_COVERAGE",
            "END_TO_END_USER_ACTION_COVERAGE", "PRIZE_SUBMISSION_COVERAGE")

FORBIDDEN_UI_WORDS = ("complete", "ready", "built_verified", "works end to end",
                      "full pipeline", "one-click", "does it all", "scrollbench")

WORD_MIN_LEVEL = 5


class AtlasError(ValueError):
    """Raised when a capability's evidence would have to be assumed."""


@dataclasses.dataclass(frozen=True)
class Evidence:
    """One rung's evidence."""

    level: str
    passed: bool
    how: str
    receipt: str | None = None

    def __post_init__(self):
        if self.level not in LEVEL_INDEX:
            raise AtlasError("unknown level %r" % (self.level,))
        if self.passed and not self.how:
            raise AtlasError("level %s claims to pass with no stated evidence" % self.level)


@dataclasses.dataclass
class Capability:
    """One thing a person can or cannot do, or one contract ARGUS owes."""

    id: str
    matrix: str
    pillar: str
    action: str
    engine: str | None = None
    evidence: tuple = ()

    def __post_init__(self):
        if self.matrix not in MATRICES:
            raise AtlasError("capability %s names unknown matrix %r" % (self.id, self.matrix))

    @property
    def level(self) -> str:
        """Highest CONTIGUOUS rung passed."""
        by = {e.level: e for e in self.evidence}
        best = -1
        for i, name in enumerate(LEVELS):
            e = by.get(name)
            if e is not None and e.passed:
                best = i
            else:
                break
        return LEVELS[best] if best >= 0 else "L0_NOT_STARTED"

    @property
    def level_index(self) -> int:
        lv = self.level
        return LEVEL_INDEX.get(lv, -1)

    @property
    def skipped_evidence(self) -> list:
        """Evidence that passes above the contiguous ceiling."""
        top = self.level_index
        return [e.level for e in self.evidence
                if e.passed and LEVEL_INDEX[e.level] > top + 0]

    @property
    def next_rung(self) -> str | None:
        i = self.level_index
        return LEVELS[i + 1] if i + 1 < len(LEVELS) else None

    @property
    def blocking_reason(self) -> str:
        nxt = self.next_rung
        if nxt is None:
            return "at the top of the ladder"
        by = {e.level: e for e in self.evidence}
        e = by.get(nxt)
        return e.how if e is not None else "no evidence recorded for %s" % nxt

    def ui_safe_label(self) -> str:
        """What a user-facing surface is allowed to say about this capability."""
        i = self.level_index
        if i >= LEVEL_INDEX["L6_FRESH_INSTALL_RELEASE_PROVEN"]:
            return "verified on a fresh install"
        if i >= LEVEL_INDEX["L5_USER_JOURNEY_PROVEN"]:
            return "proven in a user journey"
        if i >= LEVEL_INDEX["L4_REAL_DATA_ROUND_TRIP_PROVEN"]:
            return "round trip proven on real data (not yet a user journey)"
        if i >= LEVEL_INDEX["L3_INTEGRATED_WITH_REAL_ENGINE"]:
            return "wired to the real engine (not yet proven on real data)"
        if i >= LEVEL_INDEX["L2_IMPLEMENTED_UNIT_TESTED"]:
            return "unit tested only (no engine, not usable end to end)"
        if i >= LEVEL_INDEX["L1_CONTRACT_FROZEN"]:
            return "contract frozen, not implemented"
        if i >= LEVEL_INDEX["L0_DISCOVERED"]:
            return "identified, not started"
        return "not started"

    def as_dict(self) -> dict:
        return {"id": self.id, "matrix": self.matrix, "pillar": self.pillar,
                "action": self.action, "engine": self.engine,
                "level": self.level, "level_index": self.level_index,
                "ui_safe_label": self.ui_safe_label(),
                "next_rung": self.next_rung,
                "blocking_reason": self.blocking_reason,
                "skipped_evidence": self.skipped_evidence,
                "evidence": [dataclasses.asdict(e) for e in self.evidence]}


def distribution(caps: Iterable[Capability]) -> dict:
    out = {lv: 0 for lv in ("L0_NOT_STARTED",) + LEVELS}
    for c in caps:
        out[c.level] = out.get(c.level, 0) + 1
    return out


def by_matrix(caps: Iterable[Capability]) -> dict:
    out: dict = {m: [] for m in MATRICES}
    for c in caps:
        out[c.matrix].append(c)
    return out


def ui_claim_is_allowed(text: str, caps: Iterable[Capability]) -> tuple:
    """Would this user-facing sentence be a lie given current evidence?"""
    low = text.lower()
    hits = [w for w in FORBIDDEN_UI_WORDS if w in low]
    if not hits:
        return True, []
    caps = list(caps)
    if caps and all(c.level_index >= WORD_MIN_LEVEL for c in caps):
        return True, []
    return False, hits
