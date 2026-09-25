"""One reproducible, provenance-complete evidence package per run -- assembled, not typed."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from argus.core import erratum as ERRATUM
from argus.core import paths
from argus.core.receipts import sha_file, write_json

try:
    from argus.core import licence_registry as LR
except ImportError:
    LR = None

CONTRACT = "argus-evidence-package-v1"
MISSING = "MISSING"
MAX_CHAIN_DEPTH = 6

FIELD_ORDER = (
    "physical_scroll_identity",
    "acquisition_identity",
    "voxel_scale_and_axes",
    "source_coordinates",
    "ordered_transforms",
    "provider_and_code_revision",
    "checkpoint_digest_and_licence",
    "training_exposure_record",
    "parameters_and_thresholds",
    "controls",
    "metrics_and_uncertainty",
    "output_hashes",
    "human_interventions",
    "final_disposition",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class EvidencePackageError(RuntimeError):
    """Raised only when the ROOT receipt itself cannot be read -- never for a missing field."""


def _missing(note: str, checked_keys=()) -> dict:
    return {"value": MISSING, "checked_keys": list(checked_keys), "note": note}


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _looks_like_chain_path(s: str) -> bool:
    """A citation worth resolving repo-wide, not a bare in-array chunk name like '0.0.0'."""
    return isinstance(s, str) and ("/" in s or "\\" in s or _WIN_ABS.match(s) is not None)


def _find_citations(doc) -> list:
    """Every {'path': str, 'sha256': 64-hex str} pair anywhere in `doc`, path-like only."""
    out, stack = [], [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            p, s = node.get("path"), node.get("sha256")
            if (isinstance(p, str) and _looks_like_chain_path(p)
                    and isinstance(s, str) and _HEX64.match(s)):
                out.append((p, s))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _resolve_cited(rel_or_abs: str):
    """Returns (resolved_path_or_None, how)."""
    p = Path(rel_or_abs)
    if p.is_absolute() or _WIN_ABS.match(rel_or_abs):
        return (p if p.is_file() else None), "absolute_path_outside_or_inside_declared_roots"
    try:
        rp = paths.resolve_repo_relative(rel_or_abs)
    except Exception:
        return None, "repo_relative_unresolvable"
    return (rp if rp.is_file() else None), "repo_relative_via_paths.resolve_repo_relative"


def _verify_citation(rel: str, want_sha: str, cited_by: str) -> dict:
    resolved, how = _resolve_cited(rel)
    if resolved is None:
        return {"declared_path": rel, "cited_by": cited_by, "resolution": how,
                "on_disk": False, "resolved_path": None, "sha256_declared": want_sha,
                "sha256_actual": None, "hash_matches": False, "status": "MISSING_ON_DISK"}
    actual = sha_file(resolved)
    matches = actual == want_sha
    rec = {"declared_path": rel, "cited_by": cited_by, "resolution": how,
           "on_disk": True, "resolved_path": str(resolved), "sha256_declared": want_sha,
           "sha256_actual": actual, "hash_matches": matches,
           "status": "OK" if matches else "HASH_MISMATCH"}
    if not matches:
        err = ERRATUM.line_ending_equivalent(resolved, want_sha)
        if err:
            rec["status"] = "OK"
            rec["hash_matches_after_erratum"] = True
            rec["erratum"] = err
    return rec


def walk_chain(root_receipt: Path, *, max_depth: int = MAX_CHAIN_DEPTH) -> dict:
    """BFS over every citation, recursing into any that resolve to readable JSON."""
    root_doc = _load_json(root_receipt)
    if root_doc is None:
        raise EvidencePackageError(
            "the root receipt is not readable JSON: %s -- an evidence package cannot be "
            "assembled from a receipt that cannot itself be read" % root_receipt)

    docs = [(str(root_receipt), root_doc)]
    citations = []
    visited = {str(root_receipt.resolve())}
    queue = [(root_receipt, root_doc, 0)]
    while queue:
        cur_path, cur_doc, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for rel, want_sha in _find_citations(cur_doc):
            entry = _verify_citation(rel, want_sha, cited_by=str(cur_path))
            citations.append(entry)
            if not entry["on_disk"] or not entry["resolved_path"]:
                continue
            rp = Path(entry["resolved_path"])
            key = str(rp.resolve())
            if key in visited or rp.suffix.lower() != ".json":
                continue
            visited.add(key)
            sub_doc = _load_json(rp)
            if sub_doc is not None:
                docs.append((entry["declared_path"], sub_doc))
                queue.append((rp, sub_doc, depth + 1))
    return {"citations": citations, "docs": docs}




def _deep_get(doc, dotted_key: str):
    cur = doc
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def _find_first(docs: list, dotted_keys):
    """First non-empty value for any of `dotted_keys`, searched doc-by-doc in chain order (root first)."""
    for key in dotted_keys:
        for src, doc in docs:
            val, ok = _deep_get(doc, key)
            if ok and val not in (None, "", [], {}):
                return val, src, key
    return None, None, None


def _resolved_field(docs, keys, note) -> dict:
    val, src, key = _find_first(docs, keys)
    if val is None:
        return _missing(note, keys)
    return {"value": val, "source_receipt": src, "matched_key": key}




def _physical_scroll_identity(docs) -> dict:
    return _resolved_field(
        docs, ("physical_scroll",),
        "no receipt in the chain declares a physical_scroll field")


def _acquisition_identity(docs) -> dict:
    acq = _resolved_field(docs, ("acquisition_id",),
                           "no receipt in the chain declares an acquisition_id")
    seg = _resolved_field(docs, ("segment",),
                           "no receipt in the chain declares a segment")
    return {"acquisition_id": acq, "segment": seg}


def _voxel_scale_and_axes(docs) -> dict:
    scale = _resolved_field(docs, ("spacing_um", "preparation.spacing_um"),
                             "no receipt in the chain declares a voxel spacing (spacing_um)")
    shape = _resolved_field(docs, ("source.shape",),
                             "no receipt in the chain declares a source volume shape")
    axes = _missing(
        "no receipt in the chain names axis order explicitly (e.g. a labeled ('Z','Y','X') "
        "tuple); a shape array alone does not state which index is which axis, and this "
        "assembler will not guess an axis order that could silently be wrong")
    return {"voxel_scale_um": scale, "source_volume_shape": shape, "axis_order": axes}


def _source_coordinates(docs) -> dict:
    tile = _resolved_field(docs, ("selected_tile",),
                            "no receipt in the chain declares a selected_tile")
    crop = _resolved_field(docs, ("control_source.crop_bbox_y0_y1_x0_x1",),
                            "no receipt in the chain declares a control_source crop bounding box")
    return {"selected_tile": tile, "control_crop_bbox_y0_y1_x0_x1": crop}


def _ordered_transforms(docs) -> dict:
    """Reconstructed from the descriptive fields the chain actually carries, in the order a volume would pass through them (crop -> resample -> tile-select)."""
    crop = _resolved_field(docs, ("control_source.crop_bbox_y0_y1_x0_x1",),
                            "no crop bounding box declared")
    resample = _resolved_field(docs, ("source.resampling",),
                                "no resampling description declared")
    tile = _resolved_field(docs, ("selected_tile.selection_rule",),
                            "no tile-selection rule declared")
    steps = [("1_crop_to_control_bbox", crop), ("2_resample", resample),
             ("3_select_tile", tile)]
    return {
        "steps": [{"step": name, **val} for name, val in steps],
        "note": "reconstructed from this chain's descriptive fields in pipeline order "
                "(crop, then resample, then tile-select); no receipt in the chain declares a "
                "first-class ordered-transform list, so a step marked MISSING above reflects "
                "that no matching descriptive field exists, not a parsing failure",
    }


def _provider_and_code_revision(docs) -> dict:
    provider = _resolved_field(docs, ("provider",), "no receipt declares a provider name")
    provider_rev = _resolved_field(docs, ("provider_revision",),
                                    "no receipt declares a provider_revision")
    code_source = _resolved_field(
        docs, ("source.git_blob_sha1", "source.sha256"),
        "no receipt declares the running code's own git blob hash or file sha256")
    return {"provider": provider, "provider_revision": provider_rev,
            "code_identity": code_source}


def _checkpoint_digest_and_licence(docs) -> dict:
    checkpoint = _resolved_field(
        docs, ("checkpoint",), "no receipt in the chain declares a checkpoint block")
    licence = _missing(
        "no argus.core.licence_registry component was matched to this checkpoint or provider",
        ("licence_registry.manifest() lookup by provider/checkpoint name",))
    if LR is not None and isinstance(checkpoint.get("value"), dict):
        names = []
        prov, _, _ = _find_first(docs, ("provider",))
        if prov:
            names.append(str(prov))
        ckpt_path = checkpoint["value"].get("path")
        if ckpt_path:
            names.append(Path(str(ckpt_path)).stem)
        man = LR.manifest()
        hit = None
        for comp in man["components"]:
            comp_name = comp["name"].lower()
            if any(n and (n.lower() in comp_name or comp_name in n.lower()) for n in names):
                hit = comp
                break
        if hit is not None:
            licence = {"value": hit, "source_receipt": "argus.core.licence_registry",
                       "matched_key": "components[].name ~ %s" % names}
        else:
            licence = _missing(
                "checked names %s against every argus.core.licence_registry component "
                "(%s) and none matched; an unregistered component is UNDECLARED by the "
                "registry's own policy, not merely unchecked"
                % (names, [c["name"] for c in man["components"]]),
                ("licence_registry.manifest()['components'][]['name']",))
    return {"checkpoint": checkpoint, "licence": licence}


def _training_exposure_record(docs) -> dict:
    state = _resolved_field(docs, ("exposure",), "no receipt declares an exposure state")
    detail = _missing(
        "no receipt in the chain declares a structured exposure record (named trained/"
        "validation/target segment sets, e.g. the detector_exposure shape used by "
        "argus.core.prize_package); only a coarse exposure STATE (see exposure_state above) "
        "is present, and this assembler will not infer segment membership that is not stated")
    return {"exposure_state": state, "exposure_detail": detail}


def _parameters_and_thresholds(docs) -> dict:
    params = _resolved_field(docs, ("runtime.parameters",),
                              "no receipt declares model parameter count")
    patch = _resolved_field(docs, ("runtime.patch_size",),
                             "no receipt declares a runtime patch_size")
    sampling = _resolved_field(docs, ("runtime.sampling_um", "spacing_um"),
                                "no receipt declares a runtime sampling rate")
    threshold = _resolved_field(docs, ("paired_orientation_control.decision_rule",),
                                 "no receipt declares a decision threshold/rule")
    return {"model_parameters": params, "patch_size": patch, "sampling_um": sampling,
            "decision_rule": threshold}


def _controls(docs) -> dict:
    control_source = _resolved_field(
        docs, ("control_source",), "no receipt declares a control_source (known-label control)")
    paired = _resolved_field(
        docs, ("paired_orientation_control",),
        "no receipt declares a paired/orientation control block")
    return {"control_source": control_source, "paired_orientation_control": paired}


def _metrics_and_uncertainty(docs) -> dict:
    forward = _resolved_field(docs, ("paired_orientation_control.forward",),
                               "no forward-orientation metrics declared")
    reverse = _resolved_field(docs, ("paired_orientation_control.reverse",),
                               "no reverse-orientation metrics declared")
    uncertainty = _missing(
        "no receipt in the chain declares an uncertainty interval (e.g. a confidence bound) "
        "around any reported metric; point estimates only")
    return {"forward": forward, "reverse": reverse, "uncertainty": uncertainty}


def _output_hashes(docs) -> dict:
    """Flattened from the root receipt's own `outputs` tree, plus every per-file hash nested inside it (e.g."""
    root_label, root_doc = docs[0]
    outputs, ok = _deep_get(root_doc, "outputs")
    if not ok or not outputs:
        return _missing("the root receipt declares no outputs block", ("outputs",))
    flat = []

    def walk(node, where):
        if isinstance(node, dict):
            if isinstance(node.get("sha256"), str) and _HEX64.match(node["sha256"]):
                flat.append({"where": where, "path": node.get("path"),
                             "sha256": node["sha256"], "bytes": node.get("bytes")})
            for k, v in node.items():
                walk(v, where + "." + str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))

    walk(outputs, "outputs")
    return {"value": flat, "source_receipt": root_label, "matched_key": "outputs.*.sha256",
            "count": len(flat)}


