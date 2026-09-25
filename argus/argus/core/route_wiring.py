"""ONE implementation of the route rules, consumed by runner, CLI and service."""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

from argus.core import chain_traversal as CT
from argus.core import ink_socket_gate as G
from argus.core import key_list_hash as KLH
from argus.core import launch_authorization_v3 as LA3
from argus.core import official_identity as OI
from argus.core import orientation_semantics as OS
from argus.core import store_identity as SI
from argus.core import volume_acquisition as VA
from argus.core import volume_id_gate as VG
from argus.core.evaluation_envelope import DurableArtifactSet, EnvelopeRefusal

CONTRACT = "argus-route-wiring-v1"
REFUSAL_SCHEMA = "argus-route-refusal-v1"
REFUSED = G.REFUSED
PERMITTED = G.PERMITTED
MECHANICS_ONLY = G.MECHANICS_ONLY

BOTH_ORIENTATIONS = OS.REQUIRED_DEPTH_ORIENTATIONS

InkSocketRefusal = G.InkSocketRefusal


class RouteRefusal(RuntimeError):
    """A route rule refused."""

    def __init__(self, message: str, *, gate: str, receipt_path=None, record=None):
        super().__init__(message)
        self.gate = gate
        self.receipt_path = receipt_path
        self.record = record



GATES = ("INK_SOCKET", "ORIENTATIONS", "VOLUME_ID", "STORE_IDENTITY", "KEY_LIST_HASH",
         "DURABLE_ARTIFACTS", "LAUNCH_AUTHORIZATION_V3", "ROUTE_CONTINUITY", "MATERIAL_IDENTITY",
         "ACQUISITION_DRY_RUN", "OFFICIAL_IDENTITY")


def _key(s) -> str:
    return str(s or "").strip().upper().replace("_", "").replace("-", "").replace(" ", "")


def write_refusal(gate: str, *, stage: str, reasons, receipt_dir, detail=None) -> pathlib.Path:
    """One REFUSED receipt, canonical writer, append-only name."""
    from argus.core import receipts
    if gate not in GATES:
        raise ValueError("unknown gate %r; gates are %s" % (gate, GATES))
    if receipt_dir is None or not str(receipt_dir).strip():
        raise RouteRefusal("%s refused and no receipt_dir was given; a refusal with no receipt is "
                           "indistinguishable from the gate never being called" % gate, gate=gate)
    record = {
      "schema": REFUSAL_SCHEMA, "contract": CONTRACT, "gate": gate, "stage": stage,
      "verdict": REFUSED, "reasons": [str(r) for r in (reasons or [])] or ["(no reason given)"],
      "detail": detail,
      "decided_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "permitted_means": "nothing: this is a refusal",
    }
    body = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    record["receipt_sha256"] = hashlib.sha256(body).hexdigest()
    d = pathlib.Path(receipt_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    p = d / ("ROUTE_REFUSED_%s_%s_%s.json" % (gate, stamp, record["receipt_sha256"][:12]))
    if p.exists():
        raise RouteRefusal("receipt %s already exists; receipts are append-only" % p, gate=gate)
    return receipts.write_json(record, p)


def _refuse(gate, *, stage, reasons, receipt_dir, detail=None, cause=None):
    p = write_refusal(gate, stage=stage, reasons=reasons, receipt_dir=receipt_dir, detail=detail)
    msg = "%s REFUSED at %s:\n  - %s\nreceipt: %s" % (gate, stage, "\n  - ".join(
      str(r) for r in reasons), p)
    raise RouteRefusal(msg, gate=gate, receipt_path=p,
                       record={"gate": gate, "stage": stage, "reasons": list(reasons),
                               "detail": detail}) from cause



def require_orientations(orientations, *, receipt_dir, stage: str = "ink") -> tuple:
    """Both depth orders or a REFUSED receipt."""
    try:
        return OS.require_both_orientations(orientations)
    except OS.OrientationSemanticsViolation as e:
        _refuse("ORIENTATIONS", stage=stage, reasons=[str(e)], receipt_dir=receipt_dir,
                detail={"given": repr(orientations)}, cause=e)



def ink_socket_entry(*, scroll, purpose, receipt_dir, declared_volume_id, store_identity,
                     orientations, stage: str = "ink", volume_registry=None,
                     volume_registry_source=None, target_authority=None) -> dict:
    """THE FIRST CALL OF EVERY INFERENCE ENTRY POINT."""
    if receipt_dir is None or not str(receipt_dir).strip():
        G.enter(scroll, purpose=purpose, receipt_dir=receipt_dir)
    import uuid
    call_dir = pathlib.Path(receipt_dir) / ("socket_%s_%s" % (
      time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), uuid.uuid4().hex[:8]))
    rec = G.enter(scroll, purpose=purpose, receipt_dir=call_dir, stage=stage,
                  target_authority=target_authority, declared_volume_id=declared_volume_id,
                  store_identity=store_identity, volume_registry=volume_registry,
                  volume_registry_source=volume_registry_source)
    both = require_orientations(orientations, receipt_dir=receipt_dir, stage=stage)
    return dict(rec, orientations=list(both), route_wiring=CONTRACT)



