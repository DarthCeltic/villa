"""Shared fixtures for the interpretation-tail tests: a synthetic target with real Review Lab tasks, answered through the real store, so nothing here is a hand-typed \"as if reviewed\" dict."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core import actions as A
from argus.core import action_registry as R
from argus.core import paths as PATHS
from argus.core import review_store as RS

TARGET = "fixture1"


def binding(cand: str, extent, orientation="forward") -> dict:
    return {
        "source_volume": "s3://example/volume.zarr (scroll PHercTest)",
        "surface_or_candidate_id": cand,
        "physical_coordinates": {"region": [0, 400, 0, 400], "extent": list(extent)},
        "source_hashes": {"seal_manifest_sha256": "a" * 64},
        "displayed_assets": {"tile_extent": list(extent)},
        "camera_and_orientation": {"orientation": orientation},
        "producing_model_or_tool": "fixture-detector",
        "prompt_version": "argus-review-lab-v1/JUDGE_INK_CANDIDATE/fixture",
        "license_state": "CLEARED", "exposure_state": "HELD_OUT",
        "confidence_shown_to_reviewer": False,
    }


def ink_record(task_id: str, extent, cand: str | None = None) -> dict:
    return {"task_id": task_id, "task_type": "JUDGE_INK_CANDIDATE", "state": "PROPOSED",
            "control": None, "binding": binding(cand or "cand-" + task_id, extent),
            "permitted_answers": ["INK", "NOT_INK", "CANNOT_TELL"], "answers": []}


def make_target(tmp_path: Path, monkeypatch, extents=((0, 100, 0, 100),), key: str = TARGET) -> Path:
    """A `ui_audit/<key>/REVIEW_TASKS.json` under tmp_path, resolved by the real path helper."""
    d = tmp_path / "ui_audit" / key
    d.mkdir(parents=True)
    (tmp_path / "ui_audit" / "TARGETS.json").write_text(
        json.dumps({"targets": [{"key": key, "base": key}]}), encoding="utf-8")
    tasks = [ink_record("RT-%d" % i, ext) for i, ext in enumerate(extents)]
    (d / "REVIEW_TASKS.json").write_text(json.dumps({"tasks": tasks, "counts": {"tasks": len(tasks)}}),
                                         encoding="utf-8")
    monkeypatch.setattr(PATHS, "artifact_roots", lambda: [tmp_path])
    monkeypatch.setenv("ARGUS_USER_DATA", str(tmp_path / "user_data"))
    return d


def answer(d: Path, task_id: str, name: str, cls: str, value: str, session=None) -> dict:
    return RS.record_answer(target_dir=d, tasks_payload=RS.load_raw_payload(d), task_id=task_id,
                            value=value, confidence=0.9, duration_s=5.0, reviewer_id=name,
                            reviewer_class=cls, session_binding=session or "s-" + name)


def validate(d: Path, task_id: str, name: str, value: str, cls: str = "SPECIALIST") -> dict:
    return RS.record_validation(target_dir=d, tasks_payload=RS.load_raw_payload(d),
                                task_id=task_id, value=value, reviewer_id=name, reviewer_class=cls,
                                session_binding="s-" + name)


def accept_region(d: Path, task_id: str, *, validated=True) -> None:
    answer(d, task_id, "alice", "OPERATOR", "INK")
    answer(d, task_id, "bob", "COMMUNITY", "INK")
    if validated:
        validate(d, task_id, "carol", "INK")


def run(action: str, params: dict, actor: str = "human:ui", approve: bool = True) -> dict:
    """Plan, approve by the plan's OWN hash, then do -- exactly the governed sequence."""
    plan = R.REGISTRY[action][0](params)
    p = dict(params)
    if approve and "plan_sha256" in plan:
        p["approved_plan_sha256"] = plan["plan_sha256"]
    spec = A.ActionSpec.parse({"action": action, "actor": actor, "request_id": "r",
                               "idempotency_key": "k-%s" % action, "params": p})
    return R.REGISTRY[action][1](spec)


def annotate(d: Path, task_id: str, name: str, cls: str, letter: str, session=None,
             alphabet: str = "GREEK") -> dict:
    return run("glyph.annotate", {"target": TARGET, "task_id": task_id, "answer": letter,
                                  "alphabet": alphabet, "reviewer_id": name, "reviewer_class": cls,
                                  "reviewer_session": session or "s-" + name})


def settle_cell(d: Path, task_id: str, letter: str = "α", *, validated=True) -> None:
    """One accepted, lettered, validated cell: ink by alice+bob (validated by carol), letter by alice+bob (validated by carol)."""
    from argus.core import review_store as _RS
    accept_region(d, task_id, validated=validated)
    annotate(d, task_id, "alice", "OPERATOR", letter)
    annotate(d, task_id, "bob", "COMMUNITY", letter)
    if validated:
        validate(d, _RS.glyph_task_id(task_id), "carol", letter)
