"""Geometric, topological and holdout controls for a fitted spiral surface."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from argus.core import surface_metric as SM
from argus.core import topology_metric as TM

RULE_ID = "argus-spiral-fit-controls-v1"


class SpiralControlRefusal(ValueError):
    """Raised instead of returning a control verdict computed from insufficient/ambiguous input."""




def mesh_summary(x, y, z, valid, *, expected_step: float,
                  centre_at: Callable[[np.ndarray], tuple] | None = None) -> dict[str, Any]:
    """Per-mesh geometric statistics: edge lengths, quad coverage, and (if `centre_at` is given) radius-from-umbilicus percentiles."""
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    z = np.asarray(z, np.float64)
    valid = np.asarray(valid, bool)
    if x.shape != y.shape or x.shape != z.shape or x.shape != valid.shape:
        raise SpiralControlRefusal("x/y/z/valid shapes disagree: %s %s %s %s"
                                   % (x.shape, y.shape, z.shape, valid.shape))
    if valid.sum() == 0:
        raise SpiralControlRefusal("mesh has zero valid vertices")

    p = np.stack([z, y, x], axis=-1)
    edges_by_axis: dict[int, np.ndarray] = {}
    for axis in (0, 1):
        a = np.take(p, range(p.shape[axis] - 1), axis=axis)
        b = np.take(p, range(1, p.shape[axis]), axis=axis)
        va = np.take(valid, range(valid.shape[axis] - 1), axis=axis)
        vb = np.take(valid, range(1, valid.shape[axis]), axis=axis)
        d = np.linalg.norm(b - a, axis=-1)
        edges_by_axis[axis] = d[va & vb]
    edges = np.concatenate([edges_by_axis[0], edges_by_axis[1]])

    q = valid[:-1, :-1] & valid[1:, :-1] & valid[:-1, 1:] & valid[1:, 1:]
    q_count, q_total = int(q.sum()), int(q.size)

    out: dict[str, Any] = {
        "shape": list(x.shape),
        "valid_vertices": int(valid.sum()),
        "valid_fraction": float(valid.mean()),
        "valid_quads": q_count,
        "valid_quad_fraction": float(q_count / q_total) if q_total else 0.0,
        "edge_p50": float(np.percentile(edges, 50)) if edges.size else float("nan"),
        "edge_p95": float(np.percentile(edges, 95)) if edges.size else float("nan"),
        "edge_p99": float(np.percentile(edges, 99)) if edges.size else float("nan"),
        "edge_over_3x_expected": int((edges > 3 * expected_step).sum()),
        "edge_p50_axis0": (float(np.percentile(edges_by_axis[0], 50))
                           if edges_by_axis[0].size else float("nan")),
        "edge_p50_axis1": (float(np.percentile(edges_by_axis[1], 50))
                           if edges_by_axis[1].size else float("nan")),
    }
    if centre_at is not None:
        cy, cx = centre_at(z[valid])
        radius = np.hypot(y[valid] - cy, x[valid] - cx)
        out["radius_p05"] = float(np.percentile(radius, 5))
        out["radius_p50"] = float(np.percentile(radius, 50))
        out["radius_p95"] = float(np.percentile(radius, 95))
        out["radius_p01"] = float(np.percentile(radius, 1))
        out["radius_p99"] = float(np.percentile(radius, 99))
    return out




def geometric_control(meshes: dict[str, dict[str, np.ndarray]], *, expected_step: float,
                       centre_at: Callable[[np.ndarray], tuple] | None = None,
                       quad_frac_min: float = 0.50, edge_p95_tol_ratio: float = 2.0,
                       dirichlet_p90_tol: float = 6.0,
                       degenerate_fraction_tol: float = 0.05,
                       degenerate_stretch: float = 1e-3) -> dict[str, Any]:
    """Geometric self-consistency of every mesh: local smoothness/quad coverage PLUS a real isometry measurement, via..."""
    if not meshes:
        raise SpiralControlRefusal("no meshes given")
    per_mesh: dict[str, Any] = {}
    all_pass = True
    for name, m in meshes.items():
        summary = mesh_summary(m["x"], m["y"], m["z"], m["valid"],
                                expected_step=expected_step, centre_at=centre_at)
        self_scale = (max(summary["edge_p50_axis0"], 1e-6),
                     max(summary["edge_p50_axis1"], 1e-6))
        distortion = SM.flatten_distortion_summary(
            m["x"], m["y"], m["z"], self_scale, degenerate_stretch=degenerate_stretch)
        local_pass = (
            summary["valid_quad_fraction"] > quad_frac_min
            and summary["edge_over_3x_expected"] == 0
            and summary["edge_p95"] < edge_p95_tol_ratio * expected_step
        )
        if distortion["status"] == "MEASURED":
            degenerate_fraction = (distortion["degenerate_boundary_cells"]
                                   / distortion["n_measurable_cells"])
            isometry_pass = (distortion["symmetric_dirichlet_energy"]["p90"] <= dirichlet_p90_tol
                             and degenerate_fraction <= degenerate_fraction_tol)
        else:
            degenerate_fraction = 1.0
            isometry_pass = False
        mesh_pass = bool(local_pass and isometry_pass)
        all_pass = all_pass and mesh_pass
        per_mesh[name] = {
            "summary": summary,
            "self_referential_scale": list(self_scale),
            "distortion": distortion,
            "degenerate_fraction": float(degenerate_fraction),
            "local_geometry_pass": bool(local_pass),
            "isometry_pass": bool(isometry_pass),
            "pass": mesh_pass,
        }
    n_fail = sum(1 for v in per_mesh.values() if not v["pass"])
    return {
        "schema": "argus-spiral-geometric-control-v1",
        "rule_id": RULE_ID,
        "expected_step": float(expected_step),
        "thresholds": {
            "quad_frac_min": quad_frac_min,
            "edge_p95_tol_ratio": edge_p95_tol_ratio,
            "dirichlet_p90_tol": dirichlet_p90_tol,
            "degenerate_fraction_tol": degenerate_fraction_tol,
            "degenerate_stretch": degenerate_stretch,
        },
        "n_meshes": len(meshes),
        "n_fail": n_fail,
        "per_mesh": per_mesh,
        "pass": bool(all_pass),
    }




def topological_control(windings: dict[int, dict[str, np.ndarray]], *,
                         centre_at: Callable[[np.ndarray], tuple],
                         connectivity_full: bool = True,
                         crossing_margin: float = 0.0,
                         n_z_bins: int = 8, n_theta_bins: int = 8,
                         min_points_per_bin: int = 3, min_shared_bins: int = 3,
                         crossing_violation_tol: float = 0.10) -> dict[str, Any]:
    """Connectedness (does each winding sheet form one connected patch, not a fragment/merge) and non-crossing / correct winding order (do nested sheets stay nested, in index order, without..."""
    if len(windings) < 2:
        raise SpiralControlRefusal(
            "need at least 2 windings to evaluate a non-crossing / winding-order control, got %d"
            % len(windings))

    connectedness: dict[int, Any] = {}
    radius_band: dict[int, tuple[float, float]] = {}
    points: dict[int, dict[str, np.ndarray]] = {}
    all_z, all_theta = [], []
    for idx, m in sorted(windings.items()):
        valid = np.asarray(m["valid"], bool)
        if valid.sum() == 0:
            raise SpiralControlRefusal("winding %d has zero valid vertices" % idx)
        _, n_components = TM.label_components(valid, connectivity_full=connectivity_full)
        connectedness[idx] = {"n_components": n_components, "pass": n_components == 1}
        x = np.asarray(m["x"], np.float64)[valid]
        y = np.asarray(m["y"], np.float64)[valid]
        z = np.asarray(m["z"], np.float64)[valid]
        cy, cx = centre_at(z)
        radius = np.hypot(y - cy, x - cx)
        theta = np.arctan2(y - cy, x - cx)
        radius_band[idx] = (float(np.percentile(radius, 1)), float(np.percentile(radius, 99)))
        points[idx] = {"z": z, "theta": theta, "r": radius}
        all_z.append(z)
        all_theta.append(theta)

    ordered_idx = sorted(radius_band)
    midpoints = [(radius_band[i][0] + radius_band[i][1]) / 2.0 for i in ordered_idx]
    order_pass = all(midpoints[i] < midpoints[i + 1] for i in range(len(midpoints) - 1))

    z_all = np.concatenate(all_z)
    theta_all = np.concatenate(all_theta)
    z_edges = np.linspace(z_all.min(), z_all.max(), n_z_bins + 1)
    theta_edges = np.linspace(theta_all.min(), theta_all.max(), n_theta_bins + 1)

    def _binned_median_radius(idx: int) -> dict[int, tuple[float, int]]:
        p = points[idx]
        zb = np.clip(np.digitize(p["z"], z_edges[1:-1]), 0, n_z_bins - 1)
        tb = np.clip(np.digitize(p["theta"], theta_edges[1:-1]), 0, n_theta_bins - 1)
        bin_id = zb * n_theta_bins + tb
        out: dict[int, tuple[float, int]] = {}
        for b in np.unique(bin_id):
            mask = bin_id == b
            n = int(mask.sum())
            if n >= min_points_per_bin:
                out[int(b)] = (float(np.median(p["r"][mask])), n)
        return out

    binned = {idx: _binned_median_radius(idx) for idx in ordered_idx}

    crossings = []
    insufficient_overlap = []
    for i in range(len(ordered_idx) - 1):
        a, b = ordered_idx[i], ordered_idx[i + 1]
        shared = sorted(set(binned[a]) & set(binned[b]))
        if len(shared) < min_shared_bins:
            insufficient_overlap.append({"inner": a, "outer": b, "n_shared_bins": len(shared)})
            continue
        violations = [bid for bid in shared
                     if binned[a][bid][0] + crossing_margin > binned[b][bid][0]]
        violation_fraction = len(violations) / len(shared)
        if violation_fraction > crossing_violation_tol:
            crossings.append({"inner": a, "outer": b, "n_shared_bins": len(shared),
                              "n_violations": len(violations),
                              "violation_fraction": violation_fraction})
    non_crossing_pass = len(crossings) == 0

    connected_pass = all(v["pass"] for v in connectedness.values())
    n_disconnected = sum(1 for v in connectedness.values() if not v["pass"])

    return {
        "schema": "argus-spiral-topological-control-v2",
        "rule_id": RULE_ID,
        "connectivity_full": bool(connectivity_full),
        "crossing_margin": float(crossing_margin),
        "binning": {"n_z_bins": n_z_bins, "n_theta_bins": n_theta_bins,
                   "min_points_per_bin": min_points_per_bin,
                   "min_shared_bins": min_shared_bins,
                   "crossing_violation_tol": crossing_violation_tol},
        "n_windings": len(windings),
        "connectedness": connectedness,
        "n_disconnected": n_disconnected,
        "connected_pass": bool(connected_pass),
        "radius_band_p01_p99": radius_band,
        "winding_order_pass": bool(order_pass),
        "crossings": crossings,
        "insufficient_overlap_pairs": insufficient_overlap,
        "non_crossing_pass": bool(non_crossing_pass),
        "topology_clean": bool(connected_pass and order_pass and non_crossing_pass),
    }




def holdout_control(radius_by_winding: dict[int, float], *,
                     calibration_range: tuple[int, int],
                     degree: int = 1,
                     median_rel_error_tol: float = 0.15) -> dict[str, Any]:
    """Does the fit's own geometry generalise to windings NOT used to calibrate the radius/ winding law?"""
    lo, hi = calibration_range
    train = {k: v for k, v in radius_by_winding.items() if lo <= k <= hi}
    holdout = {k: v for k, v in radius_by_winding.items() if k < lo or k > hi}
    if len(train) < degree + 2:
        raise SpiralControlRefusal(
            "only %d windings fall inside the calibration range %s -- need at least %d to fit "
            "a degree-%d model without the holdout evaluation being vacuous"
            % (len(train), calibration_range, degree + 2, degree))
    if not holdout:
        raise SpiralControlRefusal(
            "no windings fall outside the calibration range %s -- there is nothing to hold out, "
            "so this control cannot be evaluated honestly (it would vacuously pass)"
            % (calibration_range,))

    train_idx = np.array(sorted(train), dtype=np.float64)
    train_r = np.array([train[k] for k in sorted(train)], dtype=np.float64)
    coeffs = np.polyfit(train_idx, train_r, degree)

    holdout_idx = sorted(holdout)
    predictions = {}
    rel_errors = []
    for k in holdout_idx:
        pred = float(np.polyval(coeffs, k))
        actual = float(holdout[k])
        rel_err = abs(pred - actual) / abs(actual) if actual != 0 else float("inf")
        predictions[k] = {"predicted": pred, "actual": actual, "relative_error": rel_err}
        rel_errors.append(rel_err)
    rel_errors = np.array(rel_errors, dtype=np.float64)
    median_rel_error = float(np.median(rel_errors))
    max_rel_error = float(np.max(rel_errors))
    passed = median_rel_error <= median_rel_error_tol

    return {
        "schema": "argus-spiral-holdout-control-v1",
        "rule_id": RULE_ID,
        "definition": (
            "a degree-%d radius(winding) model fit ONLY on windings in the declared "
            "calibration range %s, evaluated against the fit's OWN measured radius on every "
            "winding OUTSIDE that range -- a region not used to fit the model."
            % (degree, calibration_range)),
        "calibration_range": list(calibration_range),
        "degree": degree,
        "train_windings": sorted(train),
        "holdout_windings": holdout_idx,
        "model_coeffs_highest_first": [float(c) for c in coeffs],
        "predictions": predictions,
        "median_rel_error_tol": median_rel_error_tol,
        "median_rel_error": median_rel_error,
        "max_rel_error": max_rel_error,
        "pass": bool(passed),
    }




def evaluate_spiral_fit(*, geometric: dict[str, Any], topological: dict[str, Any],
                         holdout: dict[str, Any],
                         known_blockers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Combine the three controls into one receipt."""
    ready = bool(geometric["pass"] and topological["topology_clean"] and holdout["pass"])
    blockers = dict(known_blockers or {})
    return {
        "schema": "argus-spiral-fit-controls-result-v1",
        "rule_id": RULE_ID,
        "geometric_control": {"pass": geometric["pass"], "n_fail": geometric["n_fail"],
                              "n_meshes": geometric["n_meshes"]},
        "topological_control": {"pass": topological["topology_clean"],
                                "n_disconnected": topological["n_disconnected"],
                                "winding_order_pass": topological["winding_order_pass"],
                                "n_crossings": len(topological["crossings"])},
        "holdout_control": {"pass": holdout["pass"],
                            "median_rel_error": holdout["median_rel_error"],
                            "median_rel_error_tol": holdout["median_rel_error_tol"],
                            "n_holdout_windings": len(holdout["holdout_windings"])},
        "all_three_controls_pass": ready,
        "known_blockers": blockers,
        "ready_for_qualified_fit": bool(ready and not blockers.get("any", False)),
    }
