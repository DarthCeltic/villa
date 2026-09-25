"""PitchResolver: recover a render's physical pitch from evidence, in a declared order."""
from __future__ import annotations

import dataclasses
import re

VERIFIED, INFERRED, UNKNOWN = "VERIFIED", "INFERRED", "UNKNOWN"

METHODS = ("OME_TRANSFORM", "SEGMENT_META", "SOURCE_IDENTITY", "PYRAMID_RELATION",
           "TIFXYZ_GEOMETRY", "NONE")

STEP_TOLERANCE = 0.10

RATIO_TOLERANCE = 0.01

CROP_CELLS = 2.0

AXIS_AGREEMENT_FLOOR = 5e-4

PITCH_ISOTROPY_TOLERANCE = 0.01

CANVAS_RESIDUAL_TOLERANCE = 0.001

CANVAS_PROVENANCE_UNRESOLVED = "CANVAS_PROVENANCE_UNRESOLVED"


@dataclasses.dataclass
class PitchEvidence:
    method: str
    status: str
    pitch_um_yx: tuple | None = None
    pitch_voxels_yx: tuple | None = None
    voxel_um: float | None = None
    detail: dict = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["pitch_um_yx"] = list(self.pitch_um_yx) if self.pitch_um_yx else None
        d["pitch_voxels_yx"] = list(self.pitch_voxels_yx) if self.pitch_voxels_yx else None
        return d


def _ome_scale(ms0: dict, level: str):
    """The first 3+-element `scale` transform of one level, or None if the level/scale is absent."""
    for ds in ms0.get("datasets", []):
        if str(ds.get("path")) != str(level):
            continue
        for t in ds.get("coordinateTransformations", []):
            s = t.get("scale")
            if s and len(s) >= 3:
                return s
    return None


def from_ome(zattrs: dict, level: str = "0", level0_pitch_um=None) -> PitchEvidence:
    """The store's own coordinateTransformations."""
    ms = (zattrs or {}).get("multiscales")
    if not ms:
        return PitchEvidence("OME_TRANSFORM", UNKNOWN, detail={"why": "no multiscales key"})
    s = _ome_scale(ms[0], level)
    if s is None:
        return PitchEvidence("OME_TRANSFORM", UNKNOWN, detail={"why": "level %s absent" % level})
    s0 = _ome_scale(ms[0], "0")
    relative = s0 is not None and all(float(v) == 1.0 for v in s0)
    if all(float(v) == 1.0 for v in s) and not (relative and level0_pitch_um):
        return PitchEvidence(
            "OME_TRANSFORM", UNKNOWN, detail={
                "scale": s,
                "why": ("a scale of exactly [1,1,1] is the absence of a declaration, "
                        "not a pitch of one micron")})
    if not relative:
        units = [a.get("unit") for a in ms[0].get("axes", [])]
        return PitchEvidence("OME_TRANSFORM", VERIFIED,
                             pitch_um_yx=(float(s[-2]), float(s[-1])),
                             detail={"scale": s, "axes_units": units, "level": level})
    factor = (float(s[-2]), float(s[-1]))
    base = level0_pitch_um
    if base is not None and not isinstance(base, (tuple, list)):
        base = (float(base), float(base))
    if not base:
        return PitchEvidence(
            "OME_TRANSFORM", UNKNOWN, detail={
                "scale": s, "relative_factor_yx": list(factor), "level": level,
                "why": ("level 0 declares scale [1,1,1], so every level's scale is a relative "
                        "multiscale factor, not a pitch; the level pitch is level-0 pitch times "
                        "that factor and no level-0 pitch was supplied")})
    return PitchEvidence(
        "OME_TRANSFORM", INFERRED,
        pitch_um_yx=(float(base[0]) * factor[0], float(base[1]) * factor[1]),
        detail={"scale": s, "relative_factor_yx": list(factor), "level": level,
                "level0_pitch_um_yx": list(base),
                "why": ("level-0 pitch came from an outside attested/PROVEN source; the OME "
                        "block supplied only the relative factor")})


def from_segment_meta(meta: dict, render_shape_yx=None) -> PitchEvidence:
    """meta.json."""
    if not meta or meta.get("format") != "tifxyz":
        return PitchEvidence("SEGMENT_META", UNKNOWN,
                             detail={"why": "no tifxyz meta.json"})
    sc = meta.get("scale")
    if not sc:
        return PitchEvidence("SEGMENT_META", UNKNOWN, detail={"why": "no scale in meta.json"})
    d = {"tifxyz_scale": sc, "voxels_per_grid_step": (1.0 / float(sc[0])) if sc[0] else None,
         "note": ("this is a sampling density in voxels, not a pitch in microns")}
    if render_shape_yx:
        d["render_shape_yx"] = list(render_shape_yx)
    return PitchEvidence("SEGMENT_META", UNKNOWN, detail=d)


