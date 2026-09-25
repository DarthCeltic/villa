"""What must be declared before two physical-frame products are treated as spatially comparable."""
from __future__ import annotations

import dataclasses
import math

import numpy as np

CONTRACT = "argus-physical-frame-v1"

ALLOWED_UNITS = ("um", "mm", "nm")

ORIGIN_CONVENTIONS = ("CORNER_MIN_VOXEL", "CENTER_FIRST_VOXEL", "UNDECLARED")

TRANSFORM_KINDS = ("RIGID", "SIMILARITY", "AFFINE")

_AXES = frozenset(("x", "y", "z"))


class FrameContractRefusal(ValueError):
    """Raised rather than treating two physical-frame products as spatially comparable."""


@dataclasses.dataclass(frozen=True)
class PhysicalFrame:
    """One volume's physical frame: identity, axes, units, voxel size, level and origin."""

    physical_scroll: str
    volume_id: str
    frame_id: str
    axes_order: tuple[str, str, str]
    units: str
    voxel_size: tuple[float, float, float]
    pyramid_level: int
    origin_convention: str
    source_tool: str
    acquisition_id: str | None = None
    source_revision: str | None = None

    def validate(self) -> None:
        if not str(self.physical_scroll or "").strip() or not str(self.volume_id or "").strip():
            raise FrameContractRefusal(
                "PHYSICAL_IDENTITY: physical_scroll and volume_id are required -- a frame that "
                "cannot say which physical scroll and which volume it belongs to cannot be "
                "compared against anything")
        if not str(self.frame_id or "").strip():
            raise FrameContractRefusal(
                "FRAME_IDENTITY: frame_id is required -- it is the identity a transform refers "
                "to, and a frame without one cannot be the source or target of a registration")
        axes = tuple(str(a).strip().lower() for a in self.axes_order)
        if len(axes) != 3 or set(axes) != _AXES:
            raise FrameContractRefusal(
                "AXES_ORDER: axes_order must be a permutation of ('x', 'y', 'z'), got %r -- "
                "which axis is which decides what every downstream number means" % (self.axes_order,))
        if self.units not in ALLOWED_UNITS:
            raise FrameContractRefusal(
                "UNITS: units must be one of %s, got %r -- an undeclared or unrecognised unit "
                "makes voxel_size unusable without guessing" % (ALLOWED_UNITS, self.units))
        if len(self.voxel_size) != 3 or any(
                v is None or not math.isfinite(float(v)) or float(v) <= 0 for v in self.voxel_size):
            raise FrameContractRefusal(
                "VOXEL_SIZE: voxel_size must be three finite, positive numbers (one per "
                "axes_order axis), got %r" % (self.voxel_size,))
        if self.pyramid_level is None or int(self.pyramid_level) < 0:
            raise FrameContractRefusal(
                "PYRAMID_LEVEL: pyramid_level must be a non-negative integer -- without it "
                "nothing downstream knows what physical scale a voxel of this frame is")
        if self.origin_convention not in ORIGIN_CONVENTIONS or self.origin_convention == "UNDECLARED":
            raise FrameContractRefusal(
                "ORIGIN_CONVENTION: origin_convention must be declared as one of %s (not "
                "UNDECLARED) -- it decides what a transform's translation component means"
                % ([c for c in ORIGIN_CONVENTIONS if c != "UNDECLARED"],))
        if not str(self.source_tool or "").strip():
            raise FrameContractRefusal(
                "SOURCE_TOOL: source_tool is required -- provenance of the frame declaration "
                "itself is part of the frame")


