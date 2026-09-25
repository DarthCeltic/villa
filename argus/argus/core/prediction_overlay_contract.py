"""The metadata every prediction overlay must carry before any viewer may offer it, enforced where the overlay is registered."""
from __future__ import annotations

import re

ARTIFACT_KIND = "MODEL_OUTPUT"

EXPOSURE_WORDS = {
    "TRAINING_EXPOSED": "TRAINING-EXPOSED",
    "EXPOSURE_UNKNOWN": "EXPOSURE UNKNOWN",
    "HELD_OUT_UNVERIFIED": "HELD OUT (UNVERIFIED)",
}
CONTROL_WORDS = {
    "SABOTAGE_FAILED": "SABOTAGE FAILED",
    "SABOTAGE_PASSED": "SABOTAGE PASSED",
    "CONTROLS_NOT_RUN": "SABOTAGE CONTROLS NOT RUN",
}
QUALIFICATION_WORDS = {"NOT_QUALIFIED": "NOT QUALIFIED"}
REQUIRED_FIELDS = ("artifact_kind", "provider", "exposure_state", "control_verdict", "qualification_state")

_CONFIRMING = re.compile(r"\b(ink|ink[- ]?detect\w*|letters?|text|writing|reading|glyphs?|confirmed|detected|found)\b", re.I)


class ContractRefusal(ValueError):
    def __init__(self, code: str, why: str):
        super().__init__("%s: %s" % (code, why))
        self.code = code
        self.why = why


def problems(meta) -> list[dict]:
    """Every reason this metadata does not satisfy the contract, as {code, why}."""
    out: list[dict] = []
    if not isinstance(meta, dict):
        return [{"code": "PREDICTION_METADATA_ABSENT", "why": "the overlay declares no prediction metadata"}]
    for f in REQUIRED_FIELDS:
        if meta.get(f) in (None, "", {}):
            out.append({"code": "PREDICTION_FIELD_MISSING", "why": "required field %r is absent" % f})
    if meta.get("artifact_kind") not in (None, "", ARTIFACT_KIND):
        out.append({"code": "ARTIFACT_KIND_NOT_MODEL_OUTPUT", "why": "artifact_kind is %r; a prediction overlay is a %s and nothing else" % (meta.get("artifact_kind"), ARTIFACT_KIND)})
    prov = meta.get("provider")
    if prov not in (None, "", {}):
        if not isinstance(prov, dict) or not str(prov.get("id") or "").strip():
            out.append({"code": "PROVIDER_IDENTITY_MISSING", "why": "provider must be an object with a non-empty id"})
        elif _CONFIRMING.search(str(prov.get("id"))):
            out.append({"code": "PROVIDER_NAMED_AS_FINDING", "why": "a provider id names who made the output, not what it claims to find"})
    for field, words, code in (("exposure_state", EXPOSURE_WORDS, "EXPOSURE_STATE_UNRECOGNISED"), ("control_verdict", CONTROL_WORDS, "CONTROL_VERDICT_UNRECOGNISED")):
        v = meta.get(field)
        if v not in (None, "", {}) and v not in words:
            out.append({"code": code, "why": "%s %r is not one of %s" % (field, v, sorted(words))})
    q = meta.get("qualification_state")
    if q not in (None, "", {}) and q not in QUALIFICATION_WORDS:
        out.append({"code": "QUALIFICATION_STATE_REFUSED", "why": "qualification_state %r is refused: no detector is qualified, so only NOT_QUALIFIED is accepted" % (q,)})
    label = meta.get("label")
    if isinstance(label, str) and _CONFIRMING.search(label):
        out.append({"code": "LABEL_IMPLIES_A_FINDING", "why": "the label %r would present a model output as a finding" % label})
    return out


def visible_status(meta: dict) -> list[str]:
    """The words shown with the overlay, in a fixed order, derived from the declared fields."""
    bad = problems(meta)
    if bad:
        raise ContractRefusal(bad[0]["code"], bad[0]["why"])
    return ["MODEL OUTPUT", EXPOSURE_WORDS[meta["exposure_state"]], CONTROL_WORDS[meta["control_verdict"]], QUALIFICATION_WORDS[meta["qualification_state"]]]


def describe(meta: dict) -> dict:
    """The contract-checked descriptor a client may be given: identity fields and the derived status words, nothing else."""
    words = visible_status(meta)
    return {"artifact_kind": ARTIFACT_KIND, "provider": {"id": str(meta["provider"]["id"]), "version": meta["provider"].get("version")},
            "exposure_state": meta["exposure_state"], "control_verdict": meta["control_verdict"], "qualification_state": meta["qualification_state"],
            "status_words": words, "label": "Model output (%s)" % meta["provider"]["id"]}
