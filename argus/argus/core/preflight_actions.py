"""What each button will actually do, computed and shown BEFORE anything is enqueued."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import pathlib
import shutil

from . import acquisition_identity as AID
from . import scroll_index as SI

CACHE = pathlib.Path(_argus_public_path('home', 'cache/stores'))
DISK_SAFETY_MARGIN_BYTES = 5 * 1024 ** 3


def _sha_file(p: pathlib.Path):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _scroll_entries(scrolls):
    idx = SI.build()
    want = set(scrolls)
    return [e for e in idx["scrolls"] if e["scroll"] in want], idx


def download(scrolls) -> dict:
    """What a download would transfer, after deduplication."""
    entries, _ = _scroll_entries(scrolls)
    planned = {}
    already = 0
    missing_identity = []

    for e in entries:
        for acq in e.get("acquisitions", []):
            key = acq.get("acquisition_id") or acq.get("store_id")
            if not key:
                missing_identity.append({"scroll": e["scroll"], "store": acq.get("store_id")})
                continue
            prev = planned.get(key)
            if prev is None or (acq.get("committed") or 0) > (prev.get("committed") or 0):
                planned[key] = dict(acq, scroll=e["scroll"])
            already += 0

    total_objects = sum((p.get("planned") or 0) for p in planned.values())
    committed = sum((p.get("committed") or 0) for p in planned.values())
    mb_have = sum((p.get("megabytes_committed") or 0.0) for p in planned.values())
    remaining = max(0, total_objects - committed)
    est_bytes = int(remaining * (mb_have * 1e6 / committed)) if committed else None

    free = shutil.disk_usage(str(CACHE if CACHE.exists() else pathlib.Path(_argus_public_path('anchor', '')))).free
    enough = None if est_bytes is None else free > est_bytes + DISK_SAFETY_MARGIN_BYTES

    refusals = []
    if missing_identity:
        refusals.append({
          "code": "AMBIGUOUS_SOURCE_IDENTITY",
          "detail": "%d cached region(s) carry no acquisition identity, so what they contain "
                    "cannot be established. Acquisition is refused rather than guessed."
                    % len(missing_identity),
          "stores": missing_identity[:5]})
    if enough is False:
        refusals.append({
          "code": "INSUFFICIENT_DISK",
          "detail": "about %.1f GB is needed plus a %.0f GB safety margin, and %.1f GB is free."
                    % (est_bytes / 1024 ** 3, DISK_SAFETY_MARGIN_BYTES / 1024 ** 3,
                       free / 1024 ** 3)})

    return {
      "action": "download", "read_only_preflight": True,
      "scrolls": sorted(set(scrolls)),
      "unique_acquisitions": len(planned),
      "objects_required_unique": total_objects,
      "objects_already_verified": committed,
      "objects_still_to_fetch": remaining,
      "already_local_megabytes": round(mb_have, 1),
      "estimated_transfer_bytes": est_bytes,
      "estimated_transfer_note": ("extrapolated from the mean size of objects already fetched "
                                  "for these same acquisitions; it is an estimate, not a "
                                  "promise" if est_bytes else
                                  "unknown -- nothing fetched yet for these acquisitions"),
      "disk_free_bytes": free,
      "disk_sufficient": enough,
      "resume_behaviour": "a verified object is never fetched again. An interrupted transfer "
                          "resumes from its own manifest, and a resumed job must match the "
                          "stored acquisition_id or it is refused as a different job.",
      "provenance": {
        "source": "Vesuvius Challenge open data (public S3)",
        "licence": "as published by the Vesuvius Challenge; ARGUS redistributes nothing",
        "note": "downloading is a read of public data. It confers no right to redistribute."},
      "refusals": refusals,
      "may_proceed": not refusals,
    }


def read_for_ink(scrolls) -> dict:
    """What a scoring run needs, what detector would run, and what its result may be called."""
    entries, _ = _scroll_entries(scrolls)
    ready, blocked = [], []
    for e in entries:
        for seg, s in sorted(e["segments"].items()):
            has_labels = any(f.get("present") for f in s["labels"].values())
            acq = e.get("acquisitions", [])
            local_ct = any(a.get("sealed") for a in acq)
            why = []
            if not has_labels:
                why.append("no human ink markings, so there is nothing to score against")
            if not local_ct:
                why.append("scan data is not sealed locally; scoring must not stream from the "
                           "network")
            (ready if not why else blocked).append(
                {"scroll": e["scroll"], "segment": seg, "blocked_by": why})

    return {
      "action": "read_for_ink", "read_only_preflight": True,
      "scrolls": sorted(set(scrolls)),
      "segments_ready": [r for r in ready],
      "segments_blocked": blocked,
      "required_inputs": ["sealed local scan region", "human ink markings",
                          "supervision mask defining where markings are trustworthy"],
      "detector": {
        "identity": "none bundled: detectors are not part of the public release",
        "operational_verification": "NOT_MEASURED",
        "scientific_admissibility": "UNQUALIFIED",
        "why_unqualified": "no detector in this build has passed a declared cross-scroll "
                           "qualification on a physical scroll it never saw",
        "licence": "a detector without a declared licence cannot enter a prize submission "
                   "whatever it scores"},
      "exposure": {"blind_hunt": "NOT OPEN"},
      "expected_result_class": "EXPLORATORY_UNQUALIFIED",
      "expected_result_meaning": "a number you may inspect and compare. NOT a pass, not a "
                                 "cross-scroll qualification, and not citable as evidence that "
                                 "any scroll contains legible ink.",
      "both_orientations_required": "forward and reversed are scored separately and BOTH are "
                                    "required before a segment counts as complete. One "
                                    "orientation is inspectable and clearly marked partial.",
      "resources": {"device": "a local GPU", "network_during_scoring": "DENIED BY DESIGN"},
      "refusals": ([{"code": "NO_SEGMENT_READY",
                     "detail": "every selected segment is blocked; see segments_blocked"}]
                   if not ready else []),
      "may_proceed": bool(ready),
    }


def review_lab(scrolls) -> dict:
    """What a reviewer would actually see, and what is deliberately hidden from them."""
    entries, _ = _scroll_entries(scrolls)
    complete, partial, none = [], [], []
    for e in entries:
        for seg, s in sorted(e["segments"].items()):
            f, r = bool(s["results"].get("forward")), bool(s["results"].get("reversed"))
            row = {"scroll": e["scroll"], "segment": seg,
                   "forward": f, "reversed": r,
                   "label_families": sorted(k for k, v in s["labels"].items()
                                            if v.get("present"))}
            if f and r:
                complete.append(row)
            elif f or r:
                row["missing_orientation"] = "reversed" if f else "forward"
                row["status"] = "PARTIAL -- inspectable, clearly marked, never COMPLETE"
                partial.append(row)
            else:
                none.append(row)
    return {
      "action": "review_lab", "read_only_preflight": True,
      "scrolls": sorted(set(scrolls)),
      "complete_outputs": complete,
      "partial_outputs": partial,
      "no_output": none,
      "partial_policy": "a single orientation IS inspectable and is shown as a clearly marked "
                        "partial. It never renders COMPLETE or green, never classifies a "
                        "route, and is not scientifically citable until both orientations are "
                        "sealed.",
      "hidden_from_reviewer": {
        "model_confidence": "NOT SHOWN",
        "why": "a reviewer shown the model's confidence is no longer an independent judge of "
               "what is on the papyrus; they are agreeing or disagreeing with a number."},
      "shown_to_reviewer": ["the image", "its provenance", "which label family applies",
                            "the coordinates that reproduce it"],
      "refusals": [],
      "may_proceed": True,
    }


def evidence(scrolls) -> dict:
    """Every hash a reader needs to check the chain themselves."""
    entries, _ = _scroll_entries(scrolls)
    art = pathlib.Path(_argus_public_path('repo', 'artifacts'))
    receipts = {}
    for name in ():
        from argus.core import paths as _paths
        p = _paths.find_artifact(*pathlib.PurePosixPath(name).parts)
        if p.is_file():
            receipts[name] = {"path": str(p), "sha256": _sha_file(p)}

    acq = []
    for e in entries:
        for a in e.get("acquisitions", []):
            acq.append({"scroll": e["scroll"], "segment": a.get("segment"),
                        "store_id": a.get("store_id"),
                        "acquisition_id": a.get("acquisition_id"),
                        "identity_source": a.get("identity_source"),
                        "sealed": a.get("sealed")})
    return {
      "action": "evidence", "read_only_preflight": True,
      "scrolls": sorted(set(scrolls)),
      "source": {"origin": "Vesuvius Challenge open data",
                 "acquisition_families": ["each acquisition is identified by its own acquisition_id"],
                 "never_merged": "separate acquisitions are separate measurements and are never "
                                 "combined in a score"},
      "acquisitions": acq,
      "model": {"detector": "none bundled: not part of the public release",
                "training_split": "not part of the public release",
                "licence": "UNDECLARED"},
      "scorer": {"metric": "AUC via argus-metric-v1",
                 "uncertainty": "declared by the scorer",
                 "aggregation": "declared by the scorer"},
      "exposure": {"blind_hunt": "NOT OPEN"},
      "result_classification": "EXPLORATORY_UNQUALIFIED",
      "receipts": receipts,
      "refusals": [],
      "may_proceed": True,
    }


ACTIONS = {"download": download, "read_for_ink": read_for_ink,
           "review_lab": review_lab, "evidence": evidence}


def preflight(action: str, scrolls) -> dict:
    fn = ACTIONS.get(action)
    if fn is None:
        return {"error": "unknown action", "known": sorted(ACTIONS)}
    out = fn(list(scrolls))
    out["enqueue_via"] = ("the governed command queue. This preflight computed and returned; "
                          "it enqueued nothing and wrote no scientific state.")
    return out
