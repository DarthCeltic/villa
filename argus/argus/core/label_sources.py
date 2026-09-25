"""Where human ink labels actually live."""
from __future__ import annotations

import dataclasses

CONTRACT_ID = "argus-label-sources-v1"

ACCESS = (
  "PUBLIC_S3",
  "PUBLIC_HF_BUCKET",
  "HF_DATASET_REPO",
  "GATED_ACCEPT_TERMS",
  "PRIVATE",
)

CONTENT = ("HUMAN_INK_LABELS", "MODEL_PREDICTIONS", "SURFACE_GEOMETRY", "CT_VOLUME",
           "SUPERVISION_MASK", "VALIDATION_MASK")


class LabelSourceError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class LabelSource:
    """One place labels can be got from, with the command that proves it."""

    name: str
    uri: str
    access: str
    content: tuple
    scrolls: tuple
    segments: int
    bytes_total: int | None
    pitch_um: float | None
    verified_utc: str
    list_command: str
    note: str = ""

    def __post_init__(self):
        if self.access not in ACCESS:
            raise LabelSourceError("unknown access %r" % (self.access,))
        for c in self.content:
            if c not in CONTENT:
                raise LabelSourceError("unknown content %r" % (c,))
        if not self.list_command:
            raise LabelSourceError(
                "a source must carry the command that lists it. A verdict without a "
                "reproducible check cannot be re-run or audited.")

    @property
    def obtainable(self) -> bool:
        return self.access in ("PUBLIC_S3", "PUBLIC_HF_BUCKET", "HF_DATASET_REPO")

    @property
    def has_human_labels(self) -> bool:
        return "HUMAN_INK_LABELS" in self.content


SOURCES = (
  LabelSource(
    name="ink_9um_native9",
    uri="hf://buckets/scrollprize/datasets/ink_9um/labels/native9-scrollprizeorg-21slices",
    access="PUBLIC_HF_BUCKET",
    content=("HUMAN_INK_LABELS", "SUPERVISION_MASK"),
    scrolls=("PHerc0139",),
    segments=5, bytes_total=5_761_707, pitch_um=9.362,
    verified_utc="2026-09-10T02:35:00Z",
    list_command="hf buckets ls -R hf://buckets/scrollprize/datasets/ink_9um/labels/"
                 "native9-scrollprizeorg-21slices/",
    note="annotations made directly on native 9.362 um volumes."),
  LabelSource(
    name="ink_9um_aligned",
    uri="hf://buckets/scrollprize/datasets/ink_9um/labels/aligned-scrollprizeorg-21slices",
    access="PUBLIC_HF_BUCKET",
    content=("HUMAN_INK_LABELS", "SUPERVISION_MASK", "VALIDATION_MASK"),
    scrolls=("PHerc0139", "PHerc1667", "PHercParis4", "PHerc0814"),
    segments=24, bytes_total=27_590_175, pitch_um=9.6,
    verified_utc="2026-09-10T02:35:00Z",
    list_command="hf buckets ls -R hf://buckets/scrollprize/datasets/ink_9um/labels/"
                 "aligned-scrollprizeorg-21slices/",
    note="annotations transferred onto 2.4 um surface volumes then pooled 4x to ~9.6 um. "
         "Validation masks exist for 3 segments only."),
  LabelSource(
    name="ink_labels_2um",
    uri="hf://buckets/scrollprize/datasets/ink",
    access="PUBLIC_HF_BUCKET",
    content=("HUMAN_INK_LABELS", "SUPERVISION_MASK", "VALIDATION_MASK",
             "SURFACE_GEOMETRY", "CT_VOLUME"),
    scrolls=("PHerc0009B", "PHerc0139", "PHerc1667", "PHerc0814",
             "PHerc0841", "PHercParis4"),
    segments=-1, bytes_total=None, pitch_um=2.4,
    verified_utc="2026-09-10T02:33:00Z",
    list_command="hf buckets ls hf://buckets/scrollprize/datasets/ink/",
    note="the full-resolution label set, hundreds of GB because it ships surface volumes "
         "alongside the labels. Segment count not enumerated; per-scroll listing is cheap."),
  LabelSource(
    name="open_data_s3",
    uri="https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com",
    access="PUBLIC_S3",
    content=("MODEL_PREDICTIONS", "SURFACE_GEOMETRY", "CT_VOLUME"),
    scrolls=("all published",), segments=-1, bytes_total=None, pitch_um=None,
    verified_utc="2026-09-10T02:20:00Z",
    list_command="curl '<uri>/?list-type=2&prefix=<scroll>/representations/&delimiter=/'",
    note="NO human ink labels. representations/ holds predictions only, and a per-segment "
         "tif named ...tile256-stride128 is a model output, not an annotation."),
  LabelSource(
    name="hercunet_corpus_stage1_pseudolabels",
    uri="hf://datasets/jimmylomro/hercunet-corpus",
    access="HF_DATASET_REPO",
    content=("MODEL_PREDICTIONS", "SUPERVISION_MASK"),
    scrolls=("PHerc0125", "PHerc0175A", "PHerc0175B", "PHerc0191", "PHerc0211",
             "PHerc0268", "PHerc0306B", "PHerc0343", "PHerc0358", "PHerc0483A", "PHerc0483B",
             "PHerc0490A", "PHerc0490B", "PHerc0800", "PHerc0813", "PHerc0826", "PHerc0846B",
             "PHerc1218", "PHerc1447", "PHerc1545"),
    segments=-1, bytes_total=None, pitch_um=None,
    verified_utc="2026-09-16T00:00:00Z",
    list_command="curl -sL https://huggingface.co/api/datasets/jimmylomro/hercunet-corpus/"
                 "revision/main  # then fetch corpus_index/corpus_windows.txt and "
                 "corpus_index/m7_windows.txt for the per-window scroll tokens",
    note="INTAKE ONLY -- registration of existence, location and licence, not a pulled "
         "corpus. The stage-1 windows are the model's own pseudo-labels, not human "
         "annotations. Licence is 'see the GitHub repository' on the dataset card -- "
         "MIT-BY-POINTER, not a native HF dataset licence tag; treat it as weaker than a "
         "standalone data-licence grant until the author confirms it covers the "
         "pseudo-label data itself. The corpus has NOT been downloaded; this row is metadata "
         "only. A scroll listed here is exposed to this corpus and may not serve as an "
         "independent control for a model trained on it."),
)

