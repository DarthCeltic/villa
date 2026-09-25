"""The local surface metric, and the warp that carries a neighbourhood into a flat frame."""
from __future__ import annotations

import numpy as np

class ReferencePitchUnknown(RuntimeError):
    """Raised rather than defaulting."""


#: Synthetic example entries. A real deployment declares each checkpoint's reference pitch from
#: that checkpoint's own training configuration; the operator's table is not part of the public
#: release.
KNOWN_REFERENCE_PITCH_UM = {
  "example/fragment-trained-checkpoint": (
      4.0, "synthetic example: a checkpoint trained on flat fragment volumes declares the "
           "fragment volume pitch from its training configuration."),
  "example/scroll-pitch-annotated": (
      8.0, "synthetic example: labels annotated directly at a scroll acquisition pitch declare "
           "that pitch, so the two references cannot be confused."),
}


def reference_pitch_um(key: str):
    """The training-reference pitch for a checkpoint, with its evidence."""
    if key not in KNOWN_REFERENCE_PITCH_UM:
        raise ReferencePitchUnknown(
            "no declared reference pitch for %r. Read it from the checkpoint's own training "
            "configuration and add it here -- do NOT pass a plausible number. Known: %s"
            % (key, sorted(KNOWN_REFERENCE_PITCH_UM)))
    return KNOWN_REFERENCE_PITCH_UM[key]


class PitchRoleMismatch(RuntimeError):
    """Two pitches were compared that answer different questions."""


PITCH_ROLES = ("TRAINING_REFERENCE", "SURFACE_GRID", "RESAMPLE")

#: A synthetic example run: the three roles carry three different figures.
EXAMPLE_PITCH_UM = {
  "TRAINING_REFERENCE": (4.0, "synthetic example: the pitch the checkpoint was trained at"),
  "SURFACE_GRID": (8.0, "synthetic example: grid_pitch_um recorded in the run receipt"),
  "RESAMPLE": (2.0, "synthetic example: plan.pitch_um in the run manifest"),
}


def pitch(role: str, table=None):
    """One pitch, by ROLE, with its evidence."""
    table = EXAMPLE_PITCH_UM if table is None else table
    if role not in PITCH_ROLES:
        raise PitchRoleMismatch(
            "%r is not a pitch role. Roles are %s -- a bare micron figure does not say which "
            "question it answers." % (role, list(PITCH_ROLES)))
    if role not in table:
        raise ReferencePitchUnknown(
            "no %s pitch declared for this run. Read it from the run's own receipt and record "
            "it -- do NOT pass a plausible number." % role)
    return table[role]


def pitch_ratio(numerator_role: str, denominator_role: str, table=None) -> float:
    """The ratio between two pitches, permitted only for a pair that means something."""
    allowed = {("TRAINING_REFERENCE", "SURFACE_GRID"), ("SURFACE_GRID", "TRAINING_REFERENCE")}
    if (numerator_role, denominator_role) not in allowed:
        raise PitchRoleMismatch(
            "%s / %s is not a meaningful ratio. The correction relates TRAINING_REFERENCE to "
            "SURFACE_GRID; RESAMPLE is the volume sampling pitch and answers a different "
            "question, so a ratio built from it is refused."
            % (numerator_role, denominator_role))
    return pitch(numerator_role, table)[0] / pitch(denominator_role, table)[0]


def first_fundamental_form(x, y, z):
    """E, F, G from central differences of the TIFXYZ components, plus a validity mask."""
    X = np.asarray(x, np.float64)
    Y = np.asarray(y, np.float64)
    Z = np.asarray(z, np.float64)

    def d_du(A):
        g = np.zeros_like(A)
        g[1:-1, :] = (A[2:, :] - A[:-2, :]) * 0.5
        return g

    def d_dv(A):
        g = np.zeros_like(A)
        g[:, 1:-1] = (A[:, 2:] - A[:, :-2]) * 0.5
        return g

    Pu = np.stack([d_du(X), d_du(Y), d_du(Z)], -1)
    Pv = np.stack([d_dv(X), d_dv(Y), d_dv(Z)], -1)
    E = (Pu * Pu).sum(-1)
    F = (Pu * Pv).sum(-1)
    G = (Pv * Pv).sum(-1)

    good = np.isfinite(E) & np.isfinite(F) & np.isfinite(G)
    good[0, :] = good[-1, :] = False
    good[:, 0] = good[:, -1] = False
    return E, F, G, good