@dataclasses.dataclass(frozen=True)
class FrameTransform:
    """An explicit, directional registration from one physical frame to another."""

    source_frame_id: str
    target_frame_id: str
    source_level: int
    target_level: int
    transform_kind: str
    matrix: tuple[tuple[float, float, float, float], ...]
    produced_by_tool: str
    produced_by_revision: str
    produced_at: str
    method: str
    residual_error_um: float | None = None
    notes: str | None = None

    def validate(self) -> None:
        if not str(self.source_frame_id or "").strip() or not str(self.target_frame_id or "").strip():
            raise FrameContractRefusal(
                "TRANSFORM_IDENTITY: source_frame_id and target_frame_id are required")
        if self.source_frame_id == self.target_frame_id:
            raise FrameContractRefusal(
                "TRANSFORM_IDENTITY: source_frame_id and target_frame_id are the same (%r) -- a "
                "transform between a frame and itself is not a registration" % (self.source_frame_id,))
        if self.source_level is None or int(self.source_level) < 0 \
                or self.target_level is None or int(self.target_level) < 0:
            raise FrameContractRefusal(
                "TRANSFORM_LEVEL: source_level and target_level must be declared non-negative "
                "integers -- the level a transform was computed at is part of what it means")
        if self.transform_kind not in TRANSFORM_KINDS:
            raise FrameContractRefusal(
                "TRANSFORM_KIND: transform_kind must be one of %s, got %r"
                % (TRANSFORM_KINDS, self.transform_kind))
        m = self.matrix
        if len(m) != 4 or any(len(row) != 4 for row in m):
            raise FrameContractRefusal(
                "TRANSFORM_MATRIX: matrix must be 4x4 (homogeneous affine), got shape %r"
                % ([len(row) for row in m] if m else m,))
        flat = [float(v) for row in m for v in row]
        if not all(math.isfinite(v) for v in flat):
            raise FrameContractRefusal("TRANSFORM_MATRIX: matrix contains non-finite values")
        bottom = tuple(float(v) for v in m[3])
        if any(abs(a - b) > 1e-9 for a, b in zip(bottom, (0.0, 0.0, 0.0, 1.0))):
            raise FrameContractRefusal(
                "TRANSFORM_MATRIX: bottom row must be (0, 0, 0, 1) for a homogeneous affine "
                "transform, got %r" % (bottom,))
        if not str(self.produced_by_tool or "").strip() or not str(self.produced_by_revision or "").strip():
            raise FrameContractRefusal(
                "TRANSFORM_PROVENANCE: produced_by_tool and produced_by_revision are required -- "
                "a transform with no declared origin cannot be trusted over one that is unproven")
        if not str(self.produced_at or "").strip():
            raise FrameContractRefusal("TRANSFORM_PROVENANCE: produced_at is required")
        if not str(self.method or "").strip():
            raise FrameContractRefusal(
                "TRANSFORM_PROVENANCE: method is required -- how the transform was produced "
                "(manual, ICP, feature match, ...) is part of what it claims")

    def as_matrix(self) -> np.ndarray:
        return np.array(self.matrix, dtype=np.float64)


def _compose(a_to_b: FrameTransform, b_to_a: FrameTransform) -> np.ndarray:
    """Apply a_to_b then b_to_a; a true round trip returns the identity matrix."""
    return b_to_a.as_matrix() @ a_to_b.as_matrix()


def _round_trip_drift(composed: np.ndarray) -> tuple[float, float]:
    """Return (rotation/scale max abs deviation from identity, translation deviation)."""
    identity = np.eye(4)
    linear_deviation = float(np.max(np.abs(composed[:3, :3] - identity[:3, :3])))
    translation_deviation = float(np.linalg.norm(composed[:3, 3] - identity[:3, 3]))
    return linear_deviation, translation_deviation