_VOLUME_CHECKS = {
  "ACQUISITION": VG.before_acquisition,
  "INFERENCE": VG.before_inference,
  "PACKAGE_EXPORT": VG.before_package_export,
}


def verify_volume(checkpoint: str, *, receipt_dir, **kw) -> dict:
    fn = _VOLUME_CHECKS.get(checkpoint)
    if fn is None:
        _refuse("VOLUME_ID", stage=str(checkpoint), receipt_dir=receipt_dir,
                reasons=["unknown volume checkpoint %r; checkpoints are %s"
                         % (checkpoint, sorted(_VOLUME_CHECKS))])
    try:
        return fn(**kw)
    except VG.VolumeIdRefusal as e:
        _refuse("VOLUME_ID", stage=checkpoint, receipt_dir=receipt_dir,
                reasons=(e.verdict or {}).get("reasons") or [str(e)], detail=e.verdict, cause=e)



def key_list_record(keys) -> dict:
    """The ONLY way a new record hashes an ordered key list: argus-key-list-hash-v2, versioned."""
    return KLH.hash_record(keys)


def verify_key_list(keys, recorded, *, receipt_dir=None, stage: str = "key_list") -> dict:
    """Legacy conventions are accepted ONLY here, and the matched version is always named."""
    r = KLH.verify(list(keys), recorded)
    if not r["verified"] and receipt_dir is not None:
        _refuse("KEY_LIST_HASH", stage=stage, receipt_dir=receipt_dir, reasons=[r["why"]],
                detail={k: r.get(k) for k in ("recorded", "matched_version", "count")})
    return r



def _read_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None



def resolve_official_identity(claim: dict, *, receipt_dir, catalogue, cache,
                              stage: str = "official_identity") -> dict:
    """argus.core.official_identity.resolve, or a typed OFFICIAL_IDENTITY refusal receipt."""
    r = OI.resolve(claim, catalogue=catalogue, cache=cache)
    if not r["proven"]:
        _refuse("OFFICIAL_IDENTITY", stage=stage, receipt_dir=receipt_dir,
                reasons=["%s %s: %s" % (k, v["state"], v["why"])
                         for k, v in r["failing_fields"].items()],
                detail={"control_id": (claim or {}).get("control_id"),
                        "token": r.get("token"), "state": r["state"],
                        "failing_fields": sorted(r["failing_fields"]),
                        "catalogue_source": (catalogue.source if catalogue is not None
                                             else None)})
    return r


