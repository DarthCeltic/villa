"""The upstream update conveyor: watch, fetch, verify, test, promote by policy, roll back."""
from __future__ import annotations

import dataclasses
import hashlib
import random
import time
from typing import Iterable

CONVEYOR_ID = "argus-update-conveyor-v1"


UPDATE_MODES = ("UNSET", "AUTOMATIC", "ASK_FIRST", "NEVER")

MODE_COPY = {
  "AUTOMATIC": "ARGUS periodically checks upstream metadata and records exact candidate revisions. "
               "Compatibility tests and activation stay governed, and scientific activation still "
               "requires real-data evidence.",
  "ASK_FIRST": "ARGUS checks only when you ask. Compatibility tests and activation stay governed.",
  "NEVER": "ARGUS never contacts upstream. You update components yourself.",
}

CONSENT_QUESTION = (
  "May ARGUS keep its components up to date for you?\n"
  "Updates are downloaded into an isolated area, tested against controls, and only used for "
  "NEW work. Nothing you have already run is ever changed."
)


class ConveyorError(RuntimeError):
    """Raised when the conveyor would act without permission or without evidence."""


@dataclasses.dataclass
class UpdatePolicy:
    """The operator's standing answer."""

    mode: str = "UNSET"
    decided_utc: str | None = None
    changed_in_settings: bool = False

    def __post_init__(self):
        if self.mode not in UPDATE_MODES:
            raise ConveyorError("unknown update mode %r" % (self.mode,))

    @property
    def decided(self) -> bool:
        return self.mode != "UNSET"

    def set(self, mode: str, *, from_settings: bool = False) -> "UpdatePolicy":
        if mode not in UPDATE_MODES or mode == "UNSET":
            raise ConveyorError("%r is not a choosable update mode" % (mode,))
        self.mode = mode
        self.decided_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.changed_in_settings = from_settings
        return self

    def may_check_upstream(self) -> bool:
        """NEVER means no outbound request at all -- not a silent check with a quiet result."""
        return self.mode in ("AUTOMATIC", "ASK_FIRST")

    def may_promote_without_asking(self) -> bool:
        return self.mode == "AUTOMATIC"

    def prompt(self) -> dict:
        """What to show a person who has not chosen yet."""
        return {
          "question": CONSENT_QUESTION,
          "options": [
            {"mode": "AUTOMATIC", "label": "Check upstream automatically",
             "detail": MODE_COPY["AUTOMATIC"], "recommended": True},
            {"mode": "ASK_FIRST", "label": "Ask me before updating",
             "detail": MODE_COPY["ASK_FIRST"]},
            {"mode": "NEVER", "label": "Never check for updates",
             "detail": MODE_COPY["NEVER"]},
          ],
          "changeable_later": "Settings › Updates",
          "no_check_until_answered": True,
        }



SOURCE_TYPES = ("git_repository", "release_artifact", "container_image", "model_repository",
                "dataset_catalogue", "format_specification", "rules_page")

STAGES = ("DISCOVERED", "FETCHED", "STAGED", "BUILD_PASSED", "CONTROL_PASSED", "ADMITTED",
          "CURRENT", "HELD", "INCOMPATIBLE", "QUARANTINED", "SUPERSEDED", "ROLLED_BACK",
          "CLASSIFIED", "LICENSE_CHECKED", "SYNTHETIC_CONTROL_PASSED",
          "REAL_DATA_CONTROL_PASSED")

NON_USABLE = frozenset({"DISCOVERED", "FETCHED", "STAGED", "BUILD_PASSED", "CONTROL_PASSED",
                        "HELD", "INCOMPATIBLE", "QUARANTINED", "ROLLED_BACK",
                        "CLASSIFIED", "LICENSE_CHECKED", "SYNTHETIC_CONTROL_PASSED",
                        "REAL_DATA_CONTROL_PASSED"})

CONVEYOR_STAGE_ALIASES = {
    "DISCOVERED": "DISCOVERED",
    "CLASSIFIED": "CLASSIFIED",
    "LICENSE_CHECKED": "LICENSE_CHECKED",
    "PINNED": "STAGED",
    "ADAPTER_BUILT": "BUILD_PASSED",
    "SYNTHETIC_CONTROL_PASSED": "SYNTHETIC_CONTROL_PASSED",
    "REAL_DATA_CONTROL_PASSED": "REAL_DATA_CONTROL_PASSED",
    "CANDIDATE": "ADMITTED",
    "OPERATOR_PROMOTED": "CURRENT",
}

