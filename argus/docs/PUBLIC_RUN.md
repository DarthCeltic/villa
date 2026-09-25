# A real run over public data: `argus run <target manifest>`

`argus run` has two forms. `argus run --target NAME` is the governed research driver and is not part of
this release. `argus run <manifest>` is a small public driver: it reads one JSON target manifest and runs
the stages that manifest lists, using only tools this release ships and tools from Villa's own MIT tree.
It adds no science and no detector choice.

## The target that ships

`argus/public_targets/pherc0139-w016-ink9um-control.json` names a public, non-prize control:

- a 640 by 896 pixel crop (level 2, 9.596 um) of the public surface volume of PHerc0139 segment w016,
  at most 62.5 MB of chunks over HTTPS;
- the published ink, supervision and validation labels for that segment (a few kilobytes of chunks);
- the released `scrollprize/ink_9um` checkpoint, pinned by revision and sha256 (138 MB, MIT).

PHerc0139 is on neither prize list in the public survey (`argus identify` shows it), and the driver
re-checks that before it fetches anything.

## Stages

| Stage | What it does | Made of |
|---|---|---|
| identify | The surface volume URL resolves, through the public survey, to the declared scroll and volume | `argus.core.official_identity` |
| gate_target | The scroll is on no prize list | the survey's `prize_sets` |
| acquire_ct | Fetch exactly the declared crop, byte-ceilinged, then seal it with its identity | `argus.core.volume_acquisition` |
| acquire_labels | Fetch the label chunks that touch the crop | HTTPS, byte-ceilinged |
| acquire_model | Use a local file with the pinned sha256, or download and verify it | HTTPS, sha256 |
| inspect | Chunk-identity probe: every chunk present, not fill | `evidence_gate` |
| prepare | Pool the crop into the 21-slice 9 um input | Villa `prepare_9um_isotropic_input` |
| contract | The checkpoint's own config must accept the input; exposure is read from it | checkpoint config |
| infer | Flat inference in fp32 | Villa `ink_detection.inference.infer` |
| score | AUC and AP inside the validation mask, with the ink prior | `argus.core.metrics` |
| receipt | Result class, run receipt, stage lineage | `argus.core.result_class`, `stage_lineage` |

## Commands

```powershell
# explain every stage and what it needs; fetches and writes nothing
.venv\Scripts\python -m argus run pherc0139-w016-ink9um-control --dry-run

# stage the pinned checkpoint and issue one single-use authorisation for exactly this run
.venv\Scripts\python -m argus run pherc0139-w016-ink9um-control --authorize MY-RUN-1

# run it (consumes the authorisation)
.venv\Scripts\python -m argus run pherc0139-w016-ink9um-control --authorization-id MY-RUN-1
```

It needs a Python that has Villa's `vesuvius` package and PyTorch (set `ARGUS_VILLA_PYTHON`, or place it
under `ARGUS_HOME/runtimes/villa-vesuvius`) and a CUDA GPU. On a card without fp16 tensor cores (a
GTX 1660 Ti) the driver runs fp32 only; see the limits below.

## What the result is

The receipt (`artifacts/public_pipeline_runs/<target>-<run id>/PIPELINE_RUN_RECEIPT.json`) carries a result
class read from the checkpoint's own config. The checkpoint reserved this segment's validation region from
its training supervision, so the class is `KNOWN_DOMAIN_HELD_OUT_CONTROL`: a control proves the pipeline, not
a discovery. The route shows the run: acquisition done, ink inference done, candidate review waiting for a
human.

## Limits

- One crop, one checkpoint, one direction. The score says the pipeline moves; it says nothing about any
  other scroll or detector.
- The scored pixels are held out by region inside a segment the checkpoint has seen, not by scroll.
- Villa's `--amp-dtype default` still autocasts to float16 on CUDA. On a GTX 1660 Ti that yields NaN, which
  the TIFF writer turns into zeros (a constant map, AUC 0.5). The driver replaces autocast with a null
  context before Villa runs and refuses a run whose fp32 start-up does not report itself.
- Robust-MAD normalisation runs over the crop, not the whole segment.
- The store's identity proves the crop's key list; pitch and energy come from the public survey, not from the
  store.

## Data terms

The CT data and the labels are Vesuvius Challenge open data, CC BY-NC 4.0 (non-commercial, attribution).
Cite the dataset: Vesuvius Challenge, open data, PHerc0139 (https://scrollprize.org/data). The checkpoint is
MIT per its Hugging Face card and Villa is MIT. This release ships none of them.
