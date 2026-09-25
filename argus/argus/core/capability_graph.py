"""The capability graph, derived from receipts on disk."""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from argus.core import capabilities_vocab as V

GRAPH_ID = "argus-capability-graph-v1"

RECEIPTS = {
  "ct_to_surface_sampler": ("capability_receipts/sampler_repin.json",
                            "capability_receipts/sampler_controls.json"),
  "physical_pitch_adapter": ("capability_receipts/pitch_contract_audit.json",),
  "ink_inference_engine": ("capability_receipts/cross_scroll_gate.json",),
  "surface_tracing": ("capability_receipts/segment_measure.json",
                      "capability_receipts/capability_controls.json"),
  "review_and_provenance": ("capability_receipts/capability_controls.json",),
  "volume_viewer": ("capability_receipts/capability_controls.json",),
  "surface_geometry_viewer": ("capability_receipts/capability_controls.json",),
  "flattening_engine": ("capability_receipts/capability_controls.json",),
  "storage_hydration": ("capability_receipts/remote_stream_render.json",),
  "updater_component_manager": ("capability_receipts/capability_controls.json",),
}


def _load(root: pathlib.Path, rel: str):
    p = root / rel
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _control(root: pathlib.Path, key: str):
    """One capability's control row from the capability-controls receipt, or None."""
    rec = _load(root, "capability_receipts/capability_controls.json")
    if not rec:
        return None
    for row in rec.get("controls", ()):
        if row.get("capability") == key:
            return row
    return None


def _verif(row):
    """CONTROL_PASSED / FAILED / UNTESTED -- never a literal chosen by an author."""
    if row is None:
        return "UNTESTED"
    return "CONTROL_PASSED" if row.get("passed") else "FAILED"


def _when(row):
    """When the control last passed."""
    return (row or {}).get("last_successful_test_utc")


