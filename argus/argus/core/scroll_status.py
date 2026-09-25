"""ONE per-scroll status: the eight questions, the sixteen journey steps, ONE next action, ONE blocker."""
from __future__ import annotations

import time
from urllib.parse import quote

from argus.core import journey, process_contract, scroll_ids, stage_lineage

SCHEMA = "argus-scroll-status-v1"

QUESTIONS = ("eligibility", "acquisition", "local", "geometry", "detector", "result", "next",
             "evidence")
STATES = ("DONE", "AVAILABLE", "HUMAN_GATED", "BLOCKED", "NOT_REACHED", "UNKNOWN")
BLOCK_CODES = ("DATA_UNAVAILABLE", "CODE_MISSING", "LICENCE", "EXPOSURE", "RUNTIME",
               "NOT_AUTHORIZED")
_OPEN = ("AVAILABLE", "HUMAN_GATED", "BLOCKED")
_SCIENCE_CACHE: tuple[float, dict[str, dict]] | None = None

_PRIZE_WORD = {"FIRST_LETTERS": "First Letters", "GRAND_PRIZE_2027": "Grand Prize"}
_STAGE_WORD = {
    "NONE": "nothing held, nothing published", "SCAN_HELD": "a CT region is held",
    "GEOMETRY": "surface traced", "RENDER": "surface rendered", "LABELS": "ink labels indexed",
    "RESULT": "a result exists",
}
_FURTHEST_ORDER = ("NONE", "SCAN_HELD", "GEOMETRY", "RENDER", "LABELS", "RESULT")


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bytes_text(value: int) -> str:
    """A compact measured-size phrase for the human route explanation."""
    if value >= 1024 ** 3:
        return "%.1f GiB" % (value / (1024 ** 3))
    if value >= 1024 ** 2:
        return "%.1f MiB" % (value / (1024 ** 2))
    return "%d bytes" % value


def _files_text(value: int) -> str:
    return ("%s catalogued file%s" % (format(value, ","), "" if value == 1 else "s")
            if value else "an unrecorded number of files")


def _answer(answer, basis, *, tone="idle", detail="", to=None) -> dict:
    return {"answer": answer, "basis": list(basis), "refusal": None, "tone": tone,
            "detail": detail, "to": to}


def _refused(code, why, basis) -> dict:
    return {"answer": None, "basis": list(basis), "refusal": {"code": code, "why": why},
            "tone": "unknown", "detail": why, "to": None}


def _step(spec, state, why, basis, *, code=None, receipt=None, label=None) -> dict:
    if state not in STATES:
        raise ValueError("unknown step state %r" % state)
    if state == "BLOCKED" and code not in BLOCK_CODES:
        raise ValueError("a BLOCKED step must name one of %s, not %r" % (BLOCK_CODES, code))
    return {"n": spec.n, "id": spec.id, "label": label or spec.label, "screen": spec.screen,
            "state": state, "code": code, "why": why, "basis": list(basis), "receipt": receipt,
            "optional": spec.optional, "evidence_role": spec.evidence_role}


def _refusal_status(requested, code, why, *, canonical=None) -> dict:
    basis = ["argus.core.scroll_ids.resolve"]
    return {
        "schema": SCHEMA, "status": "REFUSED", "scroll": canonical, "requested": requested,
        "display": canonical or requested,
        "refusal": {"code": code, "why": why},
        "questions": {q: _refused(code, why, basis) for q in QUESTIONS},
        "steps": [],
        "next_action": {"step": "select_scroll", "n": 1, "label": "Choose a registered scroll",
                        "kind": "ACT", "to": "/explore?tab=scrolls#explore.search", "why": why, "operator_only": False,
                        "action_ids": [], "basis": basis},
        "blocker": {"step": "select_scroll", "code": code, "text": why, "basis": basis},
        "contradictions": [], "lineage": None, "generated_utc": _utc(), "read_only": True,
        "claim_ceiling": "nothing was selected, run or read for this id",
    }


def _load_shelf(shelf):
    if shelf is not None:
        return shelf
    from argus.core import scroll_shelf
    return scroll_shelf.compose_available()


def _index_rows() -> dict | None:
    try:
        from argus.core import scroll_index
        return {r["scroll"]: r for r in (scroll_index.summary().get("scrolls") or []) if r.get("scroll")}
    except Exception:
        return None


def _effort_rows() -> dict | None:
    try:
        from argus.core import scroll_effort
        return {r["scroll"]: r for r in (scroll_effort.cached().get("rows") or []) if r.get("scroll")}
    except Exception:
        return None


def _surface_facts(canon) -> dict | None:
    try:
        from argus.core import surface_status
        return surface_status.resolved(canon)
    except Exception:
        return None


