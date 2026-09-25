"""One governed plan/invoke pair for the providers that have a plan function but no execution door."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from argus.core import actions as A
from argus.core import hecate_candidate, lasagna_candidate, lasagna_maxflow_provider
from argus.core import native_3d_provider, surface_prediction_provider
from argus.core import provider_registry as PR
from argus.core import villa_provider_adapter as PVA

SCHEMA = "argus-provider-candidate-action-plan-v1"
REFUSAL_SCHEMA = PR.CANDIDATE_REFUSAL_SCHEMA
FIELDS = frozenset({"provider_id", "request", "approved_plan_sha256"})
NOT_CLAIMED = ("qualified detector", "unseen-scroll generalization", "independent physical ground truth")

_PLAN_MAY_REFUSE = ["UNKNOWN_PROVIDER", "NO_PLAN_ROUTE", "WRONG_ACTION", "UNKNOWN_REQUEST_FIELD", "PLAN_REFUSED"]
_INVOKE_MAY_REFUSE = _PLAN_MAY_REFUSE + ["PLAN_NOT_APPROVED", "EXECUTION_NOT_ADAPTED", "NOT_AUTHORIZED"]


@dataclass(frozen=True)
class Route:
    plan_fn: Callable[..., dict]
    refusals: tuple
    bind: dict
    identity_of: Callable[[dict], Any] | None = None
    expected_identity: Callable[[str], Any] | None = None
    run_fn: Callable[..., dict] | None = None


def _native_route() -> Route:
    return Route(native_3d_provider.plan, (native_3d_provider.Native3DRefusal,), {},
                 identity_of=lambda plan: (plan.get("provider") or {}).get("candidate_id"),
                 expected_identity=lambda provider_id: native_3d_provider.CANDIDATE_ID)


def _surface_route(checkpoint: str) -> Route:
    return Route(surface_prediction_provider.plan, (surface_prediction_provider.SurfacePlanRefusal,),
                 {"requested_checkpoint": checkpoint}, identity_of=lambda plan: plan.get("checkpoint"))


_HECATE = Route(hecate_candidate.plan, (hecate_candidate.HecatePlanRefusal,), {},
                identity_of=lambda plan: plan.get("provider"))

ROUTES: dict[str, Route] = {
    "hecate_24um": _HECATE,
    "hecate_96um": _HECATE,
    "ink_3d_dino_guided": _native_route(),
    "native_full_3d_ink": _native_route(),
    "lasagna_predict3d": Route(lasagna_candidate.plan, (lasagna_candidate.LasagnaPlanRefusal,), {}),
    "lasagna_maxflow": Route(lasagna_maxflow_provider.plan_lasagna,
                             (lasagna_maxflow_provider.LasagnaMaxflowRefusal, PVA.ProviderRefusal), {},
                             run_fn=lasagna_maxflow_provider.run_lasagna),
    "surface_m7_nnunet": _surface_route("surface_m7_nnunet"),
    "surface_recto": _surface_route("surface_recto"),
}

if frozenset(ROUTES) != PR.PLANNABLE_IDS:
    raise RuntimeError("provider_actions.ROUTES and provider_registry.PLANNABLE_IDS disagree: %s"
                       % sorted(frozenset(ROUTES) ^ PR.PLANNABLE_IDS))


def _signature(route: Route) -> tuple[list[str], list[str]]:
    params = inspect.signature(route.plan_fn).parameters
    accepted = [name for name in params if name not in route.bind]
    required = [name for name in accepted if params[name].default is inspect.Parameter.empty]
    return accepted, required


def _row_view(row: dict) -> dict:
    return {key: row[key] for key in ("lifecycle_state", "blocker", "blockers", "claim_ceiling", "evidence_role",
                                       "gate", "plan_action", "invoke_action", "receipt_schema", "pin")}


def _base(invoking: bool) -> dict:
    return {
        "schema": SCHEMA, "ready": False, "leases": [], "reversible": True, "executes": False,
        "changes": (["records approval of one plan hash, then refuses: no candidate provider has an execution adapter "
                     "ARGUS may run from here (EXECUTION_NOT_ADAPTED) or it is not authorized (NOT_AUTHORIZED)"]
                    if invoking else
                    ["builds one hash-bound plan for one provider; starts no process, fetches nothing, writes no file"]),
        "cost": {"gpu": "none", "seconds": "reads and hashes only the checkpoint and input files the plan names",
                 "bytes": "read-only"},
        "may_refuse": list(_INVOKE_MAY_REFUSE if invoking else _PLAN_MAY_REFUSE),
        "not_claimed": list(NOT_CLAIMED),
    }


def _not_ready(base: dict, *, code: str, why: str, provider_id: str | None = None, row: dict | None = None,
               **extra: Any) -> dict:
    out = dict(base, provider_id=provider_id, refusal_code=code, planning_refusal=why, note=why,
               would_be_refused=True, **extra)
    if row is not None:
        out["registry"] = _row_view(row)
    return out


def build_plan(params: dict, *, invoking: bool = False) -> dict:
    unknown = sorted(set(params) - FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "the provider candidate actions do not accept %s" % unknown)
    base = _base(invoking)
    provider_id = params.get("provider_id")
    if not provider_id or not isinstance(provider_id, str):
        return dict(base, required=["provider_id"], missing=["provider_id"], would_be_refused=True,
                    note="name a registered provider_id")
    row = PR.record(provider_id)
    if row is None:
        return _not_ready(base, code="UNKNOWN_PROVIDER", provider_id=provider_id,
                          why="%r is not a registered provider; nothing was substituted" % provider_id)
    if row["lifecycle_state"] in ("GATED_EXECUTABLE", "EXECUTABLE"):
        own = row["invoke_action"]
        return _not_ready(base, code="WRONG_ACTION", provider_id=provider_id, row=row,
                          why=("%s runs through the governed action %s, not through this door" % (provider_id, own)
                               if own else "%s runs in-process and needs no governed action" % provider_id))
    route = ROUTES.get(provider_id)
    if route is None:
        blocker = row["blocker"]
        return _not_ready(base, code="NO_PLAN_ROUTE", provider_id=provider_id, row=row,
                          why="no plan function exists for %s; blocked by %s: %s"
                              % (provider_id, blocker["kind"], blocker["text"]))
    request = params.get("request")
    request = {} if request is None else request
    if not isinstance(request, dict):
        return _not_ready(base, code="PLAN_REFUSED", provider_id=provider_id, row=row, why="request must be an object")
    accepted, required = _signature(route)
    stray = sorted(set(request) - set(accepted))
    if stray:
        return _not_ready(base, code="UNKNOWN_REQUEST_FIELD", provider_id=provider_id, row=row,
                          why="the %s plan does not accept %s" % (provider_id, stray), accepted=accepted)
    missing = [name for name in required if request.get(name) in (None, "")]
    if missing:
        return dict(base, provider_id=provider_id, registry=_row_view(row), required=required, missing=missing,
                    would_be_refused=True, note="the %s plan needs %s in the request" % (provider_id, missing))
    try:
        plan = route.plan_fn(**request, **route.bind)
    except (*route.refusals, OSError, TypeError, ValueError) as exc:
        return _not_ready(base, code="PLAN_REFUSED", provider_id=provider_id, row=row, why=str(exc))
    if route.identity_of is not None:
        expected = route.expected_identity(provider_id) if route.expected_identity else provider_id
        if route.identity_of(plan) != expected:
            return _not_ready(base, code="PLAN_REFUSED", provider_id=provider_id, row=row,
                              why="the request planned %r, not %s" % (route.identity_of(plan), expected))
    execution = ("provider.candidate.invoke will refuse: %s"
                 % ("NOT_AUTHORIZED (an adapter exists but no governed action authorizes it)"
                    if route.run_fn else "EXECUTION_NOT_ADAPTED (no execution adapter exists)"))
    return dict(base, ready=True, provider_id=provider_id, registry=_row_view(row), candidate_plan=plan,
                plan_sha256=plan["plan_sha256"], would_be_refused=False, execution=execution, note=execution)


def plan_candidate(params: dict) -> dict:
    return build_plan(params)


def plan_invoke(params: dict) -> dict:
    return build_plan(params, invoking=True)


def _refuse_unready(plan: dict) -> None:
    if plan.get("missing") and not plan.get("refusal_code"):
        raise A.Refused("MISSING_PARAMETER", "the plan needs %s" % plan["missing"], required=plan.get("required"),
                        missing=plan["missing"], provider_id=plan.get("provider_id"), executed=False)
    registry = plan.get("registry") or {}
    raise A.Refused(plan.get("refusal_code", "PLAN_REFUSED"), plan.get("planning_refusal", "the plan is not ready"),
                    provider_id=plan.get("provider_id"), blocker=registry.get("blocker"),
                    blockers=registry.get("blockers"), executed=False)


def do_plan(params: dict) -> dict:
    plan = build_plan(params)
    if not plan["ready"]:
        _refuse_unready(plan)
    return {"status": "OK", "executed": False, "provider_id": plan["provider_id"],
            "plan_sha256": plan["plan_sha256"], "plan": plan["candidate_plan"], "registry": plan["registry"]}


def _receipt(provider_id: str, row: dict, plan_sha256: str | None) -> dict:
    return {"schema": REFUSAL_SCHEMA, "provider_id": provider_id, "plan_sha256": plan_sha256, "executed": False,
            "lifecycle_state": row["lifecycle_state"], "claim_ceiling": row["claim_ceiling"],
            "blockers": row["blockers"], "not_claimed": list(NOT_CLAIMED)}


def _refuse_no_route(plan: dict) -> None:
    """A registered provider with no plan function: refuse execution naming its primary blocker kind."""
    row, provider_id = plan["registry"], plan["provider_id"]
    kind = row["blocker"]["kind"]
    raise A.Refused("NOT_AUTHORIZED" if kind == "NOT_AUTHORIZED" else "EXECUTION_NOT_ADAPTED",
                    plan["planning_refusal"], provider_id=provider_id, blocker_kind=kind,
                    receipt=_receipt(provider_id, row, None))


def invoke(params: dict) -> dict:
    """Always refuses."""
    plan = build_plan(params, invoking=True)
    if not plan["ready"]:
        if plan.get("refusal_code") == "NO_PLAN_ROUTE":
            _refuse_no_route(plan)
        _refuse_unready(plan)
    if params.get("approved_plan_sha256") != plan["plan_sha256"]:
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the plan_sha256 returned by /plan for this provider",
                        expected=plan["plan_sha256"], provider_id=plan["provider_id"], executed=False)
    receipt = _receipt(plan["provider_id"], plan["registry"], plan["plan_sha256"])
    if ROUTES[plan["provider_id"]].run_fn is not None:
        raise A.Refused("NOT_AUTHORIZED",
                        "an execution adapter exists for %s but no governed action authorizes running it from ARGUS; "
                        "nothing was run" % plan["provider_id"], blocker_kind="NOT_AUTHORIZED", receipt=receipt)
    raise A.Refused("EXECUTION_NOT_ADAPTED",
                    "ARGUS has no execution adapter for %s; the plan is valid and approved, and nothing was run"
                    % plan["provider_id"], blocker_kind="CODE_MISSING", receipt=receipt)