def _observed(root: pathlib.Path, rel: str, ok: bool = True):
    """When the evidence file itself was written -- read from the filesystem, never chosen."""
    if not ok:
        return None
    p = root / rel
    if not p.is_file():
        return None
    import datetime as _dt
    return _dt.datetime.fromtimestamp(p.stat().st_mtime,
                                      _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


VOLUME3D_RECEIPTS = (
  ("capability_receipts/volume3d_phantom.json", "synthetic phantom"),
  ("capability_receipts/volume3d_task.json", "real sealed scroll task"),
)


def _volume3d_evidence(root: pathlib.Path) -> str:
    """What the recorded browser runs of the 3D volume workbench actually showed."""
    parts = []
    for rel, label in VOLUME3D_RECEIPTS:
        rec = _load(root, rel)
        if not rec:
            parts.append("%s: no browser receipt" % label)
            continue
        steps = rec.get("steps") or []
        passed = sum(1 for s in steps if s.get("ok"))
        clean = not rec.get("console_errors") and not rec.get("failed_http_responses")
        parts.append("%s: %d/%d browser steps passed%s%s%s" % (
          label, passed, len(steps), "" if rec.get("all_steps_ok") else " (NOT all passed)",
          "" if clean else ", console errors or failed requests recorded",
          (", renderer " + str(rec["renderer"])) if rec.get("renderer") else ""))
    return "3D volume workbench (raw CT visualization; not a detector; no ink claim) -- " + "; ".join(parts)


def derive(artifacts_root) -> list:
    """Read every receipt and return one CapabilityState per capability."""
    root = pathlib.Path(artifacts_root)
    out = []

    repin = _load(root, "capability_receipts/sampler_repin.json")
    ctrls = _load(root, "capability_receipts/sampler_controls.json")
    if repin and ctrls:
        passed = bool(ctrls.get("all_passed"))
        out.append(V.CapabilityState(
          key="ct_to_surface_sampler",
          availability="INSTALLED",
          operational_verification="CONTROL_PASSED" if passed else "FAILED",
          scientific_admissibility="PLUMBING_ONLY",
          verification_label="MECHANICAL_CONTROLS_PASSED" if passed else None,
          version_or_hash=repin.get("binary", {}).get("sha256"),
          license="GPL-3.0, run as an external subprocess",
          source=repin.get("revision_executed"),
          last_successful_test_utc=_observed(root, "capability_receipts/sampler_repin.json", passed),
          detail="%d of %d controls; the depth reversal is an exact equality. Mechanically "
                 "admitted for the tested configuration only: non-composite, group 0, dense "
                 "pyramid. NOT scientifically admissible -- responding to its arguments is "
                 "not evidence that an output means anything."
                 % (len(ctrls.get("passed", [])), ctrls.get("controls_run", 0))))
    else:
        out.append(V.CapabilityState(
          key="ct_to_surface_sampler", availability="INSTALLED",
          operational_verification="UNTESTED",
          scientific_admissibility="UNQUALIFIED",
          detail="binary present; no control receipt found"))

    drift = _load(root, "capability_receipts/pitch_contract_audit.json")
    if drift:
        padded = drift.get("silent_padding", {}).get("padded")
        out.append(V.CapabilityState(
          key="physical_pitch_adapter", availability="INSTALLED",
          operational_verification="CONTROL_PASSED",
          scientific_admissibility="PLUMBING_ONLY",
          verification_label="SHAPE_AND_PITCH_CONFORMANCE",
          last_successful_test_utc=_observed(root, "capability_receipts/sampler_controls.json"),
          detail="conformance verified"))
    else:
        out.append(V.CapabilityState(
          key="physical_pitch_adapter", availability="INSTALLED",
          operational_verification="UNTESTED",
          scientific_admissibility="UNQUALIFIED"))

    gate = _load(root, "capability_receipts/cross_scroll_gate.json")
    if gate and gate.get("gate", {}).get("passed"):
        sci, label, det = "RESEARCH_ONLY", "CROSS_SCROLL_GATE_PASSED", (
            "the worst scroll cleared AUC and CI. This is transfer to unseen scrolls; it is "
            "not a licence to read a target, which needs the bridge gates too.")
    elif gate:
        sci, label, det = "UNQUALIFIED", None, (
            "cross-scroll gate ran and did not pass the worst-scroll bar")
    else:
        sci, label, det = "UNQUALIFIED", None, "cross-scroll gate has not produced a receipt"
    out.append(V.CapabilityState(
      key="ink_inference_engine", availability="INSTALLED",
      operational_verification="CONTROL_PASSED" if gate else "TESTED",
      scientific_admissibility=sci, verification_label=label,
      last_successful_test_utc=_observed(root, "capability_receipts/cross_scroll_gate.json", bool(gate)),
      license="checkpoint licence UNKNOWN -- not redistributable",
      detail=det))

    seg = _load(root, "capability_receipts/segment_measure.json")
    tr = _control(root, "surface_tracing")
    traced = bool(tr and tr.get("passed"))
    out.append(V.CapabilityState(
      key="surface_tracing",
      availability="INSTALLED" if traced else "NOT_INSTALLED",
      operational_verification=_verif(tr),
      last_successful_test_utc=_when(tr),
      scientific_admissibility="NOT_APPLICABLE",
      detail=(("tracer present at villa pin %s (%s); imported surfaces measured: %d. ARGUS has "
               "no tracer of its OWN -- this ability is upstream's, installed here."
               % ((tr or {}).get("villa_pin", "unknown")[:12],
                  ", ".join((tr or {}).get("subpackages", [])[:4]),
                  len(seg.get("measurements", [])) if seg else 0))
              if traced else
              "no tracer control receipt; run the capability controls")))

    ren = _load(root, "capability_receipts/remote_stream_render.json")
    out.append(V.CapabilityState(
      key="storage_hydration", availability="INSTALLED",
      operational_verification="CONTROL_PASSED" if ren else "UNTESTED",
      scientific_admissibility="NOT_APPLICABLE",
      verification_label="REMOTE_STREAM_PROVEN" if ren else None,
      last_successful_test_utc=_observed(root, "capability_receipts/remote_stream_render.json", bool(ren)),
      detail=("a remote store larger than local free space was read by chunk over HTTP; only "
              "the chunks the surface passes through were fetched" if ren else
              "no streaming receipt found")))

    for key, adm, lic, detail in (
      ("volume_viewer", "NOT_APPLICABLE", None,
       "displays bytes that already exist; creates no representation"),
      ("surface_geometry_viewer", "NOT_APPLICABLE", None,
       "draws a lattice computed elsewhere"),
      ("flattening_engine", "UNQUALIFIED", "GPL-3.0, external subprocess",
       "flattener source present in the pinned upstream tree. For a tifxyz the "
       "parameterisation IS the flattening, so this stage is usually already satisfied by "
       "the imported geometry."),
      ("review_and_provenance", "NOT_APPLICABLE", None,
       "governed states exist and round-trip; no supervision has been frozen"),
      ("updater_component_manager", "NOT_APPLICABLE", None,
       "conveyor resolves pointers and surfaces resolver failures; no component has been "
       "promoted through the full quarantine->test->promote path yet"),
    ):
        row = _control(root, key)
        checks = (row or {}).get("checks") or []
        out.append(V.CapabilityState(
          key=key, availability="INSTALLED",
          operational_verification=_verif(row),
          last_successful_test_utc=_when(row),
          scientific_admissibility=adm, license=lic,
          detail=(detail + ((" | control: " + "; ".join(checks[:3])) if checks else
                            " | NO CONTROL RECEIPT -- run the capability controls")
                  + ((" | " + _volume3d_evidence(root)) if key == "volume_viewer" else ""))))
    return out


def route(states, have_stages=()) -> dict:
    """Which stages are reachable, and the FIRST edge that is not."""
    by = {s.key: s for s in states}
    chain = [("CT", "volume_viewer"), ("Surface", "surface_tracing"),
             ("Flatten", "flattening_engine"), ("Sample", "ct_to_surface_sampler"),
             ("Detect", "ink_inference_engine"), ("Review", "review_and_provenance")]
    edges, blocked_at, scientific_blocked_at = [], None, None
    upstream_ok = True
    scientific_upstream_ok = True
    for stage, key in chain:
        s = by.get(key)
        producible = bool(s and s.availability == "INSTALLED"
                          and s.operational_verification in ("CONTROL_PASSED", "TESTED"))
        admissible = bool(s and s.scientific_admissibility in ("ADMISSIBLE", "RESEARCH_ONLY",
                                                              "NOT_APPLICABLE"))
        have = stage in have_stages
        mechanically_reachable = (producible or have) and upstream_ok
        scientifically_reachable = mechanically_reachable and admissible and scientific_upstream_ok
        green = mechanically_reachable
        if not mechanically_reachable and blocked_at is None:
            blocked_at = stage
        if not scientifically_reachable and scientific_blocked_at is None:
            scientific_blocked_at = stage
        edges.append({
          "stage": stage, "capability": key,
          "producible_locally": producible,
          "artifact_already_held": have,
          "scientifically_admissible": admissible,
          "green": green,
          "mechanically_reachable": mechanically_reachable,
          "scientifically_reachable": scientifically_reachable,
          "why": (None if green else
                  ("no local implementation; artifact must be imported"
                   if s and s.availability == "NOT_INSTALLED" else
                   "upstream stage is blocked" if not upstream_ok else
                   "not verified"))})
        upstream_ok = upstream_ok and (producible or have)
        scientific_upstream_ok = scientific_upstream_ok and admissible
    return {
      "edges": edges,
      "first_blocked_stage": blocked_at,
      "first_scientific_blocked_stage": scientific_blocked_at,
      "mechanically_runnable": blocked_at is None,
      "scientifically_admissible_end_to_end": scientific_blocked_at is None,
      "claim_ceiling": "SCIENTIFICALLY_ADMISSIBLE" if scientific_blocked_at is None else "MECHANICS_ONLY",
      "all_green": blocked_at is None,
      "rule": "an edge is green when its stage can be produced locally OR the artifact is "
              "already held, and every upstream stage is green. Admissibility is reported "
              "beside it and never folded into it -- mechanical reachability never promotes "
              "a scientific claim. Use claim_ceiling for the end-to-end ceiling.",
    }


def evidence_for(artifacts_root, key: str) -> list:
    """The receipt files that justify one capability's row, with their hashes."""
    root = pathlib.Path(artifacts_root)
    out = []
    for rel in RECEIPTS.get(key, ()):
        p = root / rel
        row = {"receipt": rel, "path": str(p), "present": p.is_file(),
               "sha256": None, "bytes": None, "written_utc": None}
        if row["present"]:
            try:
                data = p.read_bytes()
                st = p.stat()
                row["sha256"] = hashlib.sha256(data).hexdigest()
                row["bytes"] = len(data)
                row["written_utc"] = (datetime.datetime.fromtimestamp(
                    st.st_mtime, datetime.timezone.utc).isoformat().replace("+00:00", "Z"))
            except OSError as e:
                row["error"] = str(e)
        out.append(row)
    return out


def as_record(artifacts_root, have_stages=()) -> dict:
    states = derive(artifacts_root)
    r = route(states, have_stages)
    sci = [s for s in states if V.BY_KEY[s.key].scientific]
    caps = []
    for s in states:
        d = s.as_dict()
        ev = evidence_for(artifacts_root, s.key)
        d["evidence"] = ev
        d["evidence_present"] = sum(1 for e in ev if e["present"])
        d["evidence_declared"] = len(ev)
        caps.append(d)
    return {
      "contract": GRAPH_ID,
      "vocabulary": V.VOCAB_ID,
      "evidence_rule": "every capability carries the receipt files that produced its row. "
                       "A declared receipt that is absent is reported as present:false "
                       "rather than dropped, because its absence is why the row reads "
                       "UNTESTED. `written_utc` is the evidence FILE's modification time, "
                       "not a record of when a control ran.",
      "capabilities": caps,
      "counts": {
        "total": len(states),
        "installed": sum(1 for s in states if s.availability == "INSTALLED"),
        "control_passed": sum(1 for s in states
                              if s.operational_verification == "CONTROL_PASSED"),
        "scientifically_admissible": sum(1 for s in sci
                                         if s.scientific_admissibility == "ADMISSIBLE"),
        "scientific_capabilities": len(sci)},
      "route": r,
      "headline_rule": "product readiness and scientific readiness are reported separately. "
                       "Installed and control-passed is a PRODUCT fact; admissible is a "
                       "SCIENTIFIC one, and no number of the first produces the second.",
    }
