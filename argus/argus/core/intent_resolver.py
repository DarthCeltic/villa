"""Structured intent resolution in front of the ONE governed door."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from typing import Callable

from argus.core import actions as A
from argus.core import action_registry as R


_SLUG = r"[a-z0-9][a-z0-9_-]{1,62}"
_TOKEN = r"[A-Za-z0-9_.\-]+"
_URL = r"\S+"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


@dataclasses.dataclass(frozen=True)
class Intent:
    name: str
    action: str
    patterns: tuple
    build_params: Callable[["re.Match[str]"], dict]
    example: str


def _rx(pattern: str):
    return re.compile("^" + pattern + "$", re.IGNORECASE)


INTENTS: tuple[Intent, ...] = (
    Intent(
        name="workspace.create", action="workspace.create",
        patterns=(_rx(rf"create workspace (?P<slug>{_SLUG}) for scroll (?P<scroll>{_TOKEN}) "
                     rf"objective (?P<objective>.+)"),),
        build_params=lambda m: {"slug": m["slug"], "scroll": m["scroll"],
                                "objective": m["objective"].strip()},
        example="create workspace demo for scroll PHercParis4 objective locate first letters"),
    Intent(
        name="workspace.select", action="workspace.select",
        patterns=(_rx(rf"(?:select|switch to) workspace (?P<slug>{_SLUG})"),),
        build_params=lambda m: {"slug": m["slug"]},
        example="select workspace demo"),
    Intent(
        name="workspace.readiness", action="workspace.readiness",
        patterns=(_rx(rf"(?:show )?readiness for workspace (?P<slug>{_SLUG})"),
                  _rx(rf"is workspace (?P<slug>{_SLUG}) ready")),
        build_params=lambda m: {"slug": m["slug"]},
        example="show readiness for workspace demo"),
    Intent(
        name="inventory.scan", action="inventory.scan",
        patterns=(_rx(r"scan inventory"), _rx(r"what data do we have"),
                  _rx(r"show (?:the )?inventory")),
        build_params=lambda m: {},
        example="scan inventory"),
    Intent(
        name="preflight", action="preflight",
        patterns=(_rx(r"run preflight"), _rx(r"check preflight"), _rx(r"preflight check")),
        build_params=lambda m: {},
        example="run preflight"),
    Intent(
        name="job.cancel", action="job.cancel",
        patterns=(_rx(rf"cancel job (?P<job_id>{_TOKEN})"),),
        build_params=lambda m: {"job_id": m["job_id"]},
        example="cancel job job_ab12cd34ef56"),
    Intent(
        name="candidate.open", action="candidate.open",
        patterns=(_rx(r"open (?:the )?candidate gallery"),
                  _rx(rf"open candidate (?P<candidate_id>{_TOKEN})")),
        build_params=lambda m: ({"candidate_id": m["candidate_id"]}
                                if m.groupdict().get("candidate_id") else {}),
        example="open candidate gallery"),
    Intent(
        name="evidence.reproduce", action="evidence.reproduce",
        patterns=(_rx(rf"(?:show reproduction (?:command|steps) for|reproduce evidence for) "
                     rf"run (?P<run>{_TOKEN})"),),
        build_params=lambda m: {"run": m["run"]},
        example="reproduce evidence for run 20260101T000000Z_example_run"),
    Intent(
        name="secret.status", action="secret.status",
        patterns=(_rx(r"show credential status"), _rx(r"show secret status"),
                  _rx(r"credential status")),
        build_params=lambda m: {},
        example="show credential status"),
    Intent(
        name="import.plan", action="import.plan",
        patterns=(_rx(rf"plan import(?: of)? (?P<source>{_URL})"),),
        build_params=lambda m: {"source": m["source"]},
        example="plan import of https://dl.ash2txt.org/full-scrolls/x.zarr"),
    Intent(
        name="acquire.dry_run", action="acquire.dry_run",
        patterns=(_rx(rf"dry run acquisition of scroll (?P<scroll>{_TOKEN}) "
                     rf"volume (?P<volume_id>{_TOKEN}) url (?P<url>{_URL}) "
                     rf"phase (?P<phase>{_TOKEN})"),),
        build_params=lambda m: {"scroll": m["scroll"], "volume_id": m["volume_id"],
                                "url": m["url"], "phase": m["phase"]},
        example=("dry run acquisition of scroll PHercParis4 volume 20230205180739 "
                 "url https://dl.ash2txt.org/x.zarr phase A0")),
)

_DELIBERATELY_EXCLUDED = frozenset({
    "secret.set", "secret.rotate", "secret.remove",
    "acquire.execute", "provider.invoke",
    "pipeline.start", "job.resume",
    "vigiles.final_decision",
})


class IntentRefusal(Exception):
    """Refusal is a result here too."""

    def __init__(self, code: str, why: str, **extra):
        super().__init__("%s: %s" % (code, why))
        self.code, self.why, self.extra = code, why, extra

    def as_dict(self) -> dict:
        return dict({"status": "REFUSED", "code": self.code, "why": self.why}, **self.extra)


@dataclasses.dataclass(frozen=True)
class Resolution:
    intent: str
    action: str
    params: dict
    matched_text: str


def _match_all(text: str, intents=INTENTS) -> list:
    """Every intent gets at most one vote: the first of ITS OWN patterns that fullmatches."""
    norm = _norm(text)
    out: list[Resolution] = []
    for it in intents:
        for pat in it.patterns:
            m = pat.match(norm)
            if not m:
                continue
            cand = Resolution(intent=it.name, action=it.action,
                              params=it.build_params(m), matched_text=norm)
            if not any(c.action == cand.action and c.params == cand.params for c in out):
                out.append(cand)
            break
    return out


def resolve(text: str, intents=INTENTS) -> Resolution:
    matches = _match_all(text, intents)
    if not matches:
        raise IntentRefusal(
            "UNRESOLVED_INTENT",
            "%r does not match any known structured request shape. This is a fixed, closed "
            "vocabulary, not a free-text parser -- an unmapped request is refused, never "
            "guessed at" % _norm(text),
            supported=[{"intent": it.name, "action": it.action, "example": it.example}
                       for it in intents])
    if len(matches) > 1:
        raise IntentRefusal(
            "AMBIGUOUS_INTENT",
            "%r matches more than one known request shape (%s). Ambiguity is refused rather "
            "than resolved by picking one; rephrase so it matches exactly one shape"
            % (_norm(text), ", ".join(sorted({m.intent for m in matches}))),
            candidates=[{"intent": m.intent, "action": m.action, "params": m.params}
                        for m in matches])
    return matches[0]


def describe_intents(intents=INTENTS) -> list[dict]:
    """The whole closed vocabulary."""
    return [{"intent": it.name, "action": it.action, "example": it.example} for it in intents]



def _confirmable_digest(plan_result: dict) -> str:
    """Hash the DISPLAYED plan (cost, changes, leases, may_refuse, reversible, scientific boundary, action...) -- everything a human confirms against -- and nothing volatile like request_id, which..."""
    body = {k: v for k, v in plan_result.items()
            if k not in ("actor", "request_id", "idempotency_key", "fingerprint", "dry_run")}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spec(action, params, actor, request_id, idempotency_key, dry_run) -> dict:
    return {"action": action, "actor": actor, "request_id": request_id,
            "idempotency_key": idempotency_key, "params": params, "dry_run": dry_run}


def preview(text: str, *, actor: str, request_id: str | None = None,
           idempotency_key: str | None = None) -> dict:
    """Resolve TEXT and show what would happen -- cost, claims, scientific_boundary -- without changing anything."""
    request_id = request_id or uuid.uuid4().hex
    idempotency_key = idempotency_key or uuid.uuid4().hex
    try:
        res = resolve(text)
    except IntentRefusal as exc:
        return dict(exc.as_dict(), resolved=False, text=text)
    spec = _spec(res.action, res.params, actor, request_id, idempotency_key, True)
    try:
        planned = R.plan(spec)
    except A.Refused as exc:
        return dict(exc.as_dict(), resolved=True, intent=res.intent, action=res.action,
                    params=res.params, text=text)
    consequential = not bool(planned.get("read_only"))
    return {
        "status": "PREVIEW", "resolved": True, "intent": res.intent, "action": res.action,
        "params": res.params, "text": text,
        "plan": planned,
        "confirmation_required": consequential,
        "preview_sha256": _confirmable_digest(planned),
        "idempotency_key": idempotency_key,
        "note": ("re-submit this exact text to confirm_and_submit with "
                 "confirm_sha256=preview_sha256 and the SAME idempotency_key to execute. The "
                 "plan is recomputed fresh at confirmation time and must still match -- the "
                 "same approved_plan_sha256 pattern acquire.execute already uses, generalised "
                 "to every action this layer can reach") if consequential else
                ("read_only: this action changes nothing consequential and may be confirmed "
                 "with no human approval step; it still runs through submit() and gets a "
                 "normal job id and audit entry"),
    }


def confirm_and_submit(text: str, *, actor: str, idempotency_key: str,
                       confirm_sha256: str | None = None,
                       request_id: str | None = None) -> dict:
    """Re-resolve TEXT from scratch, re-plan it fresh, and -- for a consequential action -- require confirm_sha256 to equal THAT fresh plan's digest."""
    request_id = request_id or uuid.uuid4().hex
    try:
        res = resolve(text)
    except IntentRefusal as exc:
        return dict(exc.as_dict(), resolved=False, text=text)

    spec_dry = _spec(res.action, res.params, actor, request_id, idempotency_key, True)
    try:
        planned = R.plan(spec_dry)
    except A.Refused as exc:
        return dict(exc.as_dict(), resolved=True, intent=res.intent, action=res.action,
                    params=res.params, text=text)

    consequential = not bool(planned.get("read_only"))
    fresh_digest = _confirmable_digest(planned)
    if consequential:
        if not confirm_sha256:
            return {"status": "REFUSED", "code": "CONFIRMATION_REQUIRED",
                    "why": ("%r resolves to a consequential action (%s). It requires "
                            "confirm_sha256 equal to the plan's preview_sha256 before it may "
                            "run" % (_norm(text), res.action)),
                    "resolved": True, "intent": res.intent, "action": res.action,
                    "params": res.params, "plan": planned, "preview_sha256": fresh_digest}
        if confirm_sha256 != fresh_digest:
            return {"status": "REFUSED", "code": "PLAN_NOT_CONFIRMED",
                    "why": ("confirm_sha256 does not match the freshly recomputed plan for "
                            "%r. Either the request changed or system state moved since the "
                            "preview was shown (a lease, storage, the pipeline registry); "
                            "re-preview and confirm the CURRENT plan, never a stale one"
                            % _norm(text)),
                    "resolved": True, "intent": res.intent, "action": res.action,
                    "params": res.params, "plan": planned, "preview_sha256": fresh_digest}

    spec = _spec(res.action, res.params, actor, request_id, idempotency_key, False)
    try:
        result = R.submit(spec)
    except A.Refused as exc:
        result = dict(exc.as_dict())
    return dict(result, resolved=True, intent=res.intent, action=res.action,
                params=res.params, text=text, confirmation_required=consequential,
                preview_sha256=fresh_digest)