def _human_interventions(docs) -> dict:
    keys = ("human_intervention", "human_interventions", "operator_decision",
            "reviewed_by", "manual_override", "overridden_by")
    val, src, key = _find_first(docs, keys)
    if val is None:
        return _missing(
            "no receipt in the chain records any human intervention field (checked: %s); "
            "this is reported as MISSING rather than 'none', because an assembler cannot "
            "distinguish 'no intervention occurred' from 'no receipt would have recorded one'"
            % ", ".join(keys), keys)
    return {"value": val, "source_receipt": src, "matched_key": key}


def _final_disposition(docs) -> dict:
    internal = {
        "terminal": _resolved_field(docs, ("terminal", "operational_state"),
                                     "no receipt declares a terminal/operational_state"),
        "scientific_state": _resolved_field(docs, ("scientific_state",),
                                             "no receipt declares a scientific_state"),
        "claim_ceiling": _resolved_field(docs, ("claim_ceiling",),
                                          "no receipt declares a claim_ceiling"),
        "eligible_for_automatic_routing": _resolved_field(
            docs, ("eligible_for_automatic_routing",),
            "no receipt declares eligible_for_automatic_routing"),
    }
    is_vigiles = any(doc.get("schema") == "argus-vigiles-decision-v1" for _, doc in docs)
    wrapped = _missing(
        "this chain contains no argus-vigiles-decision-v1 receipt for the final-disposition "
        "wrapper to wrap, so no final-disposition record applies here; the run's own "
        "terminal/scientific_state/claim_ceiling fields above are its complete final "
        "disposition" if not is_vigiles else
        "a VIGILES decision receipt is present in this chain but no final-disposition record "
        "was found or supplied to this assembler")
    return {"run_internal_disposition": internal, "wrapped_final_disposition": wrapped}


