"""The ARGUS service: a read-only window onto receipts and heartbeats."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import sys
import time
import weakref
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core import finding_id as _FID
from argus.core import process_hardening as _process_hardening
_process_hardening.apply()
from argus.core import paths, storage_catalog

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from argus.service import request_guard as _RG
from argus.core import safe_names as _SN

from argus.core import feed as F
from argus.core import heartbeat as HB
from argus.core import storage_policy as STORAGE_POLICY
from argus.service import receipts as RECEIPTS

RUN_ROOTS = paths.artifact_read_roots()

ARTIFACT_ROOTS = paths.artifact_roots()
SERVE_ROOTS = paths.serve_roots()

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

WS_POLL_S = 2.0


def default_ws_allowed_origins() -> frozenset:
    """The exact browser Origins the observatory socket accepts: the development UI, the Compose UI and whatever the operator names in ARGUS_OBSERVE_WS_ORIGINS."""
    return frozenset({
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8792",
        *(origin.strip() for origin in os.environ.get("ARGUS_OBSERVE_WS_ORIGINS", "").split(",")
          if origin.strip()),
    })


WS_ALLOWED_ORIGINS = default_ws_allowed_origins()
SNAPSHOT_TTL_S = 5.0
assert SNAPSHOT_TTL_S > WS_POLL_S, (
    "the snapshot cache must outlive the poll interval or it can never be hit")

REFRESH_S = 5.0
_refresher: object = None

_snapshot: dict = {"at": 0.0, "value": None}

_snapshot_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _snapshot_lock_for_loop() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lk = _snapshot_locks.get(loop)
    if lk is None:
        lk = asyncio.Lock()
        _snapshot_locks[loop] = lk
    return lk


async def snapshot(force: bool = False) -> dict:
    """The observatory, built off the event loop and briefly cached."""
    now = time.time()
    if not force and _snapshot["value"] is not None \
            and now - _snapshot["at"] < SNAPSHOT_TTL_S:
        return _snapshot["value"]
    async with _snapshot_lock_for_loop():
        now = time.time()
        if not force and _snapshot["value"] is not None \
                and now - _snapshot["at"] < SNAPSHOT_TTL_S:
            return _snapshot["value"]
        value = await asyncio.to_thread(F.observatory, RUN_ROOTS, force=force)
        _snapshot.update(at=time.time(), value=value)
        return value

app = FastAPI(title="ARGUS", version="0.1.0",
              description="Read-only operational feed over ARGUS receipts.")

app.include_router(RECEIPTS.router)

from argus.service import ledger_api as LEDGER_API

app.include_router(LEDGER_API.router)

from argus.service import route_boards_api as ROUTE_BOARDS_API

app.include_router(ROUTE_BOARDS_API.router)

from argus.service import provider_api as PROVIDER_API

app.include_router(PROVIDER_API.router)

from argus.service import pipeline_api as PIPELINE_API

app.include_router(PIPELINE_API.router)


@app.on_event("startup")
async def warm() -> None:
    """Build the first snapshot before anyone asks for it."""
    await snapshot(force=True)
    global _refresher
    if _refresher is None:
        _refresher = asyncio.create_task(_refresh_loop())
    global _primer
    if _primer is None:
        _primer = asyncio.create_task(_prime_read_caches())


_primer = None


async def _prime_read_caches() -> None:
    """Read the routes every page asks for on load, once, one after another, so the first page does not pay for all of them at once."""
    for call in (lambda: asyncio.to_thread(scroll_status_route, None), prime_discovery,
                 scroll_effort, surfaces, ingest_plan, activity, runtime):
        try:
            await call()
        except Exception:
            continue


async def prime_discovery() -> None:
    """Warm the one bounded local-material scan used by every selected-scroll workspace."""
    from argus.core import local_discovery as LD

    await asyncio.to_thread(_DISCOVERY_MEMO.get, "inventory", LD.scan)


async def _refresh_loop() -> None:
    """The only thing that walks the disk."""
    while True:
        try:
            await asyncio.sleep(REFRESH_S)
            await snapshot(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(REFRESH_S)


_ALLOWED_HOSTS = _RG.allowed_hosts()


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    """Refuse DNS-rebinding (a Host that is not ours) and cross-site browser requests before any route runs."""
    if not _RG.host_ok(request.headers.get("host"), _ALLOWED_HOSTS):
        return JSONResponse(status_code=421, content={"error": "MISDIRECTED", "detail": "this Host is not one this service answers to; set ARGUS_ALLOWED_HOSTS to add one"})
    if not _RG.fetch_site_ok(request.headers.get("sec-fetch-site")):
        return JSONResponse(status_code=403, content={"error": "CROSS_SITE", "detail": "a page from another site may not call this service"})
    if any(paths.is_remote_path(v) for v in request.query_params.values()):
        return JSONResponse(status_code=400, content={"error": "REMOTE_PATH_REFUSED", "detail": "a parameter names a network path; this service reads local paths only"})
    return await call_next(request)


@app.middleware("http")
async def refuse_writes(request: Request, call_next):
    """Belt and braces."""
    if request.method.upper() in WRITE_METHODS:
        return JSONResponse(status_code=405, content={
            "error": "this service is observational",
            "detail": ("ARGUS's UI service is read-only by construction so it cannot "
                       "disturb a running or sealed experiment")})
    return await call_next(request)


@app.get("/api/control_evidence_kinds")
def control_evidence_kinds():
    """The two kinds of control evidence, kept apart: a published 3-D prediction that localizes likely ink is NOT independent physical ground truth."""
    from argus.core import control_evidence_kinds as CEK
    return CEK.payload()


@app.get("/api/updates")
def provider_updates_status():
    """Provider update state for the Updates screen: policy, watched sources, discovered updates with their semantic diff, gates, tested flags, activation readiness per scope, pins and the watchlist."""
    from argus.core import provider_updates as PU
    return PU.status()


@app.get("/api/external_evidence")
def external_evidence_status():
    """Evidence that lives outside the repository, per item: AVAILABLE_LOCALLY, NOT_MOUNTED, RECOVERABLE or MISSING."""
    from argus.core import external_evidence as EE
    return EE.status()


@app.get("/api/install_tiers")
def install_tiers_status(developer: bool = False):
    """Tiered component health (core, geometry, advanced, remote) with plan-only repair steps."""
    from argus.core import install_tiers as IT
    res = IT.check_all(IT.default_runner, developer_mode=bool(developer))
    return {"schema": "argus-install-tiers-status-v1", "read_only": True, "check": res,
            "repair": IT.repair_plan(res, developer_mode=bool(developer))}


@app.get("/api/health")
def health():
    """Health, plus the scan telemetry whose absence hid a 23,613 CPU-second regression."""
    import os as _os
    import time as _time
    try:
        cpu_s = round(sum(_os.times()[:2]), 1)
    except Exception:
        cpu_s = None
    import sys as _sys

    last = dict(getattr(F, "LAST_SCAN", {}) or {})
    age = None
    if _snapshot.get("at"):
        age = round(_time.time() - _snapshot["at"], 2)
    return {"ok": True, "service": "argus", "read_only": True,
            "run_roots": [_RG.redact_home(str(p)) for p in RUN_ROOTS],
            "roots_present": {_RG.redact_home(str(p)): p.is_dir() for p in RUN_ROOTS},
            "scan": {
                "last_seconds": last.get("seconds"),
                "runs": last.get("runs"),
                "listings_read": last.get("listings_read"),
                "listings_reused": last.get("listings_reused"),
                "snapshot_age_s": age,
                "snapshot_ttl_s": SNAPSHOT_TTL_S,
                "ws_poll_s": WS_POLL_S,
                "refresh_s": REFRESH_S,
                "ttl_exceeds_poll": SNAPSHOT_TTL_S > WS_POLL_S,
            },
            "process_cpu_seconds": cpu_s,
            "pid": _os.getpid(),
            "interpreter": {
                "executable": _RG.redact_home(_sys.executable),
                "prefix": _RG.redact_home(_sys.prefix),
                "base_prefix": _RG.redact_home(_sys.base_prefix),
                "in_a_virtualenv": _sys.prefix != _sys.base_prefix,
                "version": _sys.version.split()[0],
                "why_this_is_reported": "the executable path is a trampoline and is the same "
                                        "for every venv over this base. sys.prefix is what "
                                        "decides which site-packages are in play.",
            }}


@app.get("/api/observatory")
async def observatory():
    return await snapshot()


@app.get("/api/runs/{run_id}")
async def run(run_id: str):
    def find():
        for d in _find_runs():
            if d.name == run_id:
                return F.run_record(d)
        return None

    rec = await asyncio.to_thread(find)
    if rec is None:
        raise HTTPException(404, "no run %r" % run_id)
    return rec


@app.get("/api/file")
def file(path: str):
    """Serve one artifact, if it is inside a declared root and not under seal."""
    p = _resolve_served(path)
    if not any(_within(p, r) for r in SERVE_ROOTS):
        raise HTTPException(403, "outside the declared roots")
    if not p.is_file():
        raise HTTPException(404, "no such file")
    seal = F.sealed_by(p)
    if seal:
        raise HTTPException(423, "under an active blinded experiment (%s); sealed bytes "
                                 "do not leave the service" % seal)
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=mt, headers=_RG.file_headers(p.name, mt))


@app.get("/api/discovery")
def local_discovery(force: bool = False):
    """Bounded, read-only inventory of scroll-related material already on this machine."""
    from argus.core import local_discovery as _discovery
    if force:
        _DISCOVERY_MEMO.clear()
        return _discovery.scan(force=True)
    return _DISCOVERY_MEMO.get("inventory", _discovery.scan)


@app.get("/api/discovery/file/{asset_id}")
def local_discovery_file(asset_id: str):
    """Serve a discovered render only when it is already inside a declared serve root."""
    from argus.core import local_discovery as _discovery
    found = _discovery.file_for(asset_id)
    if not found:
        raise HTTPException(404, "discovered render is absent, changed, or not mounted in a declared serve root")
    path = found["path"]
    if (not any(_within(path, r) for r in SERVE_ROOTS)
            and found["record"].get("serve_policy") not in _discovery.TRUSTED_EXTERNAL_SERVE_POLICIES):
        raise HTTPException(423, "render is discovered and hash-verified, but its source root is not mounted for serving")
    mt = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=mt, headers=_RG.file_headers(path.name, mt))


@app.get("/api/preflight")
def preflight(action: str, scrolls: str = ""):
    """What an action WOULD do, computed before anything is enqueued."""
    from argus.core import preflight_actions as _pa
    picked = [s for s in scrolls.split(",") if s.strip()]
    return _pa.preflight(action, picked)


@app.get("/api/shelf")
def shelf():
    """Every registered scroll, grouped by what it is, with NOTHING selected."""
    from argus.core import scroll_shelf as _shelf
    try:
        return _shelf.compose("http://127.0.0.1:%d" % _self_port())
    except _shelf.ShelfError as exc:
        return {"contract": _shelf.CONTRACT, "scrolls": [], "count": 0,
                "error": "SHELF_UNAVAILABLE", "detail": str(exc),
                "why_not_empty": "an empty shelf would read as 'there are no scrolls', which "
                                 "is a claim about the corpus. This is a report about the "
                                 "service failing to compose one."}


def _self_port() -> int:
    """The port this service is bound to, so the composer can reach its siblings."""
    import os
    return int(os.environ.get("ARGUS_SERVICE_PORT") or 8787)


@app.get("/api/scrolls")
def scrolls(detail: bool = False):
    """Everything ARGUS holds, grouped by the scroll it belongs to."""
    from argus.core import scroll_index as _si
    return _si.build() if detail else _si.summary()


@app.get("/api/scroll_metadata/{scroll_id}")
def scroll_metadata(scroll_id: str, absent_ok: bool = False):
    """One scroll's physical-fact contract (Boundary B/2: winding count, umbilicus, coordinate frame, and each field's own evidence and permitted use) -- read through the ONE canonical loader,..."""
    from argus.core import scroll_dataset_metadata as SDM
    if not _SN.is_safe_name(scroll_id):
        raise HTTPException(400, "a scroll id is letters, digits, dot, dash or underscore, not a path")
    try:
        rec = SDM.load_scroll_metadata(scroll_id)
    except SDM.MetadataRefusal as exc:
        raise HTTPException(409, "refused: %s" % exc)
    if rec is None and absent_ok:
        return {"state": "NO_RECORD", "scroll_id": scroll_id,
                "why": "no ScrollDatasetMetadata record has been saved for %r yet; this is not an "
                       "empty result, and nothing substitutes a default" % scroll_id}
    if rec is None:
        raise HTTPException(
            404, "no ScrollDatasetMetadata record for %r -- none has been saved yet; this is "
                "not the same as an empty result, and nothing here substitutes a default"
                % scroll_id)
    body = SDM._dataclass_to_dict(rec)
    body["record_sha256"] = rec.record_sha256
    body["validation_problems"] = SDM.validate_record(rec)
    return body


@app.get("/api/surface_status/{scroll_id}")
def surface_status_one(scroll_id: str):
    """One scroll's status, as independent facts -- bytes/render/geometry-admissible/prize- eligible are never collapsed into a single field."""
    from argus.core import surface_status as S
    if not _SN.is_safe_name(scroll_id):
        raise HTTPException(400, "a scroll id is letters, digits, dot, dash or underscore, not a path")
    rec = S.resolved(scroll_id)
    if rec is None:
        raise HTTPException(
            404, "no surface_status record for %r -- none has been recorded yet" % scroll_id)
    return rec


@app.get("/api/surface_status")
def surface_status_all():
    """Every scroll with at least one recorded status fact."""
    from argus.core import surface_status as S
    return {"scrolls": S.all_resolved()}


@app.get("/api/capability_graph")
def capability_graph():
    """The ten product capabilities and the route, derived from receipts."""
    from argus.core import capability_graph as CG
    root = ARTIFACT_ROOTS[0]
    have = ("CT", "Surface", "Flatten", "Sample")
    return CG.as_record(root, have_stages=have)


@app.get("/api/process_contract")
def process_contract(scroll: str | None = None):
    """The complete product process, including stages that do not exist yet."""
    from argus.core import process_contract as PC
    return PC.derive(scroll=scroll)


@app.get("/api/stage_lineage")
def stage_lineage(scroll: str):
    """One physical scroll's ordered, immutable record of process-contract stage attempts."""
    from argus.core import stage_lineage as SL
    try:
        return SL.coverage(scroll)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None


from argus.core.single_flight import SingleFlightTTL as _SingleFlightTTL
from argus.core import readiness as _READY
from argus.core.single_flight import MISS as _MISS
from argus.core.single_flight import SingleFlightTimeout as _SFT
from argus.core import worker_pool as _WP

