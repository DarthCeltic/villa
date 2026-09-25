# Technical summary

**ARGUS** is a local Python and TypeScript instrument for Herculaneum scroll CT. It separates what
the software can do from what its output means scientifically, and records evidence for both.

**Claim boundary:** this release ships no detector and makes no scientific claim. It does not read
unread scrolls. A rendered candidate is not a reading. Every export packet is `never_published`.

## Components

- **CLI** (`argus setup | doctor | demo | run | start | stop | status`). `setup` shows each licence and fetches only what you
  accept, then verifies every byte. `doctor` reports UNKNOWN when a check cannot run, never a pass.
  `demo` runs offline on a synthetic fixture. `run` refuses ambiguous targets, machines that cannot
  finish, unverified inputs, and volumes outside the model's provenance policy. `start`, `stop` and
  `status` drive the Docker stack (also started by `Start-ARGUS.cmd`).
- **Core.** A single metric implementation (`argus-metric-v1`, AUC and AP with tie-averaged ranks).
  Result classes derive the strongest claim a score supports from its target class and training
  exposure; `EXPOSURE_UNKNOWN` never counts as clean. Receipts are atomic, hashed JSON. The licence
  registry gives four separate permissions per component. Every root comes from an environment
  variable with a neutral default under `ARGUS_HOME`.
- **Services.** A read-only observatory that answers every write with 405. A separate command
  service with a bearer token, a registered-callable action allowlist and a hash-chained audit log.
  A loopback-only UI transport that exposes named operations only.
- **Container stack.** One Dockerfile with a service target and a UI target, and a compose file that runs
  observe, command and ui, all published on `127.0.0.1` only. Non-root, every capability dropped,
  third-party packages pinned by version and sha256, no GPU.
- **Test kit.** Controls that decide whether a result can be trusted, as hermetic tests any user can
  run on their own data or provider.
- **UI.** React and Vite. The rooms are Home (scroll archive, plus eight facts per scroll with
  UNKNOWN shown where a value is missing), Explore, Workbench, Evidence, Review, System, Jobs and
  Sources, plus a dockable Grail Diary brief for the selected scroll. The public build always runs
  in public demo mode, which removes private surfaces from the DOM.

## Verification in this release

- **The public test kit** (`argus/tests/`, `tests/`; `python scripts/run_portable_ci.py`): hermetic tests on
  synthetic inputs of identity, input representation, zero-input, label-shuffle, phase and depth
  sabotage, label composition, boundary and geometry confounds, lineage and independence, renderer
  parity, orientation, memory limits, deterministic replay and evidence-packet integrity. See
  `docs/TEST_KIT.md`. It cannot show that a detector works on a scroll.
- `tests/test_public_release.py`: demo determinism and labelling, hand-computed metric values and
  refusals, result-class guards, licence refusals, path defaults, the read-only service's health
  route and write refusal, the release manifest, and the claim boundary in the front documents.
- Continuous integration (`.github/workflows/argus-portable-validation.yml`): manifest verification, the test kit
  and a source compile on Python 3.11, 3.12 and 3.14, and the interface build and unit tests on Node 22.
- `RELEASE_MANIFEST.json`: sha256 of every file; `python tools/verify_manifest.py` checks it.

## Not in this release

No CT data, labels or weights (their licences do not allow redistribution). No scientific results.
No prize submission packaging. No GPU in the containers. Only Windows is verified by hand.
See `docs/LIMITATIONS.md` and `PUBLIC_CLAIMS_AND_LIMITS.md`.