_ASSEMBLERS = {
    "physical_scroll_identity": _physical_scroll_identity,
    "acquisition_identity": _acquisition_identity,
    "voxel_scale_and_axes": _voxel_scale_and_axes,
    "source_coordinates": _source_coordinates,
    "ordered_transforms": _ordered_transforms,
    "provider_and_code_revision": _provider_and_code_revision,
    "checkpoint_digest_and_licence": _checkpoint_digest_and_licence,
    "training_exposure_record": _training_exposure_record,
    "parameters_and_thresholds": _parameters_and_thresholds,
    "controls": _controls,
    "metrics_and_uncertainty": _metrics_and_uncertainty,
    "output_hashes": _output_hashes,
    "human_interventions": _human_interventions,
    "final_disposition": _final_disposition,
}


def _git_head() -> str:
    from argus.core import git_state
    return git_state.head(paths.repo()) or MISSING


def _subtree_binding() -> dict:
    """Only when ARGUS is a subdirectory of a larger repository: the commit then names the enclosing repo, so the ARGUS subtree hash is what the packet is bound to."""
    from argus.core import git_state
    b = git_state.binding(paths.repo())
    return {"repo_subdir": b["subdir"], "repo_subtree_hash": b["tree_hash"]} if "tree_hash" in b else {}