def assess_store(store_dir, *, array_path: str = "", manifest_keys=None, declared=None,
                 official=None, phase: str | None = None) -> dict:
    """Read a store's OWN small metadata files and assess every identity field."""
    d = pathlib.Path(store_dir)
    ident = _read_json(d / ("STORE_IDENTITY_%s.json" % phase)) if phase else \
        _read_json(d / "STORE_IDENTITY.json")
    if ident is None and not phase:
        found = sorted(d.glob("STORE_IDENTITY_*.json"))
        ident = _read_json(found[0]) if found else None
    ident = ident or {}
    arr = d / array_path if array_path else d
    recorded = (ident.get("key_list_hash") or ident.get("required_key_list_sha256")
                or ident.get("ordered_key_list_sha256"))
    if manifest_keys is None:
        for mp in ([d / ("ACQUIRE_MANIFEST_%s.json" % phase)] if phase
                   else sorted(d.glob("ACQUIRE_MANIFEST*.json"))):
            objs = (_read_json(mp) or {}).get("objects")
            if isinstance(objs, dict) and objs:
                manifest_keys = [k for k, v in objs.items() if (v or {}).get("state")]
                break
    dec = dict(declared or {})
    for k in ("pitch_um", "energy_kev", "plane_count"):
        if k not in dec and ident.get(k) is not None:
            dec[k] = ident.get(k)
    zattrs = _read_json(d / ".zattrs")
    if zattrs is None and array_path:
        zattrs = _read_json(arr / ".zattrs")
    a = SI.assess(zarray=_read_json(arr / ".zarray"), zattrs=zattrs, manifest_keys=manifest_keys,
                  recorded_key_list_hash=recorded, declared=dec,
                  level=str(array_path or "0"), official=official)
    return dict(a, store_dir=str(d), identity_record=ident)


def assert_store(assessment: dict, *, receipt_dir, stage: str = "store") -> dict:
    if not assessment.get("all_proven"):
        f = assessment.get("fields") or {}
        _refuse("STORE_IDENTITY", stage=stage, receipt_dir=receipt_dir,
                reasons=["%s %s (%s)" % (k, (f.get(k) or {}).get("state", SI.UNKNOWN),
                                        (f.get(k) or {}).get("basis")) for k in
                         (assessment.get("unproven") or SI.FIELDS)],
                detail={"store_dir": assessment.get("store_dir"), "unproven":
                        assessment.get("unproven")})
    return assessment



def durable_artifacts(out_dir, *, required_kinds=None) -> DurableArtifactSet:
    """The one writer for arrays + summary."""
    if required_kinds is None:
        return DurableArtifactSet(out_dir)
    return DurableArtifactSet(out_dir, required_kinds=required_kinds)


def write_durable_summary(dset: DurableArtifactSet, doc: dict, filename: str, *, receipt_dir,
                          stage: str = "summary") -> dict:
    try:
        return dset.write_summary(doc, filename)
    except EnvelopeRefusal as e:
        _refuse("DURABLE_ARTIFACTS", stage=stage, receipt_dir=receipt_dir, reasons=[str(e)],
                detail={"missing_kinds": dset.missing_kinds()}, cause=e)



PACKET_FIELDS = ("runner_rel", "modules", "contract_sha256", "plan_sha256", "bindings",
                 "checkpoint_path")


def load_launch_packet(path) -> dict:
    """A launch packet names what v3 measures."""
    if path is None or not str(path).strip():
        return None
    doc = _read_json(pathlib.Path(path))
    if not isinstance(doc, dict):
        return None
    return doc


def launch_v3(authorization_id, *, packet, receipt_dir, argv=None, arms=None, stage: str = "launch",
              measure_fn=None) -> dict:
    """Measure live, validate, consume -- or a REFUSED receipt."""
    reasons = []
    if not str(authorization_id or "").strip():
        reasons.append("LAUNCH_AUTHORIZATION_V3_ABSENT: no authorization id was presented; a "
                       "launch that is not bound to a single-use v3 authorisation is refused")
    if not isinstance(packet, dict):
        reasons.append("LAUNCH_PACKET_ABSENT: no launch packet names the runner, modules, "
                       "contract, plan, inputs and checkpoint that v3 must re-measure")
    else:
        missing = [f for f in PACKET_FIELDS if packet.get(f) in (None, "")]
        if missing:
            reasons.append("LAUNCH_PACKET_INCOMPLETE: %s missing; UNKNOWN refuses" % missing)
    if reasons:
        _refuse("LAUNCH_AUTHORIZATION_V3", stage=stage, receipt_dir=receipt_dir, reasons=reasons,
                detail={"authorization_id": authorization_id})
    try:
        return LA3.launch(str(authorization_id), runner_rel=packet["runner_rel"],
                          modules=packet["modules"], contract_sha256=packet["contract_sha256"],
                          plan_sha256=packet["plan_sha256"], bindings=packet["bindings"],
                          checkpoint_path=packet["checkpoint_path"], argv=argv,
                          arms=arms if arms is not None else packet.get("arms"),
                          measure_fn=measure_fn)
    except LA3.AuthorizationRefusal as e:
        _refuse("LAUNCH_AUTHORIZATION_V3", stage=stage, receipt_dir=receipt_dir,
                reasons=[str(e)], detail={"authorization_id": authorization_id}, cause=e)



