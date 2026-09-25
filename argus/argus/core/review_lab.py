"""Review Lab: turn a hard judgement into a small, auditable task."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from typing import Iterable

CONTRACT_ID = "argus-review-lab-v1"

STATES = (
  "PROPOSED",
  "INDEPENDENTLY_REVIEWED",
  "CONSENSUS_REACHED",
  "EXPERT_OR_CONTROL_VALIDATED",
  "TRAINING_ELIGIBLE",
  "FROZEN_IN_DATASET_VERSION",
)
STATE_INDEX = {s: i for i, s in enumerate(STATES)}

SIDE_STATES = ("DISPUTED", "NEEDS_CONTEXT", "REJECTED")

TASK_TYPES = (
  "JUDGE_SEALED_REGION",
  "CHOOSE_CONTINUATION",
  "REPAIR_SURFACE_PATH",
  "PICK_BETTER_RENDER",
  "REJECT_BOTH_RENDERS",
  "MARK_SHEET_SWITCH",
  "MARK_AMBIGUOUS_REGION",
  "COMPARE_SEGMENTATIONS",
  "JUDGE_INK_CANDIDATE",
  "MARK_TEXT_LINE_DIRECTION",
  "REQUEST_MORE_CONTEXT",
  "ANNOTATE_GLYPH",
)

REVIEWER_CLASSES = ("OPERATOR", "SPECIALIST", "TRUSTED_COLLABORATOR", "COMMUNITY", "AI_AGENT")

HUMAN_CLASSES = frozenset({"OPERATOR", "SPECIALIST", "TRUSTED_COLLABORATOR", "COMMUNITY"})

EXPERT_CLASSES = frozenset({"OPERATOR", "SPECIALIST"})

MIN_INDEPENDENT_ANSWERS = 2
MIN_CONSENSUS_AGREEMENT = 0.75

GLYPH_ALPHABETS = {
  "GREEK": tuple(chr(c) for c in range(0x3B1, 0x3CA)),
  "LATIN": tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
}
GLYPH_NON_LETTERS = ("NOT_A_LETTER", "CANNOT_TELL")

HUMAN_ROLE = "HUMAN_JUDGMENT"
MODEL_ROLE = "PREDICTION_DISCOVERY_EVIDENCE"
ANSWER_ROLES = (HUMAN_ROLE, MODEL_ROLE)

ATTESTATIONS = ("SELF_DECLARED", "DEFAULT_OPERATOR", "MODEL_SOURCE")


class ReviewRefusal(ValueError):
    """Raised when a transition would let an answer become evidence it has not earned."""


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def norm_id(reviewer_id: str) -> str:
    """The identity two answers are compared on: 'Alice' and ' alice ' are one reviewer."""
    return " ".join(str(reviewer_id).split()).casefold()


@dataclasses.dataclass(frozen=True)
class Answer:
    """One reviewer's response, with everything needed to audit it later."""

    reviewer_id: str
    reviewer_class: str
    value: str
    confidence: float
    duration_s: float
    utc: str
    prompt_version: str
    notes: str | None = None
    evidence_role: str = ""
    identity_attestation: str = "SELF_DECLARED"
    session_binding: str | None = None
    source: dict | None = None
    basis: dict | None = None

    def __post_init__(self):
        if self.reviewer_class not in REVIEWER_CLASSES:
            raise ReviewRefusal("unknown reviewer class %r" % (self.reviewer_class,))
        if not norm_id(self.reviewer_id):
            raise ReviewRefusal("an answer must name its reviewer")
        if not 0.0 <= self.confidence <= 1.0:
            raise ReviewRefusal("confidence must be in [0,1], got %r" % (self.confidence,))
        if self.duration_s < 0:
            raise ReviewRefusal("duration cannot be negative")
        want = MODEL_ROLE if self.reviewer_class == "AI_AGENT" else HUMAN_ROLE
        if self.evidence_role and self.evidence_role != want:
            raise ReviewRefusal(
                "a %s answer carries the evidence role %s, not %s. A model's proposal is never "
                "HUMAN_JUDGMENT and a person's answer is never a model proposal."
                % (self.reviewer_class, want, self.evidence_role))
        object.__setattr__(self, "evidence_role", want)
        if self.identity_attestation not in ATTESTATIONS:
            raise ReviewRefusal("unknown identity attestation %r" % (self.identity_attestation,))

    @property
    def is_ai(self) -> bool:
        return self.reviewer_class == "AI_AGENT"

    @property
    def is_human(self) -> bool:
        return self.reviewer_class in HUMAN_CLASSES


