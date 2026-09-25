"""Make a normal field consistently oriented, geometrically, without ever consulting a model."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from argus.core.contracts import Refusal

DISCONTINUITY_COS = 0.30

THRESHOLD_PROVENANCE = {
    "DISCONTINUITY_COS": {
        "value": 0.30,
        "status": "PROVISIONAL_UNCALIBRATED",
        "chosen_by": "the author of this module, while writing it, on 2026-09-08",
        "derived_from": None,
        "may_be_tuned_on": [],
        "must_never_be_tuned_on": ["detector output", "any target surface",
                                   "any run whose result depends on it"],
        "what_would_calibrate_it": ("a control on segments with known sheet geometry, measuring "
                                    "the distribution of |cos| across edges that are genuinely "
                                    "continuous versus edges that genuinely cross a seam. Until "
                                    "that exists the number is a guess that happens to be "
                                    "reasonable."),
        "consequence_while_provisional": ("exploratory execution is permitted and every result "
                                          "carries the provisional flag. Certification is "
                                          "blocked."),
    }
}


def certification_blocked_reasons() -> list:
    """Why a result from this module may not be certified yet."""
    out = []
    for name, rec in THRESHOLD_PROVENANCE.items():
        if rec["status"] != "CALIBRATED":
            out.append("%s = %s is %s: %s" % (name, rec["value"], rec["status"],
                                              rec["what_would_calibrate_it"]))
    return out


@dataclass
class OrientationResult:
    oriented: np.ndarray
    component: np.ndarray
    accepted: np.ndarray
    n_components: int = 0
    rejected_components: list = field(default_factory=list)
    discontinuous_edges: int = 0
    conflict_edges: int = 0
    field_sha256: str = ""


def _neighbours(h, w):
    """4-connectivity edges as (index_a, index_b) on the flattened grid."""
    idx = np.arange(h * w).reshape(h, w)
    right = np.stack([idx[:, :-1].ravel(), idx[:, 1:].ravel()], axis=1)
    down = np.stack([idx[:-1, :].ravel(), idx[1:, :].ravel()], axis=1)
    return np.concatenate([right, down], axis=0)


def orient_normals(normals: np.ndarray, valid: np.ndarray) -> OrientationResult:
    """Sign-consistent normals per connected component."""
    if normals.ndim != 3 or normals.shape[2] != 3:
        raise ValueError("normals must be (H, W, 3)")
    if valid.shape != normals.shape[:2]:
        raise ValueError("valid must match the normal field's (H, W)")
    h, w = valid.shape
    n = normals.reshape(-1, 3).astype(np.float64)
    ok = valid.reshape(-1).copy()

    edges = _neighbours(h, w)
    a, b = edges[:, 0], edges[:, 1]
    both = ok[a] & ok[b]
    a, b = a[both], b[both]
    dots = np.einsum("ij,ij->i", n[a], n[b])

    smooth = np.abs(dots) >= DISCONTINUITY_COS
    n_disc = int((~smooth).sum())
    ea, eb, edot = a[smooth], b[smooth], dots[smooth]

    adj = {}
    for i, j, d in zip(ea, eb, edot):
        adj.setdefault(int(i), []).append((int(j), 1 if d >= 0 else -1))
        adj.setdefault(int(j), []).append((int(i), 1 if d >= 0 else -1))

    comp = np.full(h * w, -1, dtype=np.int64)
    sign = np.ones(h * w, dtype=np.int64)
    conflicts, rejected, cid = 0, [], 0

    for start in np.nonzero(ok)[0]:
        if comp[start] != -1:
            continue
        stack = [int(start)]
        comp[start] = cid
        sign[start] = 1
        members, bad = [int(start)], False
        while stack:
            u = stack.pop()
            for v, rel in adj.get(u, ()):
                want = sign[u] * rel
                if comp[v] == -1:
                    comp[v] = cid
                    sign[v] = want
                    members.append(v)
                    stack.append(v)
                elif sign[v] != want:
                    conflicts += 1
                    bad = True
        if bad:
            rejected.append({"component": cid, "pixels": len(members),
                             "why": "irreconcilable sign conflict under propagation"})
            for m in members:
                ok[m] = False
        cid += 1

    oriented = (n * sign[:, None]).reshape(h, w, 3)
    accepted = ok.reshape(h, w)
    oriented[~accepted] = 0.0

    hsh = hashlib.sha256()
    hsh.update(np.ascontiguousarray(oriented.astype(np.float32)).tobytes())
    hsh.update(np.ascontiguousarray(accepted).tobytes())

    return OrientationResult(
        oriented=oriented, component=comp.reshape(h, w), accepted=accepted,
        n_components=cid, rejected_components=rejected,
        discontinuous_edges=n_disc, conflict_edges=conflicts,
        field_sha256=hsh.hexdigest())


def component_orientations(res: OrientationResult) -> list:
    """The two orientations of EACH accepted component, paired independently."""
    out = []
    for cid in sorted(set(int(c) for c in res.component[res.accepted].ravel())):
        mask = (res.component == cid) & res.accepted
        if not mask.any():
            continue
        base = np.zeros_like(res.oriented)
        base[mask] = res.oriented[mask]
        out.append({
            "component": cid,
            "mask": mask,
            "n_pixels": int(mask.sum()),
            "orientations": [("as_computed", base), ("flipped", -base)],
            "sign_is_arbitrary": ("geometry fixes the RELATIVE signs inside this component and "
                                  "says nothing about which face is recto. Both members are "
                                  "scored."),
        })
    return out


def global_orientations(res: OrientationResult):
    """Refuses on a multi-component field."""
    if res.n_components > 1 or len(
            set(int(c) for c in res.component[res.accepted].ravel())) > 1:
        raise ValueError(
            "this field has more than one connected component, and each component's sign is "
            "independently arbitrary. A single global flip explores 2 of the 2^k sign "
            "assignments and can omit the pairing that matters. Use component_orientations().")
    return [("as_computed", res.oriented), ("globally_flipped", -res.oriented)]


def gate_orientation_consistency(normals: np.ndarray, valid: np.ndarray, *,
                                 max_rejected_fraction: float = 0.0) -> dict:
    """The automatic refusal `orient_normals` never had."""
    res = orient_normals(normals, valid)
    n_valid = int(np.count_nonzero(valid))
    n_rejected = sum(int(r["pixels"]) for r in res.rejected_components)
    fraction = (n_rejected / n_valid) if n_valid else 0.0
    record = {
        "schema": "argus-normal-orientation-gate-v1",
        "n_valid_pixels": n_valid,
        "n_rejected_pixels": n_rejected,
        "rejected_fraction": fraction,
        "max_rejected_fraction": float(max_rejected_fraction),
        "rejected_components": res.rejected_components,
        "n_components": res.n_components,
        "discontinuous_edges": res.discontinuous_edges,
        "conflict_edges": res.conflict_edges,
        "field_sha256": res.field_sha256,
        "certification_blocked_reasons": certification_blocked_reasons(),
        "verdict": "REFUSE" if fraction > max_rejected_fraction else "PASS",
    }
    if record["verdict"] == "REFUSE":
        raise Refusal(
            "GEOMETRY",
            "%.4f%% of valid pixels (%d of %d) sit in an orientation-irreconcilable component "
            "-- orient_normals already judged no consistent sign exists there, exceeding the "
            "%.4f%% tolerance" % (100 * fraction, n_rejected, n_valid,
                                  100 * max_rejected_fraction),
            record)
    return record


def validity_from_tifxyz(zmap, ymap, xmap, *, explicit_mask=None,
                         sentinel=None, require_finite=True) -> np.ndarray:
    """The TIFXYZ validity contract, explicit rather than inferred."""
    z = np.asarray(zmap)
    y = np.asarray(ymap)
    x = np.asarray(xmap)
    if explicit_mask is not None:
        m = np.asarray(explicit_mask).astype(bool)
        if m.shape != z.shape:
            raise ValueError("explicit mask shape does not match the TIFXYZ components")
        return m
    if sentinel is None and not require_finite:
        raise ValueError(
            "no explicit mask, no declared sentinel and finiteness not required: there is no "
            "contract here to apply. Declare one rather than letting a convention be assumed.")
    m = np.ones(z.shape, dtype=bool)
    if require_finite:
        m &= np.isfinite(z) & np.isfinite(y) & np.isfinite(x)
    if sentinel is not None:
        s = float(sentinel)
        with np.errstate(invalid="ignore"):
            m &= ~((z == s) & (y == s) & (x == s))
    return m
