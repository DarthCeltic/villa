# Evidence-gate defect corpus

Two corpora, run together by `python scripts/check_evidence_fixtures.py`:

**Declared** -- the JSON files in this directory. Synthetic, no scan data, hand-authored
expected numbers. They prove the `check` mode's threshold logic is internally consistent.

**Measured** -- `build_measured.py` builds real tifxyz meshes and a real local zarr v2 store
(byte-identically, from a fixed seed, into a directory you give it -- nothing is committed
as binary) and runs them through the real `argus.core.contracts` /
`argus.adapters.published_mesh` geometry code, the `measure` mode actually uses. Four cases:
`clean` (PASS), `mesh_outside_volume` (#1660), `seam_across_sheet` (#1675), and `all_fill`
(#1674, a zarr store whose chunks are declared but never written).