@dataclasses.dataclass
class Task:
    """A review task and its bound provenance."""

    task_id: str
    task_type: str
    binding: dict
    permitted_answers: tuple
    state: str = "PROPOSED"
    answers: list = dataclasses.field(default_factory=list)
    control: str | None = None
    expected_answer: str | None = None
    dataset_version: str | None = None

    REQUIRED_BINDING = (
      "source_volume", "surface_or_candidate_id", "physical_coordinates",
      "source_hashes", "displayed_assets", "camera_and_orientation",
      "producing_model_or_tool", "prompt_version", "license_state", "exposure_state",
    )

    def __post_init__(self):
        if self.task_type not in TASK_TYPES:
            raise ReviewRefusal("unknown task type %r" % (self.task_type,))
        if self.state not in STATES and self.state not in SIDE_STATES:
            raise ReviewRefusal("unknown state %r" % (self.state,))
        missing = [k for k in self.REQUIRED_BINDING if not self.binding.get(k)]
        if missing:
            raise ReviewRefusal(
                "task %s is missing bound provenance: %s. A task that cannot say what the "
                "reviewer was looking at produces an unauditable answer."
                % (self.task_id, ", ".join(missing)))
        if not self.permitted_answers:
            raise ReviewRefusal("a task must declare its permitted answers")


    def visible_to_reviewer(self) -> dict:
        """What a reviewer is allowed to see."""
        return {
          "task_id": self.task_id,
          "task_type": self.task_type,
          "permitted_answers": list(self.permitted_answers),
          "displayed_assets": self.binding["displayed_assets"],
          "camera_and_orientation": self.binding["camera_and_orientation"],
          "physical_coordinates": self.binding["physical_coordinates"],
          "prompt_version": self.binding["prompt_version"],
          "answers_so_far": "withheld until you answer",
          "is_a_control": "not disclosed",
        }

    def record(self, answer: Answer) -> None:
        if answer.value not in self.permitted_answers:
            raise ReviewRefusal(
                "%r is not a permitted answer for %s (permitted: %s)"
                % (answer.value, self.task_type, ", ".join(self.permitted_answers)))
        if any(norm_id(a.reviewer_id) == norm_id(answer.reviewer_id) for a in self.answers):
            raise ReviewRefusal("reviewer %s has already answered %s -- an independent "
                                "review cannot be a second opinion from the same reviewer"
                                % (answer.reviewer_id, self.task_id))
        if answer.session_binding and any(
                a.session_binding == answer.session_binding for a in self.answers):
            raise ReviewRefusal(
                "this session already answered %s under another name. One person answering "
                "twice under two names is not two independent reviewers; a second reviewer "
                "needs their own session." % self.task_id)
        self.answers.append(answer)


    def consensus_answers(self) -> tuple:
        """The answers a consensus is computed over: the people's, whenever there are any."""
        humans = [a for a in self.answers if a.is_human]
        if humans:
            return "HUMAN_ONLY", humans
        if self.answers:
            return "AI_ONLY", list(self.answers)
        return "NONE", []

    def tally(self) -> dict:
        counts: dict = {}
        for a in self.answers:
            counts[a.value] = counts.get(a.value, 0) + 1
        total = len(self.answers)
        top, n = (max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0))
        basis, cset = self.consensus_answers()
        ccounts: dict = {}
        for a in cset:
            ccounts[a.value] = ccounts.get(a.value, 0) + 1
        ctop, cn = (max(ccounts.items(), key=lambda kv: kv[1]) if ccounts else (None, 0))
        return {"counts": counts, "total": total, "top": top,
                "agreement": (n / total) if total else 0.0,
                "human_answers": sum(1 for a in self.answers
                                     if a.reviewer_class in HUMAN_CLASSES),
                "ai_answers": sum(1 for a in self.answers if a.is_ai),
                "independent_human_reviewers": len({norm_id(a.reviewer_id)
                                                    for a in self.answers if a.is_human}),
                "consensus_basis": basis, "consensus_counts": ccounts,
                "consensus_total": len(cset), "consensus_top": ctop,
                "consensus_agreement": (cn / len(cset)) if cset else 0.0}

    @property
    def composition(self) -> dict:
        by: dict = {}
        for a in self.answers:
            by[a.reviewer_class] = by.get(a.reviewer_class, 0) + 1
        t = self.tally()
        return {"by_class": by,
                "ai_only": t["total"] > 0 and t["human_answers"] == 0,
                "disclosure": ("this result was produced entirely by AI reviewers"
                               if (t["total"] and not t["human_answers"])
                               else "includes human review" if t["human_answers"]
                               else "no answers yet")}


    def advance(self, *, validator: Answer | None = None,
                dataset_version: str | None = None) -> str:
        """Move at most one rung, and only on evidence."""
        if self.state in SIDE_STATES:
            raise ReviewRefusal("%s is %s and is not on the path to training"
                                % (self.task_id, self.state))
        t = self.tally()
        cur = STATE_INDEX[self.state]

        if cur == STATE_INDEX["PROPOSED"]:
            if t["consensus_total"] < MIN_INDEPENDENT_ANSWERS:
                raise ReviewRefusal(
                    "%s has %d independent answers; %d are required (%d AI proposal(s) do not "
                    "count once a person has answered)"
                    % (self.task_id, t["consensus_total"], MIN_INDEPENDENT_ANSWERS,
                       t["ai_answers"] if t["consensus_basis"] == "HUMAN_ONLY" else 0))
            self.state = "INDEPENDENTLY_REVIEWED"
            return self.state

        if cur == STATE_INDEX["INDEPENDENTLY_REVIEWED"]:
            if t["consensus_agreement"] < MIN_CONSENSUS_AGREEMENT:
                self.state = "DISPUTED"
                return self.state
            self.state = "CONSENSUS_REACHED"
            return self.state

        if cur == STATE_INDEX["CONSENSUS_REACHED"]:
            if validator is None:
                raise ReviewRefusal("expert or control validation requires a validator")
            if validator.reviewer_class not in EXPERT_CLASSES:
                raise ReviewRefusal(
                    "%s cannot validate: only %s may. An AI agent may review, and may never "
                    "be the validator that promotes its own class's consensus."
                    % (validator.reviewer_class, " or ".join(sorted(EXPERT_CLASSES))))
            if any(norm_id(a.reviewer_id) == norm_id(validator.reviewer_id)
                   or (validator.session_binding and a.session_binding == validator.session_binding)
                   for a in self.answers):
                raise ReviewRefusal(
                    "%s answered %s and cannot also validate it. A validator who is one of the "
                    "answerers is agreeing with themselves." % (validator.reviewer_id, self.task_id))
            if validator.value != t["consensus_top"]:
                self.state = "DISPUTED"
                return self.state
            self.state = "EXPERT_OR_CONTROL_VALIDATED"
            return self.state

        if cur == STATE_INDEX["EXPERT_OR_CONTROL_VALIDATED"]:
            if self.composition["ai_only"]:
                raise ReviewRefusal(
                    "%s was answered only by AI agents. An all-AI consensus may inform, but "
                    "may not become a training label." % self.task_id)
            self.state = "TRAINING_ELIGIBLE"
            return self.state

        if cur == STATE_INDEX["TRAINING_ELIGIBLE"]:
            if not dataset_version:
                raise ReviewRefusal("freezing requires a dataset version")
            self.dataset_version = dataset_version
            self.state = "FROZEN_IN_DATASET_VERSION"
            return self.state

        raise ReviewRefusal("%s is already %s" % (self.task_id, self.state))

    def needs_context(self, why: str) -> str:
        self.state = "NEEDS_CONTEXT"
        self.binding["needs_context_reason"] = why
        return self.state

    @property
    def may_enter_training(self) -> bool:
        return self.state in ("TRAINING_ELIGIBLE", "FROZEN_IN_DATASET_VERSION")

    def as_record(self) -> dict:
        return {
          "contract": CONTRACT_ID,
          "task_id": self.task_id, "task_type": self.task_type,
          "state": self.state, "control": self.control,
          "may_enter_training": self.may_enter_training,
          "dataset_version": self.dataset_version,
          "binding": dict(self.binding),
          "permitted_answers": list(self.permitted_answers),
          "tally": self.tally(), "composition": self.composition,
          "answers": [dataclasses.asdict(a) for a in self.answers],
          "binding_hash": _sha(json.dumps(self.binding, sort_keys=True, default=str)),
        }