mint_ct_record = CT.ct_record
propagate_material = CT.propagate


def route_continuity(stage_records, *, declared_stages, material_records=None) -> dict:
    """A refused/missing/unknown stage ends the traversal; a material change ends it too."""
    t = CT.assess_traversal(list(stage_records), declared_stages=declared_stages)
    out = {"contract": CONTRACT, "traversal": t, "material": None,
           "continuous": bool(t["continuous"])}
    if material_records is not None:
        m = CT.assess_material(list(material_records))
        out["material"] = m
        out["continuous"] = bool(t["continuous"] and m["continuous"])
    return out


def assert_route_continuous(stage_records, *, declared_stages, material_records=None,
                            receipt_dir, stage: str = "route") -> dict:
    r = route_continuity(stage_records, declared_stages=declared_stages,
                         material_records=material_records)
    if not r["continuous"]:
        reasons = []
        if not r["traversal"]["continuous"]:
            reasons.append("BROKEN at %s: %s" % (r["traversal"]["broken_at_stage"],
                                                 r["traversal"]["why"]))
        if r["material"] is not None and not r["material"]["continuous"]:
            reasons.extend(r["material"]["problems"])
        gate = ("MATERIAL_IDENTITY" if r["traversal"]["continuous"] else "ROUTE_CONTINUITY")
        _refuse(gate, stage=stage, receipt_dir=receipt_dir, reasons=reasons,
                detail={"traversed_prefix": r["traversal"]["traversed_prefix"]})
    return r


def continuity_from_route_receipt(doc: dict) -> dict:
    """Continuity of a route receipt as written."""
    stages = list((doc or {}).get("stages") or [])
    declared = list((doc or {}).get("declared_stages") or [s.get("stage") for s in stages])
    recs = [{"stage": s.get("stage"), "state": s.get("traversal_state"),
             "physical_material_id": s.get("physical_material_id")} for s in stages]
    lineage = (doc or {}).get("material_lineage")
    return route_continuity(recs, declared_stages=declared,
                            material_records=lineage if lineage else None)



def acquire_dry_run(*, receipt_dir, scroll, volume_id, url, phase, array_path="", roi=None,
                    zarray_meta=None, byte_ceiling=None, volume_registry=None,
                    volume_registry_source=None) -> dict:
    """Volume id verified at ACQUISITION, then the plan."""
    v = VG.verify("ACQUISITION", scroll=scroll, declared_volume_id=volume_id,
                  observed_volume_id=VG.parse_volume_id_from_url(url), registry=volume_registry,
                  registry_source=volume_registry_source)
    if not v["verified"]:
        _refuse("VOLUME_ID", stage="ACQUISITION_DRY_RUN", receipt_dir=receipt_dir,
                reasons=v["reasons"], detail=v)
    try:
        plan = VA.dry_run(url=url, array_path=array_path, roi=roi, phase=phase,
                          zarray_meta=zarray_meta, scroll=scroll, volume_id=volume_id,
                          byte_ceiling=byte_ceiling)
    except VA.AcquisitionRefusal as e:
        _refuse("ACQUISITION_DRY_RUN", stage=str(phase), receipt_dir=receipt_dir, reasons=[str(e)],
                cause=e)
    targets = G.frozen_targets()
    is_target = any(_key(t) == _key(scroll) for t in (targets.get("targets") or {}))
    return dict(plan, volume_identity=v, route_wiring=CONTRACT,
                prize_target=is_target if targets.get("state") == "READ" else "UNKNOWN",
                execution_requires="launch_authorization_v3 and, for a prize target, verified "
                                   "target authority -- neither exists on this path")



