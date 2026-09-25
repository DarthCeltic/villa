# Third-party acknowledgements

> GENERATED from `argus/core/licence_registry.py`.
> Do not edit by hand: a credits page maintained separately drifts from the registry the
> moment a component is added, and nobody diffs a credits page.

ARGUS stands on other people's work. This page names it, and — just as important — says
which of it this repository is **not** permitted to redistribute.

## What the ARGUS licence covers

The Apache-2.0 grant in `LICENSE` covers **ARGUS-owned source code only**. It does not
cover CT data, upstream labels, vendored tooling or model weights. Each of those carries
its own terms, listed below. Shipping one repository licence as though it covered the
data would be a redistribution claim nobody here has the right to make.

## Included, under their own terms

| Component | Kind | Terms | Source |
| --- | --- | --- | --- |
| ARGUS | CODE | Apache-2.0 | this repository |
| torch / OpenCV / zarr | CODE | Apache-2.0 / BSD-3-Clause | PyPI |
| scikit-learn (test only) | CODE | BSD-3-Clause | PyPI scikit-learn |

## Used but NOT redistributed by this repository

These are fetched by the person running ARGUS, from their own upstream, under the
terms those upstreams set. They are not vendored here and are not in any release
archive. A clean clone of this repository contains none of them.

| Component | Kind | Terms | Source |
| --- | --- | --- | --- |
| vc_render_tifxyz / volume-cartographer | TOOL | GPL-3.0 | ScrollPrize/villa |
| Herculaneum CT volumes | DATA | CC BY-NC 4.0 | vesuvius-challenge-open-data S3 |

## UNDECLARED terms — a refusal, not a permission

For these, no licence could be established from the material as published. ARGUS
treats an undeclared term as a REFUSAL: the component may be referenced and measured
against, and it may not be redistributed, relicensed, or included in any release or
prize package until its terms are established by its owner.

This is deliberately not rounded off to "probably fine". An undeclared licence is
the one case where a good-faith guess creates the exact harm the licence exists to
prevent.

None at present. A component added with undeclared terms appears here.

## Vesuvius Challenge

ARGUS exists because the Vesuvius Challenge published its scans, its tooling and its
results openly. The scroll data, the segmentation tooling and the community's
accumulated methods are the foundation this instrument is built on.

## Decorative assets

The header illustration (`argus/ui/public/brand/argus-villa-header-v3-master.png`) is a
decorative illustration of a Roman villa. It is not a photograph, a reconstruction, a
measurement or a source, and it is never cited as scientific or historical evidence.

---

Generated 2026-09-12T05:31:53Z from registry `argus-licence-registry-v1`.