def sym_sqrt_2x2(E, F, G):
    """Symmetric positive square root of [[E,F],[F,G]] in closed form, elementwise."""
    det = np.maximum(np.asarray(E) * np.asarray(G) - np.asarray(F) ** 2, 1e-12)
    s = np.sqrt(det)
    t = np.sqrt(np.maximum(np.asarray(E) + np.asarray(G) + 2.0 * s, 1e-12))
    return (np.asarray(E) + s) / t, np.asarray(F) / t, (np.asarray(G) + s) / t


def inv_sym_2x2(a, b, d):
    det = np.maximum(a * d - b * b, 1e-12)
    return d / det, -b / det, a / det


def flat_lattice_offsets(a, b, d, n: int, pitch_ref_phys: float):
    """Grid offsets that sample an n x n patch at FLAT reference geometry."""
    ia, ib, idd = inv_sym_2x2(float(a), float(b), float(d))
    r = (np.arange(n, dtype=np.float64) - (n // 2)) * float(pitch_ref_phys)
    p, q = np.meshgrid(r, r, indexing="ij")
    return ia * p + ib * q, ib * p + idd * q


def bilinear(A, uu, vv):
    """Sample a 2-D array at fractional coordinates."""
    A = np.asarray(A, np.float64)
    h, w = A.shape
    u0 = np.clip(np.floor(uu).astype(np.int64), 0, h - 2)
    v0 = np.clip(np.floor(vv).astype(np.int64), 0, w - 2)
    fu = np.clip(uu - u0, 0.0, 1.0)
    fv = np.clip(vv - v0, 0.0, 1.0)
    return (A[u0, v0] * (1 - fu) * (1 - fv) + A[u0 + 1, v0] * fu * (1 - fv)
            + A[u0, v0 + 1] * (1 - fu) * fv + A[u0 + 1, v0 + 1] * fu * fv)


def flatten_tifxyz_patch(x, y, z, cu: int, cv: int, n: int, E, F, G,
                         pitch_ref_um: float):
    """Warped TIFXYZ maps for one patch, ready to hand to `gather_along_normals`."""
    a, b, d = sym_sqrt_2x2(E[cu, cv], F[cu, cv], G[cu, cv])
    phys_per_grid = max(float(np.sqrt(np.sqrt(max(
        float(E[cu, cv]) * float(G[cu, cv]) - float(F[cu, cv]) ** 2, 1e-12)))), 1e-9)
    pitch_ref_grid = float(pitch_ref_um) / phys_per_grid
    du, dv = flat_lattice_offsets(a, b, d, n, float(pitch_ref_um))
    uu, vv = cu + du, cv + dv
    h, w = np.asarray(x).shape
    inside = (uu >= 0) & (uu <= h - 1) & (vv >= 0) & (vv <= w - 1)
    return (bilinear(x, uu, vv), bilinear(y, uu, vv), bilinear(z, uu, vv), inside,
            {"sqrtM": (float(a), float(b), float(d)),
             "pitch_ref_grid": pitch_ref_grid,
             "phys_per_grid": phys_per_grid})


def identity_patch(x, y, z, cu: int, cv: int, n: int):
    """The UNCORRECTED patch, cut the ordinary way."""
    r = np.arange(n, dtype=np.float64) - (n // 2)
    p, q = np.meshgrid(r, r, indexing="ij")
    uu, vv = cu + p, cv + q
    h, w = np.asarray(x).shape
    inside = (uu >= 0) & (uu <= h - 1) & (vv >= 0) & (vv <= w - 1)
    return bilinear(x, uu, vv), bilinear(y, uu, vv), bilinear(z, uu, vv), inside


def depth_scale_from_normal_dev(normal_dev_deg):
    """1/cos(theta): how much further through-plane one must sample when the sheet is tilted."""
    t = np.radians(np.clip(np.abs(np.asarray(normal_dev_deg, np.float64)), 0.0, 60.0))
    return 1.0 / np.maximum(np.cos(t), 0.5)


def flatten_metric_ratio(x, y, z, scale):
    """Per-cell principal stretch (s1 >= s2 >= 0) of a flatten OUTPUT against its own grid pitch."""
    E, F, G, good = first_fundamental_form(x, y, z)
    sx, sy = float(scale[0]), float(scale[1])
    if not (sx > 0.0 and sy > 0.0):
        raise ValueError(
            "scale must be two positive numbers -- it is the grid pitch the flatten output "
            "claims to hold (meta.json.scale), and a non-positive pitch leaves the isometry "
            "target undefined")
    Mp11 = E / (sx * sx)
    Mp12 = F / (sx * sy)
    Mp22 = G / (sy * sy)
    tr = Mp11 + Mp22
    disc = np.sqrt(np.maximum((Mp11 - Mp22) ** 2 + 4.0 * Mp12 ** 2, 0.0))
    s1_sq = np.maximum((tr + disc) / 2.0, 0.0)
    s2_sq = np.maximum((tr - disc) / 2.0, 0.0)
    s1 = np.full(s1_sq.shape, np.nan)
    s2 = np.full(s2_sq.shape, np.nan)
    s1[good] = np.sqrt(s1_sq[good])
    s2[good] = np.sqrt(s2_sq[good])
    return s1, s2, good


def flatten_distortion_summary(x, y, z, scale, *, degenerate_stretch: float = 1e-3) -> dict:
    """A JSON-safe distortion report for one flatten OUTPUT tifxyz."""
    s1, s2, good = flatten_metric_ratio(x, y, z, scale)
    degenerate = good & ((s1 < degenerate_stretch) | (s2 < degenerate_stretch))
    usable = good & ~degenerate
    n_good = int(np.count_nonzero(good))
    n_usable = int(np.count_nonzero(usable))
    base = {
        "schema": "argus-flatten-distortion-v1",
        "definition": ("per-cell principal stretch of the flatten OUTPUT's own (row, col) grid, "
                       "at its declared meta.json scale, against the physical 3D distance it "
                       "actually holds. Stretch (1, 1) at a cell is a perfect local isometry."),
        "n_grid_cells": int(good.size),
        "n_measurable_cells": n_good,
        "n_usable_cells": n_usable,
        "degenerate_boundary_cells": int(np.count_nonzero(degenerate)),
        "degenerate_stretch_threshold": float(degenerate_stretch),
    }
    if n_usable == 0:
        base["status"] = "NO_USABLE_CELLS"
        base["note"] = ("every measurable cell had a principal stretch below %.1e; no "
                        "distortion reading is available" % degenerate_stretch)
        return base
    s1u, s2u = s1[usable], s2[usable]
    sd = s1u ** 2 + s2u ** 2 + 1.0 / (s1u ** 2) + 1.0 / (s2u ** 2)
    area_ratio = s1u * s2u
    shear_ratio = s1u / s2u

    def _pct(a, q):
        return float(np.percentile(a, q))

    base["status"] = "MEASURED"
    base["symmetric_dirichlet_energy"] = {
        "minimum_possible": 4.0,
        "median": _pct(sd, 50), "p90": _pct(sd, 90), "p99": _pct(sd, 99),
        "max": float(np.max(sd)),
    }
    base["area_ratio"] = {
        "median": _pct(area_ratio, 50), "min": float(np.min(area_ratio)),
        "max": float(np.max(area_ratio)),
    }
    base["shear_ratio"] = {
        "median": _pct(shear_ratio, 50), "p90": _pct(shear_ratio, 90),
        "max": float(np.max(shear_ratio)),
    }
    return base


def metric_self_consistency(x, y, z, tol: float = 1e-9) -> dict:
    """Check that the closed-form square root squares back to the metric, rather than assume it."""
    E, F, G, good = first_fundamental_form(x, y, z)
    det = E * G - F * F
    area = np.sqrt(np.maximum(det, 0.0))
    a, b, d = sym_sqrt_2x2(E, F, G)
    m00 = a * a + b * b
    m01 = a * b + b * d
    m11 = b * b + d * d
    err = max(float(np.abs(m00 - E)[good].max() if good.any() else 0.0),
              float(np.abs(m01 - F)[good].max() if good.any() else 0.0),
              float(np.abs(m11 - G)[good].max() if good.any() else 0.0))
    det_err = float(np.abs((a * d - b * b) - area)[good].max() if good.any() else 0.0)
    return {"sqrtM_squares_back_to_M": err,
            "det_sqrtM_equals_area_scale": det_err,
            "agrees": bool(err < tol and det_err < tol)}
