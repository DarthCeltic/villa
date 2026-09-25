# Surface qualification and clean-chart extraction

`python -m vesuvius.surface_qualify SURFACE.tifxyz -o OUT_DIR` answers one question about a tifxyz trace:
**which parts of it are safe to use as a chart, and if none are, say so.** It never modifies the surface and never
repairs, fills, cuts or flattens anything.

It is a companion to the report-only census in PR #1884 (`vc_tifxyz_topology`: islands, holes, tears, folds). It
does not repeat that census. It can read #1884's JSON (`--census-1884 report.json`), embeds the fields it consumes
and cross-checks the two numbers both tools compute under the same validity rule (`valid_quads`, hole count).

## What it adds

| output | what it is |
|---|---|
| angle-defect map | per-vertex `2*pi - sum(incident triangle angles)`, in degrees, at interior vertices (`--save-arrays` writes `angle_defect.npy`) |
| crease networks | triangles touching an interior vertex with `|defect| >= 5 deg`, or with an edge outside `[0.6, 1.6]` x the nominal grid spacing, dilated by 2 triangle hops; a *network* is a connected set of them. Listed with area, grid bounding box and triangle count |
| exterior-bridge hole rule | each enclosed hole gets a width, a separation from every other hole and a bridge to the mesh exterior, in grid cells. A hole is **gated** only if all three are `>= kappa` (default 3); the rest are reported, never gated. The exterior is the invalid region connected to a one-cell ring around the grid |
| clean-chart extraction | every connected component of the non-crease triangles with area `>= 250000 vox^2` is a chart. A chart is EXTRACTABLE only if every chart-boundary edge is a mesh boundary or borders an excluded triangle. `chart_labels.npy` labels the cells of extractable charts (0 = excluded) |
| explicit refusal | `status: REFUSED` with reasons when nothing safe exists (`no_valid_quads`, `nominal_spacing_mismatch`, `no_extractable_chart`), exit code 3, report still written |
| deterministic JSON + PNG | `report.json` has sorted keys, rounded floats, no timestamps or absolute paths and a `replay_sha256`; `overlay.png` (left `|defect|` heat map, right class overlay) |

## Exit codes

`0` at least one extractable chart, `3` refused (report still written), `1` error (unreadable surface, output
directory inside the source surface, ...).

## Parameters

All defaults are fixed by earlier experiments and are not tuned per surface: `--defect-deg 5 --edge-lo 0.6
--edge-hi 1.6 --dilate-hops 2 --kappa 3 --min-chart-area-vox2 250000 --diagonal shorter`. The nominal spacing is
`1 / meta.json scale[0]` (or `--nominal-spacing`); if the median grid step is outside `[0.8, 1.25]` x nominal the
tool refuses, because the edge rule assumes a near-uniform grid.

## What it does not do

* It does not census islands, tears or folds. It selects the largest edge-connected triangle component internally
  (as `vc_flatten` does) only to define "the surface", and labels that step as internal. Quads with an edge longer
  than 4 x nominal are treated as torn cells and not triangulated.
* It does not flatten. Whether an extracted chart flattens with low distortion is a separate measurement.
* It does not say the excluded regions are wrong. Whether crease networks are physical crumpling or tracing
  artefacts needs a check against the CT, which this tool does not do. Excluded means "not certified", not "bad".
* The hole rule's `kappa = 3` was calibrated on a raster export in an earlier experiment; here it is applied in
  grid cells (one cell = one export spacing). That adaptation is not yet validated on real data.
* Validity is Villa's loader rule: finite and `z > 0`; `mask.tif` is applied if present.

## Dependencies

`numpy`, `scipy`, `Pillow`, `tifffile` (all already used by `vesuvius`; `scipy` and `tifffile` are in the
`label-transfer` extra). No new dependency.

## Tests

`tests/test_surface_qualify.py` runs on tiny synthetic arrays and checks logic only (planted crease, planted hole
near the exterior, folded quad, all-clean sheet, refusals, determinism, input never mutated, CLI exit codes). It is
not evidence of usefulness on scroll data.
