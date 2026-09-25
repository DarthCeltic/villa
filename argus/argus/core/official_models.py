"""The official ScrollPrize released models, pinned by immutable revision and file hash."""
from __future__ import annotations

import dataclasses
import json
import urllib.request

CONTRACT = "argus-official-models-v1"

SEMANTICS_CONTRADICTED = "SEMANTICS_CONTRADICTED"
PHYSICAL_FACE_MAPPING_UNRESOLVED = "PHYSICAL_FACE_MAPPING_UNRESOLVED"
DECLARED_CLASS_MEANING_UNTESTED = "DECLARED_CLASS_MEANING_UNTESTED"

HF_API = "https://huggingface.co/api/models/%s?blobs=true"
HF_FILE = "https://huggingface.co/%s/resolve/%s/%s"


class ModelRegistryError(RuntimeError):
    """Raised rather than returning a partially resolved pin."""


@dataclasses.dataclass(frozen=True)
class OfficialModel:
    repo_id: str
    revision: str
    licence: str
    labels: dict
    channels: dict
    num_training: int
    weight_file: str
    weight_sha256: str
    weight_bytes: int
    why_registered: str
    what_it_does_not_establish: str
    semantic_state: str


MODELS = (
  OfficialModel(
    repo_id="scrollprize/fiber_ink_4class_selfdistill",
    revision="ec9bbc4dbc65a052fc4d78429f7ce5eff080aafe",
    licence="mit",
    labels={"background": 0, "vertical_fiber": 1, "horizontal_angular_fiber": 2, "ink": 3},
    channels={"0": "CT"},
    num_training=0,
    weight_file="p4_4class_ddp8_20260526_step029000.pth",
    weight_sha256="feef7fad198c5c53ede4193cf2686a515be574beb110cc957bd7984b52085bf2",
    weight_bytes=2260148109,
    why_registered="the released ink-headed model with a declared licence.",
    what_it_does_not_establish=
      "what its `ink` class MEANS. The class is not human-labelled, so a high score agrees with "
      "the model's own training targets, not necessarily with ink. Running it on an acquisition "
      "other than the one it was trained on is a cross-acquisition transfer as well as a "
      "cross-scroll one, and any result must report both gaps.",
    semantic_state="INK_CLASS_MEANING_UNVERIFIED",
  ),
  OfficialModel(
    repo_id="scrollprize/surface_recto_verso",
    revision="c93bb4ab0ba9d37cf7707637109cb7b729a10b24",
    licence="apache-2.0",
    labels={"background": 0, "surface1": 1, "surface2": 2, "intersection": 3},
    channels={"0": "T2"},
    num_training=31,
    weight_file="checkpoint_final.pth",
    weight_sha256="bddc7d00bfb7d045e7a58ffe2b42257f3dea73936935c2e862a1e16122afdaf8",
    weight_bytes=1132312018,
    why_registered="it is the released multiclass surface model, and it is the reason ARGUS "
                   "does not need to build a face classifier before trying the published one.",
    what_it_does_not_establish="any mapping from surface1/surface2 to physical recto/verso, in "
                               "EITHER direction. The artifact declares two surface classes and "
                               "does not declare their physical correspondence. An earlier draft "
                               "said they 'are not recto and verso', which overclaims the "
                               "negative exactly as assuming they are would overclaim the "
                               "positive: absence of a declaration is not evidence of "
                               "non-correspondence. `intersection` is the model's own declared "
                               "fourth class and is preserved independently; what it physically "
                               "represents is untested, and it is NOT assumed to be a composite "
                               "sheet -- overlap in a model's output has several possible "
                               "causes.",
    semantic_state=PHYSICAL_FACE_MAPPING_UNRESOLVED,
  ),
  OfficialModel(
    repo_id="scrollprize/surface_recto",
    revision="86f026f8be537db336e2854650db0b40c004ad49",
    licence="apache-2.0",
    labels={"background": 0, "fiber": 1},
    channels={"0": "CT"},
    num_training=1754,
    weight_file="checkpoint_final.pth",
    weight_sha256="2e7e4be80b2e3640ad5cf6bbf84628808c67bf6ef955288b27c2b34be80c776f",
    weight_bytes=819319514,
    why_registered="named as a recto surface model and used as a comparison arm.",
    what_it_does_not_establish="either identity, and that is the point. Its repository name and "
                               "documentation say recto surface; its own dataset.json declares a "
                               "single foreground label named `fiber`. Two pieces of evidence "
                               "from the same artifact disagree. An earlier draft of this file "
                               "resolved that in favour of dataset.json -- 'whatever it does, it "
                               "does not declare a recto surface class' -- which is a verdict, "
                               "not a reading. A label name is not a guarantee either: `fiber` "
                               "may be a leftover from a template. The honest state is "
                               "SEMANTICS_CONTRADICTED until inference on a known-side control "
                               "shows which description its behaviour matches.",
    semantic_state=SEMANTICS_CONTRADICTED,
  ),
  OfficialModel(
    repo_id="scrollprize/fiber_hz_vt",
    revision="0905e68f14b33d1b98fd32d726f2c97607dea80c",
    licence="apache-2.0",
    labels={"background": 0, "vt-fiber": 1, "hz-fiber": 2, "intersection": 3},
    channels={"0": "T2"},
    num_training=18,
    weight_file="checkpoint_final.pth",
    weight_sha256="a80d98f1baaaa09fac5e9d499ade91cb6f79982239cf8520fec9c217125ed899",
    weight_bytes=1132287954,
    why_registered="an INDEPENDENT cue. Fibre orientation is a different physical property from "
                   "face, and a second cue that agrees is worth more than a louder first one.",
    what_it_does_not_establish="a face label. Fibre direction is a control and a cue; it is "
                               "never a substitute for a recto/verso label, and agreement "
                               "between it and a surface class is evidence about the two "
                               "models, not about the scroll.",
    semantic_state=DECLARED_CLASS_MEANING_UNTESTED,
  ),
)

