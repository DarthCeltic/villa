# Provenance and reproduction

ARGUS writes every result as a **receipt**: an atomically written JSON file that records what ran,
on what input, under which rule, and what class of claim the result supports. A figure without a
receipt does not count.

## Example 1: reproduce the demo exactly

```powershell
.venv\Scripts\python -m argus demo --no-render --out run_a.json
.venv\Scripts\python -m argus demo --no-render --out run_b.json
```

Both receipts carry the same metric block. The fixture seed (`20260910`) and size (96 x 96) are
fixed and recorded in the receipt, so the numbers are identical on every machine:

```json
{
 "schema": "argus-cli-demo-v1",
 "metric": {"rule_id": "argus-metric-v1", "auc": 0.9376..., "ap": 0.3012..., "n": 9216, "n_positive": 299},
 "seed": 20260910,
 "size": 96,
 "offline": true,
 "gpu_used": false,
 "result": {"presentation": "DEMONSTRATION_ONLY", "target_class": "SYNTHETIC", "...": "..."}
}
```

`tests/test_public_release.py::test_demo_is_deterministic` checks this on every run.

## Example 2: a score always carries its rule

```python
from argus.core import metrics
metrics.score([0.9, 0.8, 0.4, 0.3, 0.1], [1, 0, 1, 0, 0])
# {'rule_id': 'argus-metric-v1', 'auc': 0.8333..., 'ap': 0.8333..., 'n': 5, 'n_positive': 2}
```

If the numeric definition ever changes, `RULE_ID` changes with it, so two receipts computed under
different rules cannot be confused.

## Example 3: the claim a result supports is derived, not chosen

```python
from argus.core.result_class import ResultClass
rc = ResultClass(target="synthetic fragment", target_class="LABELLED_FRAGMENT",
                 exposure_basis="HELD_OUT_BY_FOLD", detector="any detector",
                 detector_cross_scroll_qualified=False, acquisition="declared",
                 metric="AUC", score=0.75)
rc.presentation        # 'KNOWN_DOMAIN_HELD_OUT_CONTROL'
rc.display_banner()    # '... This proves the pipeline, not a discovery.'
rc.assert_not_discovery("we read the scroll")   # raises ResultClassError
```

## Example 4: verify this release

```powershell
.venv\Scripts\python tools\verify_manifest.py
```

`RELEASE_MANIFEST.json` lists the sha256 of every file in this release. The command exits non-zero
if any file is missing, has changed, or is tracked but not listed.

## What reproduction does not cover here

This release contains no CT data, labels or model weights (their licences do not allow
redistribution). Reproducing a detector result needs those inputs fetched under their own terms
with `argus setup`, and the result is still subject to the claim boundary: this release ships no
detector and makes no scientific claim. A rendered candidate is not a reading. Every export packet is
`never_published`.