def check_registered_pair(
    frame_a: PhysicalFrame,
    frame_b: PhysicalFrame,
    transform: FrameTransform | None,
    *,
    inverse_transform: FrameTransform | None = None,
    rotation_tolerance: float = 1e-3,
    translation_tolerance: float = 1e-3,
) -> dict:
    """Every clause, reported together."""
    problems: list[str] = []

    for label, frame in (("frame_a", frame_a), ("frame_b", frame_b)):
        try:
            frame.validate()
        except FrameContractRefusal as exc:
            problems.append("%s: %s" % (label, exc))

    if problems:
        return {
            "contract": CONTRACT,
            "frame_a": getattr(frame_a, "frame_id", None),
            "frame_b": getattr(frame_b, "frame_id", None),
            "ok": False,
            "problems": problems,
        }

    same_frame = frame_a.frame_id == frame_b.frame_id

    if same_frame:
        if transform is not None and (
                transform.source_frame_id != frame_a.frame_id
                or transform.target_frame_id != frame_b.frame_id):
            problems.append(
                "TRANSFORM_FRAME_MISMATCH: frame_a and frame_b declare the same frame_id (%r) "
                "but a transform was supplied for %r -> %r"
                % (frame_a.frame_id, transform.source_frame_id, transform.target_frame_id))
    elif transform is None:
        problems.append(
            "UNREGISTERED_PAIR: frame_a (%r) and frame_b (%r) declare different frames and no "
            "transform between them is declared. Two products in different physical frames are "
            "not spatially comparable without a proven registration; this is refused rather "
            "than treated as approximately aligned." % (frame_a.frame_id, frame_b.frame_id))
    else:
        try:
            transform.validate()
        except FrameContractRefusal as exc:
            problems.append("transform: %s" % exc)
        else:
            if transform.source_frame_id != frame_a.frame_id or transform.target_frame_id != frame_b.frame_id:
                problems.append(
                    "TRANSFORM_FRAME_MISMATCH: transform is declared %r -> %r but frame_a is "
                    "%r and frame_b is %r" % (transform.source_frame_id, transform.target_frame_id,
                                               frame_a.frame_id, frame_b.frame_id))
            if transform.source_level != frame_a.pyramid_level or transform.target_level != frame_b.pyramid_level:
                problems.append(
                    "LEVEL_MISMATCH: transform was computed between level %s and level %s, but "
                    "frame_a declares level %s and frame_b declares level %s. Applying it across "
                    "a level mismatch would silently change physical scale, so it is refused "
                    "rather than reinterpreted." % (transform.source_level, transform.target_level,
                                                      frame_a.pyramid_level, frame_b.pyramid_level))
            if frame_a.units != frame_b.units:
                problems.append(
                    "UNIT_MISMATCH: frame_a units %r != frame_b units %r; voxel sizes and a "
                    "transform's translation component are not comparable across units without "
                    "an explicit declared conversion, which is not part of a frame transform."
                    % (frame_a.units, frame_b.units))

    if transform is not None and inverse_transform is not None:
        transform_ok = True
        try:
            transform.validate()
        except FrameContractRefusal:
            transform_ok = False
        try:
            inverse_transform.validate()
        except FrameContractRefusal as exc:
            problems.append("inverse_transform: %s" % exc)
            transform_ok = False
        if transform_ok:
            if (inverse_transform.source_frame_id != transform.target_frame_id
                    or inverse_transform.target_frame_id != transform.source_frame_id):
                problems.append(
                    "ROUND_TRIP_DIRECTION_MISMATCH: transform is %r -> %r but inverse_transform "
                    "is %r -> %r, so composing them is not a round trip"
                    % (transform.source_frame_id, transform.target_frame_id,
                       inverse_transform.source_frame_id, inverse_transform.target_frame_id))
            else:
                composed = _compose(transform, inverse_transform)
                linear_dev, translation_dev = _round_trip_drift(composed)
                if linear_dev > rotation_tolerance or translation_dev > translation_tolerance:
                    problems.append(
                        "ROUND_TRIP_DRIFT: composing the declared %r -> %r transform with the "
                        "declared %r -> %r transform does not agree with identity within "
                        "tolerance (linear deviation %.6g > %.6g or translation deviation %.6g "
                        "> %.6g). A -> B is never assumed to be the inverse of B -> A; drift is "
                        "refused rather than silently accepted."
                        % (transform.source_frame_id, transform.target_frame_id,
                           inverse_transform.source_frame_id, inverse_transform.target_frame_id,
                           linear_dev, rotation_tolerance, translation_dev, translation_tolerance))

    return {
        "contract": CONTRACT,
        "frame_a": frame_a.frame_id,
        "frame_b": frame_b.frame_id,
        "same_frame": same_frame,
        "ok": not problems,
        "problems": problems,
    }


def require_registered_pair(
    frame_a: PhysicalFrame,
    frame_b: PhysicalFrame,
    transform: FrameTransform | None,
    *,
    used_for: str,
    inverse_transform: FrameTransform | None = None,
    rotation_tolerance: float = 1e-3,
    translation_tolerance: float = 1e-3,
) -> dict:
    """The gate: two frames must pass this before being treated as spatially comparable."""
    r = check_registered_pair(frame_a, frame_b, transform, inverse_transform=inverse_transform,
                               rotation_tolerance=rotation_tolerance,
                               translation_tolerance=translation_tolerance)
    if not r["ok"]:
        raise FrameContractRefusal(
            "%s refused for %s: frame pair %r / %r is not spatially comparable:\n  - %s"
            % (CONTRACT, used_for, r["frame_a"], r["frame_b"], "\n  - ".join(r["problems"])))
    return r


def request_template() -> dict:
    """What to ask a producer (ARGUS-native or an external VC3D/Velend/Blender adapter) for."""
    return {
        "contract": CONTRACT,
        "frame_required_fields": [f.name for f in dataclasses.fields(PhysicalFrame)],
        "transform_required_fields": [f.name for f in dataclasses.fields(FrameTransform)],
        "allowed_units": list(ALLOWED_UNITS),
        "allowed_origin_conventions": [c for c in ORIGIN_CONVENTIONS if c != "UNDECLARED"],
        "allowed_transform_kinds": list(TRANSFORM_KINDS),
        "note_on_direction": "a transform from A to B is never assumed to be the inverse of a "
                              "transform from B to A -- that is proven by composing the two and "
                              "checking the result against identity within tolerance, not assumed.",
        "note_on_external_tools": "this contract defines what a VC3D, Velend or Blender producer "
                                   "would have to declare to be admitted; it does not itself call "
                                   "those tools, which are not vendored or invokable here.",
    }
