"""Workbench renderer-fidelity annotations: not part of the public ARGUS release.

The operator's build can annotate its workbench with hash-verified renderer and flattening receipts.
Those receipts are not shipped, so every read here refuses with ConquestUnavailable (the name is kept
because the service imports it) and the routes that call it answer UNAVAILABLE: nothing is shown
rather than something unverified. The promotion block stays all False and the scale-bar arithmetic,
which needs no data, is unchanged.
"""
from __future__ import annotations

from pathlib import Path

PROMOTION = {"ink": False, "reading": False, "candidate_ready": False, "prize_authorized": False}
RECTO_VERSO = "NOT_DETERMINED"
LETTER_SCALE_WARNING = "LETTER-SCALE SHAPE UNRELIABLE"
CLASS_WORDS = {"LOW_DISTORTION": "LOW DISTORTION", "DISTORTION_MODERATE": "MODERATE DISTORTION", "DISTORTION_SEVERE": "SEVERE DISTORTION"}
PIECE_WORDS = {"FLATTENING_LOW_DISTORTION": "LOW DISTORTION", "FLATTENING_MODERATE_DISTORTION": "MODERATE DISTORTION", "FLATTENING_HIGH_DISTORTION": "HIGH DISTORTION"}
BOUNDARY = ("Renderer fidelity and parameterisation geometry only. No ink, no legibility, no detector result, no candidate promotion.")
_UNAVAILABLE = "renderer-fidelity annotations are not part of this public release; nothing is shown rather than something unverified"


class ConquestUnavailable(RuntimeError):
    """The annotation receipts are not part of this build: nothing is shown rather than something unverified."""


def render_status(*, root: Path | None = None) -> dict:
    raise ConquestUnavailable(_UNAVAILABLE)


def boundaries(*, reg: dict | None = None, root: Path | None = None) -> dict:
    raise ConquestUnavailable(_UNAVAILABLE)


def piece_flattening(*, root: Path | None = None) -> dict:
    raise ConquestUnavailable(_UNAVAILABLE)


def task_annotation(task_sha256: str, *, root: Path | None = None) -> dict:
    raise ConquestUnavailable(_UNAVAILABLE)


def scale_bar_px(local_um_per_px: float, length_um: float) -> float:
    """Pixels a scale bar of `length_um` spans when drawn with a task's OWN local um/px (never a piece or receipt average)."""
    if not local_um_per_px or local_um_per_px <= 0:
        raise ValueError("a scale bar needs a positive local um/px")
    return round(length_um / local_um_per_px, 2)
