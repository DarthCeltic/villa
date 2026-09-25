"""Route 1: a published mesh -> a CertifiedSurfaceBundle, or a refusal with evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core.contracts import (Acquisition, CertifiedSurfaceBundle, Module, Refusal,
                                  Terminal, sha256_file)

MIN_OVERLAP = 0.98
MIN_NONFILL = 0.05
MAX_STEP_JUMP = 0.001
MAX_STEP_RATIO = 8.0



class PublishedMeshAdapter(Module):
    name = "route1.published_mesh"

    CERTIFIES = Terminal.CERTIFIED_SURFACE

    def inspect(self, ctx: dict) -> dict:
        m = Path(ctx["mesh_dir"])
        have = {f: (m / f).is_file() for f in ("x.tif", "y.tif", "z.tif")}
        ok = all(have.values()) and "acquisition" in ctx
        return {"module": self.name, "can_handle": ok, "files_present": have,
                "why_not": None if ok else "a tifxyz mesh (x/y/z.tif) and a declared "
                                           "acquisition are both required"}

    def plan(self, ctx: dict) -> dict:
        a = ctx["acquisition"]
        return {"module": self.name,
                "mesh_dir": str(ctx["mesh_dir"]),
                "volume": a["volume_id"],
                "acquisition_family": Acquisition(**a).family(),
                "gates": ["mesh_identity", "frame_handshake", "chunk_identity",
                          "topology", "orientation"],
                "writes": ["bundle.json"]}

    def run(self, ctx: dict, attempt_dir: Path) -> dict:
        import tifffile
        attempt_dir.mkdir(parents=True, exist_ok=True)
        mesh_dir = Path(ctx["mesh_dir"])
        acq = Acquisition(**ctx["acquisition"])

        mesh_sha = {f: sha256_file(mesh_dir / f) for f in ("x.tif", "y.tif", "z.tif")}
        X = tifffile.imread(mesh_dir / "x.tif").astype(np.float64)
        Y = tifffile.imread(mesh_dir / "y.tif").astype(np.float64)
        Z = tifffile.imread(mesh_dir / "z.tif").astype(np.float64)
        if not (X.shape == Y.shape == Z.shape):
            raise Refusal("GEOMETRY", "tifxyz planes disagree in shape",
                          {"x": X.shape, "y": Y.shape, "z": Z.shape})
        ok = np.isfinite(X) & np.isfinite(Y) & np.isfinite(Z) & (X > 0)
        if not ok.any():
            raise Refusal("GEOMETRY", "no valid mesh cells")

        vol_shape = tuple(ctx["volume_shape"])
        scale = float(ctx.get("coord_scale", 1.0))
        oz, oy, ox = (float(v) for v in ctx.get("volume_origin_zyx", (0.0, 0.0, 0.0)))
        zi, yi, xi = (Z * scale - oz, Y * scale - oy, X * scale - ox)
        inside = ((zi >= 0) & (zi < vol_shape[0]) & (yi >= 0) & (yi < vol_shape[1])
                  & (xi >= 0) & (xi < vol_shape[2]) & ok)
        overlap = float(inside.sum() / max(int(ok.sum()), 1))
        handshake = {"volume_shape": list(vol_shape), "coord_scale": scale,
                     "volume_origin_zyx": [oz, oy, ox],
                     "mesh_bbox_zyx": [[float(zi[ok].min()), float(yi[ok].min()), float(xi[ok].min())],
                                       [float(zi[ok].max()), float(yi[ok].max()), float(xi[ok].max())]],
                     "vertices_inside_volume": overlap, "min_required": MIN_OVERLAP}
        if overlap < MIN_OVERLAP:
            raise Refusal("GEOMETRY",
                          "only %.1f%% of mesh vertices fall inside the volume; a frame "
                          "mismatch renders a black strip and exits 0 upstream (#1660)"
                          % (100 * overlap), handshake)

        chunk_ids, nonfill = self._sample_chunks(ctx, zi, yi, xi, inside)
        if nonfill is not None and nonfill < MIN_NONFILL:
            raise Refusal("DATA_UNAVAILABLE",
                          "only %.1f%% of sampled voxels are non-fill; a wrong chunk lookup "
                          "returns fill-value silently upstream (#1674), and a render of "
                          "nothing is not a render of blank papyrus" % (100 * nonfill),
                          {"chunk_identities": chunk_ids[:12]})

        topo = self._topology(zi, yi, xi, inside)
        if not topo["sheet_following"]:
            raise Refusal("GEOMETRY",
                          "this is not one continuous sheet: %.2f%% of lattice edges jump "
                          "(limit %.2f%%) and the largest is %.1fx the median edge (limit "
                          "%.1fx). Upstream #1675: growth reports plausible area "
                          "while cutting across windings, and the render still looks fibrous"
                          % (100 * topo["jump_fraction"], 100 * MAX_STEP_JUMP,
                             topo["step_max_ratio"], MAX_STEP_RATIO), topo)

        orientation, osource = self._orientation(ctx)
        bundle = CertifiedSurfaceBundle(
            acquisition=acq, mesh_sha256=mesh_sha, mesh_source=str(ctx.get("mesh_source", mesh_dir)),
            grid_shape=tuple(X.shape), coverage=float(ok.mean()),
            orientation=orientation, orientation_source=osource,
            depth_planes=int(ctx.get("depth_planes", 21)),
            physical_window_mm=ctx.get("physical_window_mm"),
            frame_handshake=handshake, chunk_identities=chunk_ids, topology=topo,
            invertible=True,
            lineage={"adapter": self.describe(), "mesh_dir": str(mesh_dir)})
        rec = {"bundle": {k: (list(v) if isinstance(v, tuple) else v)
                          for k, v in bundle.__dict__.items()
                          if k != "acquisition"},
               "acquisition": acq.__dict__, "acquisition_family": acq.family()}
        (attempt_dir / "bundle.json").write_text(json.dumps(rec, indent=1, default=str))
        return {"terminal": Terminal.CERTIFIED_SURFACE, "bundle": bundle,
                "receipt": str(attempt_dir / "bundle.json")}

    def verify(self, ctx: dict, attempt_dir: Path) -> dict:
        p = attempt_dir / "bundle.json"
        if not p.is_file():
            return {"verdict": "REFUSED", "failures": ["no bundle written"]}
        return {"verdict": "PASS", "receipt_sha256": sha256_file(p)}

    @staticmethod
    def _sample_chunks(ctx, zi, yi, xi, inside, n=None):
        """Record which chunks a render would actually read, and whether they hold data."""
        reader = ctx.get("voxel_reader")
        n = int(ctx.get("chunk_samples", n or 256))
        idx = np.flatnonzero(inside.ravel())
        if idx.size == 0:
            return [], 0.0
        rng = np.random.default_rng(0)
        pick = rng.choice(idx, size=min(n, idx.size), replace=False)
        z, y, x = zi.ravel()[pick], yi.ravel()[pick], xi.ravel()[pick]
        cid = ctx.get("chunk_id")
        if cid is None:
            cz, cy, cx = ctx.get("chunk_shape", (128, 128, 128))
            def cid(a, b, c):
                return "%d.%d.%d" % (int(a) // cz, int(b) // cy, int(c) // cx)
        ids = sorted({cid(a, b, c) for a, b, c in zip(z, y, x)})
        if reader is None:
            return ids, None
        fill = float(ctx.get("fill_value", 0))
        vals = np.array([reader(int(a), int(b), int(c)) for a, b, c in zip(z, y, x)], float)
        return ids, float((vals != fill).mean())

    @staticmethod
    def _topology(zi, yi, xi, inside):
        """Lattice continuity: how far apart neighbouring grid cells land in the volume."""
        P = np.stack([zi, yi, xi], -1)
        steps = []
        for ax in (0, 1):
            if P.shape[ax] < 2:
                continue
            a = np.take(P, np.arange(1, P.shape[ax]), axis=ax)
            b = np.take(P, np.arange(0, P.shape[ax] - 1), axis=ax)
            m = (np.take(inside, np.arange(1, P.shape[ax]), axis=ax)
                 & np.take(inside, np.arange(0, P.shape[ax] - 1), axis=ax))
            v = np.linalg.norm(a - b, axis=-1)
            if m.any():
                steps.append(v[m])
        s = np.concatenate(steps) if steps else np.array([])
        if s.size == 0:
            raise Refusal("GEOMETRY", "no measurable lattice edges; the surface is not "
                                      "connected inside the volume")
        med = float(np.median(s))
        frac = float((s > 4 * max(med, 1e-6)).mean())
        ratio = float(s.max() / max(med, 1e-6))
        return {"step_median_vox": med,
                "step_p99_vox": float(np.percentile(s, 99)),
                "step_max_vox": float(s.max()),
                "step_max_ratio": ratio,
                "jump_fraction": frac,
                "n_edges": int(s.size),
                "sheet_following": bool(frac <= MAX_STEP_JUMP and ratio <= MAX_STEP_RATIO),
                "note": ("area and fibrous appearance are not evidence of a sheet; this "
                         "measures lattice continuity (upstream #1675)")}

    @staticmethod
    def _orientation(ctx):
        """Provenance FIRST."""
        prov = (ctx.get("orientation_provenance") or "").upper()
        ctrl = (ctx.get("orientation_control") or "").upper()
        if prov in ("AS_WRITTEN", "REVERSED"):
            if ctrl and ctrl != prov:
                raise Refusal("CERTIFICATION",
                              "orientation provenance says %s and the known-ink control says "
                              "%s; a contradiction is a refusal, not a choice" % (prov, ctrl),
                              {"provenance": prov, "control": ctrl})
            return prov, ("asset provenance%s" % (", confirmed by a known-ink control" if ctrl else ""))
        raise Refusal("CERTIFICATION",
                      "orientation is undetermined; the calibration contract requires it from "
                      "provenance and refuses a default (upstream #1648)",
                      {"provenance": prov or None, "control": ctrl or None})