def from_source_identity(digest: str, known: dict) -> PitchEvidence:
    """Same bytes as a store whose pitch is declared, so the same pitch."""
    hit = known.get(digest)
    if not hit:
        return PitchEvidence("SOURCE_IDENTITY", UNKNOWN,
                             detail={"why": "digest matches no store with a declared pitch"})
    return PitchEvidence("SOURCE_IDENTITY", VERIFIED, pitch_um_yx=tuple(hit["pitch_um_yx"]),
                         detail={"matched": hit.get("name"), "digest": digest})


def from_pyramid_relation(ratio: float, anchor_pitch_um_yx, controls_passed: bool
                          ) -> PitchEvidence:
    """A verified reduction relation gives a RATIO; it needs an anchor to become a pitch."""
    if not controls_passed:
        return PitchEvidence("PYRAMID_RELATION", UNKNOWN,
                             detail={"why": ("the reduction relation was not verified against "
                                             "misalignment controls, so the ratio is not "
                                             "established")})
    if not anchor_pitch_um_yx:
        return PitchEvidence("PYRAMID_RELATION", UNKNOWN,
                             detail={"ratio": ratio,
                                     "why": "a ratio is not a pitch without an anchor"})
    return PitchEvidence("PYRAMID_RELATION", VERIFIED,
                         pitch_um_yx=(anchor_pitch_um_yx[0] * ratio,
                                      anchor_pitch_um_yx[1] * ratio),
                         detail={"ratio": ratio, "anchor_um_yx": list(anchor_pitch_um_yx)})


def tifxyz_grid_step(x, y, z, valid=None, max_step: float = 50.0):
    """Median distance between adjacent surface grid points, in source-volume voxels."""
    import numpy as np

    P = np.stack([np.asarray(x), np.asarray(y), np.asarray(z)], -1).astype(np.float64)
    ok = (np.asarray(valid) if valid is not None
          else ~((P[..., 0] == 0) & (P[..., 1] == 0) & (P[..., 2] == 0)))
    out = {}
    for axis, name in ((0, "y"), (1, "x")):
        d = np.linalg.norm(np.diff(P, axis=axis), axis=-1)
        m = (ok[1:, :] & ok[:-1, :]) if axis == 0 else (ok[:, 1:] & ok[:, :-1])
        v = d[m]
        v = v[(v > 0) & (v < max_step)]
        out[name] = (float(np.median(v)) if v.size else None, int(v.size))
    return out


