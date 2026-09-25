# Acknowledgements and licences

Generated from `argus/core/licence_registry.py` in this release. Edit the registry, not this page.

## Built by

DarthCeltic and clexious.

## Thanks

ARGUS exists because the Vesuvius Challenge published its scans, tools and results openly, and because the community built and shared real tools and data. In particular: the ScrollPrize/villa tooling (volume-cartographer and the `vesuvius` package), hengck23's published ink-detection solution code, the open-source Python stack (numpy, zarr, torch, FastAPI) and the web stack (React, Vite, OpenSeadragon, lucide).

## What the ARGUS licence covers

Apache-2.0 covers ARGUS source code only. It does not cover CT data, labels, upstream tools or model weights. Each keeps its own terms, listed below, and none of them is included in this repository.

| Component | Kind | Licence | Run locally | Bundle | Redistribute | Prize submission |
|---|---|---|---|---|---|---|
| ARGUS | CODE | Apache-2.0 | yes | yes | yes | yes |
| kaggle (official python client) | TOOL | Apache-2.0 | yes | yes | yes | yes |
| vc_render_tifxyz / volume-cartographer | TOOL | GPL-3.0 | yes | **no** | **no** | yes |
| hengck23 solution code | CODE | MIT | yes | yes | yes | yes |
| Third-party model weights (no declared licence) | MODEL_WEIGHTS | UNDECLARED | yes | **no** | **no** | **no** |
| Herculaneum CT volumes | DATA | CC BY-NC 4.0 | yes | **no** | **no** | yes |
| ink_9um labels | LABELS | UNDECLARED | yes | **no** | **no** | yes |
| timm / torch / OpenCV / zarr | CODE | Apache-2.0 / BSD-3-Clause | yes | yes | yes | yes |
| scikit-learn (test only) | CODE | BSD-3-Clause | yes | yes | yes | yes |

UNDECLARED is treated as a refusal, not as permission.