HELD_REASONS = ("LICENSE_CHANGED", "SCIENCE_CONTRACT_CHANGED", "COORDINATE_SCHEMA_CHANGED",
                "EXPOSURE_UNKNOWN", "COMPATIBILITY_BROKEN", "EXPENSIVE_QUALIFICATION",
                "PROVENANCE_INCOMPLETE", "UNKNOWN_IMPACT", "OPERATOR_ASKED_TO_BE_ASKED",
                "UPSTREAM_REVISION_CHANGED")

CONVEYOR_SEQUENCE = ("DISCOVERED", "CLASSIFIED", "LICENSE_CHECKED", "STAGED", "BUILD_PASSED",
                    "SYNTHETIC_CONTROL_PASSED", "REAL_DATA_CONTROL_PASSED", "ADMITTED",
                    "CURRENT")

_INVARIANT_FIELDS = {
    "license": "LICENSE_CHANGED",
    "coordinate_schema_hash": "COORDINATE_SCHEMA_CHANGED",
    "upstream_revision": "UPSTREAM_REVISION_CHANGED",
}

WATCH_STATES = ("OK", "RATE_LIMITED", "OFFLINE", "ERROR", "NOT_CHECKED")


@dataclasses.dataclass(frozen=True)
class Source:
    """One upstream thing ARGUS depends on."""

    source_id: str
    endpoint: str
    owner: str
    classification: str
    capability_ids: tuple
    source_type: str
    watched_channel: str
    admitted_revision: str | None
    license: str | None
    auto_promotion_allowed: bool
    required_tests: tuple
    check_interval_s: int = 3600

    def __post_init__(self):
        if self.source_type not in SOURCE_TYPES:
            raise ConveyorError("unknown source type %r" % (self.source_type,))
        if not self.capability_ids:
            raise ConveyorError("source %s supplies no capability" % self.source_id)


@dataclasses.dataclass
class Observation:
    """What a cheap check saw."""

    source_id: str
    watch_state: str
    checked_utc: str | None = None
    observed_revision: str | None = None
    etag: str | None = None
    fetched: bool = False
    error: str | None = None

    def __post_init__(self):
        if self.watch_state not in WATCH_STATES:
            raise ConveyorError("unknown watch state %r" % (self.watch_state,))

    @property
    def is_up_to_date_claim_allowed(self) -> bool:
        """Rate limited and offline are NOT 'up to date'."""
        return self.watch_state == "OK"

    def status_line(self) -> str:
        if self.watch_state == "RATE_LIMITED":
            return "Could not check — the service is rate limiting us. Last known state stands."
        if self.watch_state == "OFFLINE":
            return "Could not check — no network. Last known state stands."
        if self.watch_state == "ERROR":
            return "Could not check — %s" % (self.error or "unknown error")
        if self.watch_state == "NOT_CHECKED":
            return "Not checked yet."
        return "Checked %s" % (self.checked_utc or "just now")


@dataclasses.dataclass
class Update:
    """A specific candidate revision moving along the conveyor."""

    source_id: str
    revision: str
    stage: str = "DISCOVERED"
    digest: str | None = None
    bytes_downloaded: int = 0
    deduplicated: bool = False
    license_at_revision: str | None = None
    gates_passed: tuple = ()
    gates_failed: tuple = ()
    held_reason: str | None = None
    impact: tuple = ()
    previous_admitted: str | None = None
    notes: str | None = None
    receipts: dict = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        if self.stage not in STAGES:
            raise ConveyorError("unknown stage %r" % (self.stage,))
        if len(self.revision) < 7:
            raise ConveyorError("a revision must be an immutable identifier, got %r"
                                % (self.revision,))

    @property
    def usable_for_new_work(self) -> bool:
        return self.stage in ("ADMITTED", "CURRENT")

    def hold(self, reason: str) -> "Update":
        if reason not in HELD_REASONS:
            raise ConveyorError("unknown hold reason %r" % (reason,))
        self.stage = "HELD"
        self.held_reason = reason
        return self