_HEAVY_POOL = _WP.BoundedPool("read-heavy", **_WP.sizes(os.environ.get("ARGUS_PROFILE") or None))
_BRICK_POOL = _WP.BoundedPool("volume-brick", max_workers=2, max_pending=8)
_READY_MEASURE_MEMO = _SingleFlightTTL(ttl_s=5.0, max_entries=1)
_DISCOVERY_MEMO = _SingleFlightTTL(ttl_s=90.0, max_entries=1)


@app.exception_handler(_WP.PoolSaturated)
async def _pool_saturated(request: Request, exc: _WP.PoolSaturated):
    return JSONResponse(status_code=503, headers={"Retry-After": str(exc.retry_after_s)},
                        content={"error": "BUSY", "detail": str(exc), "retry_after_s": exc.retry_after_s})


async def _heavy(fn, *args):
    """Heavy read work goes through the bounded pool: capped concurrency, capped queue, a visible refusal beyond that."""
    return await _HEAVY_POOL.run(fn, *args)


async def _heavy_memo(memo, key, compute):
    """A fresh answer in the memo is returned without taking a worker; only a miss goes through the pool, and callers of the same key share one computation."""
    hit = memo.peek(key)
    if hit is not _MISS:
        return hit
    return await _HEAVY_POOL.run(memo.get, key, compute)


@app.exception_handler(_SFT)
async def _single_flight_timeout(request: Request, exc: _SFT):
    return JSONResponse(status_code=503, headers={"Retry-After": "5"}, content={"error": "BUSY", "detail": "a shared computation is taking too long", "retry_after_s": 5})


@app.get("/api/live")
async def live():
    """Is the process answering at all?"""
    return _READY.liveness()


def _primed() -> bool:
    """Start-up warming finished without the primer task itself failing."""
    if _primer is None or not _primer.done():
        return False
    try:
        return _primer.exception() is None
    except asyncio.CancelledError:
        return False


def _measure_for_readiness() -> dict:
    from argus.cli import hardware as HW
    from argus.core import provider_updates as PU

    return {"measured": HW.probe(), "gpu_memory": HW.gpu_memory(), "provider_lifecycle": PU.lifecycle_summary()}


@app.get("/api/ready")
async def ready():
    """Can it do useful work right now?"""
    parts = await asyncio.to_thread(_READY_MEASURE_MEMO.get, "measure", _measure_for_readiness)
    rec = _READY.compute(primed=_primed(), pools=[_HEAVY_POOL.status()], **parts)
    headers = {"Retry-After": "5"} if not rec["ready"] else {}
    return JSONResponse(status_code=200 if rec["ready"] else 503, content=rec, headers=headers)


_SCROLL_STATUS_MEMO = _SingleFlightTTL(ttl_s=2.0, max_entries=64)
_SCROLL_WORKSPACE_MEMO = _SingleFlightTTL(ttl_s=5.0, max_entries=64)


@app.get("/api/journey")
def journey_route():
    """The stable human/agent contract from raw CT through publication."""
    from argus.core import journey

    out = journey.describe()
    out["read_only"] = True
    out["agent_contract"] = {
        "openapi": "/openapi.json",
        "selected_scroll_workspace": "/api/scroll_workspace/{physical_scroll}",
        "selected_scroll_truth": "/api/scroll_truth?scroll={physical_scroll}",
        "selected_scroll_truth_compatibility": "/api/scroll_truth?scroll={physical_scroll}",
        "all_scroll_status": "/api/scroll_status",
        "zero_fetch_acquisition_plan": "/api/retrieval/plan?scroll={physical_scroll}&url={official_url}&volume_id={volume_id}&phase=A0",
        "governed_session": "POST /ui/session",
        "governed_plan": "POST /ui/plan/{action_id}",
        "governed_action": "POST /ui/act/{action_id}",
        "rule": "read next_action from selected_scroll_truth; never infer or skip a prerequisite",
    }
    return out


@app.get("/api/scroll_status")
def scroll_status_route(scroll: str | None = None):
    """The ONE per-scroll status every screen reads: the eight questions, the sixteen journey steps, one next action and one blocker (argus.core.scroll_status)."""
    from argus.core import scroll_status as SS
    return _SCROLL_STATUS_MEMO.get(scroll or "", lambda: SS.status(scroll) if scroll else SS.status_all())


@app.get("/api/scroll_truth")
def scroll_truth_route(scroll: str):
    """The single selected-scroll read: route status plus identity-matched local material."""
    if not scroll.strip():
        raise HTTPException(400, "scroll is required")
    from argus.core import scroll_truth as ST
    from argus.core import local_discovery as LD
    inventory = _DISCOVERY_MEMO.get("inventory", LD.scan)
    return ST.for_scroll(scroll, inventory=inventory)


@app.get("/api/scroll_workspace/{scroll}")
def scroll_workspace_route(scroll: str):
    """One selected-scroll snapshot for the persistent header, rooms and AI drivers."""
    if not scroll.strip():
        raise HTTPException(400, "scroll is required")
    from argus.core import local_discovery as LD
    from argus.core import scroll_workspace as SW

    def compose():
        inventory = _DISCOVERY_MEMO.get("inventory", LD.scan)
        return SW.build(scroll, inventory=inventory)

    return _SCROLL_WORKSPACE_MEMO.get(scroll, compose)


@app.get("/api/targets")
def targets(prize: str | None = None):
    """The eligible target sets, kept SEPARATE."""
    root = next((r for r in ARTIFACT_ROOTS
                 if (r / "scrollprize_crawl" / "OFFICIAL_ELIGIBLE_TARGETS_V2.json").is_file()),
                None)
    if root is None:
        packaged = Path(__file__).resolve().parents[1] / "public_target_registry.json"
        if packaged.is_file():
            d = json.loads(packaged.read_text(encoding="utf-8"))
            d["available"] = True
            d["why_v2"] = ("Official eligibility and acquisition metadata are packaged from the "
                            "read-only prize registry; local holdings and scientific readiness "
                            "remain separate facts.")
            d["operator_fences"] = {}
            d["acquisition_families"] = {}
            for row in d.get("targets") or []:
                key = "%.3fum/%.0fkeV" % (row["pitch_um"], row["energy_kev"])
                d["acquisition_families"].setdefault(key, []).append(row["scroll"])
            if prize:
                p = prize.upper()
                valid = [k for k, v in d["sets"].items() if isinstance(v, dict)]
                if p not in valid:
                    raise HTTPException(404, "unknown prize %r; have %s"
                                        % (prize, ", ".join(valid)))
                d = dict(d, targets=[t for t in d["targets"] if p in t["prizes"]],
                         filtered_to=p)
            return d
        return {
            "schema": "argus-eligible-targets-unconfigured-v1",
            "id": "UNCONFIGURED",
            "available": False,
            "sets": {},
            "targets": [],
            "acquisition_families": {},
            "operator_fences": {},
            "v1_ids_that_no_longer_resolve": [],
            "why_v2": "No official target registry is installed in this ARGUS home. Eligibility is unknown, not empty.",
            "what_this_does_not_change": [
                "Canonical scroll identities and local holdings remain visible.",
                "No scroll is treated as prize-eligible until a provenance-bound registry is installed.",
            ],
            "next_action": {
                "label": "Install or refresh the official target registry",
                "where": "System > Provider updates",
                "effect": "fetches metadata only; it does not download CT volumes",
            },
            **({"filtered_to": prize.upper()} if prize else {}),
        }
    d = json.loads((root / "scrollprize_crawl" / "OFFICIAL_ELIGIBLE_TARGETS_V2.json")
                   .read_text(encoding="utf-8"))
    if prize:
        p = prize.upper()
        if p not in d["sets"]:
            raise HTTPException(404, "unknown prize %r; have %s"
                                % (prize, ", ".join(d["sets"])))
        d = dict(d, targets=[t for t in d["targets"] if p in t["prizes"]],
                 filtered_to=p)
    return d


@app.get("/api/unroll")
def unroll(target: str | None = None):
    """The layer stack for one target, or the list of targets when none is named."""
    base = next((r / "ui_audit" for r in ARTIFACT_ROOTS
                 if (r / "ui_audit" / "TARGETS.json").is_file()), None)
    if base is None:
        if target is not None:
            raise HTTPException(404, "no exported layer stack named %r; no layer index is installed"
                                % target)
        return {
            "schema": "argus-unroll-index-unconfigured-v1",
            "available": False,
            "targets": [],
            "comparison_rule": "No exported layer stacks are installed. An empty list is not evidence that no renders exist upstream.",
            "next_action": {
                "label": "Attach or generate an identity-bound layer export",
                "where": "Workbench > Evidence",
            },
        }
    index = base / "TARGETS.json"
    idx = json.loads(index.read_text(encoding="utf-8"))
    if target is None:
        return idx
    row = next((t for t in idx["targets"] if t["key"] == target), None)
    if row is None:
        raise HTTPException(404, "unknown target %r; have %s"
                            % (target, ", ".join(t["key"] for t in idx["targets"])))
    d = base / row["base"].strip("/")
    src = d / "LAYER_SOURCES.json"
    if not src.is_file():
        raise HTTPException(404, "no layer sources for %r" % target)
    out = json.loads(src.read_text(encoding="utf-8"))
    _bind_layer_paths_for_runtime(out, d)
    out["target_row"] = row
    out["comparison_rule"] = idx["comparison_rule"]
    out["file_base"] = str(d)
    tasks = d / "REVIEW_TASKS.json"
    review_tasks = json.loads(tasks.read_text(encoding="utf-8")) if tasks.is_file() else None
    if review_tasks is not None:
        from argus.core import review_store as RS
        review_tasks = RS.merge_answers(review_tasks, d)
    out["review_tasks"] = review_tasks
    return out


def _bind_layer_paths_for_runtime(payload: dict, target_dir: Path) -> None:
    """Make generated layer manifests portable without rewriting their evidence."""

    for level in (payload.get("levels") or {}).values():
        for entry in (level.get("images") or {}).values():
            raw = entry.get("path")
            if not raw:
                continue
            name = Path(str(raw).replace("\\", "/")).name
            candidate = target_dir / name
            if candidate.is_file() and str(candidate) != str(raw):
                entry["source_path"] = raw
                entry["path"] = str(candidate)



def _interpretation_dir(target: str):
    d = paths.resolve_ui_target_dir(target)
    if d is None:
        raise HTTPException(404, "unknown target %r" % target)
    return d


@app.get("/api/reviewed_regions")
def reviewed_regions(target: str):
    """Regions at least two independent PEOPLE accepted as ink, and the settled tasks that were refused (reported, never dropped)."""
    from argus.core import review_store as RS
    from argus.core import reviewed_region_bridge as BR
    d = _interpretation_dir(target)
    payload = RS.load_payload(d)
    if payload is None:
        return {"target": target, "read_only": True, "regions": [], "refused": [],
                "why": "this target has no review tasks yet; open candidates for review first"}
    regions, refused = BR.regions_from_tasks_payload(payload)
    return {"target": target, "read_only": True, "contract": BR.CONTRACT_ID,
            "regions": regions, "refused": refused,
            "min_independent_humans": BR.MIN_INDEPENDENT_HUMANS}


@app.get("/api/reading_board")
def reading_board_route(target: str):
    """The reading board for one target."""
    from argus.core import interpretation_actions as IA
    d = _interpretation_dir(target)
    out = IA.board_state(d)
    out.update(target=target, read_only=True)
    return out


@app.get("/api/interpretation")
def interpretation_route(target: str):
    """Everything the interpretation screens need for one target, in one read."""
    from argus.core import interpretation_actions as IA
    return IA.interpretation_state(target, _interpretation_dir(target))


@app.get("/api/interpretation/packets")
def interpretation_packets(target: str):
    from argus.core import publication_packet as PP
    _interpretation_dir(target)
    try:
        return {"target": target, "read_only": True, "packets": PP.list_packets(target),
                "root": str(PP.packets_root(target))}
    except paths.PathContractViolation as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/interpretation/packet/verify")
def interpretation_packet_verify(target: str, packet: str):
    """Reopen one exported packet and re-walk every hash."""
    from argus.core import actions as ACT
    from argus.core import interpretation_actions as IA
    from argus.core import publication_packet as PP
    _interpretation_dir(target)
    try:
        return PP.verify(IA.resolve_packet(target, packet))
    except ACT.Refused as exc:
        raise HTTPException(403, exc.why) from exc
    except paths.PathContractViolation as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/science_attachments")
def science_attachments():
    """The imported science manifests that can be attached, each with its latest attachment record (path, sha256, size, status per item -- never bytes)."""
    from argus.core import science_attach as SA
    return SA.attachments_state()


@app.get("/api/user_data")
def user_data_state():
    """The private/public boundary and a read-only detach preview."""
    from argus.core import user_data as UD
    status = UD.status()
    inv = UD.inventory(readers=False)
    status["inventory"] = {
        "items": inv["count"], "present": inv["present"], "bytes": inv["bytes"],
        "by_category": inv["by_category"],
    }
    status["detach_plan"] = UD.detach_plan()
    status["release_boundary"] = {
        "rule": "user-data paths and matching content hashes are excluded from every release export",
        "verify_command": "argus userdata verify-release <export-dir>",
        "public_data_is_not_user_data": True,
    }
    return status


@app.get("/api/science_closeout")
def science_closeout():
    """What the Workbench shows about an imported closeout, computed from hash-verified files; nothing is imported in the public release."""
    from argus.core import science_attach as SA
    return SA.closeout_view()


@app.get("/api/private_science/file")
def private_science_file(manifest: str, sha256: str):
    """One private local file, only if its sha256 is one the manifest lists AND the local bytes hash to it (re-hashed on first serve, cached by path, size and mtime)."""
    from argus.core import science_attach as SA
    res = SA.serve_file(manifest, sha256)
    if res["status"] != 200:
        raise HTTPException(res["status"], detail={k: v for k, v in res.items() if k != "status"})
    mt = mimetypes.guess_type(res["name"])[0] or "application/octet-stream"
    return FileResponse(res["path"], media_type=mt, headers=_RG.file_headers(res["name"], mt))


def _sealed_queue(kind: str):
    from argus.core import sealed_review_queue as SQ
    if kind not in SQ.KIND_FILES:
        raise HTTPException(404, "unknown review queue %r" % kind)
    return SQ


@app.get("/api/sealed_review/{kind}")
def sealed_review_queue(kind: str, reviewer: str | None = None):
    """A blinded review queue cut from a sealed task file."""
    SQ = _sealed_queue(kind)
    try:
        return SQ.queue_view(kind, reviewer=reviewer)
    except (SQ.SealedQueueError, OSError, ValueError, KeyError):
        return SQ.unavailable_queue_view(kind)