def _science_artifacts(canon) -> dict:
    """Summarise verified science outputs without promoting them into route stages."""
    empty = {"state": "NONE", "physical_scroll": canon, "source": None,
             "exact_volume": None, "pieces": 0, "geometry_pass": 0, "geometry_uncertain": 0,
             "review_priority": 0, "viewable_renders": 0, "claim_ceiling": None,
             "records": 0, "renders": 0, "detector_run": False, "presented_as_ink": False,
             "to": "/workbench/a4b?scroll=%s" % quote(canon, safe="")}

    def discovered_only() -> dict:
        """Expose local science material without promoting it to an imported result."""
        try:
            from argus.core import local_discovery
            assets = [a for a in local_discovery.scan().get("assets", [])
                      if a.get("physical_scroll") == canon]
        except Exception:
            return empty
        records = sum(1 for a in assets if a.get("kind") == "SCIENCE_RECORD")
        renders = sum(1 for a in assets if a.get("kind") == "RENDER")
        if not records and not renders:
            return empty
        return {**empty, "state": "DISCOVERED_NOT_ATTACHED", "records": records, "renders": renders,
                "viewable_renders": sum(1 for a in assets if a.get("kind") == "RENDER" and a.get("viewable")),
                "claim_ceiling": "discovered local material only; not imported, served or scientifically promoted",
                "to": "/sources?tab=discovered&scroll=%s" % quote(canon, safe=""),
                "detail": ("Local identity-matched science material was found, but no verified ARGUS "
                           "integration manifest attaches it to this release. Review Sources to reconcile it.")}
    global _SCIENCE_CACHE
    now = time.monotonic()
    if _SCIENCE_CACHE is not None and now - _SCIENCE_CACHE[0] < 20 and canon in _SCIENCE_CACHE[1]:
        return _SCIENCE_CACHE[1][canon]
    try:
        from argus.core import science_attach
        view = science_attach.closeout_view()
    except Exception as exc:
        value = discovered_only()
        if value["state"] == "NONE":
            value = {**empty, "state": "UNAVAILABLE", "detail": "science closeout could not be read: %s" % exc}
        previous = _SCIENCE_CACHE[1] if _SCIENCE_CACHE and now - _SCIENCE_CACHE[0] < 20 else {}
        _SCIENCE_CACHE = (now, {**previous, canon: value})
        return value
    native = view.get("native") or {}
    if native.get("physical_scroll") != canon:
        value = discovered_only()
        previous = _SCIENCE_CACHE[1] if _SCIENCE_CACHE and now - _SCIENCE_CACHE[0] < 20 else {}
        _SCIENCE_CACHE = (now, {**previous, canon: value})
        return value
    imp = (view.get("imports") or {}).get("closeout") or {}
    if not imp.get("verified"):
        value = {**empty, "state": "IMPORT_INVALID", "source": imp.get("id"),
                 "detail": "; ".join(imp.get("problems") or ["import receipt did not verify"])}
        previous = _SCIENCE_CACHE[1] if _SCIENCE_CACHE and now - _SCIENCE_CACHE[0] < 20 else {}
        _SCIENCE_CACHE = (now, {**previous, canon: value})
        return value
    pieces = native.get("pieces") or []
    mounted = sum(1 for p in pieces
                  if (p.get("private_render") or {}).get("status") == "MOUNTED_VERIFIED")
    counts = native.get("counts") or {}
    value = {
        "state": "VERIFIED_LOCAL_OUTPUTS" if mounted else "VERIFIED_MANIFEST_NOT_MOUNTED",
        "physical_scroll": canon,
        "source": imp.get("id"),
        "source_commit": imp.get("source_commit"),
        "exact_volume": native.get("exact_volume"),
        "operation_class": native.get("operation_class"),
        "pieces": int(counts.get("pieces") or len(pieces)),
        "geometry_pass": int(counts.get("pass") or 0),
        "geometry_uncertain": int(counts.get("uncertain") or 0),
        "review_priority": int(native.get("review_priority_total") or 0),
        "viewable_renders": mounted,
        "claim_ceiling": native.get("claim_ceiling"),
        "detector_run": bool(native.get("detector_run")),
        "presented_as_ink": bool(native.get("presented_as_ink")),
        "to": "/workbench/a4b?scroll=%s" % quote(canon, safe=""),
        "detail": ("Verified native-pitch geometry/render outputs are available. The route still "
                   "controls CT acquisition and no output is promoted to ink."),
    }
    previous = _SCIENCE_CACHE[1] if _SCIENCE_CACHE and now - _SCIENCE_CACHE[0] < 20 else {}
    _SCIENCE_CACHE = (now, {**previous, canon: value})
    return value


def _exposure(canon) -> dict | None:
    try:
        from argus.core import exposure
        return exposure.target_exposure(canon)
    except Exception:
        return None


def _control_roles(canon) -> list:
    from argus.core import control_matrix
    return sorted(role for role, bound in control_matrix.ROLE_BOUND_SCROLL.items() if bound == canon)


REGISTRY_TO = "/system?tab=updates#target-registry"


def _to(canon, spec, state, *, fence, prize) -> str:
    q = "scroll=%s" % quote(canon, safe="")
    if spec.id == "select_scroll":
        return "/explore?tab=scrolls&%s#explore.search" % q
    if fence:
        return "/review?%s" % q
    if spec.id == "topology":
        return "/workbench?%s&panel=inspector&focus=blender" % q
    if spec.id == "ct_inspection":
        return "/workbench?%s&view=rawvolume" % q
    if spec.id in ("surface_prediction", "tracing"):
        return "/workbench?%s&view=segmentation" % q
    if spec.id == "normal_orientation":
        return "/workbench?%s&view=windings&panel=inspector&focus=windings" % q
    if spec.id == "flatten_render":
        return "/workbench?%s&view=surface" % q
    if spec.id == "fibers":
        return "/workbench?%s&view=fibers" % q
    if spec.id == "ink_inference":
        return "/workbench?%s&view=ink" % q
    if spec.id == "acquisition":
        return "/sources?tab=ingest&%s#sources.retrieval" % q
    if spec.id == "exact_identity":
        return "/explore?tab=scrolls&detail=%s&%s" % (quote(canon, safe=""), q)
    if spec.id == "candidate_review":
        return "/review?%s#review.queue" % q
    if spec.id == "transcription":
        return "/review?%s#review.interpretation.transcription" % q
    if spec.id == "translation":
        return "/review?%s#review.interpretation.translation" % q
    if spec.id == "evidence_comparison":
        return "/evidence?%s#evidence.gallery" % q
    if spec.id == "export_packet":
        return "/evidence?%s#evidence.packets" % q
    return "%s?%s" % (spec.screen, q)


class _Facts:
    """Everything the steps and questions read, gathered once per scroll."""

    def __init__(self, canon, readiness, lineage, index_row, effort_row, surface, exposure_rec,
                 acquisition_receipt, index_read=True, target_registry=None):
        m = readiness.get("material") or {}
        self.canon = canon
        self.readiness = readiness
        self.material = m
        self.lineage = lineage
        self.index_row = index_row
        self.index_read = index_read
        self.effort_row = effort_row
        self.surface = surface
        self.exposure = exposure_rec
        self.acq_receipt = acquisition_receipt
        registry = target_registry or {}
        self.target_registry_available = registry.get("available") is not False
        self.target_registry_why = registry.get("why")
        self.target_registry_next = registry.get("next_action") or {}
        self.products = {p["product"]: p for p in readiness.get("products") or []}
        self.prizes = list(m.get("prizes") or [])
        self.prize = bool(self.prizes)
        self.fence = m.get("operator_fence")
        self.local_data = m.get("local_data") or "NONE"
        self.furthest = m.get("furthest_stage") or "NONE"
        self.published = bool(m.get("published_upstream"))
        self.labelled = bool(m.get("labelled"))
        self.held = self.local_data == "HELD"
        self.attested = m.get("attested_store") or None
        idx = index_row or {}
        self.indexed_material = bool(int(idx.get("cached_ct_regions") or 0)
                                     or int(idx.get("physical_segments") or 0))
        self.catalogued_bytes = int(((effort_row or {}).get("holdings") or {}).get("bytes") or 0)
        self.catalogued_files = int(((effort_row or {}).get("holdings") or {}).get("files") or 0)
        self.material_here = self.held or self.local_data == "PARTIAL" or self.indexed_material
        rank = _FURTHEST_ORDER.index(self.furthest) if self.furthest in _FURTHEST_ORDER else 0
        self.has_surface = rank >= _FURTHEST_ORDER.index("GEOMETRY")
        self.has_render = rank >= _FURTHEST_ORDER.index("RENDER")
        self.has_result = rank >= _FURTHEST_ORDER.index("RESULT")
        self.roles = _control_roles(canon)
        self.title_lane = "PARIS4_TITLE_CONTROL" in self.roles

    def lin(self, stage_id):
        cell = ((self.lineage or {}).get("stages") or {}).get(stage_id) or {}
        return cell.get("latest")


