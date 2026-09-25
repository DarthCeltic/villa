# ARGUS Evidence Gate

Two honest tiers, clearly separated so neither is mistaken for the other:

**`check`** -- fast, DECLARED. You already have numbers (mesh-vs-volume overlap, seam
ratio, chunk non-fill fraction, ...) in a JSON file and want a threshold decision. Nothing
is measured here; the tool trusts the payload.

**`measure`** -- real, MEASURED. You give it an actual tifxyz mesh (`x.tif`/`y.tif`/`z.tif`)
and an acquisition declaration, and it runs the same code ARGUS's own pipeline runs --
`argus.core.contracts` and `argus.adapters.published_mesh` -- to compute frame
overlap, lattice-edge continuity, and (given a volume) chunk-identity, for real. This is
not a lookalike or a reimplementation: `evidence_gate` ships in the same wheel as `argus`
(see `pyproject.toml`) specifically so it can call the one tested implementation rather than
carry a second copy that can silently drift from it.

Both modes preserve the lessons behind upstream Villa issues **#1648, #1660, #1674, #1675,
and #1695**: geometry must be inside the volume, fill-only reads must not masquerade as
data, seams and overlaps need explicit controls, and a successful process is not
automatically a scientific success. `PASS`, `FAIL`, and `REFUSE_TO_INTERPRET` are
intentionally different, and every new receipt carries the exact command and a
credential-redacted argv, so it is reproducible without also being a place secrets end up.

## Network policy

`measure` never fetches bulk data. A local `file://` volume is always read (no network at
all). A remote `http(s)://`/`s3://` volume is read only with `--allow-network`, and even
then only a few hundred single bytes are ever requested -- see
`argus.adapters.zarr_range_reader`, which addresses one voxel at a time by computed byte
offset. Without either, the chunk-identity gate (#1674) is reported `NOT_MEASURED`, not
silently skipped and not silently passed; every other gate still runs for real on the mesh
alone.

## Examples

```text
# Declared: trust an already-computed payload
python -m evidence_gate check --mesh evidence.json --volume-url local-volume-id --receipt receipt.json

# Measured: run the real geometry gates on a real mesh, offline (local volume only)
python -m evidence_gate measure --manifest manifest.json --receipt receipt.json

# Measured, with an explicit, bounded, read-only remote volume check
python -m evidence_gate measure --manifest manifest.json --allow-network

# Volume only (no mesh): is this voxel box held, and does it carry signal?
python -m evidence_gate measure --manifest manifest.json --volume-only --probe-box 0:64:0:64:0:64
```

`--volume-only` needs only `volume_url` (and optionally `level`) in the manifest. It checks
that every chunk the half-open box touches was written (a never-written chunk reads back as
fill, which looks like dark papyrus) and that a seeded sample of voxels is not fill. It
reports one `chunk_identity` gate: `PASS` (all held, signal present), `FAIL` (nothing held or
no signal) or `REFUSE_TO_INTERPRET` (partly held, or the box lies outside the level). It says
nothing about geometry, orientation or which scan the volume is.

When a mesh run is refused by a later gate (topology, orientation) and a volume was given,
the `chunk_identity` verdict is still reported next to that refusal.

## What a PASS is, and is not

A `PASS` means the gates that ran found nothing wrong with the inputs you supplied. On a
synthetic mesh with a declared orientation it is expected, by design: the gate checks
consistency between a mesh, a volume and a declaration, not whether the mesh is a real
surface. Do not cite a PASS on a synthetic or hand-made mesh as a result; the measured
fixtures in this package exist to show that the gates can fail, not to certify anything.

A `measure` manifest is a small JSON file:

```json
{
  "mesh_dir": "relative/or/absolute/path/to/tifxyz/dir",
  "acquisition": {"volume_id": "...", "voxel_um": 9.362, "energy_kev": 113,
                 "pitch_um_per_px": 9.362, "pyramid_level": 0},
  "volume_shape": [2000, 4000, 4000],
  "volume_url": "file:///path/to/store or https://.../store",
  "orientation_provenance": "AS_WRITTEN"
}
```

`measure` requires the `argus` package and `tifffile` (`pip install "argus[evidence-gate]"`
from a checkout, or `pip install tifffile` alongside an existing ARGUS install); `check`
needs neither.