@app.get("/api/sealed_review/{kind}/{task_id}")
def sealed_review_task(kind: str, task_id: str):
    SQ = _sealed_queue(kind)
    try:
        return SQ.task_view(kind, task_id)
    except SQ.SealedQueueError as exc:
        raise HTTPException(404, detail={"code": "NO_SUCH_TASK", "why": str(exc)}) from exc


@app.get("/api/sealed_review/{kind}/{task_id}/results")
def sealed_review_results(kind: str, task_id: str, reviewer: str | None = None):
    """Every answer on one task -- disagreement and abstention included -- only to a reviewer who has answered it themselves."""
    SQ = _sealed_queue(kind)
    try:
        return SQ.results_view(kind, task_id, reviewer)
    except SQ.SealedQueueError as exc:
        raise HTTPException(404, detail={"code": "NO_SUCH_TASK", "why": str(exc)}) from exc


@app.get("/api/sealed_review/{kind}/{task_id}/layer/{name}")
def sealed_review_layer(kind: str, task_id: str, name: str):
    """One reviewer-facing layer image, only if it is MOUNTED_VERIFIED against the sealed task."""
    SQ = _sealed_queue(kind)
    try:
        res = SQ.layer_file(kind, task_id, name)
    except SQ.SealedQueueError as exc:
        raise HTTPException(404, detail={"code": "NO_SUCH_TASK", "why": str(exc)}) from exc
    if res["status"] != 200:
        raise HTTPException(res["status"], detail={k: v for k, v in res.items() if k != "status"})
    mt = mimetypes.guess_type(res["name"])[0] or "application/octet-stream"
    return FileResponse(res["path"], media_type=mt, headers=_RG.file_headers(res["name"], mt))


@app.get("/api/mesh")
async def mesh(path: str, coord_scale: float = 1.0, stride: int | None = None,
               with_windings: bool = False):
    """The mesh lattice for a viewer, with jumping edges marked."""
    from argus.core.contracts import Refusal as _R
    from argus.core.meshview import lattice
    p = _resolve_served(path)
    if not any(_within(p, r) for r in SERVE_ROOTS):
        raise HTTPException(403, "outside the declared roots")
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")
    try:
        return await asyncio.to_thread(
            lattice, p, coord_scale=coord_scale, stride=stride, with_windings=with_windings)
    except _R as e:
        raise HTTPException(422, e.reason)


@app.get("/api/windings")
async def windings(path: str, coord_scale: float = 1.0, stride: int = 4,
                   max_windings: int = 12):
    """Proposed windings from an existing tifxyz trace, grouped by the existing jump rule."""
    from argus.core.contracts import Refusal as _R
    from argus.core.windings import inspect as _inspect
    p = _resolve_served(path)
    if not any(_within(p, r) for r in SERVE_ROOTS):
        raise HTTPException(403, "outside the declared roots")
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")
    try:
        return await asyncio.to_thread(_inspect, p, coord_scale=coord_scale,
                                       stride=max(1, stride), max_windings=max(1, min(int(max_windings), 256)))
    except _R as e:
        raise HTTPException(422, e.reason)


@app.get("/api/corrections")
async def corrections(path: str):
    """Every recorded surface-correction event for one mesh, replayed onto its current state."""
    from argus.core.correction_store import merge_corrections
    p = _resolve_served(path)
    if not any(_within(p, r) for r in SERVE_ROOTS):
        raise HTTPException(403, "outside the declared roots")
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")
    return await asyncio.to_thread(merge_corrections, p)


@app.get("/api/sources")
async def sources(live: bool = True):
    """The upstream registry with local holdings, and optionally a reachability probe."""
    from argus.core import sources as S

    return await asyncio.to_thread(S.inventory, live=live)


@app.get("/api/instances")
def instances_route():
    """The ARGUS stacks running on this machine and the memory budget they share (argus.core.instance_guard)."""
    from argus.core import instance_guard as IG
    now = time.time()
    rows = IG.live()
    out = []
    for key, svc in sorted(IG.stacks(rows).items()):
        cost = IG.stack_cost_gib(svc)
        out.append({
            "ports": {s.get("role"): s.get("port") for s in svc},
            "ui_port": next((s.get("ui_port") for s in svc if s.get("ui_port")), None),
            "build_sha": next((s.get("build_sha") for s in svc if s.get("build_sha")), None),
            "idle_minutes": round((now - IG.last_active(svc)) / 60.0, 1),
            "pinned": any(s.get("pinned") for s in svc),
            "memory_gib": round(cost, 2) if cost is not None else None,
            "this_stack": key == IG.stack_key(),
        })
    free = IG.commit_free_gib()
    return {"schema": "argus-instances-v1", "stacks": out, "count": len(out),
            "ceiling": IG.max_stacks(), "reserve_gib": IG.min_commit_free_gib(),
            "idle_retire_minutes": IG.idle_evict_s() / 60.0,
            "commit_free_gib": round(free, 1) if free is not None else None,
            "rule": "a new stack must leave the reserve free; idle, unpinned stacks are retired to make "
                    "room, and only when none is retirable is a new stack refused"}


@app.get("/api/connect")
def connector_route():
    """What an MCP-capable assistant can reach, without pretending a client is attached."""
    import importlib.util

    source_ready = importlib.util.find_spec("argus.connect.launcher") is not None
    configured_runtime = os.environ.get("ARGUS_CONNECT_PYTHON")
    runtime_candidates = [Path(configured_runtime)] if configured_runtime else []
    if os.name == "nt":
        runtime_candidates.append(Path(_argus_public_path('home', 'runtime/argus-connect/Scripts/python.exe')))
    runtime_ready = any(path.is_file() for path in runtime_candidates)
    return {
        "schema": "argus-connect-status-v1",
        "source_ready": source_ready,
        "runtime_ready": runtime_ready,
        "stack_discovery": source_ready,
        "transport": "stdio",
        "tools": [
            "argus_overview", "argus_list_scrolls", "argus_scroll_brief", "argus_findings",
            "argus_jobs", "argus_list_actions", "argus_plan_action", "argus_ui_link",
        ],
        "permissions": {"read": True, "plan": True, "execute": False},
        "target_detail": (
            "allowed" if os.environ.get("ARGUS_CONNECT_TARGET_DETAIL", "").lower() == "allow"
            else "withheld"
        ),
        "client_attachment": {
            "observable": False,
            "state": "CHECK_IN_CLIENT",
            "why": "the assistant owns the stdio process; ARGUS cannot see whether that client launched it",
            "proof": "the assistant's tool list must contain argus_overview",
        },
        "boundary": (
            "the connector can read allowlisted state and request dry-run plans; execution stays in "
            "the governed ARGUS approval flow"
        ),
    }


@app.get("/api/oversight")
async def oversight(live_sources: bool = False):
    """What this machine can do, what it is safe to run, and what to do next."""
    from argus.core import oversight as O

    return await asyncio.to_thread(O.snapshot, live_sources=live_sources)


@app.get("/api/ingest/plan")
async def ingest_plan():
    """What is actually staged, what it costs on disk, and whether more will fit."""
    import shutil as _sh
    from pathlib import Path as _P

    from argus.cli import hardware as _HW

    FLOOR_BYTES = int(_HW.FLOOR_FREE_DISK_GIB * (1 << 30))

    def _size(p: _P) -> int:
        try:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        except OSError:
            return 0

    def _load():
        out = {"schema": "argus-ingest-plan-v1", "holdings": [], "read_only": True}
        fragment_records = storage_catalog.fragments(
            fallback_root=paths.science_data("fragments"))
        for material in fragment_records:
            d = _P(material["path"]) if material.get("path") else None
            if d is not None and d.is_dir():
                tifs = list(d.rglob("*.tif"))
                has_mask = any(d.rglob("*mask.png"))
                has_ink = any(d.rglob("*inklabels.png"))
                complete = bool(tifs) and has_mask and has_ink
                out["holdings"].append({
                    "kind": "fragment", "id": d.name, "bytes": _size(d),
                    "planes": len(tifs), "complete": complete,
                    "assets": {"mask": has_mask, "inklabels": has_ink},
                    "why_incomplete": (None if complete else
                                       "%d planes, mask=%s, inklabels=%s"
                                       % (len(tifs), has_mask, has_ink)),
                    "storage": material})
            else:
                out["holdings"].append({
                    "kind": "fragment", "id": material["member"],
                    "bytes": material.get("bytes") or 0, "planes": 0, "complete": False,
                    "assets": {"mask": False, "inklabels": False},
                    "why_incomplete": "material is not local; restore through the storage conveyor",
                    "storage": material})
        du = _sh.disk_usage(_argus_public_path('anchor', ''))
        out["storage"] = {
            "free_bytes": du.free, "total_bytes": du.total,
            "floor_bytes": FLOOR_BYTES,
            "headroom_bytes": max(du.free - FLOOR_BYTES, 0),
            "headroom_note": ("any staging plan must fit inside the headroom; a plan that "
                              "does not is refused rather than shrunk"),
        }
        out["staged_bytes_total"] = sum(h["bytes"] for h in out["holdings"])
        return out

    return await asyncio.to_thread(_load)


@app.get("/api/blockers")
async def blockers():
    """What is stopped or blocked. The operator's own blocker receipts are not part of the public release."""
    return {"schema": "argus-blockers-v1", "items": [], "available": False,
            "why": ("blocker receipts are not part of the public release; an empty list here "
                    "is not a claim that nothing is blocked")}


@app.get("/api/findings")
async def findings(scroll: str | None = None, aliases: str | None = None, q: str | None = None,
                   kind: str | None = None, limit: int = 50, offset: int = 0):
    """Findings as the Grail needs them: filtered here, never shipped whole to a panel."""
    from argus.core import findings_query as FQ

    kinds = [k.strip().upper() for k in kind.split(",")] if kind else None
    al = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []

    def _load():
        return FQ.query(scroll=scroll, aliases=al, q=q, kinds=kinds, limit=limit, offset=offset)

    return await asyncio.to_thread(_load)


@app.get("/api/community")
async def community(scroll: str | None = None, aliases: str | None = None,
                    key: str | None = None):
    """Claims reported by others and NOT reproduced here."""
    from argus.core import findings_query as FQ

    al = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []
    return await asyncio.to_thread(lambda: FQ.community(scroll=scroll, aliases=al, key=key))


@app.get("/api/scroll-effort")
async def scroll_effort():
    """Per scroll: what is held on this machine, and how many findings mention it."""
    from argus.core import scroll_effort as SE

    return await _heavy(SE.cached)


@app.get("/api/prize-boards")
async def prize_boards_route(route: str | None = None):
    """The scroll-shaped prize boards, each kept separate: FIRST_LETTERS, GRAND_PRIZE and the official PHerc. Paris 4 title prize (PARIS4_TITLE)."""
    from argus.core import prize_boards as PB

    def _load():
        names = [route] if route else list(PB.REQUIREMENTS)
        return {"contract": PB.CONTRACT, "boards": {n: PB.board(n) for n in names}}

    try:
        return await asyncio.to_thread(_load)
    except PB.BoardRefusal as exc:
        return JSONResponse({"refused": str(exc)}, status_code=400)


@app.get("/api/established")
async def established():
    """What is already settled, with receipts and caveats, so it is not re-asked."""
    from pathlib import Path as _P

    def _load():
        p = _P(__file__).resolve().parents[2] / "artifacts" / "ESTABLISHED_FACTS.json"
        if not p.is_file():
            return {"present": False,
                    "why": "no established-facts record exists in this checkout"}
        d = json.loads(p.read_text(encoding="utf-8"))
        d["present"] = True
        return d

    return await asyncio.to_thread(_load)


@app.get("/api/ingest")
async def ingest(live: bool = False):
    """What ARGUS can actually ingest, and through which vetted actions."""
    from argus.core import jobs as J
    from argus.core import sources as S

    def _load():
        acts = []
        for name, a in sorted(J.ACTIONS.items()):
            acts.append({"name": name, "mutating": bool(getattr(a, "mutating", True)),
                         "describe": getattr(a, "describe", ""),
                         "args": sorted(getattr(a, "args", {}) or {})})
        return {"actions": acts,
                "n_actions": len(acts),
                "n_mutating": sum(1 for a in acts if a["mutating"]),
                "runners": sorted(J.RUNNERS),
                "control_plane": {
                    "read_only_here": True,
                    "mutations_live_in": "argus.service.jobs_api",
                    "enabled_env": "ARGUS_JOBS_API_ENABLED=1",
                    "why_separate": ("this app has no write routes by construction and a test "
                                     "asserts the route table; adding a POST would convert a "
                                     "guarantee into a comment")},
                "implemented": True,
                "previous_ui_claim": "no ingest service exists -- FALSE, corrected"}

    out = await asyncio.to_thread(_load)
    out["sources"] = await asyncio.to_thread(S.inventory, live=live)
    return out


def _resources_probe() -> dict:
    """One accelerator/CPU/disk probe for the whole app."""
    import shutil as _sh
    import subprocess as _sp

    from argus.core import activity as _ACT

    out = {}
    try:
        r = _sp.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True,
                    timeout=20)
        u, t, g = [int(x.strip()) for x in r.stdout.strip().splitlines()[0].split(",")]
        out["gpu"] = {"used_mib": u, "total_mib": t, "util_pct": g,
                      "busy": u > _ACT.GPU_BUSY_MIB or g > _ACT.GPU_BUSY_PCT}
    except Exception:
        out["gpu"] = None
    try:
        import psutil
        out["cpu_pct"] = psutil.cpu_percent(interval=0.1)
        vm = psutil.virtual_memory()
        out["ram"] = {"used_gib": round(vm.used / 2**30, 1),
                      "total_gib": round(vm.total / 2**30, 1), "pct": vm.percent}
    except Exception:
        out["cpu_pct"], out["ram"] = None, None
    out["disks"] = {}
    for root in (_argus_public_path('anchor', ''), _argus_public_path('anchor', '')):
        try:
            d = _sh.disk_usage(root)
            out["disks"][root] = {"free_gib": round(d.free / 2**30, 1),
                                  "total_gib": round(d.total / 2**30, 1)}
        except OSError:
            pass
    return out




_ACTIVITY_MEMO = _SingleFlightTTL(ttl_s=3.0, max_entries=1)
_SURFACES_MEMO = _SingleFlightTTL(ttl_s=3.0, max_entries=1)


@app.get("/api/activity")
async def activity():
    """What is actually happening, across EVERY canonical root."""
    from argus.core import activity as ACT

    def _load() -> dict:
        res = _resources_probe()
        led = ACT.job_ledger()
        out = ACT.derive(resources=res, ledger=led)
        out["feed_coverage"] = {
            "observatory_feed_roots": len(RUN_ROOTS),
            "activity_roots": len(out.get("roots") or []),
            "note": ("the observatory feed scans fewer roots than this derivation does, so a "
                     "count of zero runs in the feed is a statement about the feed and not "
                     "about the machine"),
        }
        return out

    return await _heavy_memo(_ACTIVITY_MEMO, "activity", _load)


