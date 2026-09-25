"""What a surface must declare before ARGUS will ingest it."""
from __future__ import annotations

import dataclasses
import re

CONTRACT = "argus-surface-ingest-v1"

RECTO, VERSO, COMPOSITE, UNKNOWN = "RECTO", "VERSO", "COMPOSITE", "UNKNOWN"
FACES = (RECTO, VERSO, COMPOSITE, UNKNOWN)

SINGLE_FACE = (RECTO, VERSO)

ESTABLISHED_BY = (
  "INSPECTED_AGAINST_CT",
  "SEGMENTATION_METADATA",
  "PUBLISHED_BY_PRODUCER",
  "INFERRED_FROM_GEOMETRY",
  "ASSERTED_WITHOUT_EVIDENCE",
  "UNDECLARED",
)

REFUSED_GENERATION = {
  "NORMAL_OFFSET": "a surface produced by offsetting another along its normals is the SAME face "
                   "at a different depth. Labelling it as the opposite face makes every "
                   "subsequent statement about 'the other side' a statement about this side.",
}

REQUIRED = (
  "physical_scroll", "segment_id", "surface_id", "ct_volume_id", "tifxyz_path",
  "normal_orientation", "face", "face_established_by", "recto_shaved_or_missing",
  "pitch_um", "energy_kev", "pyramid_level", "source_licence",
  "redistribution_permitted", "parent_geometry", "generation_command", "hashes",
)

NORMAL_ORIENTATIONS = ("OUTWARD_FROM_SCROLL_CENTRE", "INWARD_TO_SCROLL_CENTRE", "UNDECLARED")


class SurfaceRefusal(ValueError):
    """Raised rather than ingesting something that cannot be described."""


@dataclasses.dataclass(frozen=True)
class SurfaceDeclaration:
    physical_scroll: str
    segment_id: str
    surface_id: str
    ct_volume_id: str
    tifxyz_path: str
    normal_orientation: str
    face: str
    face_established_by: str
    recto_shaved_or_missing: str
    pitch_um: float | None
    energy_kev: float | None
    pyramid_level: int | None
    source_licence: str
    redistribution_permitted: str
    parent_geometry: str | None
    generation_command: str | None
    hashes: dict


def check(d: SurfaceDeclaration) -> dict:
    """Every clause, reported together."""
    problems = []

    for field in ("physical_scroll", "segment_id", "surface_id", "ct_volume_id", "tifxyz_path"):
        if not str(getattr(d, field) or "").strip():
            problems.append(
              "%s is not declared. A surface that cannot say which physical scroll, segment, "
              "surface or CT volume it belongs to cannot be scored against anything." % field)

    if d.face not in FACES:
        problems.append("face %r is not one of %s. An unrecognised face is refused rather than "
                        "rounded to the nearest binary." % (d.face, list(FACES)))
    if d.face_established_by not in ESTABLISHED_BY:
        problems.append(
          "face_established_by %r is not declared. 'RECTO' asserted by a filename, by a "
          "convention, by memory and by inspection against CT are four different objects "
          "wearing one word." % (d.face_established_by,))
    if d.face in SINGLE_FACE and d.face_established_by == "UNDECLARED":
        problems.append(
          "face is %s but nothing says how that was established. A single-face claim with no "
          "provenance is an assertion, and this contract exists to stop assertions becoming "
          "orientation policy." % d.face)

    if d.normal_orientation not in NORMAL_ORIENTATIONS:
        problems.append(
          "normal_orientation %r is not declared. Which way the normals point decides what "
          "'backward along the normal' means, and therefore what a depth sweep sweeps."
          % (d.normal_orientation,))

    if str(d.recto_shaved_or_missing or "").strip().upper() not in ("YES", "NO", "UNKNOWN"):
        problems.append(
          "recto_shaved_or_missing must be YES, NO or UNKNOWN. If the recto was shaved away "
          "then a 'recto' label names material that is not there.")

    gen = str(d.generation_command or "")
    for key, why in REFUSED_GENERATION.items():
        if key in gen.upper() and d.face in SINGLE_FACE:
            problems.append("generation_command declares %s and the surface claims face %s: %s"
                            % (key, d.face, why))

    if d.pitch_um is None or d.energy_kev is None:
        problems.append(
          "pitch_um and energy_kev must both be declared. Acquisition is not a detail: two "
          "volumes of the same scroll at different energies are different measurements.")
    if d.pyramid_level is None:
        problems.append("pyramid_level is not declared, so nothing downstream knows what one "
                        "voxel of this surface means.")

    if not str(d.source_licence or "").strip():
        problems.append("source_licence is UNDECLARED, which is a refusal and never a "
                        "presumption of permission.")
    if str(d.redistribution_permitted or "").strip().upper() not in ("YES", "NO", "UNKNOWN"):
        problems.append("redistribution_permitted must be YES, NO or UNKNOWN. UNKNOWN is "
                        "allowed and means the artifact may not leave this machine.")

    if not isinstance(d.hashes, dict) or not d.hashes:
        problems.append("hashes are not declared. Without them a later reader cannot tell "
                        "whether the surface they have is the surface that was scored.")

    return {
      "contract": CONTRACT,
      "surface": d.surface_id,
      "ok": not problems,
      "problems": problems,
      "face": d.face,
      "usable_where_a_single_face_is_required": d.face in SINGLE_FACE and not problems,
      "why_not_if_not": (None if d.face in SINGLE_FACE else
                         "face is %s. It is a real answer and is recorded as one; it is refused "
                         "wherever a single physical side is required, rather than being "
                         "rounded to whichever binary looked likelier." % d.face),
    }


def require(d: SurfaceDeclaration) -> dict:
    r = check(d)
    if not r["ok"]:
        raise SurfaceRefusal("surface %s refused:\n  - %s"
                             % (d.surface_id, "\n  - ".join(r["problems"])))
    return r


def require_single_face(d: SurfaceDeclaration, *, used_for: str) -> None:
    """Refuse COMPOSITE and UNKNOWN where one physical side is needed."""
    if d.face not in SINGLE_FACE:
        raise SurfaceRefusal(
          "%s requires a single physical face and this surface declares %s. It is not coerced "
          "to %s or %s: a surface that genuinely contains both faces, or whose face nobody "
          "established, does not acquire one by being needed."
          % (used_for, d.face, RECTO, VERSO))


def request_template() -> dict:
    """What to ask a producer for, in the words the contract will check."""
    return {
      "contract": CONTRACT,
      "required_fields": list(REQUIRED),
      "face": {"allowed": list(FACES),
               "composite_and_unknown_are_real_answers": True,
               "never_coerced": "a surface that contains both faces, or whose face nobody "
                                "established, is not assigned one because a pipeline wants it."},
      "face_established_by": list(ESTABLISHED_BY),
      "normal_orientation": list(NORMAL_ORIENTATIONS),
      "refused_generation_methods": REFUSED_GENERATION,
      "note_on_offset": "a verso surface produced by offsetting a recto surface along its "
                        "normals is refused. That objection is a COMMUNITY-REPORTED claim that "
                        "has not been reproduced, so the refusal is not 'offset is proven "
                        "wrong' -- it is that nobody has established the label, and a label "
                        "that cannot be established is not a label.",
    }
