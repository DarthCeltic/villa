# The public test kit

The test kit is a set of hermetic tests, and the controls behind them, that let you check your own
data or your own provider without any private material. It needs no network, no GPU and no scan data.
It publishes no outcomes.

```bash
python scripts/run_portable_ci.py                        # every group; exits non-zero if any fails
python scripts/run_portable_ci.py --list                 # the groups and their files
python scripts/run_portable_ci.py --group null-and-sabotage --group renderer-parity
python scripts/run_portable_ci.py --json kit-result.json # also write a machine-readable result
python -m pytest argus/tests tests                       # the same tests through pytest
```

Install with `pip install -e ".[service,test]"` first. Every test runs with `ARGUS_HOME` (and every other
root) pointed at a fresh temporary directory (`conftest.py`), so nothing reads or writes your own state.
A test that imports torch at module level would create a CUDA context merely by being collected, so such
modules are left out of collection and the run says so; set `ARGUS_GPU_TESTS=1` to include them. The
kit itself has none.

## What each group checks

| Group | Question | Main files |
|---|---|---|
| `identity` | Are a scroll, a volume, a block and a pixel pitch what they say they are? | `argus/tests/test_scroll_ids.py`, `test_public_identity.py`, `test_block_identity.py`, `test_physical_frame_contract.py`, `test_pitch.py` |
| `input-representation` | Is the input the detector sees the input it was declared to see? | `test_input_boundaries.py`, `test_depth_composite.py`, `test_compositor.py`, `test_physical_resample.py`, `test_signed_normal.py` |
| `null-and-sabotage` | Does the score fall to chance when one thing is taken away? | `tests/test_public_null_controls.py`, `argus/tests/test_pairwise.py`, `test_pseudolabel_controls.py`, `test_spiral_fit_controls.py`, `test_copy_out_in_defect_control.py` |
| `labels` | Where do labels come from, how are they scored, and what may a score be called? | `test_label_sources.py`, `test_scoring.py`, `test_metrics.py`, `test_result_class.py` |
| `geometry` | Are boundary, topology and surface-geometry confounds measured instead of assumed away? | `test_surface_metric.py`, `test_topology_metric.py`, `test_surface_consistency.py`, `test_surface_contract.py`, `test_blender_roundtrip.py` |
| `lineage-and-independence` | Is the evidence independent of what the model trained on, and is that declared? | `test_lineage.py`, `test_stage_lineage.py`, `test_arm_isolation.py`, `test_science_arbiter.py`, `test_exposure.py`, `test_scroll_generalization.py` |
| `renderer-parity` | Do two independent implementations of sampling, tiling and resampling agree? | `tests/test_public_renderer_parity.py` |
| `orientation` | Is the sheet-normal sign decided, gated and reported? | `test_orientation_semantics.py`, `test_normal_orientation.py` |
| `memory-and-limits` | Does a run know its memory, VRAM, disk and process limits before it starts? | `test_long_run_preflight.py`, `test_availability_bounds.py`, `test_owned_process_tree.py`, `test_resource_sampler.py`, `test_job_preflight.py`, `test_install_tiers.py` |
| `deterministic-replay` | Does the same seed replay to the same bytes, and does an interrupted tiled run resume? | `test_tiled_run_resume.py`, `tests/test_public_release.py`, and the replay tests in `tests/test_public_null_controls.py` |
| `evidence-integrity` | Can a packet be exported, reopened and proven unchanged, with its limitations attached? | `test_publication_packet.py`, `tests/test_public_evidence_gate.py`, `tests/test_evidence_products_schema.py`, `test_manifest.py` |
| `service-and-install` | Do the services refuse what they must, and do the Docker files and licences say what they claim? | `test_bff_transport.py`, `test_command_boundary.py`, `test_request_guard.py`, `test_service_receipts.py`, `test_service_jobs_api.py`, `test_rescan_boundaries.py`, `test_ledger_v2.py`, `test_licence_registry.py`, `test_licence_resolver.py`, `tests/test_docker_runtime_contract.py`, `tests/test_windows_consumer_launcher.py`, and the static interface guards `test_ui_governed_hash.py`, `test_ui_scroll_status.py`, `test_wb_responsive_contract.py`, `test_install_tiers_ui.py` |

## Pointing the controls at your own data or provider

The tests use synthetic inputs so they can run anywhere. The functions they exercise take arrays, so the
same controls run on yours. All of them are in this repository; none needs a private module.

**Zero input and label shuffle.** Score your detector's output against your labels with the one canonical
metric, then against shuffled labels. The real score has to beat every shuffle, and a constant output must
score exactly 0.5.