def from_tifxyz(x, y, z, *, voxel_um: float | None, render_shape_yx, grid_shape_yx,
                meta_scale: float | None = None, valid=None) -> PitchEvidence:
    """Measure the grid pitch, then divide by how much denser the render is than the grid."""
    step = tifxyz_grid_step(x, y, z, valid)
    sy, ny = step["y"]
    sx, nx = step["x"]
    if sy is None or sx is None:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN,
                             detail={"why": "no valid adjacent grid pairs"})
    ry = render_shape_yx[0] / grid_shape_yx[0]
    rx = render_shape_yx[1] / grid_shape_yx[1]
    iy, ix = round(ry), round(rx)
    if iy < 1 or ix < 1:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN,
                             detail={"render_over_grid": [ry, rx],
                                     "why": "the render is coarser than the surface grid"})
    axis_dev = abs(ry - rx) / max(ry, rx)
    axis_allowed = max(AXIS_AGREEMENT_FLOOR,
                       CROP_CELLS * (1.0 / grid_shape_yx[0] + 1.0 / grid_shape_yx[1]))
    if axis_dev > axis_allowed:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN,
                             detail={"render_over_grid": [ry, rx],
                                     "axis_disagreement": axis_dev,
                                     "axis_allowed_by_cropping": axis_allowed,
                                     "crop_cells": CROP_CELLS,
                                     "grid_shape_yx": list(grid_shape_yx),
                                     "canvas_provenance": CANVAS_PROVENANCE_UNRESOLVED,
                                     "why": ("the render scale implied by height and by width "
                                             "differ by %.3f%%, more than the %.3f%% that "
                                             "cropping this %dx%d grid could explain. An "
                                             "isotropic render cannot do that, so this canvas "
                                             "is not reproducible from this mesh by this "
                                             "formula and its provenance is unresolved"
                                             % (100 * axis_dev, 100 * axis_allowed,
                                                grid_shape_yx[0], grid_shape_yx[1]))})
    dev = max(abs(ry - iy) / iy, abs(rx - ix) / ix)
    if dev > RATIO_TOLERANCE:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN,
                             detail={"render_over_grid": [ry, rx],
                                     "nearest_integer_yx": [iy, ix],
                                     "relative_deviation": dev,
                                     "tolerance": RATIO_TOLERANCE,
                                     "canvas_provenance": CANVAS_PROVENANCE_UNRESOLVED,
                                     "why": ("the render/grid ratio is %.3f%% from the nearest "
                                             "whole number, too far to be boundary cropping"
                                             % (100 * dev))})
    detail = {"axis_disagreement": axis_dev, "axis_allowed_by_cropping": axis_allowed,
              "grid_step_voxels_yx": [sy, sx], "n_pairs_yx": [ny, nx],
              "render_over_grid_yx": [ry, rx], "integer_upsample_yx": [iy, ix],
              "ratio_relative_deviation": dev,
              "grid_shape_yx": list(grid_shape_yx),
              "render_shape_yx": list(render_shape_yx)}
    if meta_scale:
        expected = 1.0 / float(meta_scale)
        detail["meta_expected_step_voxels"] = expected
        rel = max(abs(sy - expected), abs(sx - expected)) / expected
        detail["meta_agreement_rel_error"] = rel
        if rel > STEP_TOLERANCE:
            return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN, detail=dict(
                detail, why=("the measured grid step disagrees with meta.json's own scale by "
                             "%.1f%%, so one of the two files is not describing this grid"
                             % (100 * rel))))
    pv = (sy / iy, sx / ix)
    detail["render_pitch_voxels_yx"] = list(pv)
    iso = abs(pv[0] - pv[1]) / max(pv[0], pv[1])
    detail["pitch_isotropy_deviation"] = iso
    if iso > PITCH_ISOTROPY_TOLERANCE:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN, detail=dict(
            detail, canvas_provenance=CANVAS_PROVENANCE_UNRESOLVED,
            pitch_isotropy_tolerance=PITCH_ISOTROPY_TOLERANCE,
            why=("the pitch implied by height and by width differ by %.2f%%. The grid steps "
                 "themselves are anisotropic, so this is not one pitch and returning it as "
                 "one would be a number nobody could act on" % (100 * iso))))
    if voxel_um is None:
        return PitchEvidence("TIFXYZ_GEOMETRY", UNKNOWN, pitch_voxels_yx=pv, detail=dict(
            detail, why=("the pitch is measured in voxels; converting to microns needs the "
                         "scan's voxel size, which is separate evidence and was not supplied")))
    return PitchEvidence("TIFXYZ_GEOMETRY", INFERRED,
                         pitch_um_yx=(pv[0] * voxel_um, pv[1] * voxel_um),
                         pitch_voxels_yx=pv, voxel_um=voxel_um, detail=detail)


def canvas_from_zattrs(zattrs: dict) -> dict:
    """The published store's own canvas."""
    ms = (zattrs or {}).get("multiscales")
    if isinstance(ms, list) and ms:
        for ds in (ms[0].get("datasets") or []):
            shape = ds.get("canvas_size") or ds.get("shape")
            if shape:
                return {"status": "AUTHORITATIVE", "rank": "a", "canvas_yx": list(shape)[-2:],
                        "source": ".zattrs multiscales datasets"}
    for k in ("canvas_size", "shape"):
        if (zattrs or {}).get(k):
            return {"status": "AUTHORITATIVE", "rank": "a",
                    "canvas_yx": list(zattrs[k])[-2:], "source": ".zattrs %s" % k}
    return {"status": "UNKNOWN", "rank": "a",
            "why": ("the store declares no canvas. That is the condition under which the "
                    "canvas is unresolved, not a licence to compute one")}