def resolve_pointer(pointer: str, resolver) -> str:
    """Turn a mutable tag into the immutable revision it currently names."""
    rev = resolver(pointer)
    if not rev or len(rev) < 7:
        raise ConveyorError("%r did not resolve to an immutable revision" % (pointer,))
    return rev



COMMON_GATES = ("source_identity", "immutable_revision", "build_succeeds", "deps_resolve",
                "license_reviewed", "no_secret_files", "schemas_parse", "api_compatible",
                "fixtures_deterministic", "controls_pass", "sabotage_still_fails",
                "no_verdict_change_without_receipt", "resources_within_policy",
                "rollback_package_exists")

CAPABILITY_GATES = {
  "surface_tracing": ("coordinate_frame", "level", "index_base", "validity", "topology"),
  "flattening": ("bijection", "orientation", "distortion", "reverse_mapping"),
  "ct_to_surface_sampling": ("axis_order", "coordinate_level", "scale", "depth_direction",
                             "validity_rules", "independent_control_agreement"),
  "adaptation": ("physical_field", "origin", "operation_order", "quantization",
                 "reverse_mapping"),
  "ink_inference": ("architecture_strict_load", "input_contract", "exposure_manifest",
                    "heldout_eligibility", "output_head_control", "full_forward_control",
                    "canonical_scoring_compatible", "license_admissible"),
  "review_active_learning": ("consensus_gate", "expert_gate", "planted_control_behaviour",
                             "identity_withheld", "immutable_dataset_version"),
}


def required_gates(source: Source) -> tuple:
    g = list(COMMON_GATES)
    for cid in source.capability_ids:
        g += list(CAPABILITY_GATES.get(cid, ()))
    return tuple(dict.fromkeys(g))


def evaluate(update: Update, source: Source, results: dict) -> Update:
    """Apply the gate results and decide the next stage."""
    need = required_gates(source)
    passed = tuple(g for g in need if results.get(g) is True)
    failed = tuple(g for g in need if results.get(g) is False)
    unknown = tuple(g for g in need if g not in results)
    update.gates_passed, update.gates_failed = passed, failed
    if failed:
        update.stage = "INCOMPATIBLE" if "api_compatible" in failed else "HELD"
        update.held_reason = update.held_reason or "COMPATIBILITY_BROKEN"
        return update
    if unknown:
        return update.hold("UNKNOWN_IMPACT")
    update.stage = "CONTROL_PASSED"
    return update


def promote(update: Update, source: Source, policy: UpdatePolicy, *,
            license_now: str | None, science_contract_changed: bool,
            coordinate_schema_changed: bool, exposure_known: bool,
            destination_busy: bool) -> Update:
    """The only path to ADMITTED."""
    if not policy.decided:
        raise ConveyorError("no update policy has been chosen yet; ARGUS must ask first")
    if update.stage not in ("CONTROL_PASSED", "REAL_DATA_CONTROL_PASSED"):
        raise ConveyorError("%s is %s and has not passed its controls"
                            % (update.revision[:12], update.stage))
    if license_now != source.license:
        return update.hold("LICENSE_CHANGED")
    if science_contract_changed:
        return update.hold("SCIENCE_CONTRACT_CHANGED")
    if coordinate_schema_changed:
        return update.hold("COORDINATE_SCHEMA_CHANGED")
    if not exposure_known:
        return update.hold("EXPOSURE_UNKNOWN")
    if destination_busy:
        return update.hold("COMPATIBILITY_BROKEN")
    if not source.auto_promotion_allowed or not policy.may_promote_without_asking():
        return update.hold("OPERATOR_ASKED_TO_BE_ASKED")
    update.previous_admitted = source.admitted_revision
    update.stage = "ADMITTED"
    return update


def roll_back(update: Update) -> Update:
    """Quarantine the bad revision, restore the previous digest, keep both records."""
    if not update.previous_admitted:
        raise ConveyorError("no previous admitted revision to roll back to")
    update.stage = "QUARANTINED"
    update.notes = ("rolled back to %s; this revision is preserved, not deleted"
                    % update.previous_admitted[:12])
    return update


