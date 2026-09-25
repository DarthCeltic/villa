"""Sealed probability-plane runs: public stub.

The operator's sealed runs, their seal registry and the scoring harness that read them are not
part of the public release. This stub keeps every name other ARGUS modules use, with the same
shapes, and refuses every read: no sealed run ships, so there is nothing to rank or score.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

PUBLIC_NOTE = "sealed probability-plane runs are not part of the public release"

SEAL_REGISTRY = None
MANIFEST = None
RUN_ROOT = None
SEGMENT = None
SCROLL = None
RUN = None
ORIENTATIONS = ("forward", "reversed")

BLOCK_PX = 640
HALO_PX = 96
OUTER_PX = BLOCK_PX + 2 * HALO_PX

PLANE_LAYERS = ("prob", "labels", "scored_mask")


class SealBroken(Exception):
    """The sealed bytes are not the bytes the seal record sealed."""


class SealRecordRefusal(ValueError):
    """The seal record cannot supply an identity."""


@dataclass(frozen=True)
class SealedRun:
    """One sealed run, addressed by its seal record (never available in the public build)."""
    manifest_path: pathlib.Path
    run_dir: pathlib.Path
    segment: str
    scroll: str
    orientations: tuple
    manifest_sha256: str | None

    @classmethod
    def from_seal_record(cls, manifest_path, *, run_dir=None) -> "SealedRun":
        raise SealRecordRefusal("%s (%s)" % (PUBLIC_NOTE, manifest_path))

    def record(self) -> dict:
        return {}

    def verify_record_hash(self) -> dict:
        return {"manifest_path": str(self.manifest_path), "recorded": None, "recomputed": None,
                "holds": False, "note": PUBLIC_NOTE}

    def physical_material_id(self) -> str:
        from argus.core import chain_traversal as CT
        return CT.mint_material_id(physical_scroll=self.scroll, segment=self.segment)


@dataclass(frozen=True)
class Block:
    """One sealed block, addressed by its file."""
    orientation: str
    name: str
    path: pathlib.Path
    y0: int
    y1: int
    x0: int
    x1: int

    @property
    def inner(self) -> tuple:
        return (self.y0 + HALO_PX, self.y1 - HALO_PX, self.x0 + HALO_PX, self.x1 - HALO_PX)

    @property
    def outer(self) -> tuple:
        return (self.y0, self.y1, self.x0, self.x1)


def default_run() -> SealedRun:
    """No default sealed run ships with the public release."""
    raise SealRecordRefusal(PUBLIC_NOTE)


def _unavailable(*_a, **_kw):
    raise FileNotFoundError(PUBLIC_NOTE)


verify_seal = _unavailable
require_seal = _unavailable
blocks = _unavailable
plane = _unavailable
block_footprint = _unavailable
scored_population = _unavailable
coverage_reconciliation = _unavailable
composited = _unavailable
reconstruct = _unavailable
tiles = _unavailable
bootstrap_units = _unavailable
aggregate = _unavailable
tile_permutation_null = _unavailable
conditioned = _unavailable
paired = _unavailable
export_plane = _unavailable


def layers(orientation: str, *, run=None) -> dict:
    return {layer: {"present": False, "path": None, "reason_absent": PUBLIC_NOTE}
            for layer in PLANE_LAYERS}


def summary(*, run=None) -> dict:
    return {"available": False, "note": PUBLIC_NOTE}