@app.get("/api/grail/annotations")
async def grail_annotations():
    """The operator's own notes on diary surfaces."""
    from argus.core import annotations as _A
    try:
        return {"schema": "argus-grail-annotations-v1", "ok": True,
                "summary": _A.summary(), "by_target": _A.grouped(),
                "write_through": "the governed transport, POST /ui/annotations",
                "these_are_not_evidence": "operator notes are unverified human sentences and "
                                          "are never an input to any derivation."}
    except Exception as exc:
        return {"schema": "argus-grail-annotations-v1", "ok": False,
                "why": "%s: %s" % (type(exc).__name__, exc),
                "by_target": {}, "summary": None}


@app.get("/api/grail/attachments/{attachment_id}")
async def grail_attachment(attachment_id: str):
    """One photo/clip from the notebook."""
    from argus.core import annotations as _A
    from fastapi.responses import Response

    got = await asyncio.to_thread(_A.read_attachment, attachment_id)
    if got is None:
        return JSONResponse(status_code=404, content={"error": "no such attachment"})
    content, mime = got
    return Response(content=content, media_type=mime,
                     headers={"Cache-Control": "no-store"})


@app.get("/api/grail")
async def grail():
    """The Grail Diary: every knowledge surface, on one clock, at a glance."""
    import json as _json

    p = paths.repo("corpus", "project_grail.json")
    if not p.is_file():
        return {"schema": "argus-grail-v1", "present": False,
                "why": "no snapshot has been built on this machine",
                "how": "python scripts/project_grail.py --build",
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        doc = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"schema": "argus-grail-v1", "present": False,
                "why": "the snapshot on disk could not be read: %s" % type(exc).__name__,
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    age_s = max(0.0, time.time() - p.stat().st_mtime)
    fails = doc.get("failures") or []
    warns = doc.get("warnings") or []
    findings = doc.get("findings") or {}
    counts = findings.get("counts") or {}

    return {
        "schema": "argus-grail-v1",
        "present": True,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_utc": doc.get("generated_utc"),
        "age_s": round(age_s, 1),
        "snapshot_is_stale": age_s > 86400,
        "overall_state": doc.get("overall_state"),
        "failures": fails,
        "warnings": warns,
        "counts": {
            "failures": len(fails),
            "warnings": len(warns),
            "findings": counts.get("findings"),
            "with_ledger_entry": counts.get("with_ledger_entry"),
        },
        "obstacles": doc.get("obstacle_register"),
        "resource_guard": doc.get("resource_guard"),
        "read_only": True,
        "rebuild_with": "python scripts/project_grail.py --build",
        "why_age_is_reported": "the diary exists because good instruments ran on different "
                               "clocks and nothing said so. A panel that hid its own age would "
                               "reproduce the failure it was built to end.",
    }


@app.get("/api/integrity")
async def integrity():
    """Expose the current two-part ledger truth to every UI consumer."""
    from argus.core import ledger_v2

    def _load():
        try:
            status = dict(ledger_v2.status())
            readable = True
            why = None
        except Exception as exc:
            status, readable, why = None, False, type(exc).__name__
        historical = (status or {}).get("historical", {})
        current = (status or {}).get("current", {})
        current_state = current.get("state")
        compact = {"status": (
            "INTACT" if current_state == ledger_v2.CURRENT_CHAIN_VERIFIED else
            "BROKEN" if current_state in (ledger_v2.CURRENT_CHAIN_BROKEN,
                                           ledger_v2.CURRENT_CHAIN_UNKNOWN) else
            "UNKNOWN"),
            "at": current.get("at"), "why": current.get("why") or current.get("reason"),
            "n": current.get("n"), "head": current.get("head")}
        return {
            "schema": "argus-integrity-v2",
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "chain": compact,
            "historical": historical,
            "current": current,
            "certification": (status or {}).get("certification", {
                "historical_records": "SUPPRESSED",
                "new_records": "SUPPRESSED" if not readable else "UNKNOWN"}),
            "readable": readable,
            "why_unreadable": why,
            "ledger": "the ARGUS command ledger (historical v1 and current v2)",
            "verified_by": "argus.core.ledger_v2.status",
            "means": {
                "INTACT": "the current v2 records link to their predecessor and match their own hashes",
                "BROKEN": "the current chain cannot be verified; current certification is suppressed",
                "HISTORICAL_CHAIN_BROKEN": "the preserved v1 history is broken and is never re-attested",
                "CURRENT_CHAIN_VERIFIED": "records appended to v2 verify; this does not rehabilitate v1",
                "EMPTY": "no command has ever been recorded",
            },
            "consequence_of_broken": (
                "historical v1 certification remains suppressed; current v2 certification is "
                "allowed only when its own status says CURRENT_CHAIN_VERIFIED"),
            "read_only": True,
        }

    return await asyncio.to_thread(_load)


@app.get("/api/monitor")
async def monitor():
    """A compact, phone-sized read of what is happening."""
    import subprocess as _sp

    snap = await snapshot()

    _resources = _resources_probe

    def _git():
        try:
            from argus.core import git_state as _gs
            repo = str(ROOT)
            def g(*a):
                return _gs.git(repo, *a, timeout=30)[1].strip()
            head = g("rev-parse", "--short", "HEAD")
            branch = g("rev-parse", "--abbrev-ref", "HEAD")
            sb = g("status", "-sb").splitlines()[:1]
            porcelain = _gs.status_porcelain(repo) or ""
            dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
            sub = _gs.describe(repo)
            return {"head": head, "branch": branch, "tracking": sb[0] if sb else "",
                    "dirty_paths": dirty,
                    **({"subdir": sub["subdir"], "tree_hash": sub["tree"]}
                       if sub["layout"] == "subdir" else {}),
                    "in_sync": ("ahead" not in (sb[0] if sb else "")
                                and "behind" not in (sb[0] if sb else ""))}
        except Exception:
            return None

    def _campaign():
        try:
            from argus.core import campaign as C
            return C.read()
        except Exception:
            return None

    res = await asyncio.to_thread(_resources)
    git = await asyncio.to_thread(_git)
    camp = await asyncio.to_thread(_campaign)
    runs = snap.get("runs") or []
    active = [r for r in runs if str(r.get("state", "")).upper() in
              ("RUNNING", "ACTIVE", "STARTED")]
    attention = [r for r in runs if str(r.get("state", "")).upper() in
                 ("BLOCKED", "REFUSED", "STALLED", "FAILED")]
    return {
        "schema": "argus-monitor-v1",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "snapshot_age_s": round(time.time() - _snapshot["at"], 2) if _snapshot["at"] else None,
        "snapshot_ttl_s": SNAPSHOT_TTL_S,
        "shared_refresh": True,
        "n_runs": len(runs),
        "active": [{"id": r.get("id"), "state": r.get("state"), "stage": r.get("stage")}
                   for r in active[:5]],
        "attention": [{"id": r.get("id"), "state": r.get("state"),
                       "reason": r.get("reason")} for r in attention[:5]],
        "campaign": camp,
        "resources": res,
        "git": git,
        "read_only": True,
        "sealed_policy": ("counts and states only; no sealed score, map or preview is "
                          "reachable through this route"),
    }


@app.get("/api/checkpoint_lifecycle")
def checkpoint_lifecycle():
    """The checkpoint-lifecycle contract itself -- classes, which may be promoted, and the one-direction rule."""
    from argus.core import checkpoint_lifecycle as CL
    return {
      "contract": CL.CONTRACT,
      "checkpoint_classes": [
        {"id": c, "promotable": c in CL.PROMOTABLE_CLASSES,
         "why": ("has a quarantine + multi-scroll promotion path" if c in CL.PROMOTABLE_CLASSES
                 else "no promotion path from a per-scroll branch to GENERAL -- the directive "
                      "this contract implements names one for the Reasoner only")}
        for c in CL.CHECKPOINT_CLASSES
      ],
      "contamination_risk_kinds": list(CL.CONTAMINATION_RISK_KINDS),
      "physical_evidence_kinds": list(CL.PHYSICAL_EVIDENCE_KINDS),
      "min_scrolls_for_promotion": CL.MIN_SCROLLS_FOR_PROMOTION,
      "quarantine_states": list(CL.QUARANTINE_STATES),
      "invariant": (
        "no scroll-specific learned parameters, optimizer state, normalization statistics, "
        "thresholds, pseudo-labels or unreviewed exemplars may move laterally into another "
        "scroll below Reasoning. Scroll-specific physical evidence may persist only under "
        "that scroll's identity and may flow upward into its own evidence packet."),
      "training_status": "no training has occurred under this contract; it governs future "
                        "checkpoints, none exist yet",
    }


@app.get("/api/capabilities")
async def capabilities():
    """Every discovered upstream capability and how far it actually got."""
    from pathlib import Path as _P

    from argus.core import capabilities as C

    def _load():
        p = _P(__file__).resolve().parents[1] / "capabilities.json"
        if not p.is_file():
            return {"present": False,
                    "why": "no capability ledger has been generated on this machine yet"}
        led = C.load(p)
        s = led.summary()
        s["present"] = True
        s["capabilities"] = [
            {"id": c.capability_id, "stage": c.stage, "state": c.state,
             "entrypoint": c.entrypoint, "upstream_path": c.upstream_path,
             "upstream_commit": c.upstream_commit, "adapter": c.argus_adapter,
             "ui_route": c.ui_route, "regression_test": c.regression_test,
             "blocker": c.blocker}
            for c in sorted(led.caps.values(), key=lambda c: (c.stage, c.capability_id))]
        s["validation_errors"] = led.validate_all()
        return s

    return await asyncio.to_thread(_load)


@app.get("/api/upstream_status")
async def upstream_status():
    """The pinned Villa checkout and the last upstream differential, read-only."""
    from argus.core import upstream_status as U

    return await asyncio.to_thread(U.read)


@app.get("/api/upstream_sources")
async def upstream_sources():
    """The reviewed external-source registry, with staged-only activation semantics."""
    from argus.core import upstream_sources as US
    return await asyncio.to_thread(US.manifest)


@app.get("/api/science_intake")
async def science_intake():
    """Hash-bound science boundaries imported without mounting private arrays or running models."""
    from argus.core import science_intake as SI
    return await asyncio.to_thread(SI.imported_science_register)


_SCIENCE_IMPORT_UNAVAILABLE = ("imported science annotations are not part of the public release; "
                               "nothing is shown rather than something unverified")


def _science_import_unavailable() -> JSONResponse:
    return JSONResponse({"status": "UNAVAILABLE", "reason": _SCIENCE_IMPORT_UNAVAILABLE,
                         "promotion": {"ink": False, "reading": False, "candidate_ready": False,
                                       "prize_authorized": False}}, status_code=200)


@app.get("/api/conquest_status")
async def conquest_status():
    """Renderer status from an imported science intake; unavailable in the public release."""
    return _science_import_unavailable()


@app.get("/api/conquest_flattening")
async def conquest_flattening():
    """Flattening annotations from an imported science intake; unavailable in the public release."""
    return _science_import_unavailable()


@app.get("/api/conquest_task_annotation")
async def conquest_task_annotation(task_sha256: str):
    """The flattening annotation for one task, by its task_sha256; unavailable in the public release."""
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{64}", task_sha256 or ""):
        raise HTTPException(422, "task_sha256 must be a 64-character lowercase hex digest")
    return _science_import_unavailable()


@app.get("/api/platform_contract")
async def platform_contract():
    """Platform-selection rules; actual device choice belongs to a preflight with observed capabilities."""
    from argus.core import platform_capabilities as PC
    return {
        "schema": "argus-platform-contract-v1",
        "backends": ["cuda", "mps", "cpu"],
        "selection": "capability preflight; no silent accelerator-to-CPU fallback",
        "dtype": "float32",
        "mps_3d_requirement": "max_pool3d must be available",
        "mps_lane_status": PC.MPS_LANE_STATUS,
        "mps_provider_execution_validated": False,
        "centered_depth_window": "required for the provider contract",
        "example_depth_window": PC.validate_centered_depth_window(start=1, length=2, total=4),
    }


@app.get("/api/geometry_qa_contract")
async def geometry_qa_contract():
    """The independent geometry-QA vocabulary; no result is fabricated by this route."""
    from argus.core import surface_geometry_qa as QA
    from argus.core import upstream_qa_adapters as UQA
    return {
        "schema": "argus-surface-geometry-qa-v2",
        "native_checks": ["mask_escape", "adjacent_winding_gap", "ridge_profile_support"],
        "native_rule": "ARGUS_NATIVE arithmetic; the caller cites every threshold, there are no built-in defaults",
        "upstream_adapters": {
            sid: {"pin": UQA._source(sid)["pin"], "license_status": UQA._source(sid)["license_status"],
                  "checkout": UQA.resolve_pinned_checkout(sid), "license_gate": UQA.license_gate(sid)}
            for sid in ("labelscope", "vc-segqa")
        },
        "statuses": [QA.PASS, QA.REFUSE, QA.MEASURED, QA.UNAVAILABLE,
                      QA.INSUFFICIENT_OVERLAP, QA.NOT_RUN],
        "provenances": [QA.PROVENANCE_NATIVE, QA.PROVENANCE_UPSTREAM],
        "geometry_only": True,
        "ink_established": False,
    }


@app.get("/api/surfaces")
async def surfaces():
    """The five operational status surfaces: resources, updates, downloads, release, campaign."""
    from argus.core import operations_status as OS

    return await _heavy_memo(_SURFACES_MEMO, "surfaces", OS.all_surfaces)


@app.get("/api/fiber_layers")
async def fiber_layers(scroll: str | None = None):
    """Inventory the retained fibre-model output without presenting it as a reading."""
    from argus.core import fiber_layer as FL

    return await asyncio.to_thread(FL.inventory, scroll)


@app.get("/api/segmentation_status")
async def segmentation_status(scroll: str | None = None):
    """Inventory official, identity-bound segmentation seals without launching inference."""
    from argus.core import segmentation_adapter as SA

    return await asyncio.to_thread(SA.official_inventory, scroll)


@app.get("/api/fiber_layer")
async def fiber_layer(scroll: str | None = None, patch: int = 12):
    """Serve one bounded, identity-matched fibre-output preview as a PNG."""
    from argus.core import fiber_layer as FL
    from argus.core.fiber_layer import FiberLayerRefusal

    try:
        png, meta = await asyncio.to_thread(FL.preview, scroll, patch)
    except FiberLayerRefusal as exc:
        raise HTTPException(409, str(exc)) from exc
    from fastapi.responses import Response
    return Response(content=png, media_type="image/png", headers={
        "X-Argus-Scroll": meta["physical_scroll"],
        "X-Argus-Fibre-Patch": str(meta["patch"]),
        "X-Argus-Fibre-Contract": meta["schema"],
        "X-Argus-Fibre-Semantics": str(meta["semantic_state"]),
        "X-Argus-Not-Ink": "true",
        "Cache-Control": "no-store",
    })


@app.get("/api/providers/hecate/layer_inventory")
async def hecate_layer_inventory(scroll: str | None = None):
    """Inventory registered Hecate run receipts without presenting one as a reading."""
    from argus.core import hecate_layer as HL

    return await asyncio.to_thread(HL.inventory, scroll)


@app.get("/api/providers/hecate/layer")
async def hecate_layer(scroll: str, acquisition_id: str):
    """Serve one identity-matched Hecate run's already-produced output references."""
    from argus.core import hecate_layer as HL

    try:
        return await asyncio.to_thread(HL.preview, scroll, acquisition_id)
    except HL.HecateLayerRefusal as exc:
        return {"schema": HL.SCHEMA, "state": "REFUSED", "read_only": True, "why": str(exc)}


@app.get("/api/providers/hecate/layer_3d_slice")
async def hecate_layer_3d_slice(scroll: str, acquisition_id: str, orientation: str = "primary",
                                z: int = 0):
    """Serve one z-slice of a declared Hecate 3-D output as a grayscale PNG."""
    from argus.core import hecate_layer as HL

    try:
        png, meta = await asyncio.to_thread(
            HL.render_3d_slice, scroll, acquisition_id, orientation, z)
    except HL.HecateLayerRefusal as exc:
        raise HTTPException(409, str(exc)) from exc
    from fastapi.responses import Response
    return Response(content=png, media_type="image/png", headers={
        "X-Argus-Scroll": meta["physical_scroll"],
        "X-Argus-Acquisition": meta["acquisition_id"],
        "X-Argus-Orientation": meta["orientation"],
        "X-Argus-Z": str(meta["z"]),
        "X-Argus-Depth": str(meta["depth"]),
        "X-Argus-Not-Ink": "true",
        "Cache-Control": "no-store",
    })


@app.get("/api/advisor")
async def advisor():
    """Measured conditions joined to what the project's own record says about each."""
    from argus.core import advisor as A

    return await asyncio.to_thread(A.advise)


@app.get("/api/planes")
async def planes_index(scroll: str | None = None):
    """Which fragments can be looked at, and how deep they go."""
    from argus.core import planes as P

    def _registered_scroll(fragment: str) -> str | None:
        """Read a fragment's explicit local identity; never infer one from its name."""
        return _local_asset_provenance(
            P.FRAGS / fragment,
            ("RENDERING_IDENTITY.json", "STORE_IDENTITY.json"),
            "staged_surface_volume",
        )["identity"]

    def _dims(name: str) -> dict:
        import tifffile

        files = sorted((P.FRAGS / name / "surface_volume").glob("*.tif"))
        if not files:
            return {"h": 0, "w": 0}
        arr = tifffile.memmap(str(files[0]), mode="r")
        h, w = arr.shape[-2:]
        del arr
        return {"h": int(h), "w": int(w)}

    def _load():
        names = P.staged()
        identities = {n: _registered_scroll(n) for n in names}
        matching = [n for n in names if scroll and identities[n] == scroll]
        visible = matching if scroll else names
        provenance = {
            n: _local_asset_provenance(
                P.FRAGS / n,
                ("RENDERING_IDENTITY.json", "STORE_IDENTITY.json"),
                "staged_surface_volume",
            )
            for n in names
        }
        return {"selected_scroll": scroll,
                "staged": visible,
                "selected_fragment": visible[0] if visible else None,
                "assets": [{"fragment": n, "scroll": identities[n],
                            "source_path": str(P.FRAGS / n),
                            "status": "available" if identities[n] else "unverified",
                            "provenance": provenance[n]}
                           for n in visible],
                "unmatched": ([{"fragment": n, "scroll": identities[n],
                                 "status": ("unverified" if identities[n] is None else "mismatched"),
                                 "why": ("no explicit local scroll identity is registered"
                                         if identities[n] is None else
                                         "explicit local identity does not match the selected scroll"),
                                 "provenance": provenance[n]}
                               for n in names if n not in matching]
                               if scroll else []),
                "planes": {n: P.plane_count(n) for n in visible},
                "dims": {n: _dims(n) for n in visible},
                "contrast_default": "fixed clip window (argus.core.planes.CLIP_MAX)",
                "not_reachable": ["any sealed run root"],
                "why": "only the staged fragment directory is addressable"}

    return await asyncio.to_thread(_load)


@app.get("/api/plane")
async def plane(fragment: str, plane: int = 32, y: int = 0, x: int = 0,
                h: int = 512, w: int = 512, auto: bool = False, reverse: bool = False,
                zoom: int = 1, scroll: str | None = None):
    """One fixed-contrast crop as a PNG, with its parameters in the headers."""
    from argus.core import planes as P
    from argus.core.planes import PlaneRefusal, crop

    if not _SN.is_safe_name(fragment):
        return JSONResponse(status_code=400, content={"error": "refused", "why": "a fragment is a staged directory name, not a path"})
    if scroll:
        identity = None
        for name in ("RENDERING_IDENTITY.json", "STORE_IDENTITY.json"):
            record = P.FRAGS / fragment / name
            if record.is_file():
                try:
                    body = json.loads(record.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    body = {}
                identity = str(body.get("physical_scroll") or body.get("scroll_id") or "").strip() or None
                break
        if identity != scroll:
            raise HTTPException(
                409,
                "refusing to render %s for %s: the fragment has no matching explicit local "
                "scroll identity" % (fragment, scroll),
            )

    def _load():
        return crop(fragment, plane, y, x, h, w, auto=auto, reverse=reverse,
                    zoom=zoom)

    try:
        out = await asyncio.to_thread(_load)
    except PlaneRefusal as exc:
        return JSONResponse(status_code=400, content={"error": "refused", "why": str(exc)})
    from fastapi.responses import Response
    return Response(
        content=out["png"], media_type="image/png",
        headers={"X-Argus-Fragment": out["fragment"],
                 "X-Argus-Plane": "%d of %d" % (out["plane"], out["n_planes"]),
                 "X-Argus-Depth-Order": out["depth_order"],
                 "X-Argus-Window": "y%d x%d h%d w%d" % (out["y"], out["x"], out["h"],
                                                        out["w"]),
                 "X-Argus-Zoom": str(out.get("zoom", 1)),
                 "X-Argus-Field-Of-View": out.get("field_of_view", ""),
                 "X-Argus-Field-Available": str(out.get("field_available", True)),
                 "X-Argus-Contrast": out["contrast"]["mode"],
                 "X-Argus-Comparable": str(out["contrast"]["comparable"]),
                 "X-Argus-Reproduce": out["reproduce"],
                 "Cache-Control": "no-store"})


VOLUME_SERVE_ROOTS = [paths.science_data()]

from argus.core.zarr_volume import ZarrVolumeRefusal as ZV_REFUSAL


def _resolve_volume_store(store: str) -> Path:
    p = paths.resolve_served_path(store, VOLUME_SERVE_ROOTS)
    if not any(paths.within_root(p, r) for r in VOLUME_SERVE_ROOTS):
        raise HTTPException(403, "outside the declared roots")
    from argus.core import mask_registry as MR
    if MR.is_mask_store(p):
        try:
            MR.verify(p)
        except MR.MaskRefused as exc:
            raise HTTPException(409, "refusing to serve this mask store (%s): %s" % (exc.code, exc.why)) from exc
    ident = MR.identity_of(p)
    if str(ident.get("asset_type") or "").strip().lower() == "prediction_overlay":
        from argus.core import prediction_overlay_contract as POC
        bad = POC.problems(ident.get("prediction"))
        if bad:
            raise HTTPException(409, "refusing to serve this prediction overlay (%s): %s" % (bad[0]["code"], bad[0]["why"]))
    return p


def _registered_store_scroll(store: Path) -> str | None:
    """Return only an identity asserted by the store's own metadata."""
    record = store / "STORE_IDENTITY.json"
    if not record.is_file():
        return None
    try:
        body = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return str(body.get("physical_scroll") or body.get("scroll_id") or "").strip() or None


def _registered_store_volume(store: Path) -> str | None:
    """The exact volume id the store's own identity record declares, if any."""
    record = store / "STORE_IDENTITY.json"
    try:
        body = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return str(body.get("volume_id") or "").strip() or None


def _identity_roi(store: Path) -> dict | None:
    """The region the store's own identity says it holds, with its centre, for viewers of a sparse store (its level middle can be a hole)."""
    try:
        body = json.loads((store / "STORE_IDENTITY.json").read_text(encoding="utf-8"))
        roi = body.get("roi")
        v = {k: int(roi[k]) for k in ("z0", "z1", "y0", "y1", "x0", "x1")}
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if v["z1"] <= v["z0"] or v["y1"] <= v["y0"] or v["x1"] <= v["x0"]:
        return None
    return {"array_path": str(body.get("array_path") or "0"), "roi": v,
            "centre_zyx": [(v["z0"] + v["z1"]) // 2, (v["y0"] + v["y1"]) // 2,
                           (v["x0"] + v["x1"]) // 2]}


def _looks_like_raw_ct_store(store: Path, metadata: dict) -> bool:
    """Keep label and derived surface stores out of the raw-CT inventory."""
    identity = store / "STORE_IDENTITY.json"
    if identity.is_file():
        try:
            body = json.loads(identity.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        declared = str(body.get("asset_type") or body.get("kind") or "").strip().lower()
        if declared:
            return declared in {"raw_ct", "raw_ct_ome_zarr", "ome_zarr_raw_ct", "volume"}
        return True
    lowered = str(store).replace("\\", "/").lower()
    derived_markers = ("inklabels", "supervision_mask", "validation_mask", "surface_volume",
                       "/labels/", "/masks/")
    return not any(marker in lowered for marker in derived_markers)


def _local_asset_provenance(path: Path, names: tuple[str, ...], asset_type: str) -> dict:
    """Return the small, non-secret provenance record for a locally served asset."""
    for name in names:
        record = path / name
        if not record.is_file():
            continue
        try:
            body = json.loads(record.read_text(encoding="utf-8"))
            raw = record.read_bytes()
        except (OSError, ValueError):
            return {
                "identity": None, "source": "local", "type": asset_type,
                "roi": None, "path": str(path), "receipt": None, "hash": None,
                "orientation": None, "status": "unverified",
                "identity_record": str(record), "why": "identity sidecar is unreadable",
            }
        if not isinstance(body, dict):
            return {
                "identity": None, "source": "local", "type": asset_type,
                "roi": None, "path": str(path), "receipt": None, "hash": None,
                "orientation": None, "status": "unverified",
                "identity_record": str(record), "why": "identity sidecar is not an object",
            }
        identity = str(body.get("physical_scroll") or body.get("scroll_id") or "").strip() or None
        return {
            "identity": identity,
            "source": "local",
            "type": asset_type,
            "roi": body.get("roi") or body.get("region_of_interest"),
            "path": str(path),
            "receipt": body.get("receipt") or body.get("receipt_path") or body.get("receipt_id"),
            "hash": body.get("sha256") or body.get("render_sha256") or body.get("output_sha256"),
            "orientation": body.get("orientation") or body.get("axis_order") or body.get("axes"),
            "status": "available" if identity else "unverified",
            "identity_record": str(record),
            "identity_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {
        "identity": None, "source": "local", "type": asset_type,
        "roi": None, "path": str(path), "receipt": None, "hash": None,
        "orientation": None, "status": "unverified",
        "identity_record": None, "why": "no explicit local identity is registered",
    }


@app.get("/api/volumes")
async def volumes_index(scroll: str | None = None):
    """Which real OME-Zarr stores are reachable under the declared volume roots, discovered by walking for a real `.zattrs` declaring `multiscales` -- the same \"ask the service, don't hardcode a path\"..."""
    from argus.core.zarr_volume import probe_store

    def _load():
        all_stores = []
        for root in VOLUME_SERVE_ROOTS:
            if not root.is_dir():
                continue
            for zattrs in root.rglob(".zattrs"):
                try:
                    d = json.loads(zattrs.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if "multiscales" not in d:
                    continue
                store = zattrs.parent
                if not _looks_like_raw_ct_store(store, d):
                    continue
                try:
                    info = probe_store(store)
                except Exception:
                    continue
                identity = _registered_store_scroll(store)
                all_stores.append({"store": str(store), "scroll": identity, "volume_id": _registered_store_volume(store),
                           "openable_levels": [l["level"] for l in info["levels"] if l["openable"]],
                           "declared_levels": [l["level"] for l in info["levels"]],
                           "status": "available" if identity else "unverified",
                           "provenance": _local_asset_provenance(
                               store, ("STORE_IDENTITY.json",), "raw_ct_ome_zarr"
                           )})
        out = [row for row in all_stores if not scroll or row["scroll"] == scroll]
        unmatched = []
        if scroll:
            unmatched = [
                {"store": row["store"], "scroll": row["scroll"],
                 "status": "unverified" if row["scroll"] is None else "mismatched",
                 "why": ("no explicit local scroll identity is registered"
                         if row["scroll"] is None else
                         "explicit local identity does not match the selected scroll"),
                 "provenance": row["provenance"]}
                for row in all_stores if row not in out
            ]
        return {"selected_scroll": scroll,
                "stores": out,
                "unmatched": unmatched,
                "selected_store": out[0]["store"] if len(out) == 1 else None}

    return await asyncio.to_thread(_load)


@app.get("/api/volume_meta")
async def volume_meta(store: str, scroll: str | None = None):
    """Every pyramid level an OME-Zarr store declares, and whether each is actually openable."""
    from argus.core.zarr_volume import ZarrVolumeRefusal, probe_store
    p = await asyncio.to_thread(_resolve_volume_store, store)
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")
    try:
        probe = await asyncio.to_thread(probe_store, p)
    except ZarrVolumeRefusal as exc:
        raise HTTPException(422, exc.reason)
    probe["identity_roi"] = _identity_roi(p)
    if scroll:
        from argus.core import scroll_dataset_metadata as SDM
        try:
            probe["scroll_identity_check"] = SDM.identity_preflight_against_probe(scroll, probe)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return probe


@app.get("/api/volume_plane")
async def volume_plane(store: str, level: str, plane: str, index: int, u: int = 0, v: int = 0,
                       h: int = 512, w: int = 512, allow_partial: bool = False,
                       scroll: str | None = None):
    """One fixed-contrast PNG crop of a real pyramid level -- same CLIP_MAX convention `/api/plane` already uses, so two CT pictures from different ARGUS viewers stay comparable."""
    from argus.core.planes import CLIP_MAX, MAX_PIXELS, MAX_SIDE
    from argus.core.zarr_volume import ZarrVolumeRefusal, read_plane
    if h < 1 or w < 1 or h > MAX_SIDE or w > MAX_SIDE or h * w > MAX_PIXELS:
        return JSONResponse(status_code=400, content={
            "error": "refused",
            "why": "volume crop must be positive and at most %d by %d pixels (%d total)"
                   % (MAX_SIDE, MAX_SIDE, MAX_PIXELS),
        })
    p = await asyncio.to_thread(_resolve_volume_store, store)
    if scroll and _registered_store_scroll(p) != scroll:
        raise HTTPException(
            409,
            "refusing to render this volume for %s: its local STORE_IDENTITY does not match"
            % scroll,
        )
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")

    def _load():
        return read_plane(p, level, plane, index, u, v, h, w, allow_partial=allow_partial)

    try:
        out = await asyncio.to_thread(_load)
    except ZarrVolumeRefusal as exc:
        return JSONResponse(status_code=400,
                            content={"error": "refused", "why": exc.reason, "evidence": exc.evidence})

    import io

    import numpy as np
    from PIL import Image
    shown = np.clip(out["data"].astype(np.float32), 0, CLIP_MAX) * (255.0 / CLIP_MAX)
    buf = io.BytesIO()
    Image.fromarray(shown.astype(np.uint8), mode="L").save(buf, format="PNG")
    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(), media_type="image/png",
        headers={"X-Argus-Store": store, "X-Argus-Level": level, "X-Argus-Plane": plane,
                 "X-Argus-Index": str(index), "X-Argus-Window": "u%d v%d h%d w%d" % (u, v, h, w),
                 "X-Argus-Scale": str(out["scale"]),
                 "X-Argus-Missing-Chunks": str(len(out["missing_chunks"])),
                 "X-Argus-Contrast": "fixed", "X-Argus-Comparable": "True",
                 "Cache-Control": "no-store"})


def _triple(text: str | None, name: str):
    if text is None or text == "":
        return None
    try:
        v = [int(x) for x in text.split(",")]
    except ValueError:
        raise HTTPException(422, "%s must be three comma-separated integers (z,y,x)" % name)
    if len(v) != 3:
        raise HTTPException(422, "%s must be three comma-separated integers (z,y,x)" % name)
    return v


def _brick_store(store: str, scroll: str | None, volume: str | None) -> Path:
    """The identity gate for a volume brick, stricter than the plane viewer's: the selected physical scroll is REQUIRED, the store must assert that same scroll itself, and a named volume must match the..."""
    if not scroll or not scroll.strip():
        raise HTTPException(422, "the selected physical scroll is required: a volume brick is never served for an unnamed scroll")
    p = _resolve_volume_store(store)
    record = p / "STORE_IDENTITY.json"
    body = {}
    if record.is_file():
        try:
            body = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            body = {}
    registered = str(body.get("physical_scroll") or body.get("scroll_id") or "").strip() or None
    if registered is None:
        raise HTTPException(409, "this store asserts no physical-scroll identity, so it is not shown for %s" % scroll)
    if registered != scroll:
        raise HTTPException(409, "refusing to serve this volume for %s: the store's own identity is %s" % (scroll, registered))
    if volume:
        declared = str(body.get("volume_id") or "").strip() or None
        if declared != volume:
            raise HTTPException(409, "refusing: the requested volume %s does not match the store's declared volume %s"
                                % (volume, declared))
    if F.sealed_by(p):
        raise HTTPException(423, "under an active blinded experiment")
    return p


def _brick_args(centre, origin, edge, shape, budget_mib, max_texture, vram_mib):
    return {"centre": _triple(centre, "centre"), "origin": _triple(origin, "origin"),
            "edge_or_shape": (_triple(shape, "shape") or int(edge)),
            "budget_bytes": int(budget_mib) * (1 << 20), "max_texture": (int(max_texture) if max_texture else None),
            "vram_budget_bytes": (int(vram_mib) * (1 << 20) if vram_mib else None)}


@app.get("/api/volume_brick_plan")
async def volume_brick_plan(store: str, level: str, scroll: str | None = None, volume: str | None = None,
                            centre: str | None = None, origin: str | None = None, edge: int = 256,
                            shape: str | None = None, budget_mib: int = 64, max_texture: int | None = None,
                            vram_mib: int | None = None):
    """What a brick would cost, decided before a byte is read: bytes, estimated GPU memory, every limit, the exact ROI and the store's declared spacing."""
    from argus.core import volume_brick as VB
    p = await asyncio.to_thread(_brick_store, store, scroll, volume)
    kw = _brick_args(centre, origin, edge, shape, budget_mib, max_texture, vram_mib)
    try:
        out = await asyncio.to_thread(lambda: VB.plan(p, level, **kw))
    except VB.BrickRefused as exc:
        return JSONResponse(status_code=400, content={"error": "refused", "code": exc.code, "why": exc.why, "evidence": exc.evidence})
    except ZV_REFUSAL as exc:
        return JSONResponse(status_code=400, content={"error": "refused", "code": "DATA_UNAVAILABLE", "why": exc.reason, "evidence": exc.evidence})
    out["scroll"] = scroll
    out["volume"] = volume
    return out


@app.get("/api/volume_masks")
async def volume_masks(scroll: str, volume: str, level: str, artifact_type: str):
    """Registered masks of ONE type that may be applied to a brick of this scroll, volume and level -- and the ones of that type that may not, with why."""
    from argus.core import mask_registry as MR
    if artifact_type not in MR.MASK_TYPES:
        raise HTTPException(422, "artifact_type must be one of %s" % sorted(MR.MASK_TYPES))

    def _scan():
        try:
            registry = MR.load_registry()
        except MR.MaskRefused as exc:
            return {"scroll": scroll, "volume": volume, "level": level, "artifact_type": artifact_type, "masks": [], "refused": [],
                    "registry_error": {"code": exc.code, "why": exc.why}}
        offered, refused = [], []
        seen: set = set()
        for e in registry:
            if e.get("artifact_type") != artifact_type:
                continue
            store = Path(str(e.get("store", "")))
            present = store.exists()
            seen.add(str(store.resolve()) if present else str(store))
            row = {"store": str(store), "mask_id": e.get("mask_id"), "artifact_type": artifact_type, "label": MR.MASK_TYPES[artifact_type],
                   "producer": e.get("producer"), "source_volume": e.get("volume_id"), "physical_scroll": e.get("physical_scroll"),
                   "source_digest": e.get("source_digest"), "content_sha256": e.get("content_sha256"), "merkle_root": e.get("merkle_root"), "level": level}
            why = None
            if not present or not any(paths.within_root(store.resolve(), r) for r in VOLUME_SERVE_ROOTS):
                why = "the registered store is not present under a declared volume root"
            elif F.sealed_by(store):
                why = "under an active blinded experiment"
            elif str(e.get("physical_scroll") or "").strip() != scroll:
                why = "the mask is registered for %s, not %s" % (e.get("physical_scroll"), scroll)
            elif str(e.get("volume_id") or "").strip() != volume:
                why = "the mask was registered against volume %s, not %s" % (e.get("volume_id"), volume)
            elif str(e.get("level")) != level:
                why = "the mask is registered at level %s, not %s" % (e.get("level"), level)
            else:
                try:
                    MR.verify(store, registry=registry)
                except MR.MaskRefused as exc:
                    why = "%s: %s" % (exc.code, exc.why)
            (refused if why else offered).append({**row, **({"why": why} if why else {})})
        for root in VOLUME_SERVE_ROOTS:
            if not root.is_dir():
                continue
            for record in root.rglob("STORE_IDENTITY.json"):
                store = record.parent
                ident = MR.identity_of(store)
                if str(ident.get("asset_type") or "").strip().lower() != artifact_type or str(store.resolve()) in seen:
                    continue
                refused.append({"store": str(store), "artifact_type": artifact_type, "label": MR.MASK_TYPES[artifact_type], "level": level,
                                "why": "UNREGISTERED: this mask store is not in the server's registered mask manifest"})
        return {"scroll": scroll, "volume": volume, "level": level, "artifact_type": artifact_type, "masks": offered, "refused": refused}

    return await asyncio.to_thread(_scan)


@app.get("/api/prediction_overlays")
async def prediction_overlays(scroll: str, volume: str, level: str):
    """Model outputs registered for this scroll, volume and level, each carrying the mandatory MODEL OUTPUT contract; incomplete ones are refused."""
    from argus.core import prediction_overlay_contract as POC

    def _scan():
        offered, refused = [], []
        for root in VOLUME_SERVE_ROOTS:
            if not root.is_dir():
                continue
            for record in root.rglob("STORE_IDENTITY.json"):
                try:
                    body = json.loads(record.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if str(body.get("asset_type") or "").strip().lower() != "prediction_overlay":
                    continue
                store = record.parent
                row = {"store": str(store), "physical_scroll": body.get("physical_scroll"), "source_volume": body.get("volume_id"), "level": level}
                why = None
                if F.sealed_by(store):
                    why = "under an active blinded experiment"
                elif str(body.get("physical_scroll") or "").strip() != scroll:
                    why = "the overlay is registered for %s, not %s" % (body.get("physical_scroll"), scroll)
                elif str(body.get("volume_id") or "").strip() != volume:
                    why = "the overlay was made against volume %s, not %s" % (body.get("volume_id"), volume)
                else:
                    bad = POC.problems(body.get("prediction"))
                    if bad:
                        why = "; ".join("%s: %s" % (b["code"], b["why"]) for b in bad)
                if why:
                    refused.append({**row, "why": why})
                else:
                    offered.append({**row, **POC.describe(body["prediction"]), "drawn_by_this_viewer": False})
        return {"scroll": scroll, "volume": volume, "level": level, "overlays": offered, "refused": refused}

    return await asyncio.to_thread(_scan)


@app.get("/api/volume_brick")
async def volume_brick(store: str, level: str, scroll: str | None = None, volume: str | None = None,
                       centre: str | None = None, origin: str | None = None, edge: int = 256,
                       shape: str | None = None, budget_mib: int = 64, max_texture: int | None = None,
                       vram_mib: int | None = None, allow_partial: bool = False):
    """One bounded brick of raw CT as contiguous uint8 (z slowest), metadata in the X-Argus-Brick-Meta header."""
    from fastapi.responses import Response

    from argus.core import volume_brick as VB
    p = await asyncio.to_thread(_brick_store, store, scroll, volume)
    kw = _brick_args(centre, origin, edge, shape, budget_mib, max_texture, vram_mib)
    try:
        body, meta = await _BRICK_POOL.run(lambda: VB.read(p, level, allow_partial=allow_partial, **kw))
    except VB.BrickRefused as exc:
        return JSONResponse(status_code=400, content={"error": "refused", "code": exc.code, "why": exc.why, "evidence": exc.evidence})
    except ZV_REFUSAL as exc:
        return JSONResponse(status_code=400, content={"error": "refused", "code": "DATA_UNAVAILABLE", "why": exc.reason, "evidence": exc.evidence})
    meta["scroll"] = scroll
    meta["volume"] = volume
    return Response(content=body, media_type="application/octet-stream",
                    headers={"X-Argus-Brick-Meta": json.dumps(meta, separators=(",", ":")),
                             "X-Argus-Contrast": "fixed", "Cache-Control": "no-store"})


_TASK_REFUSAL_STATUS = {"UNKNOWN_QUEUE": 404, "NO_SUCH_TASK": 404, "TASKS_NOT_IMPORTED": 409, "SEAL_MISMATCH": 409, "COORDINATE_UNRESOLVED": 422}


def _task_refusal(exc) -> "JSONResponse":
    status = _TASK_REFUSAL_STATUS.get(exc.code, 409)
    return JSONResponse(status_code=status, content={"error": "refused", "code": exc.code, "why": exc.why})


@app.get("/api/workbench_tasks")
async def workbench_tasks(scroll: str | None = None):
    """Sealed review tasks that can be opened in the Workbench: ids and whether their location resolves."""
    from argus.core import workbench_task_binding as TB
    try:
        return {"tasks": await asyncio.to_thread(lambda: TB.list_tasks(scroll))}
    except TB.BindingRefused as exc:
        return _task_refusal(exc)


@app.get("/api/workbench_task/{kind}/{task_id}")
async def workbench_task(kind: str, task_id: str):
    """Where one sealed task lives in the CT (store/level/box), its geometry status and which layers may be drawn with it."""
    from argus.core import workbench_task_binding as TB
    try:
        return await asyncio.to_thread(lambda: TB.bind(kind, task_id))
    except TB.BindingRefused as exc:
        return _task_refusal(exc)


@app.get("/api/workbench_task/{kind}/{task_id}/mesh")
async def workbench_task_mesh(kind: str, task_id: str):
    """The task's own hashed mesh clipped to its ROI."""
    from argus.core import workbench_task_binding as TB
    try:
        return await asyncio.to_thread(lambda: TB.mesh_in_roi(kind, task_id))
    except TB.BindingRefused as exc:
        return _task_refusal(exc)


_RUNTIME_MEMO = _SingleFlightTTL(ttl_s=3.0, max_entries=1)


@app.get("/api/runtime")
async def runtime():
    """Runtime, locks, services and content-store health -- measured, never declared."""
    def _load():
        import hashlib
        import platform
        import shutil
        import socket
        import sys

        lock = ROOT / "runtime" / "requirements.lock.txt"
        freeze = ROOT / "runtime" / "RUNTIME_FREEZE.json"
        want = {}
        if freeze.is_file():
            try:
                want = json.loads(freeze.read_text(encoding="utf-8"))
            except ValueError:
                want = {}
        lock_text = lock.read_text(encoding="utf-8") if lock.is_file() else ""
        lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
        declared = (want.get("lockfile") or {}).get("sha256")
        from argus.core import runtime_identity
        installed = runtime_identity.audit(ROOT) if lock.is_file() and freeze.is_file() else {
            "matches": False, "missing": [], "mismatched": []
        }

        torch_v = cuda_v = None
        torch_error = None
        torch_optional = bool((want.get("torch") or {}).get("optional"))
        try:
            import torch
            torch_v, cuda_v = torch.__version__, torch.version.cuda
        except ModuleNotFoundError as exc:
            torch_error = None if torch_optional else f"{type(exc).__name__}: {exc}"[:500]
        except Exception as exc:
            torch_error = f"{type(exc).__name__}: {exc}"[:500]

        from argus.core import git_state as _gs
        provenance = _gs.source_provenance(ROOT)
        _layout = _gs.describe(ROOT)

        def answers(port: int) -> bool:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return True
            except OSError:
                return False

        service_ports = {
            "observe": int(os.environ.get("ARGUS_OBSERVE_PORT", "8787")),
            "command": int(os.environ.get("ARGUS_COMMAND_PORT", "8788")),
            "transport": int(os.environ.get("ARGUS_UI_TRANSPORT_PORT", "8789")),
            "ui": int(os.environ.get("ARGUS_UI_PORT", "5173")),
        }
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(service_ports)) as pool:
            services = dict(zip(service_ports, pool.map(answers, service_ports.values())))

        stores = {}
        fragment_rows = storage_catalog.fragments(
            fallback_root=paths.science_data("fragments"))
        for name, path in (("runs", paths.runs()),
                           ("fragments", paths.science_data("fragments")),
                           ("state", _argus_public_path('home', 'state')),
                           ("holdings", _argus_public_path('home', 'state/holdings.json'))):
            p_ = Path(path)
            stores[name] = {"present": p_.exists(),
                            "entries": (len(list(p_.iterdir())) if p_.is_dir() else None)}
        stores["fragments"].update({
            "catalogued": len(fragment_rows),
            "local": sum(1 for row in fragment_rows if row.get("present")),
            "restorable": sum(1 for row in fragment_rows
                              if not row.get("present") and row.get("restorable")),
        })

        disks = {}
        for d in (_argus_public_path('anchor', ''), _argus_public_path('anchor', '')):
            try:
                t, u, f = shutil.disk_usage(d)
                disks[d] = {"free_gib": round(f / 2 ** 30, 1),
                            "total_gib": round(t / 2 ** 30, 1)}
            except OSError:
                disks[d] = {"error": "unreadable"}

        return {
            "schema": "argus-runtime-v1",
            "service_identity": {
                "api_contract": "argus-ui-contract-20260921-v1",
                "source_root": str(ROOT),
                "build_sha": os.environ.get("ARGUS_BUILD_SHA") or "unknown",
                "source_revision": provenance.get("source_revision")
                                   or os.environ.get("ARGUS_BUILD_SHA") or "unknown",
                "source_provenance": ("SOURCE_PROVENANCE.json" if provenance
                                      else "this tree is the source checkout"),
                "truth_package": os.environ.get("ARGUS_TRUTH_PACKAGE", "runtime-state"),
                "artifact_roots": [str(p) for p in ARTIFACT_ROOTS],
                **({"source_subdir": _layout["subdir"], "source_subtree_hash": _layout["tree"]}
                   if _layout["layout"] == "subdir" else {}),
            },
            "interpreter": {"running": sys.version.split()[0],
                            "declared": (want.get("python") or {}).get("version"),
                            "matches": sys.version.split()[0] ==
                                       (want.get("python") or {}).get("version")},
            "platform": platform.platform(),
            "torch": {"running": torch_v, "declared": (want.get("torch") or {}).get("version"),
                      "cuda_running": cuda_v,
                      "cuda_declared": (want.get("torch") or {}).get("cuda"),
                      "matches": torch_v == (want.get("torch") or {}).get("version"),
                      "optional": torch_optional,
                      "error": torch_error},
            "lock": {"path": str(lock), "present": bool(lock_text),
                     "sha256": lock_sha, "declared_sha256": declared,
                     "matches": bool(lock_sha and declared and lock_sha == declared),
                     "installed_matches": installed["matches"],
                     "missing": installed["missing"],
                     "mismatched": installed["mismatched"],
                     "why_it_matters": ("a venv that happens to run is a weaker thing than "
                                        "the venv the numbers came from")},
            "services": services,
            "service_ports": service_ports,
            "content_stores": stores,
            "disks": disks,
            "credentials": {"served_here": False,
                            "where": "the command service, behind a token"},
        }

    return await _heavy_memo(_RUNTIME_MEMO, "runtime", _load)


@app.get("/api/gates")
async def gates():
    """VIGILES: which gate is shut, why, and exactly what would satisfy it."""
    from argus.core import gates as G

    return await asyncio.to_thread(G.evaluate)


@app.get("/api/vigiles/final")
async def vigiles_final():
    """Read the named final-disposition wrapper; absence is PENDING, never YES or NO."""
    from argus.core import actions as A
    from argus.core import belisarius_gate as BG
    return await asyncio.to_thread(BG.current, state_root=A.STATE)


@app.get("/api/chain")
async def chain():
    """The science chain status, read only, with the things a status page must not do."""
    from argus.core import chain_status as CS

    def _load():
        logs = {}
        reg = Path(__file__).resolve().parent / "worker_logs.json"
        if reg.is_file():
            try:
                logs = json.loads(reg.read_text(encoding="utf-8"))
            except ValueError:
                logs = {}
        out = CS.chain(logs)
        public = ("schema", "utc", "state", "why", "arms_expected", "roots", "history", "history_means",
                  "live_workers", "single_gpu_owner", "single_gpu_owner_means", "arms_live",
                  "current_arm", "current_stage", "guarantees", "links")
        for key in [k for k in out if k not in public]:
            out[key] = ({"state": "NOT_IN_PUBLIC_RELEASE", "staged": False, "preview": None}
                        if isinstance(out[key], dict) else None)
        return out

    return await asyncio.to_thread(_load)


@app.get("/api/route_gates")
async def route_gates():
    """The route rules and the continuity of the latest miniature-route receipt."""
    from argus.core import route_wiring as RW

    def _load():
        out = {k: v for k, v in RW.describe().items()
               if k in ("contract", "items", "delegates_to", "claim_ceiling", "permitted_means")}
        root = paths.artifact_write_root() / "miniature_route"
        runs = sorted(root.glob("run_*/MINIATURE_ROUTE.json")) if root.is_dir() else []
        legacy = root / "MINIATURE_ROUTE.json"
        latest = runs[-1] if runs else (legacy if legacy.is_file() else None)
        if latest is None:
            out["latest_route"] = {"state": "UNKNOWN", "why": "no miniature-route receipt"}
            return out
        try:
            doc = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out["latest_route"] = {"state": "UNKNOWN", "path": str(latest),
                                   "why": "unreadable: %s" % exc}
            return out
        schema = doc.get("schema")
        continuity = RW.continuity_from_route_receipt(doc)
        out["latest_route"] = {
            "path": str(latest),
            "schema": schema,
            "state": "STALE_SCHEMA" if schema != "argus-miniature-route-v2" else (
                "CONTINUOUS" if continuity.get("continuous") else "BROKEN"
            ),
            "continuity": continuity,
            "schema_rule": "argus-miniature-route-v2 is required for current traversal claims",
        }
        control_root = paths.find_artifact("route_controls/control_e2e")
        controls = sorted(control_root.glob("ROUTE_RECEIPT*.json"), key=lambda p: p.stat().st_mtime) \
            if control_root.is_dir() else []
        control = controls[-1] if controls else None
        if control is not None and control.is_file():
            try:
                control_doc = json.loads(control.read_text(encoding="utf-8"))
                control_continuity = RW.continuity_from_route_receipt(control_doc)
                out["latest_control_route"] = {
                    "path": str(control),
                    "schema": control_doc.get("schema"),
                    "state": ("CONTINUOUS" if control_continuity.get("continuous") else "BROKEN"),
                    "continuity": control_continuity,
                    "claim_ceiling": control_doc.get("claim_ceiling", RW.MECHANICS_ONLY),
                    "not_a_scientific_result": control_doc.get("not_a_claim") or
                        "mechanics control only; not qualification or a reading",
                }
            except (OSError, ValueError) as exc:
                out["latest_control_route"] = {"state": "UNKNOWN", "path": str(control),
                                                "why": "unreadable: %s" % exc}
        return out

    return await asyncio.to_thread(_load)


_WS_OPEN = 0
LS_REMOTE_MIN_INTERVAL_S = 60.0
_LAST_LS_REMOTE = -1e9
WS_MAX_CONNECTIONS = max(1, int(os.environ.get("ARGUS_WS_MAX_CONNECTIONS", "16") or 16))
_WS_BODY = (None, "")


def _snapshot_body(snap) -> str:
    """Serialise a snapshot once, however many sockets are watching it: the cached snapshot object is the same object until it is rebuilt."""
    global _WS_BODY
    if _WS_BODY[0] is not snap:
        _WS_BODY = (snap, json.dumps(snap, default=str, sort_keys=True))
    return _WS_BODY[1]


@app.websocket("/ws/observatory")
async def ws_observatory(ws: WebSocket):
    """Push the observatory whenever it changes."""
    if ws.headers.get("origin") not in WS_ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    global _WS_OPEN
    if _WS_OPEN >= WS_MAX_CONNECTIONS:
        await ws.close(code=1013)
        return
    _WS_OPEN += 1
    last = None
    try:
        await ws.accept()
        while True:
            snap = await snapshot()
            body = _snapshot_body(snap)
            if body != last:
                await ws.send_text(body)
                last = body
            else:
                await ws.send_text(json.dumps({"schema": "argus-keepalive-v1",
                                               "server_time": time.time()}))
            await asyncio.sleep(WS_POLL_S)
    except WebSocketDisconnect:
        return
    finally:
        _WS_OPEN -= 1


@app.get("/api/storage")
def storage():
    """Storage conveyor state, read only."""
    import json as _json
    import pathlib as _pl
    jl = _pl.Path(_argus_public_path('home', 'state/storage/catalog.jsonl'))
    assets = []
    if jl.is_file():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    assets.append(_json.loads(line))
                except ValueError:
                    pass
    by = {}
    for a in assets:
        st = a.get("state") or "UNKNOWN"
        e = by.setdefault(st, {"assets": 0, "bytes": 0})
        e["assets"] += 1
        e["bytes"] += a.get("logical_bytes") or 0
    import shutil as _sh
    try:
        _du = _sh.disk_usage(_argus_public_path('anchor', ''))
        free = {"argus_home": round(_du.free / (1 << 30), 1)}
    except OSError:
        free = {}
    evictable = by.get("EVICTABLE", {}).get("bytes", 0)
    return {
        "ok": True, "read_only": True,
        "catalog": str(jl), "catalog_present": jl.is_file(),
        "assets": len(assets),
        "free_gib": free,
        "by_state": {k: {"assets": v["assets"],
                         "gib": round(v["bytes"] / (1 << 30), 2)}
                     for k, v in sorted(by.items())},
        "would_free_gib_if_evicted_now": round(evictable / (1 << 30), 2),
        "pinned": sum(1 for a in assets if a.get("pinned")),
        "blocked": {k: v["assets"] for k, v in by.items() if k.startswith("BLOCKED")},
        "restore_ready": sum(1 for a in assets
                             if a.get("state") == "EVICTED" and a.get("hydrate_cmd")),
        "evicted_without_restore_command": [
            a.get("asset_id") for a in assets
            if a.get("state") == "EVICTED" and not a.get("hydrate_cmd")],
        "largest": sorted(
            [{"asset_id": a.get("asset_id"), "name": a.get("logical_name"),
              "project": a.get("project"), "state": a.get("state"),
              "gib": round((a.get("logical_bytes") or 0) / (1 << 30), 2),
              "local": a.get("local_state"), "remote": a.get("remote_state")}
             for a in assets], key=lambda x: -x["gib"])[:25],
        "mutations_are_not_here": "archive, verify, evict and hydrate are not performed by this read-only service",
    }


@app.get("/api/current_state")
def current_state(refresh: bool = False):
    """Upstream truth in three separate fields; the operator's own state package is not part of the public release."""
    import json as _json
    import pathlib as _pl
    import subprocess as _sp
    import time as _time

    live_ref = None
    refreshed = False
    refresh_error = None
    global _LAST_LS_REMOTE
    if refresh and (_time.monotonic() - _LAST_LS_REMOTE) < LS_REMOTE_MIN_INTERVAL_S:
        refresh_error = "a live refresh ran less than %d s ago; not repeated (this read-only route will not be made to contact the network on every request)" % LS_REMOTE_MIN_INTERVAL_S
        refresh = False
    if refresh:
        _LAST_LS_REMOTE = _time.monotonic()
        try:
            r = _sp.run(["git", "ls-remote", "https://github.com/ScrollPrize/villa.git",
                         "refs/heads/main"], capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout.strip():
                live_ref = r.stdout.split()[0]
                refreshed = True
            else:
                refresh_error = (r.stderr or "empty response")[-200:]
        except Exception as e:
            refresh_error = repr(e)[:200]

    watch = {}
    watch_source = None
    wf = _pl.Path(paths.repo("corpus", "upstream_snapshot.json"))
    if wf.is_file():
        try:
            watch = _json.loads(wf.read_text(encoding="utf-8"))
            watch_source = str(wf)
        except ValueError:
            watch = {}
    snap_utc = watch.get("scanned") or watch.get("utc")
    age_h = None
    if snap_utc:
        try:
            import calendar as _cal
            t = _cal.timegm(_time.strptime(snap_utc, "%Y-%m-%dT%H:%M:%SZ"))
            age_h = round((_time.time() - t) / 3600.0, 2)
        except ValueError:
            age_h = None

    return {
        "ok": True, "read_only": True,
        "state_package": {"available": False,
                          "why": "the operator's own state package is not part of the public release"},
        "upstream_truth": {
            "live_git_ref": live_ref,
            "snapshot": {"recorded_head": (watch.get("git_delta") or {}).get(
                             "live_main_head"),
                         "snapshot_utc": snap_utc, "age_hours": age_h,
                         "source": watch_source},
            "refresh_actually_ran": refreshed,
            "refresh_error": refresh_error,
            "how_to_refresh": "GET /api/current_state?refresh=1",
            "why_three_fields": ("a cached ref and a live ref are indistinguishable unless "
                                 "the response says whether a refresh ran")},
    }


@app.get("/api/workspaces")
def workspaces(slug: str | None = None):
    """Every workspace on disk, each labelled PRODUCTION / FIXTURE / UNCLASSIFIED."""
    import re as _re
    import time as _time

    from argus.core import scroll_ids as _sid
    from argus.core import workspace as _ws
    from argus.core.actions import ACTORS as _ACTORS
    from argus.core.actions import Refused as _Refused

    RULES = [
        {"rule": "SCROLL_NOT_CANONICAL",
         "test": "argus.core.scroll_ids.resolve(record['scroll']) raises",
         "why": ("a workspace is scoped to ONE PHYSICAL SCROLL. `resolve` refuses an id "
                 "that is not in CANONICAL and never returns a nearest match, so an id it "
                 "refuses names no object this project can hold. `PHercTest` and `A` are "
                 "not scrolls.")},
        {"rule": "ACTOR_KIND_NOT_DECLARED",
         "test": "record['created_by'].split(':')[0] not in argus.core.actions.ACTORS",
         "why": ("ActionSpec.parse refuses any actor whose kind is not one of %s. A record "
                 "whose creator fails that check could not have been written through the "
                 "command surface at all -- it was written by a harness calling "
                 "workspace.create() directly." % (_ACTORS,))},
    ]
    WITHHOLDING = {
        "rule": "OBJECTIVE_STATES_NO_WORK",
        "verdict": "UNCLASSIFIED -- never FIXTURE",
        "test": ("every word of record['objective'], ignoring 'test', already appears "
                 "inside record['slug']"),
        "why": ("an objective is meant to say what the WORK is. When it only repeats the "
                "slug it is naming the mechanism that wrote the record. Neither engine "
                "rule can separate such a row from real work, so it is WITHHELD from the "
                "production list and shown in developer mode -- not called a fixture."),
    }

    rows, counts = [], {"PRODUCTION": 0, "FIXTURE": 0, "UNCLASSIFIED": 0}
    for rec in _ws.list_workspaces():
        fired, evidence = [], []
        try:
            canonical = _sid.resolve(rec.get("scroll") or "")
        except KeyError as e:
            canonical = None
            fired.append("SCROLL_NOT_CANONICAL")
            evidence.append(str(e))
        kind = str(rec.get("created_by") or "").split(":", 1)[0]
        if kind not in _ACTORS:
            fired.append("ACTOR_KIND_NOT_DECLARED")
            evidence.append("created_by %r has kind %r, which ActionSpec.parse refuses; "
                            "declared kinds are %s"
                            % (rec.get("created_by"), kind, list(_ACTORS)))
        if fired:
            klass = "FIXTURE"
        else:
            toks = [t for t in _re.split(r"[^a-z0-9]+", (rec.get("objective") or "").lower())
                    if t and t != "test"]
            slug_l = str(rec.get("slug") or "").lower()
            if not toks or all(t in slug_l for t in toks):
                klass = "UNCLASSIFIED"
                evidence.append(
                    "no fixture rule fired, but the objective %r states no work beyond the "
                    "slug %r itself. Withheld from the production list rather than guessed "
                    "at in either direction."
                    % (rec.get("objective"), rec.get("slug")))
            else:
                klass = "PRODUCTION"
        counts[klass] += 1
        rows.append(dict(rec, classification={
            "class": klass, "rules_fired": fired, "evidence": evidence,
            "canonical_scroll": canonical}))

    from argus.core.actions import STATE as _STATE

    cur_path = _STATE / "current_workspace.json"
    cur_slug, cur_err = None, None
    if cur_path.is_file():
        try:
            cur_slug = (json.loads(cur_path.read_text(encoding="utf-8")) or {}).get("slug")
        except (OSError, ValueError) as e:
            cur_err = "current pointer unreadable: %s" % e
    else:
        cur_err = "no current pointer at %s" % cur_path
    cur_row = next((r for r in rows if r.get("slug") == cur_slug), None)
    cur_class = (cur_row or {}).get("classification", {}).get("class")

    want = slug or (cur_slug if cur_class == "PRODUCTION" else None)
    preview_of, preview_class = None, None
    if slug:
        prow = next((r for r in rows if r.get("slug") == slug), None)
        preview_class = (prow or {}).get("classification", {}).get("class")
        preview_of = slug

    readiness, readiness_note = None, None
    if want and want != cur_slug:
        try:
            readiness = _ws.readiness(want)
            readiness_note = ("read-only preview of %r. It is NOT the selected workspace; "
                              "selecting one is an action, not a page load." % want)
        except (_Refused, OSError) as e:
            readiness_note = "readiness could not be read for %r: %s" % (want, e)
    elif cur_row is None:
        readiness_note = ("no workspace is selected, so there is nothing to report readiness "
                          "for" if cur_err is None else cur_err)
    elif cur_class != "PRODUCTION":
        readiness_note = ("the selected workspace %r is classified %s, so ARGUS will not "
                          "present its readiness as production state. Select a production "
                          "workspace." % (cur_slug, cur_class))
    else:
        try:
            readiness = _ws.readiness(cur_slug)
        except (_Refused, OSError) as e:
            readiness_note = "readiness could not be read: %s" % e

    return {
        "schema": "argus-workspaces-v1",
        "utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "read_only": True,
        "how_it_is_read": ("this route only reads. It submits no job, acquires no lease and "
                           "writes no file, so opening a screen that calls it cannot mutate."),
        "classification_rules": RULES,
        "withholding_rule": WITHHOLDING,
        "workspaces": rows,
        "counts": counts,
        "total": len(rows),
        "current": {"slug": cur_slug, "class": cur_class, "error": cur_err,
                    "is_production": cur_class == "PRODUCTION"},
        "readiness": readiness,
        "readiness_note": readiness_note,
        "readiness_of": want,
        "preview_of": preview_of,
        "preview_class": preview_class,
        "mutation_lives_elsewhere": ("creating or selecting a workspace is an ActionSpec "
                                     "posted to the command service, which requires an "
                                     "explicit operator session. It is not reachable from a "
                                     "page load."),
    }



@app.get("/api/command-jobs")
def command_jobs(limit: int = 40):
    """Every job the command service has recorded, read straight off disk."""
    import time as _time

    from argus.core import action_registry as _AR
    from argus.core import jobs as _JOBS
    from argus.core.actions import STATE as _STATE

    TERMINAL = ("SUCCEEDED", "REFUSED", "CANCELLED", "PLANNED_ONLY")
    root = _STATE / "jobs"
    rows = []
    if root.is_dir():
        for d in root.iterdir():
            f = d / "job.json"
            if not f.is_file():
                continue
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            state = j.get("state")
            terminal = state in TERMINAL
            rows.append({
                "job_id": j.get("job_id"), "action": j.get("action"),
                "actor": j.get("actor"), "state": state,
                "started_utc": j.get("started_utc"), "finished_utc": j.get("finished_utc"),
                "terminal": terminal,
                "cancellable_as_recorded": bool(j.get("cancellable")),
                "effective_cancellable": bool(j.get("cancellable")) and not terminal,
                "refusal": ((j.get("result") or {}).get("code")
                            if isinstance(j.get("result"), dict) else None),
                "why": ((j.get("result") or {}).get("why")
                        if isinstance(j.get("result"), dict) else None),
                "age_s": _age_s(j.get("started_utc")),
            })
    rows.sort(key=lambda r: str(r.get("started_utc") or ""), reverse=True)

    stuck = [r for r in rows if r["state"] == "RUNNING"]
    mislabeled = [r for r in rows
                  if r["cancellable_as_recorded"] and r["terminal"]]
    by_state = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    described = _JOBS.describe_actions()
    job_actions = sorted(a["action"] for a in described)
    job_mutating = sum(1 for a in described if a.get("mutating"))

    return {
        "schema": "argus-command-jobs-v1",
        "utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "read_only": True,
        "total": len(rows),
        "by_state": by_state,
        "jobs": rows[: max(1, min(limit, 500))],
        "stuck": stuck,
        "stuck_note": ("a RUNNING job with no process behind it. `workspace.readiness` is "
                       "written RUNNING at submit and only updated by the submitting "
                       "process; the Workspace screen submitted one on every page load."),
        "cancellable_but_finished": len(mislabeled),
        "cancellable_note": ("`cancellable` is recorded once at submit time and never "
                             "cleared, so a finished job still carries it. Use "
                             "`effective_cancellable`."),
        "registries": {
            "command_surface": {
                "module": "argus.core.action_registry.REGISTRY",
                "count": len(_AR.REGISTRY),
                "actions": sorted(_AR.REGISTRY),
                "reached_by": "POST /cmd/submit with an ActionSpec, operator session required",
            },
            "stage_surface": {
                "module": "argus.core.jobs",
                "count": len(job_actions),
                "mutating": job_mutating,
                "actions": job_actions,
                "reached_by": "the jobs control plane (ARGUS_JOBS_API_ENABLED=1), a separate app",
            },
            "why_two_numbers": ("these are DIFFERENT allowlists over different surfaces. "
                                "Quoting one number as though it described the other is how "
                                "two screens disagreed about the same system."),
        },
        "asking_is_a_mutation": ("submitting any of these is a POST to the command service "
                                 "and requires an explicit operator session. This route "
                                 "reads the ledger and nothing else."),
    }


@app.get("/api/resumable-jobs")
def resumable_jobs(limit: int = 100):
    """Every job that stopped part-way, and exactly where: units done and pending, the unit it resumes at, and why one that cannot be resumed cannot."""
    from argus.core import job_resume as _JR

    return _JR.overview(limit=max(1, min(limit, 200)))


@app.get("/api/open-problems")
def open_problems():
    """Official problem tracks plus the latest consented Villa issue snapshot; no network."""
    from argus.core import open_problems as _OP

    return _OP.read()


def _age_s(utc: str | None):
    if not utc:
        return None
    try:
        t = time.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    import calendar
    return round(time.time() - calendar.timegm(t), 1)



def _within(p: Path, root: Path) -> bool:
    """Kept as a thin alias: `argus.core.paths.within_root` is the canonical implementation, shared with the governed write door (bff.py) so a path-containment decision can never quietly diverge between..."""
    return paths.within_root(p, root)


def _resolve_served(path: str) -> Path:
    """Kept as a thin alias over `argus.core.paths.resolve_served_path` -- see that function's docstring for the full reasoning (unchanged from when it lived here)."""
    return paths.resolve_served_path(path, SERVE_ROOTS)


def _find_runs():
    for r in RUN_ROOTS:
        if not r.is_dir():
            continue
        for d in r.iterdir():
            if not d.is_dir():
                continue
            if (d / "run.json").is_file() or (d / HB.FILENAME).is_file():
                yield d
            else:
                for sub in (x for x in d.iterdir() if x.is_dir()):
                    if (sub / "run.json").is_file() or (sub / HB.FILENAME).is_file():
                        yield sub


def selftest() -> bool:
    from fastapi.testclient import TestClient
    ck = []
    c = TestClient(app)

    ck.append(("health answers", c.get("/api/health").status_code == 200))
    ck.append(("the observatory answers with the feed schema",
               c.get("/api/observatory").json().get("schema") == F.FEED_SCHEMA))

    methods = set()
    for r in app.routes:
        methods |= set(getattr(r, "methods", set()) or set())
    ck.append(("the route table contains no write method",
               not (methods & WRITE_METHODS)))
    ck.append(("a write is refused with 405 and says why",
               c.post("/api/observatory").status_code == 405))
    ck.append(("every write verb is refused",
               all(c.request(m, "/api/health").status_code == 405
                   for m in ("POST", "PUT", "PATCH", "DELETE"))))

    ck.append(("a path outside the declared roots is 403",
               c.get("/api/file", params={"path": str(ROOT / "PROJECT.md")}).status_code == 403))
    ck.append(("a traversal out of a root is 403 however it is spelled",
               c.get("/api/file", params={
                   "path": str(paths.artifacts() / ".." / "PROJECT.md")}).status_code == 403))

    sealed = paths.artifacts("sealed_fixture")
    if (sealed / "BLINDING_RECORD.txt").is_file():
        probe = next((p for p in sealed.rglob("*") if p.is_file()), None)
        ck.append(("a file under an active seal is 423, not served",
                   probe is not None
                   and c.get("/api/file", params={"path": str(probe)}).status_code == 423))
    else:
        ck.append(("the sealed experiment is present to test against", False))

    ck.append(("an unknown run is 404", c.get("/api/runs/nope").status_code == 404))

    j = c.get("/api/sources", params={"live": "false"}).json()
    ck.append(("the source registry lists every declared source and says it is read-only",
               j.get("read_only") is True and len(j.get("sources", [])) >= 5))
    ck.append(("reachable and held are separate fields, never merged",
               all("local" in s_ and "probe_result" in s_ for s_ in j["sources"])))

    o = c.get("/api/oversight").json()
    ck.append(("oversight reports the execution boundary rather than a green light",
               o["boundary"]["ui_can_start_work"] is False
               and "operational" not in json.dumps(o).lower().replace("operations", "")))
    ck.append(("every recommendation cites the measurement that produced it",
               all(r.get("because") and r.get("action") for r in o["recommendations"])))
    ck.append(("no credential value is ever returned",
               all(isinstance(v, bool) for v in o["environment_flags"].values())))

    ad = c.get("/api/advisor").json()
    ck.append(("the advisor cites findings and never claims to act",
               ad["acts"] is False and ad["record_available"] is True
               and ad["record_size"] > 100))
    ck.append(("every surfaced finding carries an id and a way to read it",
               all(f.get("id") and f.get("read_it")
                   for cond in ad["conditions"] for f in cond["record_says"])))
    ck.append(("retracted findings are excluded from advice",
               ad["retracted_excluded"] > 0
               and all(f["id"] not in ()
                       for cond in ad["conditions"] for f in cond["record_says"])))

    meshes = paths.artifacts("meshes")
    one = next((d for d in meshes.iterdir() if (d / "x.tif").is_file()), None)         if meshes.is_dir() else None
    if one is not None:
        j = c.get("/api/mesh", params={"path": str(one)}).json()
        ck.append(("a real mesh lattice is served and declares it is not image evidence",
                   j.get("is_not_image_evidence") is True and len(j.get("points", [])) > 100))
        numeric = all(isinstance(v, (int, float)) for pt in j.get("points", [])[:50]
                      for v in pt)
        ck.append(("the lattice is coordinates only, with no encoded array anywhere",
                   numeric
                   and all(len(pt) == 2 for pt in j.get("points", []))
                   and not any(isinstance(v, str) and len(v) > 400 for v in j.values())))
    else:
        ck.append(("a mesh is present to serve", False))
    ck.append(("a mesh outside the roots is 403",
               c.get("/api/mesh", params={"path": str(ROOT)}).status_code == 403))

    r = c.get("/api/receipts")
    ck.append(("the sanitized receipts route is mounted and answers",
               r.status_code == 200 and r.json()["schema"] == RECEIPTS.RECEIPTS_SCHEMA))
    ck.append(("every declared receipt key is answered, present or absent",
               set(r.json()["receipts"]) == {k.key for k in RECEIPTS.KEYS}))
    ck.append(("mounting it did not add a write route",
               not (methods & WRITE_METHODS)))

    with c.websocket_connect("/ws/observatory", headers={
            "origin": "http://127.0.0.1:8792"}) as ws:
        first = json.loads(ws.receive_text())
        ck.append(("the websocket sends the observatory first",
                   first.get("schema") == F.FEED_SCHEMA))

    ok = True
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
        ok &= bool(good)
    print("selftest: %d/%d passed" % (sum(1 for _, g in ck if g), len(ck)))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(0 if selftest() else 1)
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port)
