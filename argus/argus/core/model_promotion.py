"""The evidence envelope required before a trained checkpoint can advance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argus.core import checkpoint_lifecycle as CL
from argus.core import receipts

CONTRACT = "argus-model-promotion-v1"


class PromotionRefusal(ValueError):
    """The checkpoint has not supplied a complete lifecycle evidence envelope."""


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  default=str).encode("utf-8")).hexdigest()


def _load(value, label: str) -> tuple[dict, dict]:
    if isinstance(value, dict):
        body = dict(value)
        return body, {"kind": "inline", "sha256": _sha(body)}
    if not isinstance(value, str) or not value.strip():
        raise PromotionRefusal("%s is required" % label)
    p = Path(value)
    if not p.is_file():
        raise PromotionRefusal("%s does not point to an evidence file: %s" % (label, p))
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionRefusal("%s is not valid JSON: %s" % (label, p)) from exc
    if not isinstance(body, dict):
        raise PromotionRefusal("%s must contain a JSON object" % label)
    return body, {"kind": "file", "path": str(p), "sha256": receipts.sha_file(p)}


def validate(*, model: str, to_state: str, lifecycle: dict) -> dict:
    """Validate the lifecycle packet needed for a qualifying model rung."""
    if not isinstance(lifecycle, dict):
        raise PromotionRefusal("lifecycle packet is required for a qualifying rung")
    required = ("checkpoint_id", "checkpoint_sha256", "checkpoint_class",
                "source_scroll_id", "dest_scroll_id", "artifact_kind",
                "dest_is_general", "exposure", "licence", "evaluation")
    missing = [key for key in required if lifecycle.get(key) in (None, "")]
    if missing:
        raise PromotionRefusal("lifecycle packet is missing: %s" % ", ".join(missing))
    checkpoint_sha = lifecycle["checkpoint_sha256"]
    if (not isinstance(checkpoint_sha, str) or len(checkpoint_sha) != 64 or
            any(c not in "0123456789abcdef" for c in checkpoint_sha)):
        raise PromotionRefusal("checkpoint_sha256 must be a lowercase full sha256")
    if lifecycle["checkpoint_class"] not in CL.CHECKPOINT_CLASSES:
        raise PromotionRefusal("unknown checkpoint class %r" % lifecycle["checkpoint_class"])
    try:
        CL.assert_no_lateral_contamination(
            artifact_kind=lifecycle["artifact_kind"],
            source_scroll_id=lifecycle["source_scroll_id"],
            dest_scroll_id=lifecycle["dest_scroll_id"],
            dest_is_general=bool(lifecycle["dest_is_general"]),
        )
    except CL.LifecycleRefusal as exc:
        raise PromotionRefusal(str(exc)) from exc
    exposure, exposure_ref = _load(lifecycle["exposure"], "exposure")
    licence, licence_ref = _load(lifecycle["licence"], "licence")
    evaluation, evaluation_ref = _load(lifecycle["evaluation"], "evaluation")
    if exposure.get("verified") is not True or exposure.get("unknown_channels"):
        raise PromotionRefusal("exposure must be verified with no unknown channels")
    if licence.get("verified") is not True or licence.get("admissible") is not True:
        raise PromotionRefusal("licence must be verified and admissible")
    if evaluation.get("status") != "PASS" or evaluation.get("holdout_frozen") is not True:
        raise PromotionRefusal("evaluation must PASS with a frozen holdout")
    if to_state == "HUNT_QUALIFIED" and evaluation.get("cross_scroll_controls") is not True:
        raise PromotionRefusal("HUNT_QUALIFIED requires passing cross-scroll controls")
    receipt = {
        "contract": CONTRACT,
        "verdict": "PROMOTION_PACKET_VERIFIED",
        "model": model,
        "to_state": to_state,
        "checkpoint_id": lifecycle["checkpoint_id"],
        "checkpoint_sha256": checkpoint_sha,
        "checkpoint_class": lifecycle["checkpoint_class"],
        "source_scroll_id": lifecycle["source_scroll_id"],
        "dest_scroll_id": lifecycle["dest_scroll_id"],
        "artifact_kind": lifecycle["artifact_kind"],
        "dest_is_general": bool(lifecycle["dest_is_general"]),
        "evidence": {"exposure": exposure_ref, "licence": licence_ref,
                     "evaluation": evaluation_ref},
        "scientific_boundary": "promotion packet verified; current pin and scientific qualification remain operator-controlled",
    }
    receipt["promotion_packet_sha256"] = _sha(receipt)
    return receipt
