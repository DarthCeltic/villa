"""The mandatory preflight gate an operation on a scroll's real data must pass before it runs."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from argus.core import official_identity as OI
from argus.core import paths
from argus.core import scroll_ids

CONTRACT = "argus-eligible-target-operation-gate-v1"

GEOMETRY_ONLY = "GEOMETRY_ONLY"
INK_INFERENCE = "INK_INFERENCE"
TRAINING = "TRAINING"
READING_TRANSCRIPTION = "READING_TRANSCRIPTION"
OPERATION_CLASSES = (GEOMETRY_ONLY, INK_INFERENCE, TRAINING, READING_TRANSCRIPTION)

ELIGIBLE_TARGET_DEFAULT_CEILING = GEOMETRY_ONLY

REFUSED = "REFUSED"
PERMITTED = "PERMITTED"

FRESHNESS_THRESHOLD_DAYS = max(1, int(os.environ.get("ARGUS_REGISTRY_FRESH_DAYS", "30")))

_REVERIFIED_RE = re.compile(r"(?:CORRECTED|RE-?VERIFIED)\s+(\d{4}-\d{2}-\d{2})")
_SCROLL_SEGMENT_RE = re.compile(r"/([^/]+)/volumes/[^/]+\.zarr/?$")


class EligibleTargetOperationRefusal(RuntimeError):
    """Raised by `require()`/`gate()`."""

    def __init__(self, message: str, verdict: dict | None = None):
        super().__init__(message)
        self.verdict = verdict


def _norm(s) -> str:
    return str(s or "").strip()


def scroll_segment_from_source(source) -> str | None:
    """The scroll-name path segment directly enclosing `volumes/<id>....zarr`, from a URL, a local path string, or a store-identity dict carrying `source_url`/`canonical_source_url`/ `volume_url`."""
    if isinstance(source, dict):
        for key in ("source_url", "canonical_source_url", "volume_url", "url"):
            if source.get(key):
                return scroll_segment_from_source(source[key])
        return None
    s = _norm(source)
    if not s:
        return None
    path = urllib.parse.urlsplit(s).path if "://" in s else s.replace("\\", "/")
    m = _SCROLL_SEGMENT_RE.search(path)
    return m.group(1) if m else None


def _reverification_date(known_facts) -> datetime.date | None:
    """The latest CORRECTED/RE-VERIFIED date named in an entry's `known_facts`, or None."""
    best = None
    for fact in known_facts or ():
        for m in _REVERIFIED_RE.finditer(_norm(fact)):
            try:
                d = datetime.date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if best is None or d > best:
                best = d
    return best


def _parse_utc(s) -> datetime.datetime | None:
    s = _norm(s)
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def default_registry_path() -> Path:
    return paths.repo("corpus", "scrolls", "registry.json")


def load_registry(path=None) -> dict:
    p = Path(path) if path is not None else default_registry_path()
    raw = p.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    return {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "doc": doc}


def _find_entry(doc: dict, resolved_scroll: str) -> dict | None:
    for obj in doc.get("objects") or []:
        oid = obj.get("object_id")
        if not oid:
            continue
        try:
            if scroll_ids.resolve(oid) == resolved_scroll:
                return obj
        except KeyError:
            continue
    return None