def advance(update: Update, to_stage: str, *, receipt: dict) -> Update:
    """The enforced transition graph (Boundary 4)."""
    if to_stage not in CONVEYOR_SEQUENCE:
        raise ConveyorError(
            "%r is not on the enforced sequence %s" % (to_stage, CONVEYOR_SEQUENCE))
    if update.stage not in CONVEYOR_SEQUENCE:
        raise ConveyorError(
            "%s is at legacy stage %r, not on the enforced sequence -- advance() only moves "
            "an update that is already somewhere in %s"
            % (update.revision[:12], update.stage, CONVEYOR_SEQUENCE))
    cur_idx = CONVEYOR_SEQUENCE.index(update.stage)
    to_idx = CONVEYOR_SEQUENCE.index(to_stage)
    if to_idx != cur_idx + 1:
        raise ConveyorError(
            "cannot advance %s from %s to %s: the next stage must be %s, no skipping"
            % (update.revision[:12], update.stage, to_stage, CONVEYOR_SEQUENCE[cur_idx + 1]))
    if not receipt or not receipt.get("hashes"):
        raise ConveyorError(
            "advancing %s to %s requires a receipt with hashes; none given"
            % (update.revision[:12], to_stage))

    if to_stage == "CURRENT" and not receipt.get("operator_promoted"):
        raise ConveyorError(
            "CURRENT requires an explicit operator promotion in the receipt "
            "(operator_promoted=True) -- a candidate does not become current on its own")

    if to_stage == "SYNTHETIC_CONTROL_PASSED" and receipt.get("real_data"):
        raise ConveyorError(
            "a receipt claiming real_data may not be recorded as SYNTHETIC_CONTROL_PASSED -- "
            "use REAL_DATA_CONTROL_PASSED, or drop the claim")
    if to_stage == "REAL_DATA_CONTROL_PASSED" and not receipt.get("real_data"):
        raise ConveyorError(
            "REAL_DATA_CONTROL_PASSED requires the receipt to say real_data=True -- a "
            "synthetic-fixture pass may not impersonate a real-data one")

    for field, reason in _INVARIANT_FIELDS.items():
        if field not in receipt:
            continue
        for prior_stage, prior_receipt in update.receipts.items():
            if field in prior_receipt and prior_receipt[field] != receipt[field]:
                update.receipts[to_stage] = receipt
                return update.hold(reason)

    update.receipts[to_stage] = receipt
    update.stage = to_stage
    return update



@dataclasses.dataclass(frozen=True)
class ProjectPin:
    """Exactly what a project ran with."""

    project_id: str
    revision_of: dict
    capability_graph_version: str
    plan_hash: str
    created_utc: str

    def upgraded(self, new_pins: dict, *, now: str) -> "ProjectPin":
        return ProjectPin(project_id=self.project_id + "+1",
                          revision_of={**self.revision_of, **new_pins},
                          capability_graph_version=self.capability_graph_version,
                          plan_hash=self.plan_hash, created_utc=now)


def pins_unaffected(pins: Iterable[ProjectPin], update: Update) -> list:
    """Which existing projects keep their exact bytes."""
    return [p.project_id for p in pins
            if p.revision_of.get(update.source_id) != update.revision]



def next_check_delay(interval_s: int, consecutive_failures: int, *,
                     rng: random.Random | None = None) -> float:
    """Exponential backoff with jitter, so many clients do not hammer one service together."""
    rng = rng or random.Random()
    base = interval_s * (2 ** min(consecutive_failures, 6))
    return base * (0.75 + rng.random() * 0.5)


def freshness_report(source: Source, obs: Observation, *, staged_utc: str | None,
                     snapshot_utc: str | None, update: Update | None) -> dict:
    """Five separate freshnesses."""
    return {
      "source_id": source.source_id,
      "upstream_ref_checked": obs.checked_utc,
      "upstream_watch_state": obs.watch_state,
      "may_claim_up_to_date": obs.is_up_to_date_claim_allowed,
      "local_staged_source_utc": staged_utc,
      "generated_snapshot_utc": snapshot_utc,
      "refresh_actually_ran": obs.fetched,
      "update_stage": update.stage if update else None,
      "admitted_revision": source.admitted_revision,
      "observed_revision": obs.observed_revision,
      "status_line": obs.status_line(),
      "note": "a recent local timestamp is not evidence that upstream was checked",
    }


def content_address(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