def canvas_authority(zattrs: dict, mesh_canvas_yx=None, *,
                     tolerance: float = CANVAS_RESIDUAL_TOLERANCE) -> dict:
    """Resolve a canvas under the frozen authority order."""
    auth = canvas_from_zattrs(zattrs)
    if auth["status"] != "AUTHORITATIVE":
        if mesh_canvas_yx is None:
            return {"status": CANVAS_PROVENANCE_UNRESOLVED, "authority": auth,
                    "why": "neither a declared canvas nor a mesh-derived one"}
        return {"status": CANVAS_PROVENANCE_UNRESOLVED, "authority": auth,
                "mesh_canvas_yx": list(mesh_canvas_yx),
                "why": ("only a mesh-derived canvas is available, and a mesh-derived canvas "
                        "is not self-authorising")}
    if mesh_canvas_yx is None:
        return {"status": "RESOLVED", "canvas_yx": auth["canvas_yx"], "authority": auth,
                "agreement": None,
                "why": "the store declares its canvas and nothing contradicts it"}
    ay, ax = auth["canvas_yx"]
    my, mx = mesh_canvas_yx
    ry = abs(my - ay) / max(ay, 1)
    rx = abs(mx - ax) / max(ax, 1)
    agree = max(ry, rx) <= tolerance
    return {"status": "RESOLVED" if agree else CANVAS_PROVENANCE_UNRESOLVED,
            "canvas_yx": auth["canvas_yx"],
            "authority": auth, "mesh_canvas_yx": [my, mx],
            "residual_px_yx": [my - ay, mx - ax],
            "relative_residual_yx": [ry, rx], "tolerance": tolerance,
            "agreement": agree,
            "never_adjusted": ("the mesh-derived canvas is reported, never applied. Nothing "
                               "here resizes, crops, pads or forces an isotropic scale"),
            "why": ("the mesh reproduces the declared canvas" if agree else
                    "the mesh-derived canvas differs from the declared one by %d x %d px; "
                    "the declaration stands and the provenance of the difference is "
                    "unresolved" % (my - ay, mx - ax))}


def render_canvas(stored: int, render_scale: float, scale32) -> int:
    """The renderer's own formula: max(1, lround(stored * (render_scale / float32(scale))))."""
    import numpy as np

    v = float(stored) * (float(render_scale) / float(np.float32(scale32)))
    frac = v - int(v)
    n = int(v) + (1 if frac >= 0.5 else 0) if v >= 0 else int(v) - (1 if -frac >= 0.5 else 0)
    return max(1, n)


def identify_volume_by_bbox(bbox, candidates: dict) -> dict:
    """Which source volume is this surface cut from?"""
    if not bbox or len(bbox) != 2:
        return {"status": "UNKNOWN", "why": "no bbox in meta.json"}
    lo, hi = bbox
    fits = []
    for name, shape in sorted(candidates.items()):
        if not shape or len(shape) < 3:
            continue
        zyx_hi = [hi[2], hi[1], hi[0]]
        zyx_lo = [lo[2], lo[1], lo[0]]
        if all(h <= s for h, s in zip(zyx_hi, shape)) and all(v >= 0 for v in zyx_lo):
            fits.append(name)
    if not fits:
        return {"status": "UNKNOWN", "candidates": sorted(candidates),
                "why": ("no declared volume is large enough to contain this surface's bbox, "
                        "so the source is not among the candidates offered")}
    if len(fits) > 1:
        return {"status": "AMBIGUOUS", "survivors": fits,
                "why": ("%d declared volumes contain the bbox; the segment does not name "
                        "which one it was cut from" % len(fits))}
    return {"status": "IDENTIFIED", "volume": fits[0],
            "why": "exactly one declared volume is large enough to contain the surface"}


def resolve(*evidence: PitchEvidence) -> dict:
    """First conclusive answer in hierarchy order."""
    order = {m: i for i, m in enumerate(METHODS)}
    tried = sorted(evidence, key=lambda e: order.get(e.method, 99))
    for e in tried:
        if e.status in (VERIFIED, INFERRED) and e.pitch_um_yx:
            return {"status": e.status, "method": e.method,
                    "pitch_um_yx": list(e.pitch_um_yx),
                    "pitch_voxels_yx": list(e.pitch_voxels_yx) if e.pitch_voxels_yx else None,
                    "evidence": [x.as_dict() for x in tried],
                    "basis": ("resolved by %s; every stronger method was tried and did not "
                              "answer" % e.method)}
    return {"status": UNKNOWN, "method": "NONE", "pitch_um_yx": None,
            "pitch_voxels_yx": next((list(e.pitch_voxels_yx) for e in tried
                                     if e.pitch_voxels_yx), None),
            "evidence": [x.as_dict() for x in tried],
            "basis": "no method in the hierarchy produced a pitch; it stays unknown"}


def refuse_filename_inference(name: str) -> dict:
    """A name is a hint about intent, never a measurement."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*um", name or "", re.I)
    return {"filename_hint_um": float(m.group(1)) if m else None,
            "used": False,
            "why": ("a folder name can disagree with the declared pitch of the volume beside it; "
                    "filename inference is not in the hierarchy")}
