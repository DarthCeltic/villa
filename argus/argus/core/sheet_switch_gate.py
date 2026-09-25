"""A LOCALIZING, automatic refusal gate for the mid-trace geometry signature a sheet switch leaves in the fields ARGUS actually computes today."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from argus.core.contracts import Refusal
from argus.core import windings as W
from argus.core.coordinate_map import INVALID_SENTINEL
from argus.core.normal_orientation import validity_from_tifxyz

SCHEMA = "argus-sheet-switch-gate-v1"


def landlocked_components(valid: np.ndarray, comp: np.ndarray, n_comp: int) -> list[dict]:
    """Component ids whose members never touch a hole or the grid's outer edge."""
    h, w = valid.shape
    out = []
    for cid in range(n_comp):
        ys, xs = np.nonzero(comp == cid)
        if ys.size == 0:
            continue
        touches_frame = bool((ys == 0).any() or (ys == h - 1).any()
                             or (xs == 0).any() or (xs == w - 1).any())
        touches_hole = False
        if not touches_frame:
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if (~valid[ys + dy, xs + dx]).any():
                    touches_hole = True
                    break
        if not touches_frame and not touches_hole:
            out.append({
                "component": int(cid),
                "n_points": int(ys.size),
                "grid_bbox": {"y0": int(ys.min()), "y1": int(ys.max()) + 1,
                             "x0": int(xs.min()), "x1": int(xs.max()) + 1},
            })
    return out


def evaluate(valid: np.ndarray, Z: np.ndarray, Y: np.ndarray, X: np.ndarray) -> dict:
    """The anomaly evidence for one already-loaded mesh."""
    gj = W.grid_jumps(valid, Z, Y, X)
    if gj is None:
        raise Refusal("GEOMETRY", "no adjacent valid cells to measure")
    jump_r, jump_d, median_step = gj
    comp, n_comp = W._components(valid, jump_r, jump_d)
    landlocked = landlocked_components(valid, comp, n_comp)
    return {
        "schema": SCHEMA,
        "grid_shape": list(valid.shape),
        "n_components": n_comp,
        "median_step_vox": median_step,
        "landlocked_components": landlocked,
        "verdict": "REFUSE" if landlocked else "PASS",
        "signature": ("a component fully surrounded by a DIFFERENT component, never by a hole "
                      "or the grid's outer edge -- see this module's docstring for exactly what "
                      "this can and cannot detect"),
        "fundamental_limit": ("measures 3-D distance and normal continuity only; a switch that "
                              "stays close in 3-D space while moving one winding around the "
                              "umbilicus is invisible to this signature. Needs a per-vertex "
                              "winding-number field ARGUS does not currently compute -- see "
                              "this module's docstring."),
    }


def gate(mesh_dir, *, coord_scale: float = 1.0, sentinel=INVALID_SENTINEL) -> dict:
    """Read one TIFXYZ mesh and refuse automatically on a landlocked-component anomaly."""
    d = Path(mesh_dir)
    X, Y, Z = W._read_tifxyz(d, coord_scale)
    valid = validity_from_tifxyz(Z, Y, X, sentinel=sentinel, require_finite=True)
    if not valid.any():
        raise Refusal("GEOMETRY", "the mesh has no valid cells")
    result = evaluate(valid, Z, Y, X)
    if result["verdict"] == "REFUSE":
        names = ", ".join("component %d (%d cells, bbox %s)"
                          % (c["component"], c["n_points"], c["grid_bbox"])
                          for c in result["landlocked_components"])
        raise Refusal(
            "GEOMETRY",
            "%d winding component(s) are landlocked mid-trace, surrounded entirely by a "
            "different component and never by a hole or the mesh's outer edge -- the local-"
            "geometry signature of a mid-trace switch: %s. Repair the named region and re-run "
            "this gate to verify" % (len(result["landlocked_components"]), names),
            result)
    return result