def reliability(tasks: Iterable[Task], reviewer_id: str) -> dict:
    """Measure a reviewer against controls only -- never against the majority."""
    seen = scored = correct = 0
    by_type: dict = {}
    for t in tasks:
        a = next((x for x in t.answers if x.reviewer_id == reviewer_id), None)
        if a is None:
            continue
        seen += 1
        if t.control in ("PLANTED_ERROR", "BLIND_CONTROL") and t.expected_answer:
            scored += 1
            ok = a.value == t.expected_answer
            correct += ok
            d = by_type.setdefault(t.task_type, {"scored": 0, "correct": 0})
            d["scored"] += 1
            d["correct"] += ok
    return {"reviewer_id": reviewer_id, "tasks_answered": seen,
            "control_tasks_scored": scored,
            "control_accuracy": (correct / scored) if scored else None,
            "by_task_type": by_type,
            "note": "accuracy is measured against controls with a known answer, never "
                    "against the majority"}


SEALED_AFFIRMATIVE = "LIKELY_SIGNAL"
SEALED_ABSTAIN = "REFUSE"
SEALED_CHOICES = ("LIKELY_SIGNAL", "LIKELY_STRUCTURE", "UNCERTAIN", "REFUSE")


def promotion_status(answers: Iterable, *, affirmative: str = SEALED_AFFIRMATIVE,
                     abstain: str = SEALED_ABSTAIN) -> dict:
    """Whether a sealed task may become a CANDIDATE, computed from its answers alone."""
    rows = []
    for a in answers:
        rows.append(dataclasses.asdict(a) if dataclasses.is_dataclass(a) else dict(a))
    ai = [r for r in rows if r.get("reviewer_class") == "AI_AGENT"]
    humans = [r for r in rows if r.get("reviewer_class") in HUMAN_CLASSES]
    seen_image = [r for r in humans if (r.get("basis") or {}).get("images_viewed_attested") is True]
    counted = [r for r in seen_image if r.get("value") != abstain]
    people = {norm_id(r.get("reviewer_id") or "") for r in counted} - {""}
    sessions = [r.get("session_binding") for r in counted if r.get("session_binding")]
    counts: dict = {}
    for r in humans:
        counts[r.get("value")] = counts.get(r.get("value"), 0) + 1
    sub_counts: dict = {}
    for r in counted:
        sub_counts[r.get("value")] = sub_counts.get(r.get("value"), 0) + 1
    top, n = (max(sub_counts.items(), key=lambda kv: kv[1]) if sub_counts else (None, 0))
    agreement = (n / len(counted)) if counted else 0.0
    reasons = []
    if ai:
        reasons.append("AI_ANSWER_PRESENT")
    if len(humans) != len(seen_image):
        reasons.append("NO_IMAGE_BASIS")
    if len(people) < MIN_INDEPENDENT_ANSWERS:
        reasons.append("FEWER_THAN_2_INDEPENDENT_HUMANS")
    if len(set(sessions)) != len(sessions):
        reasons.append("REVIEWER_SESSIONS_NOT_DISTINCT")
    if len(sub_counts) > 1:
        reasons.append("DISAGREEMENT")
    elif counted and top != affirmative:
        reasons.append("CONSENSUS_NOT_AFFIRMATIVE")
    promotable = not reasons
    return {"status": "PROMOTABLE" if promotable else "NOT_PROMOTABLE", "reasons": reasons,
            "independent_human_reviewers": len(people), "human_answers": len(humans),
            "ai_answers": len(ai), "counts": counts,
            "abstentions": sorted({r.get("reviewer_id") for r in humans if r.get("value") == abstain}),
            "disagreement": len(sub_counts) > 1, "consensus_top": top, "agreement": round(agreement, 4),
            "note": "a candidate is a place worth a closer look; it is not ink, a letter or a reading"}


