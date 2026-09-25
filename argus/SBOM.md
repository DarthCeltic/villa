# ARGUS software bill of materials

Emitted from `argus/core/licence_registry.py` by the release tooling. Do not edit by
hand: change the registry and re-emit, or the two will disagree and the SBOM will be the
one that is wrong.

## Why there are four permission columns and not a licence column

A licence field answers "what is this licensed under". The question that decides whether
ARGUS may ship something is "what may we DO with it", and they are not the same. CC BY-NC
data is safe to fetch and use and must never be redistributed; a single licence string
leaves that to be interpreted correctly at packaging time, which is how a non-commercial
dataset ends up inside a release tarball.

**UNDECLARED is a refusal, not a blank.** A component that carries no grant at all is
refused in every column that requires one: that is the absence of permission, not a gap.

| Component | Kind | Licence | Run | Bundle | Redistribute | Prize |
|---|---|---|---|---|---|---|
| ARGUS | CODE | Apache-2.0 | yes | yes | yes | yes |
| vc_render_tifxyz / volume-cartographer | TOOL | GPL-3.0 | yes | NO | NO | yes |
| Herculaneum CT volumes | DATA | CC BY-NC 4.0 | yes | NO | NO | yes |
| torch / OpenCV / zarr | CODE | Apache-2.0 / BSD-3-Clause | yes | yes | yes | yes |
| scikit-learn (test only) | CODE | BSD-3-Clause | yes | yes | yes | yes |

## What each grant rests on

### ARGUS

- **kind:** CODE  **family:** PERMISSIVE  **spdx:** Apache-2.0
- **source:** this repository
- **evidence:** Sole copyright holder; LICENSE carries the canonical Apache-2.0 text with the copyright line named in LICENSE. The prior MIT LICENSE is preserved at LICENSE.MIT.superseded rather than deleted, so the change is visible instead of silent.

- **note:** THE GRANT COVERS ARGUS-OWNED CODE ONLY. It does not reach scan-derived artifacts (which inherit CC BY-NC 4.0 from the data terms), ink labels, or any model checkpoint. A permissive licence on the code that produced an artifact does not launder the artifact's source terms. See NOTICE.

### vc_render_tifxyz / volume-cartographer

- **kind:** TOOL  **family:** COPYLEFT  **spdx:** GPL-3.0
- **source:** ScrollPrize/villa
- **evidence:** volume-cartographer/LICENSE (the villa repository ROOT LICENSE is MIT; GPL-3.0 applies only to the volume-cartographer subtree, confirmed by a live scan of the upstream repository)

- **note:** run as an EXTERNAL subprocess and never incorporated. That boundary is a choice ARGUS made, not a claim that copying GPL code is forbidden. If a build is ever distributed, the source-offer obligation attaches.

### Herculaneum CT volumes

- **kind:** DATA  **family:** NONCOMMERCIAL  **spdx:** CC BY-NC 4.0
- **source:** vesuvius-challenge-open-data S3
- **evidence:** Vesuvius Challenge data terms

- **note:** fetched from source at run time and never redistributed. The installer must not bundle a byte of it.

### torch / OpenCV / zarr

- **kind:** CODE  **family:** PERMISSIVE  **spdx:** Apache-2.0 / BSD-3-Clause
- **source:** PyPI
- **evidence:** package metadata

- **note:** fetched by the installer from authoritative sources with hashes recorded

### scikit-learn (test only)

- **kind:** CODE  **family:** PERMISSIVE  **spdx:** BSD-3-Clause
- **source:** PyPI scikit-learn
- **evidence:** declared 2026-09-11 in pyproject [project.optional-dependencies] test; installed version 1.9.1; BSD-3-Clause is permissive and checked, not assumed

- **note:** used ONLY to prove the canonical metric against an independent implementation. Not bundled, not redistributed, and not part of any submission.

## Unknown is a refusal

UNDECLARED components are refused in every column that requires a grant. The absence of a licence is not a gap for the packager to fill in.