MULTICLASS_CLASSES = ("background", "surface1", "surface2", "intersection")


def by_id(repo_id: str) -> OfficialModel:
    for m in MODELS:
        if m.repo_id == repo_id:
            return m
    raise ModelRegistryError("not a registered official model: %r" % repo_id)


def verify_against_upstream(repo_id: str, *, timeout: int = 60) -> dict:
    """Re-resolve the pin from upstream and report agreement."""
    m = by_id(repo_id)
    try:
        d = json.loads(urllib.request.urlopen(HF_API % repo_id, timeout=timeout).read())
    except Exception as exc:
        return {"repo_id": repo_id, "checked": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    live_rev = d.get("sha")
    weights = {s.get("rfilename"): (s.get("lfs") or {}) for s in d.get("siblings", [])}
    lfs = weights.get(m.weight_file, {})
    return {
      "repo_id": repo_id,
      "checked": True,
      "pinned_revision": m.revision,
      "live_revision": live_rev,
      "revision_matches": live_rev == m.revision,
      "pinned_weight_sha256": m.weight_sha256,
      "live_weight_sha256": lfs.get("sha256"),
      "weight_matches": lfs.get("sha256") == m.weight_sha256,
      "note": "a mismatch means upstream moved. The pin is NOT updated here: a registry that "
              "follows upstream is not a pin, and the run that used it needs to know.",
    }


def assert_all_classes_preserved(classes) -> None:
    """Refuse an output that has already collapsed the four classes."""
    got = tuple(classes)
    if set(MULTICLASS_CLASSES) - set(got):
        raise ModelRegistryError(
          "output declares %s; all four of %s must survive. `intersection` is the model's own "
          "declared class: folding it into surface1 or surface2 destroys an observation at the "
          "moment it is made, before anything has tested what it represents."
          % (list(got), list(MULTICLASS_CLASSES)))


def as_record() -> dict:
    return {
      "contract": CONTRACT,
      "models": [dataclasses.asdict(m) for m in MODELS],
      "multiclass_classes": list(MULTICLASS_CLASSES),
      "pinned_by": "immutable revision and weight sha256, never a branch name. `main` moves, "
                   "and a receipt that names a branch names nothing.",
      "nothing_is_downloaded_here": "this module pins and verifies. It does not fetch weights "
                                    "and does not run inference.",
      "semantic_states": {m.repo_id: m.semantic_state for m in MODELS},
      "physical_face_mapping_is_unresolved": "the artifact declares two surface classes and does "
                                             "not declare their physical correspondence. Nothing "
                                             "here asserts the mapping in EITHER direction: "
                                             "absence of a declaration is not evidence of "
                                             "non-correspondence.",
    }
