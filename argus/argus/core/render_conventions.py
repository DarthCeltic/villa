"""Named surface-render conventions."""
from __future__ import annotations

SHIPPED = "ARGUS_PHASE0_TRILINEAR"
PIXEL_CENTRE = "PIXEL_CENTRE_PHASE_0_5_TRILINEAR"
DEFAULT = SHIPPED

CONVENTIONS = {
    SHIPPED: {"phase": (0.0, 0.0), "interpolation": "trilinear", "label": "ARGUS phase 0 (shipped)", "is_default": True,
              "meaning": "fine pixel o maps to grid coordinate o/F"},
    PIXEL_CENTRE: {"phase": (0.5, 0.5), "interpolation": "trilinear", "label": "Pixel-centre convention (new, Villa-matching)", "is_default": False,
                   "meaning": "fine pixel o maps to grid coordinate (o+0.5)/F, as Villa's vc_render_tifxyz does"},
}


class UnknownConvention(ValueError):
    pass


def upsample_gridstep(A, ok, F: int, convention: str | None = None):
    """Upsample a coarse mesh-coordinate array by exactly F fine samples per coarse step, under a NAMED render convention."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    py, px = phase_for(convention)
    H0, W0 = A.shape
    H, W = H0 * F, W0 * F
    oy = (np.arange(H, dtype=np.float64) + float(py)) / F
    ox = (np.arange(W, dtype=np.float64) + float(px)) / F
    OY, OX = np.meshgrid(oy, ox, indexing="ij")
    src = np.where(ok, A, np.nan).astype(np.float64)
    return map_coordinates(src, [OY, OX], order=1, mode="nearest")


def phase_for(convention: str | None) -> tuple:
    """The (rows, cols) phase for a NAMED convention."""
    name = convention or DEFAULT
    if name not in CONVENTIONS:
        raise UnknownConvention("render convention %r is not one of %s" % (name, sorted(CONVENTIONS)))
    return CONVENTIONS[name]["phase"]


def registry(*, voxel_um: float | None = None) -> list:
    rows = []
    for name, c in CONVENTIONS.items():
        rows.append({"id": name, "label": c["label"], "phase": list(c["phase"]), "interpolation": c["interpolation"], "meaning": c["meaning"],
                     "is_default": c["is_default"], "changes_existing_renders": False,
                     "offset_from_shipped_um": None if (name == SHIPPED or voxel_um is None) else round(0.5 * voxel_um, 2)})
    return rows
