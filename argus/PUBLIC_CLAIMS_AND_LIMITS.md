# What ARGUS claims, and what it does not

This is the one page to read before quoting anything ARGUS shows. It is written so that a sentence taken
from it cannot be turned into a bigger claim than the evidence supports.

ARGUS is a local instrument. It records where a result came from, why a step could not run, and what class
of claim a result supports. It is a working tool and a demonstration on synthetic data. It is not a reading
of any scroll.

## The claim boundary

This holds for everything this repository can show you, whatever a test or a screen reports.

1. **This release ships no detector and makes no scientific claim.** An ink-family provider row may only
   carry a claim ceiling that refuses a detector claim, and no provider row can hold the role of independent
   evidence or ground truth.
2. **It does not read unread scrolls.** Nothing in this release searches a scroll for ink or claims text.
3. **A rendered candidate is not a reading.** An image that looks like it contains letters is not evidence of
   text. A result class (`argus/core/result_class.py`) travels with every score so a caption cannot be
   cropped away from what the score is.
4. **Every packet is `never_published`.** Export packets carry `never_published: true` and their limitations
   block. ARGUS prepares packets for review and never publishes, uploads or submits anything itself; that is
   a human decision made outside ARGUS.

## What ARGUS does claim

| Claim | What backs it |
|---|---|
| One canonical AUC and average-precision implementation, with tied scores sharing their ranks | `argus/core/metrics.py`, checked against an independent implementation to twelve decimal places (`argus/tests/test_scoring.py`) |
| A control result can never be presented as a discovery | `argus/core/result_class.py`: `assert_not_discovery` raises instead of returning false |
| The demo is offline, deterministic and labelled `DEMONSTRATION_ONLY` | `tests/test_public_release.py` |
| A missing or unknown fact is reported as such, never as a pass | `argus doctor` reports `UNKNOWN`; unresolved provenance, unknown exposure channels and undeclared licences are refusals |
| Controls are wired correctly and can fail | The public test kit plants each defect and requires it to be caught (`docs/TEST_KIT.md`) |
| The services refuse what they must | The read-only service answers every write with 405; the command service needs a token; the UI transport binds to loopback only |
| Exported packets are tamper-evident | `argus/core/publication_packet.py` verifies bytes, files, limitations and claim limits |
| What ARGUS may do with each component | `SBOM.md`, `argus/core/licence_registry.py`: four separate permissions, `UNDECLARED` is a refusal |

## What ARGUS does not claim

- It does not claim ink, readability, a reading, a transcription or a prize result, on any scroll.
- It does not claim that any detector, weights or provider are qualified, or that any model transfers
  between scrolls. Declaring what `MODEL_PACKAGE_BOUNDARY.md` asks for makes a package reviewable, not
  qualified.
- It does not claim that a rendered image, a probability map or a candidate region is text.
- It does not claim performance on your hardware. Limits are measured or declared as not measured; see below.
- It does not claim that the Linux and macOS install paths work. Only Windows is verified by hand.

## What the public test kit can show, and what it cannot

The kit (`argus/tests/`, `tests/`; `python scripts/run_portable_ci.py`) is hermetic and synthetic.

It **can** show that a control is wired correctly, that it fails when its defect is planted, that a
computation replays to the same bytes, that two implementations of the same rendering contract agree, that
declared limits are checked before a run, and that a packet or a manifest reports a change. It covers scroll,
volume and pitch identity, input representation, zero-input, label-shuffle, phase and depth sabotage, label
composition, boundary and geometry confounds, lineage and independence, renderer parity, orientation
sensitivity, memory limits, deterministic replay and evidence-packet integrity.

It **cannot** show that a detector works on a scroll, that a model transfers between scrolls, that a label set
is correct, that a renderer other than this one agrees with it, or anything about material that is not in the
test. A green kit is a statement about the instrument, not about a scroll. `docs/TEST_KIT.md` lists what each
group does not cover.

## What this release is built from

The release is built from an explicit allowlist: a file reaches this repository only if it was named and
justified (`RELEASE_MANIFEST.json` records the sha256 of every file that did). It contains no scientific
results, and nothing in it depends on material outside it.

## Model and data package boundary, in brief

ARGUS ships no model weights, no CT data and no labels. A package it will work with declares an immutable
revision, a full `sha256`, a licence with evidence, provenance, its input representation and frame, per-channel
exposure and lineage, a hardware profile that is measured or says it is not, and a claim ceiling that refuses a
detector claim; and it has to survive the sabotage controls in the test kit. Promotion is an operator act on a
verified packet and never changes what ARGUS claims. The full contract, with the module that enforces each item,
is `MODEL_PACKAGE_BOUNDARY.md`.

## Hardware and measured limits

ARGUS does not guess a machine's limits and does not promise a runtime.

- `argus doctor` measures RAM, disk, CPU and, where the driver can be asked, the GPU, and reports `UNKNOWN` where
  it cannot. `argus status` says whether the Docker stack is ready and why not.
- A run is sized before it starts: `argus.core.install_tiers` estimates VRAM from patch size and batch size,
  plans a torch-level memory cap so an over-budget job fails instead of silently spilling into system RAM, and
  detects that spill in a trace; free-disk and RAM guards refuse a plan whose free space or usage is unknown.
- `scripts/resource_sampler.py` samples CPU, RAM, VRAM and disk of a running job, reports peaks, and records a
  floor as crossed only when a sample was below it. A reading that could not be taken is `null`, never zero.
- The Docker containers have no GPU. The first image build needs about 10 GiB of free disk.
- Hardware profiles in provider rows are `MEASURED`, `ASSUMED_NOT_MEASURED`, `DECLARED_NOT_MEASURED` or
  `NOT_MEASURED`, and say which.

To measure your own machine, run `scripts/resource_sampler.py` around a build or a test-kit run. Its figures
describe that run on that machine; they are not a guarantee for another machine.

## Where to look

- `README.md` and `QUICKSTART.md`: start ARGUS.
- `docs/LIMITATIONS.md`: what has not been verified.
- `docs/CAPABILITIES.md`: capabilities on three separate axes (available, verified here, scientifically admissible).
- `docs/TEST_KIT.md`: the test kit and how to point it at your own data or provider.
- `MODEL_PACKAGE_BOUNDARY.md`: what a package must declare and what ARGUS never ships.
- `SECURITY.md`: the security boundaries.