def preflight(*, declared_physical_scroll: str, target_volume_id: str, operation_class: str,
              authorized: bool, target_volume_source, non_geometry_operation_on_eligible_target_authorized: bool = False,
              registry_path=None, now_utc: str | None = None) -> dict:
    """The decision alone."""
    reasons = []
    now = _parse_utc(now_utc) or datetime.datetime.now(datetime.timezone.utc)

    resolved_declared = None
    raw_declared = _norm(declared_physical_scroll)
    if not raw_declared:
        reasons.append("UNDECLARED_SCROLL: declared_physical_scroll is empty")
    else:
        try:
            resolved_declared = scroll_ids.resolve(raw_declared)
        except KeyError as e:
            reasons.append("UNRESOLVABLE_DECLARED_SCROLL: %s" % e)

    observed_segment = scroll_segment_from_source(target_volume_source)
    resolved_observed = None
    if target_volume_source is None:
        reasons.append(
          "UNVERIFIABLE_IDENTITY_BINDING: no target_volume_source was given, so the declared "
          "scroll cannot be independently checked against the object actually being read. This "
          "gate refuses rather than trust a bare label: a bare declared label is never trusted.")
    elif observed_segment is None:
        reasons.append(
          "VOLUME_SOURCE_UNPARSEABLE: could not extract a scroll path segment (the component "
          "directly before 'volumes/<id>....zarr') from %r" % (target_volume_source,))
    else:
        try:
            resolved_observed = scroll_ids.resolve(observed_segment)
        except KeyError as e:
            reasons.append("UNRESOLVABLE_OBSERVED_SCROLL: the volume source names %r, which does "
                           "not resolve: %s" % (observed_segment, e))
    if resolved_declared and resolved_observed and resolved_declared != resolved_observed:
        reasons.append(
          "IDENTITY_BINDING_MISMATCH: declared physical_scroll %r resolves to %r, but the volume "
          "actually being read names %r, which resolves to %r -- these are DIFFERENT scrolls. "
          "This is a false identity binding; refusing rather than trusting the declared label."
          % (raw_declared, resolved_declared, observed_segment, resolved_observed))

    registry_doc, registry_sha, registry_source = None, None, None
    entry = None
    reg_freshness = {"checked": False}
    if resolved_declared is not None:
        try:
            loaded = load_registry(registry_path)
            registry_doc, registry_sha, registry_source = loaded["doc"], loaded["sha256"], loaded["path"]
        except (OSError, ValueError) as e:
            reasons.append("REGISTRY_UNREADABLE: %s. UNKNOWN refuses rather than assumes "
                           "non-eligible." % e)
        if registry_doc is not None:
            entry = _find_entry(registry_doc, resolved_declared)
            if entry is None:
                reasons.append(
                  "REGISTRY_ENTRY_NOT_FOUND: no corpus/scrolls/registry.json entry resolves to "
                  "%s; eligibility cannot be confirmed and this gate refuses rather than assume "
                  "the scroll is not a prize target" % resolved_declared)
            captured_at = _parse_utc(registry_doc.get("captured_at"))
            entry_reverified = _reverification_date((entry or {}).get("known_facts"))
            age_days = (now - captured_at).days if captured_at else None
            reverified_age_days = (now.date() - entry_reverified).days if entry_reverified else None
            fresh = bool(captured_at) and age_days is not None and age_days <= FRESHNESS_THRESHOLD_DAYS
            fresh_by_entry = (reverified_age_days is not None
                              and reverified_age_days <= FRESHNESS_THRESHOLD_DAYS)
            reg_freshness = {
              "checked": True, "captured_at": registry_doc.get("captured_at"),
              "age_days": age_days, "threshold_days": FRESHNESS_THRESHOLD_DAYS,
              "entry_reverified_date": entry_reverified.isoformat() if entry_reverified else None,
              "fresh_by_captured_at": fresh, "fresh_by_entry_reverification": fresh_by_entry,
              "state": "FRESH" if (fresh or fresh_by_entry) else "STALE",
            }
            if not fresh and not fresh_by_entry:
                reasons.append(
                  "STALE_REGISTRY: registry.json captured_at=%s is %s days old (threshold %d) and "
                  "the %s entry carries no CORRECTED/RE-VERIFIED marker within that window. Refusing rather than silently trusting a potentially stale eligible-volume id."
                  % (registry_doc.get("captured_at"), age_days, FRESHNESS_THRESHOLD_DAYS,
                     resolved_declared))

    eligible = False
    registered_volume_id = None
    if entry is not None:
        gp = entry.get("grand_prize_2027") or {}
        eligible = bool(gp.get("eligible"))
        if eligible:
            registered_volume_id = gp.get("volume_id")
            declared_vol = _norm(target_volume_id)
            if not registered_volume_id:
                reasons.append(
                  "ELIGIBLE_TARGET_NO_REGISTERED_VOLUME: %s is eligible but registry.json names "
                  "no grand_prize_2027.volume_id" % resolved_declared)
            elif not declared_vol:
                reasons.append("UNDECLARED_TARGET_VOLUME: target_volume_id is empty")
            elif declared_vol != registered_volume_id:
                reasons.append(
                  "EXACT_VOLUME_MISMATCH: operation declares target_volume_id %r but the "
                  "registered eligible volume_id for %s is %r -- an exact string comparison, "
                  "never a fuzzy one" % (declared_vol, resolved_declared, registered_volume_id))
            parsed_token = OI.parse_volume_store_token(target_volume_source) \
                if not isinstance(target_volume_source, dict) else None
            reg_token = registered_volume_id.split("-", 1)[0] if registered_volume_id else None
            if parsed_token and reg_token and parsed_token != reg_token:
                reasons.append(
                  "URL_VOLUME_TOKEN_MISMATCH: the volume source names token %r but the registered "
                  "eligible volume_id for %s starts with %r" % (parsed_token, resolved_declared,
                                                                reg_token))

    op = _norm(operation_class).upper()
    if op not in OPERATION_CLASSES:
        reasons.append("UNKNOWN_OPERATION_CLASS: %r is not one of %s" % (operation_class,
                                                                          list(OPERATION_CLASSES)))
    elif eligible and op != ELIGIBLE_TARGET_DEFAULT_CEILING \
            and non_geometry_operation_on_eligible_target_authorized is not True:
        reasons.append(
          "OPERATION_CLASS_ABOVE_CEILING_ON_ELIGIBLE_TARGET: %r requested against %s, a live "
          "eligible prize target. Only %s is permitted here unless the SEPARATE "
          "non_geometry_operation_on_eligible_target_authorized flag is also explicitly True -- "
          "it is never inferred from authorized and never defaults to permitting more than "
          "geometry." % (op, resolved_declared, ELIGIBLE_TARGET_DEFAULT_CEILING))

    if authorized is not True:
        reasons.append("NOT_AUTHORIZED: authorized must be explicitly True, never assumed, "
                       "defaulted, or merely truthy; got %r" % (authorized,))

    return {
      "contract": CONTRACT,
      "verdict": REFUSED if reasons else PERMITTED,
      "reasons": reasons,
      "declared_physical_scroll": raw_declared or None,
      "resolved_scroll": resolved_declared,
      "observed_volume_scroll_segment": observed_segment,
      "resolved_observed_scroll": resolved_observed,
      "target_volume_id": _norm(target_volume_id) or None,
      "operation_class": op or None,
      "authorized": authorized,
      "non_geometry_operation_on_eligible_target_authorized":
        non_geometry_operation_on_eligible_target_authorized,
      "registry": {"path": registry_source, "sha256": registry_sha},
      "eligible_target": eligible,
      "registered_volume_id": registered_volume_id,
      "registry_freshness": reg_freshness,
      "decided_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
      "permitted_means": "only that this specific claim about scroll identity, eligible-volume "
                         "match, declared operation class and freshness checks out. It does not "
                         "authorise ink, detector, training or reading -- ink_socket_gate and "
                         "target_acquisition_packet still own that decision untouched.",
    }