def _steps(f: _Facts) -> list[dict]:
    S = {s.id: s for s in journey.STEPS}
    out: dict[str, dict] = {}
    prod = f.products

    out["select_scroll"] = _step(S["select_scroll"], "DONE", "%s is selected" % f.canon,
                                 ["argus.core.scroll_ids.resolve"])

    confusable = sorted(c for c in scroll_ids.CANONICAL if scroll_ids.is_confusable(f.canon, c))
    conf_note = (" Easily confused with %s; check the id before acting." % ", ".join(confusable)
                 if confusable else "")
    scan_ids = [a.get("scan_id") for a in f.material.get("acquisitions") or [] if a.get("scan_id")]
    bound = ("target registry unavailable, so the official acquisition is UNKNOWN" if not f.target_registry_available else
             "bound to declared acquisition(s) %s" % ", ".join(scan_ids) if scan_ids else
             "no acquisition is declared, so the acquisition is UNKNOWN (ARGUS never infers one "
             "from a file name)")
    out["exact_identity"] = _step(
        S["exact_identity"], "DONE",
        "%s resolved exactly, with no alias or nearest match; %s.%s" % (f.canon, bound, conf_note),
        ["argus.core.scroll_ids.resolve", "argus.core.scroll_shelf acquisitions"])

    acq = f.acq_receipt or {}
    raw_lin = f.lin("raw_ct")
    if f.fence:
        out["acquisition"] = _step(S["acquisition"], "BLOCKED", "operator fence: %s" % f.fence,
                                   ["argus.core.scroll_shelf operator_fence"], code="NOT_AUTHORIZED")
    elif f.held:
        out["acquisition"] = _step(S["acquisition"], "DONE",
                                   "identity-bound bytes are held in a sealed local store",
                                   ["argus.core.scroll_shelf local_data=HELD"])
    elif f.attested and not f.material_here:
        out["acquisition"] = _step(
            S["acquisition"], "AVAILABLE",
            "an operator-ATTESTED store for this scroll is on this machine (attested by %s). It is "
            "not sealed and not verified, so it is not counted as a sealed holding: seal it "
            "through a receipted acquisition, or verify its identity, before anything downstream "
            "relies on it" % ", ".join(f.attested.get("attested_by") or ["an unnamed operator"]),
            ["argus.core.scroll_shelf attested_store", "argus.core.store_identity holding_state"],
            label="Seal or verify the attested store")
    elif f.material_here:
        out["acquisition"] = _step(
            S["acquisition"], "DONE",
            "bounded holdings only: cached CT regions or traced segments are indexed here; the "
            "full volume is not held", ["argus.core.scroll_index", "argus.core.scroll_shelf"])
    elif f.catalogued_bytes:
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED",
            "%s across %s is already on this computer. It includes CT cache pieces and derived "
            "outputs, so existing renders can remain viewable, but the older files do not carry one "
            "receipt proving which exact CT pieces are present. Verify and reuse the matching pieces "
            "first; retrieve only a required gap, then seal that bounded input" %
            (_bytes_text(f.catalogued_bytes), _files_text(f.catalogued_files)),
            ["argus.core.scroll_effort holdings", "argus.core.acquisition_identity"],
            code="DATA_UNAVAILABLE", label="Verify the CT pieces already here")
    elif acq.get("state") == "NOT_AUTHORISED":
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED",
            "the recorded acquisition packet was evaluated and refused (failed gates: %s); no "
            "fetch occurred" % ", ".join(acq.get("failed_gates") or ["unspecified"]),
            ["argus.core.process_contract._acquisition_receipt"], code="NOT_AUTHORIZED",
            receipt=acq.get("receipt"))
    elif raw_lin and raw_lin.get("outcome") in ("REFUSED", "BLOCKED"):
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED", raw_lin.get("detail") or "the last raw_ct attempt did not complete",
            ["argus.core.stage_lineage raw_ct"], code="NOT_AUTHORIZED",
            receipt=raw_lin.get("receipt_path"))
    elif not f.target_registry_available:
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED",
            "%s ARGUS cannot determine the official acquisition or whether CT is published until "
            "the provenance-bound registry is installed." %
            (f.target_registry_why or "The official target registry is unavailable."),
            ["argus.core.scroll_shelf target_registry"], code="DATA_UNAVAILABLE",
            label=f.target_registry_next.get("label") or "Install or refresh the target registry")
    elif f.published and f.prize:
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED",
            "the volume is published upstream, but a prize target's retrieval needs a passing "
            "identity, ROI, byte-ceiling and control packet and none has passed (packet state: %s)"
            % acq.get("state", "PACKET_REQUIRED"),
            ["argus.core.process_contract._acquisition_receipt"], code="NOT_AUTHORIZED")
    elif f.published:
        out["acquisition"] = _step(
            S["acquisition"], "AVAILABLE",
            "an official acquisition is published; a bounded, zero-fetch acquisition plan can be "
            "opened", ["argus.core.scroll_shelf published_upstream"])
    else:
        out["acquisition"] = _step(
            S["acquisition"], "BLOCKED",
            "nothing is published upstream and nothing is held, so there is no CT to acquire",
            ["argus.core.scroll_shelf"], code="DATA_UNAVAILABLE")

    if f.material_here:
        out["ct_inspection"] = _step(
            S["ct_inspection"], "AVAILABLE",
            "read-only volume and plane viewers can open the CT held for this scroll",
            ["argus.core.process_contract ct_inspection"])
    else:
        out["ct_inspection"] = _step(S["ct_inspection"], "NOT_REACHED",
                                     "no sealed identity-bound CT input is available for this scroll yet",
                                     ["argus.core.scroll_shelf"])

    sp = prod.get("surface_prediction") or {}
    if sp.get("state") == "READY":
        out["surface_prediction"] = _step(S["surface_prediction"], "DONE", sp.get("why", ""),
                                          ["argus.core.material_readiness surface_prediction"])
    elif sp.get("state") == "REMOTE_ONLY":
        out["surface_prediction"] = _step(
            S["surface_prediction"], "AVAILABLE", sp.get("why", ""),
            ["argus.core.material_readiness surface_prediction"])
    elif f.material_here and sp.get("state") == "PRODUCIBLE":
        out["surface_prediction"] = _step(
            S["surface_prediction"], "AVAILABLE", sp.get("why", ""),
            ["argus.core.material_readiness surface_prediction"])
    elif not f.material_here:
        out["surface_prediction"] = _step(S["surface_prediction"], "NOT_REACHED",
                                          "needs the CT first: %s" % sp.get("why", "no CT is held"),
                                          ["argus.core.material_readiness surface_prediction"])
    else:
        out["surface_prediction"] = _step(
            S["surface_prediction"], "BLOCKED", sp.get("why", "no provider is registered"),
            ["argus.core.material_readiness surface_prediction"], code="CODE_MISSING")

    mesh = prod.get("surface_mesh") or {}
    if f.has_surface:
        out["tracing"] = _step(S["tracing"], "DONE",
                               "registered geometry exists (%s)" % _STAGE_WORD[f.furthest],
                               ["argus.core.scroll_shelf furthest_stage", "argus.core.material_readiness"])
    elif f.material_here:
        out["tracing"] = _step(S["tracing"], "AVAILABLE", mesh.get("why", "geometry can be produced"),
                               ["argus.core.material_readiness surface_mesh"])
    else:
        out["tracing"] = _step(S["tracing"], "NOT_REACHED",
                               "needs the CT first: %s" % mesh.get("why", "no CT is held"),
                               ["argus.core.material_readiness surface_mesh"])

    topo = f.lin("topology_repair")
    if not f.has_surface:
        out["topology"] = _step(S["topology"], "NOT_REACHED", "no surface geometry exists yet",
                                ["argus.core.scroll_shelf furthest_stage"])
    elif topo and topo.get("outcome") == "COMPLETED":
        out["topology"] = _step(S["topology"], "DONE", topo.get("detail") or "topology repair completed",
                                ["argus.core.stage_lineage topology_repair"],
                                receipt=topo.get("receipt_path"))
    else:
        out["topology"] = _step(
            S["topology"], "HUMAN_GATED",
            (topo.get("detail") if topo else
             "no topology decision is recorded; the topology metric is a diagnostic and repair is "
             "a human act"),
            ["argus.core.stage_lineage topology_repair", "argus.core.topology_metric"],
            receipt=(topo or {}).get("receipt_path"))

    if not f.has_surface:
        out["normal_orientation"] = _step(S["normal_orientation"], "NOT_REACHED",
                                          "no surface geometry exists yet",
                                          ["argus.core.scroll_shelf furthest_stage"])
    else:
        out["normal_orientation"] = _step(
            S["normal_orientation"], "HUMAN_GATED",
            "%s: no independent orientation evidence (a human verification or a photometric or "
            "fibre-direction cue) is recorded for this scroll, and a sign from centre-direction "
            "geometry alone stays unconfirmed whatever its confidence"
            % "ORIENTATION_UNCONFIRMED",
            ["argus.core.signed_normal orientation_status"])

    flat = prod.get("flattened_surface") or {}
    if f.has_render:
        out["flatten_render"] = _step(S["flatten_render"], "DONE",
                                      "a registered flat or rendered product exists",
                                      ["argus.core.scroll_shelf filters.has_render"])
    elif f.has_surface:
        out["flatten_render"] = _step(
            S["flatten_render"], "AVAILABLE",
            flat.get("why", "an accepted mesh is required before flattening")
            + "; topology must be decided first" if out["topology"]["state"] != "DONE" else
            flat.get("why", "flattening can run"),
            ["argus.core.material_readiness flattened_surface"])
    else:
        out["flatten_render"] = _step(S["flatten_render"], "NOT_REACHED",
                                      "an accepted mesh is required before flattening",
                                      ["argus.core.material_readiness flattened_surface"])

    fibers_state = next((s["state"] for s in process_contract.derived_stages() if s["id"] == "fibers"),
                        "UNKNOWN")
    fiber_products = [prod.get(k) for k in ("fiber_prediction", "fiber_tracks") if prod.get(k)]
    if fiber_products and all(p["state"] == "READY" for p in fiber_products):
        out["fibers"] = _step(S["fibers"], "DONE", "identity-bound fiber products are registered",
                              ["argus.core.material_readiness fiber_prediction, fiber_tracks"])
    elif not f.material_here:
        out["fibers"] = _step(S["fibers"], "NOT_REACHED", "fibers are recovered from CT; none is held",
                              ["argus.core.scroll_shelf"])
    else:
        why = "; ".join("%s: %s" % (p["product"], p["why"]) for p in fiber_products
                        if p["state"] != "READY") or "no fiber product is registered"
        out["fibers"] = _step(
            S["fibers"], "BLOCKED", why,
            ["argus.core.material_readiness fiber products", "argus.core.provider_registry"],
            code="CODE_MISSING" if fibers_state == "MISSING_PROVIDER" else "DATA_UNAVAILABLE")

    ink_lin = f.lin("ink_2d") or f.lin("ink_3d")
    ink_done = bool(ink_lin and ink_lin.get("outcome") == "COMPLETED")
    has_result = f.has_result or ink_done
    run_scored = ink_done and (ink_lin.get("recorded_by") or {}).get("tool") == "argus run"
    if not f.has_render and not ink_done:
        out["ink_inference"] = _step(S["ink_inference"], "NOT_REACHED",
                                     "ink inference needs a rendered surface",
                                     ["argus.core.scroll_shelf filters.has_render"])
    elif f.has_result or ink_done:
        out["ink_inference"] = _step(
            S["ink_inference"], "DONE",
            "a result exists; it is operational evidence from an unqualified detector, not a reading",
            ["argus.core.scroll_shelf furthest_stage", "argus.core.stage_lineage ink_2d, ink_3d"])
    elif f.prize:
        out["ink_inference"] = _step(
            S["ink_inference"], "BLOCKED",
            "any detector run on a prize target is operator-only while no detector is qualified",
            ["argus.core.material_readiness detectors"], code="NOT_AUTHORIZED")
    else:
        out["ink_inference"] = _step(
            S["ink_inference"], "AVAILABLE",
            "an unqualified detector can run through the governed runner; its output is evidence, "
            "not a reading", ["argus.core.material_readiness detectors"])

    kinds = sorted({k for k in (_kind_for(r) for r in f.roles) if k})
    if not has_result:
        held_bits = [b for b in (("control role %s" % ", ".join(f.roles)) if f.roles else "",
                                 "ink labels" if f.labelled else "") if b]
        out["evidence_comparison"] = _step(
            S["evidence_comparison"], "NOT_REACHED",
            "there is no inference result to compare yet" +
            ("; held for comparison: %s" % ", ".join(held_bits) if held_bits else ""),
            ["argus.core.control_evidence_kinds", "argus.core.control_matrix"])
    elif kinds or f.labelled or run_scored:
        out["evidence_comparison"] = _step(
            S["evidence_comparison"], "AVAILABLE",
            "comparison evidence: %s. None is independent physical ground truth."
            % ", ".join(kinds + (["ink labels"] if f.labelled else [])
                        + (["the published labels the run scored against (see its receipt)"] if run_scored and not f.labelled else [])),
            ["argus.core.control_evidence_kinds"])
    else:
        out["evidence_comparison"] = _step(
            S["evidence_comparison"], "BLOCKED",
            "no control evidence or labels are registered for this scroll",
            ["argus.core.control_evidence_kinds"], code="DATA_UNAVAILABLE")

    rev = f.lin("review")
    if rev and rev.get("outcome") == "COMPLETED":
        out["candidate_review"] = _step(S["candidate_review"], "DONE", rev.get("detail") or "review completed",
                                        ["argus.core.stage_lineage review"], receipt=rev.get("receipt_path"))
    elif has_result:
        out["candidate_review"] = _step(S["candidate_review"], "HUMAN_GATED",
                                        "candidates from a result await independent human review",
                                        ["argus.core.scroll_shelf furthest_stage"])
    else:
        out["candidate_review"] = _step(S["candidate_review"], "NOT_REACHED",
                                        "there are no candidates to review yet",
                                        ["argus.core.scroll_shelf furthest_stage"])

    for sid, upstream in (("transcription", "candidate_review"), ("translation", "transcription")):
        cell = f.lin(sid)
        if cell and cell.get("outcome") == "COMPLETED":
            out[sid] = _step(S[sid], "DONE", cell.get("detail") or "completed",
                             ["argus.core.stage_lineage %s" % sid], receipt=cell.get("receipt_path"))
        elif out[upstream]["state"] == "DONE":
            out[sid] = _step(S[sid], "HUMAN_GATED",
                             "%s is a human act over independently accepted input" % S[sid].label.lower(),
                             ["argus.core.stage_lineage %s" % sid])
        else:
            out[sid] = _step(S[sid], "NOT_REACHED", "needs %s first" % S[upstream].label.lower(),
                             ["argus.core.stage_lineage %s" % sid])

    pack = f.lin("evidence")
    if pack and pack.get("outcome") == "COMPLETED":
        out["export_packet"] = _step(S["export_packet"], "DONE", pack.get("detail") or "packet assembled",
                                     ["argus.core.stage_lineage evidence"], receipt=pack.get("receipt_path"))
    elif f.has_surface:
        out["export_packet"] = _step(
            S["export_packet"], "AVAILABLE",
            "the package builder assembles what receipts support and refuses unsupported claims; "
            "it does not submit", ["argus.core.evidence_package"])
    else:
        out["export_packet"] = _step(S["export_packet"], "NOT_REACHED",
                                     "there is no geometry or result to package yet",
                                     ["argus.core.scroll_shelf furthest_stage"])
    return [out[sid] for sid in journey.step_ids()]