```python
import numpy as np
from argus.core import metrics

scores, labels = my_scores, my_labels                     # arrays of the same shape
real = metrics.score(scores, labels)                      # {'rule_id': 'argus-metric-v1', 'auc': ..., 'ap': ...}
null = [metrics.auc(scores, np.random.default_rng(s).permutation(labels.ravel()).reshape(labels.shape))
        for s in range(200)]
assert real["auc"] > max(null)
assert metrics.auc(np.zeros_like(scores), labels) == 0.5
```

**Phase and depth sabotage.** `tests/test_public_null_controls.py` shows a phase-randomising helper (same
amplitude spectrum, random phase) and a depth sabotage on a stack sampled with
`argus.core.depth_composite.sample_along_normal(volume, points, normals, layers)`. Run your detector on the
sabotaged inputs: a score that survives is evidence about the pipeline, not about the signal.

**Channel swap, mirror equivariance, collapse and leakage.** `argus/core/pseudolabel_controls.py` takes any
callable `model_fn(array) -> array`:

```python
from argus.core import pseudolabel_controls as P

P.channel_swap_test(model_fn, x, swap=(0, 1))                 # does the model use the channel it should?
P.mirror_orientation_equivariance(model_fn, x, axis=-1)       # f(flip(x)) == flip(f(x))?
P.collapse_report(teacher_out, student_out)                   # constant or near-constant student?
P.leakage_report(train_lineages, eval_lineages)               # shared segments, volumes, samples or scrolls
P.cross_scroll_gate({"scrollA": 0.7, "scrollB": 0.71, "scrollC": 0.69}, min_scrolls=3, floor=0.5)
```

**Cross-scroll generalisation.** `argus.core.scroll_generalization.evaluate` takes one
`HeldOutEvaluation(scroll, weights, scores, labels)` per held-out scroll and a `WeightIdentity(weight_id,
sha256, trained_on_scrolls)`. It refuses a scroll that is in the weights' training manifest before it looks
at a score, needs at least three held-out scrolls, and gates on the worst scroll with a frozen bootstrap seed.
It can only reject or qualify weights; it makes no claim of its own.

**Renderer parity.** Push the same volume, points and normals through your renderer and through
`argus.core.depth_composite.sample_along_normal`, and compare the arrays; `tests/test_public_renderer_parity.py`
shows the comparison with a plain-loop reference and shows that it fails when the coordinates are off by half
a voxel. Tiling parity uses `argus.core.compositor.composite`; pitch parity uses
`argus.core.physical_resample.resample_2d`.

**Memory and VRAM.** `argus.core.install_tiers.vram_estimate_gib(patch_size, batch_size)` and
`choose_patch_and_batch(vram_budget_gib, candidates)` size a run before it starts; `enforce_cap_plan` writes
the torch-level cap that makes an over-budget job fail instead of spilling into system RAM;
`detect_vram_spill(samples)` finds that spill in a trace. `scripts/resource_sampler.py --out DIR --duration N`
samples CPU, RAM, VRAM and disk of a running job, and reports a floor as crossed only if a sample was below it.
`argus.core.long_run_preflight` judges every declared field of a long-run plan (pitch, compression axis, exposure, licence for its purpose, checkpoint hash, measured cost) with an explicit predicate and refuses sentinels such as `UNKNOWN` or `UNDECLARED`.

**Evidence packets.** `argus.core.publication_packet.verify(packet_path)` reopens an exported packet and
notices a flipped byte, a deleted file, a stripped limitations block or a weakened claim limit.
`evidence-gate check|measure` checks a mesh against measured volume facts and refuses on missing evidence.

## What a pass shows, and what it cannot

A pass shows that a control is wired correctly, that it can fail, and that it behaves the same on every
run. The tests plant the defect a control is meant to catch and require it to be caught.

A pass does **not** show that a detector reads a scroll. The kit runs on synthetic data and says nothing
about ink, readability or transfer. In particular:

- the renderer-parity tests compare this release's primitives with plain-loop references; they do not
  compare ARGUS with any external renderer, and the upstream parity tests that need one are not part of
  this kit;
- the resume test covers tiled-run planning; interrupted-optimiser equivalence needs torch and is not in
  the kit;
- the Docker tests check the compose and launcher contracts; they do not build the image;
- nothing here uses real scans, real labels or real weights, so nothing here is evidence about them.

This release ships no detector and makes no scientific claim, whatever these tests report.
`PUBLIC_CLAIMS_AND_LIMITS.md` is the full statement of what ARGUS does and does not claim.