def require(**kwargs) -> dict:
    v = preflight(**kwargs)
    if v["verdict"] == REFUSED:
        raise EligibleTargetOperationRefusal(
          "ELIGIBLE-TARGET OPERATION GATE REFUSED:\n  - %s" % "\n  - ".join(v["reasons"]),
          verdict=v)
    return v


def gate(*, receipt_dir, **kwargs) -> dict:
    """`require()`, plus an append-only receipt written either way."""
    if receipt_dir is None or not _norm(receipt_dir):
        raise EligibleTargetOperationRefusal(
          "gate() needs an explicit receipt_dir; a refusal with no receipt is indistinguishable "
          "from the gate never being called")
    v = preflight(**kwargs)
    from argus.core import receipts
    d = Path(receipt_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode("utf-8")) \
        .hexdigest()[:12]
    scroll_tag = re.sub(r"[^A-Za-z0-9]+", "", v.get("resolved_scroll") or v.get(
      "declared_physical_scroll") or "NONE")
    name = "ELIGIBLE_TARGET_GATE_%s_%s_%s_%s.json" % (v["verdict"], scroll_tag, stamp, digest)
    p = d / name
    if p.exists():
        raise EligibleTargetOperationRefusal("receipt %s already exists; receipts are append-only"
                                             % p)
    receipt = dict(v, receipt_sha256=hashlib.sha256(
      json.dumps(v, sort_keys=True, default=str).encode("utf-8")).hexdigest())
    path = receipts.write_json(receipt, p)
    receipt = dict(receipt, receipt_path=str(path))
    if v["verdict"] == REFUSED:
        raise EligibleTargetOperationRefusal(
          "ELIGIBLE-TARGET OPERATION GATE REFUSED:\n  - %s\nreceipt: %s"
          % ("\n  - ".join(v["reasons"]), path), verdict=receipt)
    return receipt
