"""Durable answers for the Review Lab, kept apart from the generated proposal file."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import time
from pathlib import Path

from argus.core import review_lab as RL
from argus.core.review_lab import Answer, ReviewRefusal, Task

ANSWERS_FILENAME = "REVIEW_ANSWERS.jsonl"
VALIDATIONS_FILENAME = "REVIEW_VALIDATIONS.jsonl"
TASKS_FILENAME = "REVIEW_TASKS.json"
GLYPH_TASKS_FILENAME = "GLYPH_TASKS.json"

DEFAULT_REVIEWER_ID = "operator"
DEFAULT_REVIEWER_CLASS = "OPERATOR"

_NAME = re.compile(r"^\w[\w .@'\-]{0,62}$", re.UNICODE)


class ReviewStoreError(ValueError):
    pass


def clean_reviewer_name(name) -> str:
    """A reviewer name a person can be held to: one line, short, no markup."""
    n = " ".join(str(name or "").split())
    if not _NAME.match(n):
        raise ReviewRefusal(
            "a reviewer name is 1-63 characters: letters, digits, spaces and . _ @ ' - only. "
            "Answers are attributed to this name, so it has to be one a person can be held to.")
    return n


def answers_path(target_dir: Path) -> Path:
    return Path(target_dir) / ANSWERS_FILENAME


def validations_path(target_dir: Path) -> Path:
    return Path(target_dir) / VALIDATIONS_FILENAME


def _append_line(p: Path, record: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_lines(p: Path) -> dict:
    if not p.is_file():
        return {}
    out: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out.setdefault(rec["task_id"], []).append(rec)
    return out


def append_answer(target_dir: Path, record: dict) -> None:
    """Append one answer record, atomically enough for a single-writer local tool."""
    _append_line(answers_path(target_dir), record)


def load_answers(target_dir: Path) -> dict:
    """{task_id: [answer record, ...]}, in the order recorded."""
    return _load_lines(answers_path(target_dir))


def append_validation(target_dir: Path, record: dict) -> None:
    _append_line(validations_path(target_dir), record)


def load_validations(target_dir: Path) -> dict:
    return _load_lines(validations_path(target_dir))


def _answer_from(rec: dict) -> Answer:
    return Answer(**{k: v for k, v in rec.items() if k in Answer.__dataclass_fields__})


def _task_from_record(rec: dict) -> Task:
    answers = [_answer_from(a) for a in rec.get("answers", [])]
    return Task(
        task_id=rec["task_id"], task_type=rec["task_type"], binding=dict(rec["binding"]),
        permitted_answers=tuple(rec["permitted_answers"]), state=rec.get("state", "PROPOSED"),
        answers=answers, control=rec.get("control"),
        expected_answer=rec.get("expected_answer"), dataset_version=rec.get("dataset_version"),
    )


def _validation_view(v: dict, accepted: bool) -> dict:
    return {"reviewer_id": v.get("reviewer_id"), "reviewer_class": v.get("reviewer_class"),
            "value": v.get("value"), "utc": v.get("utc"), "role": "ADJUDICATOR",
            "evidence_role": RL.HUMAN_ROLE, "notes": v.get("notes"),
            "identity_attestation": v.get("identity_attestation"), "applied": accepted}


def _replay(rec: dict, extra: list, validations: list) -> dict:
    t = _task_from_record(rec)
    for a in extra:
        try:
            t.record(_answer_from(a))
        except ReviewRefusal:
            continue
    while True:
        try:
            t.advance()
        except ReviewRefusal:
            break
    views = []
    for v in validations:
        applied = False
        if t.state == "CONSENSUS_REACHED":
            try:
                t.advance(validator=_answer_from(dict(v, notes=v.get("notes"))))
                applied = True
            except ReviewRefusal:
                applied = False
        views.append(_validation_view(v, applied))
    out = t.as_record()
    out["validations"] = views
    return out


def merge_answers(tasks_payload: dict, target_dir: Path) -> dict:
    """The freshly-generated REVIEW_TASKS.json payload, with every stored answer (and every stored validation) replayed on top."""
    stored = load_answers(target_dir)
    valid = load_validations(target_dir)
    if not stored and not valid:
        return tasks_payload
    out = dict(tasks_payload)
    merged_tasks = []
    for rec in tasks_payload.get("tasks", []):
        extra = stored.get(rec["task_id"])
        if not extra:
            merged_tasks.append(rec)
            continue
        merged_tasks.append(_replay(rec, extra, valid.get(rec["task_id"], [])))
    out["tasks"] = merged_tasks
    return out


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record_answer(*, target_dir: Path, tasks_payload: dict, task_id: str, value: str,
                  confidence: float, duration_s: float, notes: str | None = None,
                  reviewer_id: str = DEFAULT_REVIEWER_ID,
                  reviewer_class: str = DEFAULT_REVIEWER_CLASS,
                  session_binding: str | None = None, basis: dict | None = None) -> dict:
    """Validate and durably record one HUMAN answer."""
    if reviewer_class not in RL.HUMAN_CLASSES:
        raise ReviewRefusal(
            "%r is not a human reviewer class (%s). A model's proposal is recorded through "
            "record_ai_proposal with its source identity, never as a person's answer."
            % (reviewer_class, ", ".join(sorted(RL.HUMAN_CLASSES))))
    unnamed = reviewer_id == DEFAULT_REVIEWER_ID and reviewer_class == DEFAULT_REVIEWER_CLASS
    reviewer_id = clean_reviewer_name(reviewer_id)
    merged = merge_answers(tasks_payload, target_dir)
    rec = next((r for r in merged.get("tasks", []) if r["task_id"] == task_id), None)
    if rec is None:
        raise ReviewStoreError("no task %r in this target's review tasks" % task_id)
    t = _task_from_record(rec)
    answer = Answer(
        reviewer_id=reviewer_id, reviewer_class=reviewer_class, value=value,
        confidence=confidence, duration_s=duration_s, utc=_stamp(), notes=notes,
        prompt_version=rec["binding"]["prompt_version"],
        identity_attestation="DEFAULT_OPERATOR" if unnamed else "SELF_DECLARED",
        session_binding=session_binding, basis=basis,
    )
    t.record(answer)
    append_answer(target_dir, dataclasses.asdict(answer) | {"task_id": task_id})
    try:
        t.advance()
    except ReviewRefusal:
        pass
    return next(r for r in merge_answers(tasks_payload, target_dir)["tasks"]
                if r["task_id"] == task_id)


def record_ai_proposal(*, target_dir: Path, tasks_payload: dict, task_id: str, value: str,
                       source_id: str, source_version: str, confidence: float = 0.0,
                       notes: str | None = None) -> dict:
    """Record ONE model proposal as an AI_AGENT answer with the identity of what produced it."""
    sid, ver = str(source_id or "").strip(), str(source_version or "").strip()
    if not sid or not ver:
        raise ReviewRefusal("a model proposal must name its source and its version; an anonymous "
                            "proposal cannot be audited or reproduced")
    from argus.core import sealed_review_queue as SQ
    SQ.refuse_sealed_ai(task_id)
    merged = merge_answers(tasks_payload, target_dir)
    rec = next((r for r in merged.get("tasks", []) if r["task_id"] == task_id), None)
    if rec is None:
        raise ReviewStoreError("no task %r in this target's review tasks" % task_id)
    t = _task_from_record(rec)
    answer = Answer(
        reviewer_id="ai:%s@%s" % (sid, ver), reviewer_class="AI_AGENT", value=value,
        confidence=confidence, duration_s=0.0, utc=_stamp(), notes=notes,
        prompt_version=rec["binding"]["prompt_version"],
        identity_attestation="MODEL_SOURCE",
        source={"source_id": sid, "source_version": ver, "kind": "EXTERNAL_HTR_OCR_PROPOSAL"},
    )
    t.record(answer)
    append_answer(target_dir, dataclasses.asdict(answer) | {"task_id": task_id})
    return next(r for r in merge_answers(tasks_payload, target_dir)["tasks"]
                if r["task_id"] == task_id)


def record_validation(*, target_dir: Path, tasks_payload: dict, task_id: str, value: str,
                      reviewer_id: str, reviewer_class: str, notes: str | None = None,
                      session_binding: str | None = None) -> dict:
    """Record an attributed expert validation (the adjudicator) of a task at CONSENSUS_REACHED."""
    reviewer_id = clean_reviewer_name(reviewer_id)
    merged = merge_answers(tasks_payload, target_dir)
    rec = next((r for r in merged.get("tasks", []) if r["task_id"] == task_id), None)
    if rec is None:
        raise ReviewStoreError("no task %r in this target's review tasks" % task_id)
    if rec["state"] != "CONSENSUS_REACHED":
        raise ReviewRefusal(
            "%s is %s. Only a task at CONSENSUS_REACHED can be validated: the validator confirms "
            "what two independent people already agreed on, they do not stand in for them."
            % (task_id, rec["state"]))
    validator = Answer(
        reviewer_id=reviewer_id, reviewer_class=reviewer_class, value=value, confidence=1.0,
        duration_s=0.0, utc=_stamp(), notes=notes, prompt_version=rec["binding"]["prompt_version"],
        session_binding=session_binding)
    _task_from_record(rec).advance(validator=validator)
    entry = dataclasses.asdict(validator) | {"task_id": task_id, "role": "ADJUDICATOR"}
    append_validation(target_dir, entry)
    return next(r for r in merge_answers(tasks_payload, target_dir)["tasks"]
                if r["task_id"] == task_id)



def glyph_task_id(source_task_id: str) -> str:
    return "RT-glyph-" + hashlib.sha256(source_task_id.encode("utf-8")).hexdigest()[:12]


def glyph_tasks_path(target_dir: Path) -> Path:
    return Path(target_dir) / GLYPH_TASKS_FILENAME


def load_glyph_tasks(target_dir: Path) -> dict:
    p = glyph_tasks_path(target_dir)
    if not p.is_file():
        return {"schema": "argus-glyph-tasks-v1", "tasks": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "argus-glyph-tasks-v1", "tasks": []}


def build_glyph_task(source_record: dict, alphabet: str) -> Task:
    """The glyph task for one ink task, built but not stored."""
    if alphabet not in RL.GLYPH_ALPHABETS:
        raise ReviewRefusal("alphabet must be one of %s, not %r"
                            % (sorted(RL.GLYPH_ALPHABETS), alphabet))
    binding = dict(source_record["binding"])
    binding.update({
        "source_task_id": source_record["task_id"], "alphabet": alphabet,
        "prompt_version": "argus-review-lab-v1/ANNOTATE_GLYPH",
        "selected_because": "an accepted ink region awaiting a letter judgment",
        "confidence_shown_to_reviewer": False})
    return Task(task_id=glyph_task_id(source_record["task_id"]), task_type="ANNOTATE_GLYPH",
                binding=binding,
                permitted_answers=tuple(RL.GLYPH_ALPHABETS[alphabet]) + RL.GLYPH_NON_LETTERS)


def ensure_glyph_task(target_dir: Path, source_record: dict, alphabet: str) -> dict:
    """The glyph task for one ACCEPTED ink region, created once and never rewritten."""
    task = build_glyph_task(source_record, alphabet)
    payload = load_glyph_tasks(target_dir)
    for existing in payload["tasks"]:
        if existing["task_id"] == task.task_id:
            if existing["binding"].get("alphabet") != alphabet:
                raise ReviewRefusal(
                    "glyph task %s was created with the %s alphabet; it cannot be re-annotated "
                    "in %s" % (task.task_id, existing["binding"].get("alphabet"), alphabet))
            return existing
    rec = task.as_record()
    payload["tasks"].append(rec)
    p = glyph_tasks_path(target_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec


def load_raw_payload(target_dir: Path) -> dict | None:
    """Every review task for one target (ink and glyph), as generated, before any answer is replayed."""
    tp = Path(target_dir) / TASKS_FILENAME
    if not tp.is_file():
        return None
    payload = json.loads(tp.read_text(encoding="utf-8"))
    glyphs = load_glyph_tasks(target_dir)
    if glyphs["tasks"]:
        payload = dict(payload, tasks=list(payload.get("tasks", [])) + list(glyphs["tasks"]))
    return payload


def load_payload(target_dir: Path) -> dict | None:
    """`load_raw_payload` with every stored answer and validation replayed on top."""
    raw = load_raw_payload(target_dir)
    return None if raw is None else merge_answers(raw, target_dir)