def _kind_for(role):
    from argus.core import control_evidence_kinds
    return control_evidence_kinds.kind_for_role(role)


def _next_and_blocker(f: _Facts, steps: list[dict]):
    spec_by_id = {s.id: s for s in journey.STEPS}
    pick = next((s for s in steps if s["state"] in _OPEN and not s["optional"]), None)
    if pick is None:
        pick = next((s for s in steps if s["state"] != "DONE"), None)
    if pick is None:
        nxt = {"step": None, "n": None, "label": "Review the finished packet", "kind": "ACT",
               "to": "/evidence?scroll=%s#evidence.packets" % quote(f.canon, safe=""), "why": "every journey step is done",
               "operator_only": False, "action_ids": [], "basis": ["argus.core.scroll_status"]}
        return nxt, None
    spec = spec_by_id[pick["id"]]
    kind = {"AVAILABLE": "ACT", "HUMAN_GATED": "DECIDE", "BLOCKED": "RESOLVE_BLOCKER"}.get(
        pick["state"], "WAIT")
    operator_only = (pick["code"] == "NOT_AUTHORIZED"
                     or (f.prize and pick["id"] in ("acquisition", "ink_inference"))
                     or f.title_lane)
    to = (_to(f.canon, spec, pick["state"], fence=f.fence, prize=f.prize)
          if f.target_registry_available or pick["id"] != "acquisition"
          else REGISTRY_TO)
    nxt = {"step": pick["id"], "n": pick["n"], "label": pick["label"], "kind": kind,
           "to": to,
           "why": pick["why"], "operator_only": bool(operator_only),
           "action_ids": list(spec.actions), "basis": pick["basis"]}
    at = steps.index(pick)
    ahead = next((s for s in steps[at:] if s["state"] == "BLOCKED" and (s is pick or not s["optional"])),
                 None)
    blocker = ({"step": ahead["id"], "code": ahead["code"], "text": ahead["why"],
                "at_next_step": ahead is pick, "basis": ahead["basis"]} if ahead else None)
    return nxt, blocker


