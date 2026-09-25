# Capability matrix

This matrix covers this public release only. It uses three columns that are never merged:

- **Available**: the code is present and runs in this release.
- **Verified here**: a command or test in this release checks it (`argus/tests/`, `tests/`, or a
  `--selftest` named below).
- **Scientifically admissible**: whether its output may be presented as evidence about a scroll.

Claim boundary: **this release ships no detector and makes no scientific claim. It does not read
unread scrolls. A rendered candidate is not a reading. Every export packet is `never_published`.**

| Capability | Available | Verified here | Scientifically admissible |
|---|---|---|---|
| Synthetic demo (`argus demo`) | yes | `tests/test_public_release.py` | no; `DEMONSTRATION_ONLY` by construction |
| Machine and install diagnosis (`argus doctor`) | yes | `tests/test_public_release.py` (runs to its summary) | not applicable |
| Docker stack (`Start-ARGUS.cmd`, `argus start`, `argus stop`, `argus status`) | yes | `tests/test_docker_runtime_contract.py` and `tests/test_windows_consumer_launcher.py` check the compose and launcher contracts; the image build itself is not run by the test kit | not applicable |
| Canonical metric (`argus.core.metrics`, `argus-metric-v1`) | yes | `argus/tests/test_metrics.py`, `argus/tests/test_scoring.py` (against an independent implementation) | not applicable; it is a tool |
| Result classes (a control is never a discovery) | yes | `argus/tests/test_result_class.py` | not applicable; it is a guard |
| Zero-input, label-shuffle, phase and depth sabotage | yes | `tests/test_public_null_controls.py` (synthetic) | not applicable; they are controls |
| Renderer parity (sampling, tiling, resampling) | yes | `tests/test_public_renderer_parity.py` (synthetic, against plain-loop references); no external renderer is compared | not applicable |
| Scroll, volume, block and pitch identity | yes | `argus/tests/test_scroll_ids.py`, `test_block_identity.py`, `test_pitch.py` and neighbours | not applicable |
| Lineage, exposure and cross-scroll gate | yes | `argus/tests/test_lineage.py`, `test_exposure.py`, `test_scroll_generalization.py` (synthetic scores) | not applicable; the gate refuses or qualifies weights, it makes no claim itself |
| Orientation sensitivity (sheet-normal sign, both orientations) | yes | `argus/tests/test_signed_normal.py`, `test_normal_orientation.py` | not applicable |
| Memory, VRAM and disk preflight | yes | `argus/tests/test_long_run_preflight.py`, `test_install_tiers.py`, `test_resource_sampler.py` | not applicable |
| Evidence packet export and verification | yes | `argus/tests/test_publication_packet.py`, `tests/test_public_evidence_gate.py` | a packet is `never_published`; it supports review, not a claim |
| Licence registry (four permissions per component) | yes | `argus/tests/test_licence_registry.py` | not applicable |
| Read-only observatory service | yes | `tests/test_public_release.py` (health route, write refused), `argus/tests/test_request_guard.py` | not applicable |
| Command service with token and audit log | yes | `argus/tests/test_command_boundary.py`, `test_bff_transport.py`, `test_ledger_v2.py` | not applicable |
| Receipt path sanitiser (no local paths or tokens in served receipts) | yes | `argus/tests/test_service_receipts.py`, `python -m argus.service.receipts --selftest` | not applicable |
| Attribution guard (refuses commit messages that credit a tool as an author) | yes | `python -m argus.core.authorship --selftest` | not applicable |
| UI production build | yes | `npm ci && npm run build`, `npm run test:unit` (continuous integration) | not applicable |
| Public demo mode (non-public surfaces removed from the page) | yes, always on in this build | reviewed by hand; not covered by the Python test kit | not applicable |
| Acquisition planning (dry run, no fetch) | yes | not tested in this release | not applicable |
| Governed research pipeline (`argus run --target`) | **not shipped**: its driver and target manifests stay private | not applicable | not applicable |
| Public bounded pipeline run (`argus run <manifest>`) | yes, one non-prize target | offline tests `argus/tests/test_public_pipeline.py`; run once on the maintainers' machine over public data (see `docs/PUBLIC_RUN.md`); needs Villa's runtime and a GPU | no; a control proves the pipeline, not a discovery |
| Ink detection on any scroll | **no detector ships** | not applicable | **no** |
| Reading unread scrolls | **not in this release** | not applicable | **no** |
| Prize submission packaging | not in this release | not applicable | no |
| Model weights, CT data, labels | **never shipped** | not applicable | not applicable |