INFERENCE_ENTRY_POINTS = {
  "argus/adapters/ink_maps.py": "InkMapAdapter.run",
}

INFERENCE_EXEMPT = {
  "argus/cli/cmd_demo.py": "synthetic DEMONSTRATION_ONLY fixture generated in memory; reads no "
                           "volume, no scroll, no store",
  "argus/core/route_wiring.py": "this module (the socket itself)",
  "argus/core/ink_socket_gate.py": "the socket rule",
  "argus/run.py": "wires InkMapAdapter, whose run() is the gated entry point",
  "argus/adapters/_ink_maps_patch.py": "depth-gate and verdict helpers called only from inside "
                                       "InkMapAdapter.run, after the socket",
  "argus/core/dependency_radius.py": "architecture receptive-field probe on RANDOM weights and "
                                     "random input; reads no volume",
  "argus/core/feed.py": "status feed; names the detector receipt file to count readings, reads "
                        "no voxel",
  "argus/core/process_contract.py": "declarative capability/process inventory; it names stages "
                                     "but executes no inference and opens no voxel",
  "argus/core/villa_provider_adapter.py": "read-only upstream subprocess adapter; its option "
                                            "names include inference settings but it does not "
                                            "run a detector over CT voxels",
  "argus/core/science_candidates.py": "declarative intake registry; names `vesuvius.predict` inside a "
                                      "contract string and executes nothing",
  "argus/core/scroll_workflow.py": "workflow planner; the marker is a message saying no inference is "
                                   "performed, and it launches nothing itself",
  "argus/core/surface_prediction_provider.py": "PLAN-ONLY surface (not ink) provider: builds an argv it "
                                               "returns and never runs; states so in its docstring",
  "argus/core/tiled_run_resume.py": "receipt analysis for interrupted tiled runs; names upstream flags "
                                    "but starts no process and reads no voxel",
  "argus/core/provider_registry.py": "declarative provider-row view: names `vesuvius.predict` as a capability id it "
                                     "reads from the ledger and in a blocker sentence; it imports no model, opens no "
                                     "volume and starts no process -- execution is only through provider.invoke / "
                                     "provider.candidate.invoke (argus.core.provider_actions), which are the gated doors",
}

LAUNCH_ENTRY_POINTS = {
  "argus/cli/cmd_run.py": "run",
  "argus/core/action_registry.py": "do_pipeline_start",
}


def describe() -> dict:
    """What the service reports: which function owns each item, by module and qualified name."""
    fns = {
      "1_ink_socket": ink_socket_entry, "2_volume_identity": verify_volume,
      "3_key_list_hash": key_list_record, "4_store_identity": assert_store,
      "5_durable_artifacts": durable_artifacts, "6_orientations": require_orientations,
      "7_launch_v3": launch_v3, "8_route_continuity": route_continuity,
      "9_material_identity": route_continuity, "10_acquire_dry_run": acquire_dry_run,
      "11_official_identity": resolve_official_identity,
    }
    return {"contract": CONTRACT,
            "items": {k: "%s.%s" % (f.__module__, f.__qualname__) for k, f in fns.items()},
            "delegates_to": {"ink_socket_gate": G.CONTRACT, "volume_id_gate": VG.CONTRACT,
                             "key_list_hash": KLH.CANONICAL, "store_identity": SI.CONTRACT,
                             "launch_authorization": LA3.CONTRACT,
                             "chain_traversal": CT.CONTRACT,
                             "durable_set": "argus-durable-artifact-set-v1",
                             "volume_acquisition": VA.CONTRACT,
                             "official_identity": OI.CONTRACT},
            "inference_entry_points": INFERENCE_ENTRY_POINTS,
            "inference_exempt": INFERENCE_EXEMPT,
            "launch_entry_points": LAUNCH_ENTRY_POINTS,
            "claim_ceiling": MECHANICS_ONLY,
            "permitted_means": "only that a rule did not refuse; nothing is launched because a "
                               "gate passed"}