def new_task_id(seed: str) -> str:
    return "RT-" + _sha(seed + str(time.time()))[:12]


def humanise(machine_state: str) -> str:
    """Level 1 language."""
    return {
      "NOT_RUN": "Ready for the next step",
      "FAILED_GENERALIZATION": "Did not transfer to another scroll",
      "BLOCKED_MISSING_FACT": "Needs information",
      "RESEARCH_ONLY": "Research use only",
      "PROPOSED": "Waiting for review",
      "INDEPENDENTLY_REVIEWED": "Reviewed once",
      "CONSENSUS_REACHED": "Reviewers agree",
      "EXPERT_OR_CONTROL_VALIDATED": "Checked by a specialist",
      "TRAINING_ELIGIBLE": "Can be used for training",
      "FROZEN_IN_DATASET_VERSION": "Locked into a dataset",
      "DISPUTED": "Reviewers disagree",
      "NEEDS_CONTEXT": "Needs more surrounding context",
    }.get(machine_state, re.sub(r"_", " ", machine_state).capitalize())




UNCERTAINTY_REASONS = (
  "LOW_CONFIDENCE",
  "HIGH_DISAGREEMENT",
  "GEOMETRY_AMBIGUOUS",
  "OUT_OF_DISTRIBUTION",
  "CONTROL_SAMPLE",
)