def _questions(f: _Facts, steps: list[dict], nxt: dict) -> dict:
    q = {}

    prizes = [_PRIZE_WORD.get(p, p.replace("_", " ").title()) for p in f.prizes]
    if f.title_lane:
        prizes.append("Paris 4 title prize")
    exp = f.exposure or {}
    exp_note = (" Exposure registry: %s." % exp["contamination_banner"]
                if exp.get("contamination_banner") else "")
    if not f.target_registry_available:
        registry_why = f.target_registry_why or "The official target registry is unavailable."
        q["eligibility"] = _refused(
            "DATA_UNAVAILABLE",
            registry_why if "eligibility is unknown" in registry_why.lower()
            else "%s Eligibility is unknown, not empty." % registry_why,
            ["argus.core.scroll_shelf target_registry"])
    else:
        q["eligibility"] = _answer(
        " · ".join(prizes) if prizes else "not eligible (control / development)",
        ["argus.core.scroll_shelf prizes", "argus.core.exposure.target_exposure",
         "argus.core.control_matrix.ROLE_BOUND_SCROLL"],
        tone="warn" if prizes else "idle",
        detail=("On the published prize list(s) named. Eligibility is not progress."
                if prizes else
                "Not on any prize list. Used to measure the instrument, never to claim a reading.")
        + exp_note + (" Operator fence: %s." % f.fence if f.fence else ""))

    acqs = [a for a in f.material.get("acquisitions") or [] if a.get("scan_id")]
    if not f.target_registry_available:
        q["acquisition"] = _refused(
            "DATA_UNAVAILABLE",
            "%s The official scan, volume and publication status are unknown." %
            (f.target_registry_why or "The official target registry is unavailable."),
            ["argus.core.scroll_shelf target_registry"])
    elif not acqs:
        q["acquisition"] = _refused(
            "DATA_UNAVAILABLE",
            "no acquisition is declared for this scroll; ARGUS never infers one from a file name",
            ["argus.core.scroll_shelf acquisitions"])
    else:
        a = acqs[0]
        more = " (+%d more)" % (len(acqs) - 1) if len(acqs) > 1 else ""
        q["acquisition"] = _answer("%s · %s%s" % (a["scan_id"], a.get("family") or "pitch/energy UNKNOWN", more),
                                   ["argus.core.scroll_shelf acquisitions"],
                                   detail="Declared by the prize registry; store state %s."
                                   % (a.get("store_state") or "unknown"))

    if f.held:
        local = _answer("held, sealed store", ["argus.core.scroll_shelf local_data=HELD"], tone="ok",
                        detail="A sealed store on this machine binds this scroll's identity.")
    elif f.attested and not f.material_here:
        local = _answer("attested store held, not sealed",
                        ["argus.core.scroll_shelf attested_store"], tone="warn",
                        detail="A store an operator attested by hand is on this machine. It is "
                               "not sealed and its identity is not verified.")
    elif f.material_here:
        local = _answer("partial: cached regions or segments indexed",
                        ["argus.core.scroll_index", "argus.core.scroll_shelf"], tone="ok",
                        detail="Some material is indexed here; no complete sealed volume is held.")
    elif f.catalogued_bytes:
        local = _answer("%s found here; CT coverage unverified" % _bytes_text(f.catalogued_bytes),
                        ["argus.core.scroll_effort holdings"],
                        tone="warn",
                        detail=("The dated inventory records %s across %s for this scroll. Existing "
                                "renders and derived outputs remain usable as their own artifacts. "
                                "The CT portion is older cache material without one receipt proving "
                                "the exact bounded input complete, so ARGUS must verify and reuse it "
                                "before retrieving any missing piece." %
                                (_bytes_text(f.catalogued_bytes), _files_text(f.catalogued_files))))
    else:
        local = _answer("none here", ["argus.core.scroll_shelf", "argus.core.scroll_index"],
                        detail="Nothing is held or indexed for this scroll on this machine.")
    q["local"] = local

    q["geometry"] = _answer(
        ("nothing held; upstream publication unknown" if f.furthest == "NONE" and not f.target_registry_available
         else "published upstream, not held here" if f.furthest == "NONE" and f.published
         else _STAGE_WORD.get(f.furthest, f.furthest)),
        ["argus.core.scroll_shelf furthest_stage", "argus.core.surface_status"],
        tone="ok" if f.has_surface else "idle",
        detail=_surface_detail(f))

    detectors = f.readiness.get("detectors") or []
    if not f.readiness.get("route"):
        q["detector"] = _refused("DATA_UNAVAILABLE", f.readiness.get("why") or "readiness was not composed",
                                 ["argus.core.material_readiness detectors"])
    else:
        counts = {}
        for d in detectors:
            counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
        qualified = counts.get("QUALIFIED", 0)
        labels = ("%d label representation(s)" % f.material.get("label_representations", 0)
                  if f.labelled else "no ink labels")
        q["detector"] = _answer(
            ("%d qualified detector(s)" % qualified) if qualified else "no qualified detector",
            ["argus.core.material_readiness detectors"], tone="ok" if qualified else "bad",
            detail="%d candidate detector(s) inventoried (%s); none is eligible for automatic "
                   "routing. Labels: %s." % (
                       len(detectors), ", ".join("%d %s" % (n, v.lower()) for v, n in sorted(counts.items())) or "none",
                       labels))

    idx = f.index_row
    if not f.index_read and not f.has_result:
        q["result"] = _refused("DATA_UNAVAILABLE", "the scroll index was not read, so a result may exist that "
                               "this status has not seen", ["argus.core.scroll_index"])
    else:
        done, partial = int((idx or {}).get("results_complete") or 0), int((idx or {}).get("results_partial") or 0)
        if f.has_result or done:
            q["result"] = _answer("%d complete result(s)" % max(done, 1), ["argus.core.scroll_index results_complete"],
                                  tone="warn", detail="A result is operational evidence from an unqualified "
                                  "detector. A certified stage is not a verified reading.")
        elif partial:
            q["result"] = _answer("%d partial result(s), none complete" % partial,
                                  ["argus.core.scroll_index results_partial"], detail="Partial runs are not results.")
        else:
            q["result"] = _answer("none recorded", ["argus.core.scroll_index"],
                                  detail="No run declares this scroll.")

    q["next"] = _answer(
        nxt["label"] + (" (operator only)" if nxt["operator_only"] else ""), nxt["basis"],
        tone="warn" if nxt["operator_only"] else "ok", detail=nxt["why"], to=nxt["to"])

    lin = f.lineage or {}
    counts = lin.get("counts") or {}
    receipts = [s["receipt"] for s in steps if s.get("receipt")]
    n_att = counts.get("attempted", 0)
    q["evidence"] = _answer(
        ("%d stage(s) attempted, chain %s" % (n_att, lin.get("chain_state"))) if n_att else "no stage attempt recorded",
        ["argus.core.stage_lineage", "argus.core.surface_status"], tone="ok" if n_att else "idle",
        detail=("%d receipt(s) cited by the journey steps. " % len(receipts) if receipts else "")
        + "Evidence opens for this scroll and says what is absent rather than borrowing another "
          "scroll's images.",
        to="/evidence?scroll=%s" % quote(f.canon, safe=""))
    return q


