"""Blinded human-review queues and the gate that keeps every reading path away from an unreviewed sealed task: public stub.

The operator's sealed review packages, their task schemas and screening diagnostics are not part
of the public release. This stub keeps every name the service, the action registry and the
reading paths use. No sealed queue is installed, so every queue reads NOT_INSTALLED, every sealed
task id is refused as unknown, and no answer can be recorded. The gate itself is kept: a reading
path handed a sealed task id is refused.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from argus.core import actions as A
from argus.core import review_lab as RL

CONTRACT_ID = "argus-sealed-review-queue-v1"
PUBLIC_NOTE = "sealed review packages are not part of the public release"
SEALS_IMPORT = "sealed_review_seals"
KIND_FILES = {"scroll_blocks": "REVIEW_TASKS_BLOCKS.json"}
TASK_TYPE = "JUDGE_SEALED_REGION"
PROMPT_VERSION = "argus-sealed-review-v1"
CHOICES = RL.SEALED_CHOICES
CHOICE_MEANING = {
    "LIKELY_SIGNAL": "a structure here could be a written mark on the sheet surface (a place worth a closer "
                     "look; not a claim of ink)",
    "LIKELY_STRUCTURE": "a structural feature of the material (fibre, fold, edge, texture) that is not a mark",
    "UNCERTAIN": "cannot tell from what is shown",
    "REFUSE": "abstain: decline to judge this task. Recorded and shown; never counted as agreement",
}
#: Sealed task ids: a queue prefix, a 17-digit stamp and a region index.
SEALED_ID_RE = re.compile(r"(?<![A-Za-z0-9])(PHB-\d{17}-R\d+)(?![A-Za-z0-9])")
GATE_STATEMENT = ("No reading, OCR, transcription or translation consumes a sealed task until two named people "
                  "have independently reviewed it with the image in front of them and none of the answers is "
                  "an AI class.")
ANSWER_FIELDS = frozenset({"kind", "task_id", "value", "reviewer_id", "reviewer_class", "images_viewed",
                           "confidence", "duration_s", "notes", "reviewer_session", "approved_plan_sha256"})


class SealedQueueError(ValueError):
    pass


class UnreviewedTaskRefused(RuntimeError):
    """A reading path was handed a sealed task that has not been promoted."""

    def __init__(self, refs: dict):
        self.refs = refs
        super().__init__(
            "sealed review task(s) %s are not promoted: %s. %s"
            % (", ".join(sorted(refs)), "; ".join("%s (%s)" % (k, ",".join(v) or "unreviewed")
                                                  for k, v in sorted(refs.items())), GATE_STATEMENT))


def load_sealed(kind: str, *, root: Path | None = None) -> dict:
    if kind not in KIND_FILES:
        raise SealedQueueError("unknown queue %r; the queues are %s" % (kind, sorted(KIND_FILES)))
    raise SealedQueueError(PUBLIC_NOTE)


def find_task(kind: str, task_id: str, *, root: Path | None = None) -> dict:
    load_sealed(kind, root=root)
    raise SealedQueueError("no task %r in the %s queue" % (task_id, kind))


def kind_of(task_id: str, *, root: Path | None = None) -> str | None:
    return None


def queue_notice(kind: str) -> dict:
    return {"gate": GATE_STATEMENT, "blinding": "which windows are controls is withheld by the server; "
                                               "other reviewers' answers are withheld until you answer",
            "abstention": CHOICE_MEANING["REFUSE"], "no_ink_claim": "nothing here is presented as ink and no "
                                                                      "detector was run"}


def store_dir(kind: str) -> Path:
    from argus.core import paths
    return paths.artifact_write_root() / "review" / kind


def promotion(task_id: str, *, root: Path | None = None) -> dict | None:
    return None


def sealed_refs(obj, _depth: int = 0) -> set:
    """Every sealed task id mentioned anywhere in `obj` (values and keys, any depth)."""
    found: set = set()
    if _depth > 12:
        return found
    if isinstance(obj, str):
        found.update(SEALED_ID_RE.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found.update(sealed_refs(k, _depth + 1))
            found.update(sealed_refs(v, _depth + 1))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            found.update(sealed_refs(v, _depth + 1))
    return found


def unpromoted(obj, *, root: Path | None = None) -> dict:
    """{sealed task id: [reasons]} for every sealed id in `obj`; none can be promoted here."""
    return {ref: ["UNKNOWN_SEALED_TASK"] for ref in sorted(sealed_refs(obj))}


def assert_promoted(obj, *, root: Path | None = None) -> dict:
    """The module-boundary gate."""
    refs = sealed_refs(obj)
    if not refs:
        return {"checked": 0}
    raise UnreviewedTaskRefused(unpromoted(obj, root=root))


def refuse_sealed_ai(task_id: str) -> None:
    if sealed_refs(task_id):
        raise RL.ReviewRefusal(
            "%s is a sealed blinded review task. A model's proposal is never recorded on it: an AI answer "
            "would disqualify the task from promotion, and no OCR/HTR path may consume an unreviewed task."
            % task_id)


def queue_view(kind: str, *, reviewer: str | None = None, root: Path | None = None) -> dict:
    load_sealed(kind, root=root)
    return unavailable_queue_view(kind)


def unavailable_queue_view(kind: str) -> dict:
    """Return the truthful empty contract for an optional queue data pack."""
    return {"schema": "argus-sealed-review-queue-view-v1", "contract": CONTRACT_ID,
            "read_only": True, "queue": kind, "state": "NOT_INSTALLED",
            "integrity": "NOT_VERIFIED",
            "why": "the sealed review package is not installed on this host (%s)" % PUBLIC_NOTE,
            "n_tasks": 0, "root_sha256": "", "sealed": False, "tasks": [],
            "answered_tasks": 0, "promotable_tasks": 0,
            "choices": [{"value": c, "meaning": CHOICE_MEANING[c]} for c in CHOICES],
            "notice": queue_notice(kind)}


def task_view(kind: str, task_id: str, *, root: Path | None = None) -> dict:
    return find_task(kind, task_id, root=root)


def results_view(kind: str, task_id: str, reviewer: str | None, *, root: Path | None = None) -> dict:
    return find_task(kind, task_id, root=root)


def layer_file(kind: str, task_id: str, name: str, *, root: Path | None = None) -> dict:
    return find_task(kind, task_id, root=root)


def _plan_hash(action: str, body: dict) -> str:
    b = {k: v for k, v in body.items() if k != "plan_sha256"}
    return hashlib.sha256(json.dumps({"action": action, "body": b}, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def plan_answer(p: dict) -> dict:
    out = {"changes": ["would record one named person's answer on one sealed task; no sealed queue is "
                       "installed in the public build"],
           "cost": {"bytes": "< 1 KB", "gpu": "none", "seconds": "< 2"}, "leases": [], "reversible": False,
           "may_refuse": ["HUMAN_ACTOR_REQUIRED", "NO_SUCH_TASK"],
           "read_only": False, "evidence_role": RL.HUMAN_ROLE, "gate": GATE_STATEMENT,
           "would_be_refused": True, "refusal": {"code": "NO_SUCH_TASK", "why": PUBLIC_NOTE}}
    out["plan_sha256"] = _plan_hash("review.blind.answer", out)
    return out


def do_answer(spec: A.ActionSpec) -> dict:
    if not str(spec.actor).startswith("human:"):
        raise A.Refused("HUMAN_ACTOR_REQUIRED",
                        "review.blind.answer records a person's judgment and can only be requested by a "
                        "human actor; %r is not one. A script cannot become a reviewer." % spec.actor)
    raise A.Refused("NO_SUCH_TASK", PUBLIC_NOTE)


PLANNERS = {"review.blind.answer": plan_answer}
DOERS = {"review.blind.answer": do_answer}