def propose_from_uncertainty(*, task_type, binding, permitted_answers, model_confidence,
                             reason, seed="", control=None, expected_answer=None) -> Task:
    """Turn a place the model is unsure about into a task a person can actually answer."""
    if reason not in UNCERTAINTY_REASONS:
        raise ReviewRefusal("unknown uncertainty reason %r" % (reason,))
    if not 0.0 <= float(model_confidence) <= 1.0:
        raise ReviewRefusal("model_confidence must be in [0,1]")
    b = dict(binding)
    b["selected_because"] = reason
    b["model_confidence"] = float(model_confidence)
    b["confidence_shown_to_reviewer"] = False
    return Task(task_id=new_task_id(seed or task_type), task_type=task_type, binding=b,
                permitted_answers=tuple(permitted_answers), control=control,
                expected_answer=expected_answer)


@dataclasses.dataclass(frozen=True)
class SupervisionVersion:
    """A frozen set of review-derived labels, and what it is allowed to be used for."""

    version: str
    task_ids: tuple
    created_utc: str
    human_answer_count: int
    ai_answer_count: int
    control_accuracy: float | None

    def __post_init__(self):
        if not self.task_ids:
            raise ReviewRefusal("a supervision version with no tasks is not a version")


def freeze_supervision(tasks: Iterable[Task], version: str) -> SupervisionVersion:
    """Freeze only what earned it."""
    tasks = list(tasks)
    eligible = [t for t in tasks if t.may_enter_training]
    if not eligible:
        raise ReviewRefusal(
            "no task has reached TRAINING_ELIGIBLE. Freezing here would put unvalidated "
            "answers into supervision, which is the one thing this pipeline exists to stop.")
    for t in eligible:
        if t.state == "TRAINING_ELIGIBLE":
            t.advance(dataset_version=version)
    h = sum(t.tally()["human_answers"] for t in eligible)
    a = sum(t.tally()["ai_answers"] for t in eligible)
    ctrl = [t for t in tasks if t.control in ("PLANTED_ERROR", "BLIND_CONTROL")
            and t.expected_answer]
    acc = None
    if ctrl:
        hit = sum(1 for t in ctrl if t.tally()["top"] == t.expected_answer)
        acc = hit / len(ctrl)
    return SupervisionVersion(version=version,
                              task_ids=tuple(t.task_id for t in eligible),
                              created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              human_answer_count=h, ai_answer_count=a,
                              control_accuracy=acc)


def measured_improvement(*, supervision: SupervisionVersion, before: float, after: float,
                         metric: str, eval_set_id: str,
                         eval_disjoint_from_supervision: bool) -> dict:
    """Report a retraining delta, or refuse to."""
    if not eval_disjoint_from_supervision:
        raise ReviewRefusal(
            "the evaluation set is not declared disjoint from the supervision. A delta "
            "measured on regions the labels came from reports memorisation, not learning.")
    return {
      "contract": CONTRACT_ID,
      "supervision_version": supervision.version,
      "tasks_in_supervision": len(supervision.task_ids),
      "human_answers": supervision.human_answer_count,
      "ai_answers": supervision.ai_answer_count,
      "reviewer_control_accuracy": supervision.control_accuracy,
      "metric": metric,
      "eval_set_id": eval_set_id,
      "eval_disjoint_from_supervision": True,
      "before": before, "after": after,
      "delta": round(after - before, 6),
      "improved": after > before,
      "caveat": "a positive delta on one held-out set is evidence about that set. It is not "
                "cross-scroll generalization, which has its own gate.",
    }