def _capability_lanes(f: _Facts, steps: list[dict], science: dict) -> list[dict]:
    """Collapse the sixteen mechanical journey steps into the parallel work a person can see."""
    by_id = {row["id"]: row for row in steps}
    spec_by_id = {s.id: s for s in journey.STEPS}
    groups = (
        ("acquisition", "Input", ("select_scroll", "exact_identity", "acquisition", "ct_inspection")),
        ("surface", "Surface", ("surface_prediction", "tracing", "topology", "normal_orientation", "flatten_render")),
        ("structure", "Structure", ("fibers", "ink_inference")),
        ("evidence", "Evidence", ("evidence_comparison", "candidate_review")),
        ("reading", "Reading", ("transcription", "translation", "export_packet")),
    )
    out = []
    for key, label, ids in groups:
        rows = [by_id[i] for i in ids if i in by_id]
        done = sum(r["state"] == "DONE" for r in rows)
        blocked = [r for r in rows if r["state"] == "BLOCKED"]
        open_rows = [r for r in rows if r["state"] in _OPEN]
        if key == "surface" and science.get("pieces"):
            if science.get("viewable_renders") or science.get("pieces"):
                state = "AVAILABLE"
                summary = "%d piece(s) found; route stages remain separately gated" % science["pieces"]
            else:
                state = "AVAILABLE"
                summary = "science outputs found; registration is still required"
        elif done == len(rows):
            state, summary = "DONE", "all recorded stages complete"
        elif done or open_rows:
            state = "AVAILABLE" if open_rows else "DONE"
            summary = "%d of %d stages complete" % (done, len(rows))
        elif blocked:
            state, summary = "BLOCKED", blocked[0]["why"]
        else:
            state, summary = "NOT_REACHED", "waiting for an earlier stage"
        next_row = open_rows[0] if open_rows else (blocked[0] if blocked else None)
        target = None
        if next_row and next_row["id"] in spec_by_id:
            target = (
                REGISTRY_TO
                if next_row["id"] == "acquisition" and not f.target_registry_available
                else _to(f.canon, spec_by_id[next_row["id"]], next_row["state"],
                         fence=f.fence, prize=f.prize)
            )
        out.append({
            "id": key, "label": label, "state": state, "done": done, "total": len(rows),
            "summary": summary, "next": next_row["label"] if next_row else None,
            "to": target,
            "basis": sorted({b for r in rows for b in r.get("basis", [])}),
        })
    return out


