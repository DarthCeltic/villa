"""CLI: ``python -m vesuvius.surface_qualify SURFACE.tifxyz -o OUT_DIR``.

Exit codes: 0 = at least one safe chart is extractable, 3 = REFUSED (unsafe or nothing safe; the report is still written), 1 = error.
The source surface is opened read-only and OUT_DIR may not be inside it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

from .core import DEFAULTS, analyze
from .report import canonical_json, render_overlay, with_replay_hash


def load_tifxyz(path: Path) -> tuple[np.ndarray, float | None]:
    """Read x.tif/y.tif/z.tif (+ optional mask.tif) read-only.  Returns (P (H,W,3) float64 with invalid = -1, nominal spacing in voxels = 1 / meta scale)."""
    import tifffile

    for n in ("x.tif", "y.tif", "z.tif", "meta.json"):
        if not (path / n).is_file():
            raise FileNotFoundError(f"{path}: missing {n}")
    xyz = [np.asarray(tifffile.imread(str(path / f"{c}.tif")), dtype=np.float64) for c in "xyz"]
    if not (xyz[0].shape == xyz[1].shape == xyz[2].shape and xyz[0].ndim == 2):
        raise ValueError("x/y/z must be equal-shape 2D arrays")
    P = np.stack(xyz, -1)
    if (path / "mask.tif").is_file():  # Villa applies mask.tif: masked-out vertices are invalid
        m = np.asarray(tifffile.imread(str(path / "mask.tif")))
        if m.shape == P.shape[:2]:
            P[m <= 0] = -1.0
    scale = json.loads((path / "meta.json").read_text(encoding="utf-8")).get("scale")
    nominal = (1.0 / float(scale[0])) if scale and float(scale[0]) > 0 else None
    return P, nominal


def _find_census(doc: dict, surface: Path) -> dict | None:
    want = str(surface).replace("\\", "/").rstrip("/")
    for e in doc.get("surfaces", []):
        s = str(e.get("surface", "")).replace("\\", "/").rstrip("/")
        if s and (s == want or s.split("/")[-1] == surface.name):
            return None if "error" in e else e
    return None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vesuvius.surface_qualify", description="Safe qualification and conservative clean-chart extraction for a tifxyz surface. Never modifies the surface; never repairs.")
    ap.add_argument("surface", type=Path, help="a *.tifxyz directory (read-only)")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--census-1884", type=Path, help="JSON report from vc_tifxyz_topology (PR #1884); consumed, not recomputed")
    ap.add_argument("--nominal-spacing", type=float, help="voxels; default 1 / meta.json scale")
    for k in ("defect_deg", "edge_lo", "edge_hi", "kappa", "min_chart_area_vox2"):
        ap.add_argument("--" + k.replace("_", "-"), dest=k, type=float, default=DEFAULTS[k])
    ap.add_argument("--dilate-hops", type=int, default=DEFAULTS["dilate_hops"])
    ap.add_argument("--diagonal", choices=("shorter", "villa"), default=DEFAULTS["diagonal"])
    ap.add_argument("--save-arrays", action="store_true", help="also write angle_defect.npy, cell_class.npy, chart_labels.npy")
    ap.add_argument("--no-png", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        surface = a.surface.resolve()
        out = a.out_dir.resolve()
        if out == surface or surface in out.parents:
            raise ValueError("refusing to write inside the source surface directory")
        P, nominal = load_tifxyz(surface)
        census = None
        if a.census_1884:
            census = _find_census(json.loads(a.census_1884.read_text(encoding="utf-8")), a.surface)
        params = {k: getattr(a, k) for k in ("defect_deg", "edge_lo", "edge_hi", "kappa", "min_chart_area_vox2", "dilate_hops", "diagonal")}
        rep, arrays = analyze(P, nominal=a.nominal_spacing or nominal, params=params, census=census, name=surface.name)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(canonical_json(with_replay_hash(rep)), encoding="ascii", newline="\n")
        if not a.no_png:
            render_overlay(arrays, out / "overlay.png", defect_deg=a.defect_deg)
        if a.save_arrays:
            np.save(out / "angle_defect.npy", arrays["angle_defect_deg"])
            np.save(out / "cell_class.npy", arrays["cell_class"])
            np.save(out / "chart_labels.npy", arrays["chart_labels"])
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{rep['status']}  extractable_charts={rep['extraction']['extractable_charts']}  reasons={rep['refusal_reasons']}")
    return 0 if rep["status"] == "EXTRACTABLE" else 3


if __name__ == "__main__":
    sys.exit(main())
