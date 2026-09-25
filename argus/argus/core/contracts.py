"""ARGUS core contracts: terminal states, the module protocol, and the surface bundle."""
from __future__ import annotations

import dataclasses
import hashlib
import inspect as _inspect
import json
import re
from pathlib import Path
from typing import Any


class Terminal:
    CERTIFIED_SURFACE = "CERTIFIED_SURFACE"
    CERTIFIED_2D = "CERTIFIED_2D"
    CERTIFIED_INK_CANDIDATE = "CERTIFIED_INK_CANDIDATE"
    REFUSED = "REFUSED"
    ALL = (CERTIFIED_SURFACE, CERTIFIED_2D, CERTIFIED_INK_CANDIDATE, REFUSED)

    RANK = {CERTIFIED_SURFACE: 1, CERTIFIED_2D: 2, CERTIFIED_INK_CANDIDATE: 3}

    @classmethod
    def rank(cls, t: str | None) -> int:
        return cls.RANK.get(t or "", 0)

    @classmethod
    def highest(cls, terminals) -> str | None:
        """The best certification among a set."""
        best = max((t for t in terminals if t in cls.RANK), key=cls.rank, default=None)
        return best


class Refusal(Exception):
    """Raised anywhere a run cannot honestly continue."""

    CLASSES = ("GEOMETRY", "REPRESENTATION", "DETECTOR_DOMAIN", "CERTIFICATION",
               "DATA_UNAVAILABLE", "MISSING_UPSTREAM_ASSET")

    def __init__(self, cls: str, reason: str, evidence: dict | None = None):
        if cls not in self.CLASSES:
            raise ValueError("unknown refusal class %r" % cls)
        super().__init__("%s: %s" % (cls, reason))
        self.cls, self.reason, self.evidence = cls, reason, evidence or {}

    def as_dict(self) -> dict:
        return {"terminal": Terminal.REFUSED, "refusal_class": self.cls,
                "reason": self.reason, "evidence": self.evidence}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


@dataclasses.dataclass
class Acquisition:
    """What was scanned and how."""
    volume_id: str
    voxel_um: float
    energy_kev: int
    pitch_um_per_px: float
    pyramid_level: int | None = None

    def family(self) -> str:
        return "%.1fum_%dkeV" % (round(self.voxel_um, 1), self.energy_kev)


@dataclasses.dataclass
class CertifiedSurfaceBundle:
    """The only thing a detector is allowed to receive."""
    acquisition: Acquisition
    mesh_sha256: dict
    mesh_source: str
    grid_shape: tuple
    coverage: float
    orientation: str
    orientation_source: str
    depth_planes: int
    physical_window_mm: float | None = None
    frame_handshake: dict | None = None
    chunk_identities: list | None = None
    topology: dict | None = None
    invertible: bool = False
    lineage: dict | None = None

    def verify(self) -> list:
        f = []
        if self.orientation not in ("AS_WRITTEN", "REVERSED"):
            f.append("orientation is %r; an undetermined or contradictory orientation is a "
                     "REFUSAL, never a default (ARGUS calibration contract)" % self.orientation)
        if not self.orientation_source or "scored higher" in self.orientation_source.lower():
            f.append("orientation must come from asset provenance confirmed against a known-ink "
                     "control, never from which direction scored higher")
        if not self.mesh_sha256 or any(len(v) != 64 for v in self.mesh_sha256.values()):
            f.append("mesh identity must be full sha256 per file; a path is not an identity")
        if self.coverage <= 0:
            f.append("coverage is %r" % self.coverage)
        if self.frame_handshake is None:
            f.append("no mesh<->volume frame handshake (upstream #1660: a frame mismatch "
                     "renders a black strip and exits 0)")
        if self.chunk_identities is None:
            f.append("no sampled chunk identities (upstream #1674: wrong chunk indices render "
                     "fill-value silently)")
        if self.topology is None:
            f.append("no topology evidence (upstream #1675: growth reports plausible "
                     "area while cutting across windings)")
        if not self.invertible:
            f.append("2D->CT invertibility not established")
        return f


class Module:
    """Every ARGUS stage implements these five."""

    name = "unnamed"

    CERTIFIES: str | None = None

    def inspect(self, ctx: dict) -> dict:
        raise NotImplementedError

    def plan(self, ctx: dict) -> dict:
        raise NotImplementedError

    def run(self, ctx: dict, attempt_dir: Path) -> dict:
        raise NotImplementedError

    def verify(self, ctx: dict, attempt_dir: Path) -> dict:
        raise NotImplementedError

    def describe(self) -> dict:
        src = Path(_inspect.getfile(self.__class__))
        return {"module": self.name, "class": self.__class__.__name__,
                "certifies": self.CERTIFIES,
                "source": str(src), "source_sha256": sha256_file(src) if src.is_file() else None}


SCROLL_ID = re.compile(r"\bPHerc[A-Za-z0-9]*\d|\bScroll\s?\d", re.I)


def assert_no_scroll_specific_logic(path: Path) -> list:
    """Mechanically catch the failure mode that turns a general system into a single-target one: a scroll id appearing in a CONDITIONAL."""
    bad = []
    in_block = None
    triple_double = chr(34) * 3
    triple_single = chr(39) * 3
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if in_block is not None:
            if in_block in s:
                in_block = None
            continue
        opened = False
        for delim in (triple_double, triple_single):
            if s.startswith(delim) and s.count(delim) == 1:
                in_block = delim
                opened = True
                break
        if opened:
            continue
        if s.startswith("#") or s.startswith('"') or s.startswith("'"):
            continue
        if not SCROLL_ID.search(s):
            continue
        if re.match(r"^(if|elif|while)\b", s) or re.search(r"\b(if|elif)\b.*==", s):
            bad.append("%s:%d branches on a scroll id: %s" % (path.name, i, s[:90]))
    return bad