def _progress_summary(f: _Facts, steps: list[dict], science: dict) -> dict:
    """Human-sized progress without pretending the journey is one linear checklist."""
    counts = {state: sum(row["state"] == state for row in steps) for state in STATES}
    optional_missing = sum(
        row["optional"] and row["state"] not in ("DONE", "AVAILABLE") for row in steps
    )
    required_blocked = sum(
        (not row["optional"]) and row["state"] == "BLOCKED" for row in steps
    )
    done_rows = [row for row in steps if row["state"] == "DONE"]
    furthest = done_rows[-1] if done_rows else None
    evidence_attempted = int(((f.lineage or {}).get("counts") or {}).get("attempted") or 0)
    evidence_chain = (f.lineage or {}).get("chain_state")
    verified_outputs = int(science.get("viewable_renders") or 0)
    if not verified_outputs and science.get("state") in ("VERIFIED_LOCAL_OUTPUTS", "VERIFIED_MANIFEST_NOT_MOUNTED"):
        verified_outputs = int(science.get("pieces") or 0)
    input_state = ("SEALED" if f.held else "PARTIAL_INDEXED" if f.material_here
                   else "CATALOGUED_UNSEALED" if f.catalogued_bytes else "NOT_LOCAL")
    if f.title_lane:
        scope = (
            "Recorded geometry, rendering or apparatus work is not a transcription or "
            "translation; those remain unfinished "
            "until independent human review records them."
        )
    elif f.has_result:
        scope = (
            "A detector result is recorded, but it remains evidence from an unqualified "
            "detector until independent review; it is not a reading."
        )
    elif f.has_render:
        scope = "A surface render is recorded. No accepted reading is recorded for this scroll."
    elif f.has_surface:
        scope = "Surface geometry is recorded. No accepted reading is recorded for this scroll."
    else:
        scope = "No accepted reading is recorded for this scroll."
    if evidence_attempted or verified_outputs:
        separate = []
        if evidence_attempted:
            separate.append("%d attempted stage%s%s" % (
                evidence_attempted, "" if evidence_attempted == 1 else "s",
                " with a %s receipt chain" % evidence_chain.lower() if evidence_chain else ""))
        if verified_outputs:
            separate.append("%d verified geometry/render output%s" % (
                verified_outputs, "" if verified_outputs == 1 else "s"))
        scope = (
            "Route receipts: %d of %d complete. Separate evidence: %s. Those outputs remain "
            "visible, but they do not replace an earlier missing identity-bound input. %s"
            % (counts["DONE"], len(steps), " and ".join(separate), scope)
        )
    return {
        "done": counts["DONE"],
        "ready": counts["AVAILABLE"],
        "decisions": counts["HUMAN_GATED"],
        "blocked": required_blocked,
        "later": counts["NOT_REACHED"],
        "optional_missing": optional_missing,
        "total": len(steps),
        "furthest_complete": ({"n": furthest["n"], "id": furthest["id"],
                               "label": furthest["label"]} if furthest else None),
        "input_state": input_state,
        "evidence_attempted": evidence_attempted,
        "evidence_chain_state": evidence_chain,
        "verified_outputs": verified_outputs,
        "scope": scope,
    }


_STEP_VERBS = {
    "DONE": "Review", "AVAILABLE": "Open and continue", "HUMAN_GATED": "Open the decision",
    "BLOCKED": "Open the blocker", "UNKNOWN": "Check",
}


def _step_destination(f: _Facts, spec, state: str) -> str:
    if spec.id == "acquisition" and not f.target_registry_available:
        return REGISTRY_TO
    return _to(f.canon, spec, state, fence=f.fence, prize=f.prize)


def _with_step_actions(f: _Facts, steps: list[dict]) -> list[dict]:
    """Give EVERY route stage one primary action that opens the exact screen and control for this scroll."""
    spec_by_id = {s.id: s for s in journey.STEPS}
    out = []
    for i, row in enumerate(steps):
        spec = spec_by_id.get(row["id"])
        row = dict(row)
        if spec is None:
            row.update(to=None, action_label=None, waits_for=None)
            out.append(row); continue
        if row["state"] == "NOT_REACHED":
            prior = next((p for p in steps[:i] if p["state"] != "DONE" and not p.get("optional")), None)
            if prior is not None and prior["id"] in spec_by_id:
                row.update(to=_step_destination(f, spec_by_id[prior["id"]], prior["state"]),
                           action_label="Go to what this waits for: %d. %s" % (prior["n"], prior["label"]),
                           waits_for=prior["id"])
            else:
                row.update(to=_step_destination(f, spec, row["state"]),
                           action_label="Open: %s" % row["label"], waits_for=None)
        else:
            row.update(to=_step_destination(f, spec, row["state"]),
                       action_label="%s: %s" % (_STEP_VERBS.get(row["state"], "Open"), row["label"]),
                       waits_for=None)
        out.append(row)
    return out


def _work_queue(f: _Facts, steps: list[dict]) -> list[dict]:
    """Every presently actionable required item, each with its exact UI destination."""
    spec_by_id = {s.id: s for s in journey.STEPS}
    verbs = {
        "AVAILABLE": "Open and continue",
        "HUMAN_GATED": "Open the decision",
        "BLOCKED": "Open the blocker",
    }
    rows = []
    for row in steps:
        if row["state"] not in _OPEN or row["optional"]:
            continue
        spec = spec_by_id[row["id"]]
        rows.append({
            "step": row["id"], "n": row["n"], "label": row["label"],
            "state": row["state"], "why": row["why"],
            "to": (_to(f.canon, spec, row["state"], fence=f.fence, prize=f.prize)
                   if f.target_registry_available or row["id"] != "acquisition"
                   else REGISTRY_TO),
            "action_label": "%s: %s" % (verbs[row["state"]], row["label"]),
            "action_ids": list(spec.actions), "basis": row["basis"],
        })
    return rows