CORRECTION = {
  "was": "a label source can be reported as gated or absent after a search that could "
         "not see it",
  "is": "the human ink labels listed above are in PUBLIC HuggingFace storage buckets",
  "why_it_was_believed": "a dataset-search API cannot see storage buckets and returns an "
                         "empty list, and a bucket that publishes predictions only can make "
                         "that empty result look corroborated",
  "the_general_lesson": "record the command, not the verdict. 'I searched and found nothing' "
                        "is a statement about the search.",
}


def obtainable_human_label_sources() -> list:
    return [s for s in SOURCES if s.has_human_labels and s.obtainable]


def scrolls_with_human_labels(max_pitch_um: float | None = None) -> list:
    out = set()
    for s in obtainable_human_label_sources():
        if max_pitch_um is not None and (s.pitch_um or 0) > max_pitch_um:
            continue
        out.update(s.scrolls)
    return sorted(out)


ELIGIBLE_PITCH_BAND_UM = (8.0, 10.0)


def cross_scroll_feasibility(min_scrolls: int = 3) -> dict:
    """Can a leave-one-scroll-out gate be run at all, and on what?"""
    lo, hi = ELIGIBLE_PITCH_BAND_UM
    at_9um = [s for s in obtainable_human_label_sources()
              if s.pitch_um is not None and lo <= s.pitch_um <= hi]
    scrolls = sorted({sc for s in at_9um for sc in s.scrolls})
    return {
      "contract": CONTRACT_ID,
      "min_scrolls_required": min_scrolls,
      "pitch_band_um": list(ELIGIBLE_PITCH_BAND_UM),
      "scrolls_with_human_labels_near_9um": scrolls,
      "count": len(scrolls),
      "feasible": len(scrolls) >= min_scrolls,
      "sources": [s.name for s in at_9um],
      "total_bytes": sum(s.bytes_total or 0 for s in at_9um),
      "native_9um_scrolls": sorted({sc for s in at_9um if s.name.endswith("native9")
                                    for sc in s.scrolls}),
      "caveat": "feasible means the DATA exists and is obtainable. It does not mean the gate "
                "passes, and it says nothing about label quality, supervision coverage or "
                "whether the eligible targets resemble these scrolls.",
      "what_this_does_not_unblock": [
        "any search of an unread scroll",
        "any claim about an eligible target",
        "any acquisition family not represented here"],
    }


def as_record() -> dict:
    return {
      "contract": CONTRACT_ID,
      "correction": CORRECTION,
      "sources": [dataclasses.asdict(s) | {"obtainable": s.obtainable,
                                           "has_human_labels": s.has_human_labels}
                  for s in SOURCES],
      "cross_scroll": cross_scroll_feasibility(),
    }