def _count_missing(fields: dict) -> int:
    n = 0
    stack = [fields]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("value") == MISSING:
                n += 1
            else:
                stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return n


def assemble(receipt_path, *, out_path=None) -> dict:
    """Assemble one evidence package from `receipt_path`'s declared provenance chain."""
    p = Path(receipt_path)
    if not p.is_absolute():
        resolved, _how = _resolve_cited(str(receipt_path))
        if resolved is None:
            raise EvidencePackageError(
                "the root receipt %r does not resolve to a file under any declared root"
                % str(receipt_path))
        p = resolved
    if not p.is_file():
        raise EvidencePackageError("the root receipt does not exist: %s" % p)

    chain = walk_chain(p)
    docs = chain["docs"]
    fields = {name: _ASSEMBLERS[name](docs) for name in FIELD_ORDER}

    package = {
        "contract": CONTRACT,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root_receipt": {"path": str(p), "sha256": sha_file(p)},
        "repo_head": _git_head(),
        **_subtree_binding(),
        "provenance_chain": chain["citations"],
        "chain_receipts": [src for src, _ in docs],
        "fields": fields,
        "field_order": list(FIELD_ORDER),
        "summary": {
            "fields_total": len(FIELD_ORDER),
            "fields_missing": _count_missing(fields),
            "citations_total": len(chain["citations"]),
            "citations_hash_mismatch": sum(1 for c in chain["citations"]
                                           if c["status"] == "HASH_MISMATCH"),
            "citations_missing_on_disk": sum(1 for c in chain["citations"]
                                             if c["status"] == "MISSING_ON_DISK"),
        },
        "honesty_policy": "every field in field_order is present above even when its value is "
                          "MISSING; a field this assembler could not resolve is never omitted.",
    }
    if out_path is not None:
        write_json(package, out_path)
    return package