def _surface_detail(f: _Facts) -> str:
    fields = ((f.surface or {}).get("fields")) or {}
    if not fields:
        return "No surface-status fact is recorded; geometry is read from the shelf's furthest stage only."
    bits = ["%s=%s" % (k, v.get("value")) for k, v in sorted(fields.items()) if isinstance(v, dict)]
    return "Recorded surface facts, each tiered and independent: %s." % "; ".join(bits[:6])


_UNSET = object()


def status(scroll: str, *, shelf: dict | None = None, lineage_root=None, _index=_UNSET,
           _effort=_UNSET) -> dict:
    """The one status for `scroll`."""
    try:
        canon = scroll_ids.resolve(scroll)
    except KeyError as exc:
        return _refusal_status(scroll, "IDENTITY_UNKNOWN", str(exc.args[0] if exc.args else exc))
    from argus.core import material_readiness

    shelf = _load_shelf(shelf)
    readiness = material_readiness.build(canon, shelf=shelf)
    if not readiness.get("route"):
        return _refusal_status(scroll, readiness.get("code") or "NOT_ON_SHELF",
                               readiness.get("why") or "the scroll is not on the shelf",
                               canonical=canon)

    index = _index_rows() if _index is _UNSET else _index
    effort = _effort_rows() if _effort is _UNSET else _effort
    lineage = stage_lineage.coverage(canon, root=lineage_root)
    facts = _Facts(canon, readiness, lineage, (index or {}).get(canon), (effort or {}).get(canon),
                   _surface_facts(canon), _exposure(canon),
                   process_contract._acquisition_receipt(canon), index_read=index is not None,
                   target_registry=shelf.get("target_registry"))
    steps = _steps(facts)
    nxt, blocker = _next_and_blocker(facts, steps)
    science = _science_artifacts(canon)
    contradictions = []
    idx = facts.index_row or {}
    if facts.local_data == "NONE" and int(idx.get("cached_ct_regions") or 0):
        contradictions.append(
            "local data: the shelf says NONE but the scroll index holds %d cached CT region(s)"
            % int(idx["cached_ct_regions"]))
    if lineage.get("chain_state") == "BROKEN":
        contradictions.append("stage lineage chain does not verify; attempts past the break are not reported")
    if science["state"] in ("VERIFIED_LOCAL_OUTPUTS", "VERIFIED_MANIFEST_NOT_MOUNTED"):
        attempted = int((lineage.get("counts") or {}).get("attempted") or 0)
        if steps[2]["state"] in ("BLOCKED", "NOT_REACHED") and (science["pieces"] or attempted):
            contradictions.append(
                "science outputs are verified for this scroll, but the acquisition/route ledger is "
                "not converged; outputs remain review-only until their identity-bound source is "
                "attached to the route")
    counts = {s: sum(1 for x in steps if x["state"] == s) for s in STATES}
    return {
        "schema": SCHEMA, "status": "OK", "scroll": canon, "requested": scroll,
        "display": facts.material.get("display") or canon, "refusal": None,
        "questions": _questions(facts, steps, nxt),
        "capability_lanes": _capability_lanes(facts, steps, science),
        "progress_summary": _progress_summary(facts, steps, science),
        "work_queue": _work_queue(facts, steps),
        "steps": _with_step_actions(facts, steps), "step_counts": counts, "next_action": nxt, "blocker": blocker,
        "contradictions": contradictions, "science_artifacts": science,
        "lineage": {"chain_state": lineage.get("chain_state"), "counts": lineage.get("counts"),
                    "path": lineage.get("chain_path")},
        "generated_utc": _utc(), "read_only": True,
        "claim_ceiling": "readiness and recorded facts only; no stage was run, no detector output "
                         "is called ink or text, and no claim of a qualified detector, unseen-scroll "
                         "generalization or prize eligibility is made",
    }


def status_all(*, shelf: dict | None = None, lineage_root=None) -> dict:
    """Every shelf scroll, composed from ONE shelf so the rows cannot disagree with each other."""
    shelf = _load_shelf(shelf)
    index, effort = _index_rows(), _effort_rows()
    rows = []
    for r in shelf.get("scrolls") or []:
        if not r.get("scroll"):
            continue
        try:
            rows.append(status(r["scroll"], shelf=shelf, lineage_root=lineage_root, _index=index,
                               _effort=effort))
        except Exception as exc:
            rows.append(_refusal_status(r["scroll"], "STATUS_ERROR",
                                        "%s: %s" % (type(exc).__name__, exc),
                                        canonical=r["scroll"]))
    usable = [row for row in rows if row.get("status") == "OK"]
    kind_rank = {"ACT": 4, "DECIDE": 3, "RESOLVE_BLOCKER": 2, "WAIT": 1}

    def summary(row):
        progress = row.get("progress_summary") or {}
        nxt = row.get("next_action") or {}
        return {
            "scroll": row.get("scroll"), "display": row.get("display"),
            "done": int(progress.get("done") or 0),
            "total": int(progress.get("total") or 0),
            "verified_outputs": int(progress.get("verified_outputs") or 0),
            "ready": int(progress.get("ready") or 0),
            "decisions": int(progress.get("decisions") or 0),
            "blocked": int(progress.get("blocked") or 0),
            "next": {k: nxt.get(k) for k in
                     ("label", "to", "why", "kind", "operator_only", "step")},
        }

    most_complete = sorted(
        usable,
        key=lambda row: (
            int((row.get("progress_summary") or {}).get("done") or 0),
            int((row.get("progress_summary") or {}).get("verified_outputs") or 0),
            int((row.get("progress_summary") or {}).get("evidence_attempted") or 0),
            row.get("scroll") or "",
        ), reverse=True)
    best_next = sorted(
        usable,
        key=lambda row: (
            kind_rank.get((row.get("next_action") or {}).get("kind"), 0),
            not bool((row.get("next_action") or {}).get("operator_only")),
            int((row.get("progress_summary") or {}).get("ready") or 0),
            int((row.get("progress_summary") or {}).get("done") or 0),
            int((row.get("progress_summary") or {}).get("verified_outputs") or 0),
            row.get("scroll") or "",
        ), reverse=True)
    recommendations = {
        "best_next_work": [summary(row) for row in best_next[:5]],
        "most_complete": [summary(row) for row in most_complete[:5]],
        "method": (
            "Best next work prefers a runnable action, then a human decision, then a resolvable "
            "blocker; within that it prefers non-operator work, ready stages, completed route "
            "receipts and verified outputs. Most complete uses route receipts first and verified "
            "outputs second. These are workflow rankings, never scientific scores."
        ),
    }
    return {"schema": SCHEMA, "journey": journey.describe(), "count": len(rows), "scrolls": rows,
            "recommendations": recommendations, "generated_utc": _utc(), "read_only": True}
