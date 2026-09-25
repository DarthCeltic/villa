"""The checkpoint-lifecycle contract: which learned state may go where, and which may not."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time

CONTRACT = "argus-checkpoint-lifecycle-v1"

CHECKPOINT_CLASSES = ("CP-GEOMETRY-GENERAL", "CP-PERCEPTION-GENERAL", "CP-REASONER-GENERAL")

PROMOTABLE_CLASSES = ("CP-REASONER-GENERAL",)

BRANCH_SCOPES = ("GENERAL", "PER_SCROLL")

CONTAMINATION_RISK_KINDS = (
  "LEARNED_PARAMETERS", "OPTIMIZER_STATE", "NORMALIZATION_STATISTICS", "THRESHOLDS",
  "PSEUDO_LABELS", "UNREVIEWED_EXEMPLARS",
)

PHYSICAL_EVIDENCE_KINDS = ("MESH", "SURFACE", "COORDINATE_MAP", "RENDERING", "CORRECTION")

ALL_ARTIFACT_KINDS = CONTAMINATION_RISK_KINDS + PHYSICAL_EVIDENCE_KINDS

MIN_SCROLLS_FOR_PROMOTION = 2

QUARANTINE_STATES = (
  "QUARANTINED", "MULTI_SCROLL_SUITE_RUNNING", "MULTI_SCROLL_SUITE_PASSED",
  "MULTI_SCROLL_SUITE_FAILED", "PROMOTED_TO_GENERAL",
)
QUARANTINE_INDEX = {s: i for i, s in enumerate(QUARANTINE_STATES)}

ADAPTATION_STATUS = ("EXPLORATORY", "BLESSED_GENERAL")


class LifecycleRefusal(ValueError):
    """Raised when an operation would let scroll-specific state travel somewhere the contract does not allow, or would treat exploratory work as qualifying evidence."""


def _sha(obj) -> str:
    return hashlib.sha256(
      json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())



def assert_no_lateral_contamination(*, artifact_kind: str, source_scroll_id: str | None,
                                    dest_scroll_id: str | None,
                                    dest_is_general: bool = False) -> None:
    """The single function every write path touching more than one scroll's directory must call before it copies or references an artifact across that boundary."""
    if artifact_kind not in ALL_ARTIFACT_KINDS:
        raise LifecycleRefusal("unknown artifact kind %r" % artifact_kind)
    is_risk = artifact_kind in CONTAMINATION_RISK_KINDS
    if dest_is_general:
        if is_risk:
            raise LifecycleRefusal(
              "%s may not move from scroll %r directly into a GENERAL checkpoint. The only "
              "route for scroll-specific learned state to become general is quarantine and "
              "the multi-scroll promotion suite (see promote_quarantined_improvement), which "
              "is a separate, explicit step -- never a side effect of copying a file."
              % (artifact_kind, source_scroll_id))
        return
    if source_scroll_id is not None and dest_scroll_id is not None and source_scroll_id != dest_scroll_id:
        raise LifecycleRefusal(
          "%s may not move laterally from scroll %r to scroll %r. %s"
          % (artifact_kind, source_scroll_id, dest_scroll_id,
             "This is exactly the contamination the checkpoint-lifecycle contract exists to "
             "prevent below Reasoning." if is_risk else
             "Physical evidence persists only under the scroll's own identity."))


def assert_may_promote_to_general(checkpoint_class: str) -> None:
    """Refuses unconditionally for Geometry and Perception -- see this module's header."""
    if checkpoint_class not in CHECKPOINT_CLASSES:
        raise LifecycleRefusal("unknown checkpoint class %r" % checkpoint_class)
    if checkpoint_class not in PROMOTABLE_CLASSES:
        raise LifecycleRefusal(
          "%s has no promotion path from a per-scroll branch to GENERAL. The directive this "
          "contract implements describes one for the Reasoner only; extending it to %s would "
          "be a new decision, not an inference this module is authorised to make."
          % (checkpoint_class, checkpoint_class))



@dataclasses.dataclass(frozen=True)
class EvidencePacketEntry:
    artifact_kind: str
    ref: str
    content_sha256: str
    utc: str

    def __post_init__(self):
        if self.artifact_kind not in PHYSICAL_EVIDENCE_KINDS:
            raise LifecycleRefusal(
              "an evidence packet holds physical evidence only (%s); %r is a learned-state "
              "kind and must never be sealed into a scroll's evidence packet"
              % (", ".join(PHYSICAL_EVIDENCE_KINDS), self.artifact_kind))


@dataclasses.dataclass
class EvidencePacket:
    """One scroll's immutable record of its own physical evidence."""

    scroll_id: str
    entries: list = dataclasses.field(default_factory=list)
    sealed: bool = False
    sealed_utc: str | None = None

    def add_entry(self, entry: EvidencePacketEntry) -> None:
        if self.sealed:
            raise LifecycleRefusal(
              "evidence packet for scroll %r is sealed; an immutable packet does not accept "
              "new entries -- start a new packet version instead" % self.scroll_id)
        self.entries.append(entry)

    def seal(self) -> str:
        if self.sealed:
            raise LifecycleRefusal("packet for scroll %r is already sealed" % self.scroll_id)
        self.sealed = True
        self.sealed_utc = _utc()
        return self.packet_sha256

    @property
    def packet_sha256(self) -> str:
        return _sha({"scroll_id": self.scroll_id,
                    "entries": [dataclasses.asdict(e) for e in self.entries]})

    def as_record(self) -> dict:
        return {"contract": CONTRACT, "scroll_id": self.scroll_id, "sealed": self.sealed,
               "sealed_utc": self.sealed_utc, "packet_sha256": self.packet_sha256,
               "entries": [dataclasses.asdict(e) for e in self.entries]}



@dataclasses.dataclass(frozen=True)
class FrozenQualification:
    """A snapshot taken BEFORE a holdout is opened."""

    reasoner_checkpoint_id: str
    reasoner_checkpoint_sha256: str
    exemplar_library_id: str
    exemplar_library_sha256: str
    frozen_utc: str

    @property
    def freeze_sha256(self) -> str:
        return _sha(dataclasses.asdict(self))


def freeze_for_blind_qualification(*, reasoner_checkpoint_id: str,
                                   reasoner_checkpoint_sha256: str, exemplar_library_id: str,
                                   exemplar_library_sha256: str) -> FrozenQualification:
    if len(reasoner_checkpoint_sha256) != 64 or len(exemplar_library_sha256) != 64:
        raise LifecycleRefusal(
          "both the reasoner checkpoint and the exemplar library must be pinned by full "
          "sha256, or 'frozen' is an assertion nobody can check")
    return FrozenQualification(
        reasoner_checkpoint_id=reasoner_checkpoint_id,
        reasoner_checkpoint_sha256=reasoner_checkpoint_sha256,
        exemplar_library_id=exemplar_library_id,
        exemplar_library_sha256=exemplar_library_sha256, frozen_utc=_utc(),
    )


def assert_still_frozen(freeze: FrozenQualification, *, current_checkpoint_sha256: str,
                        current_exemplar_sha256: str) -> None:
    """Called after a holdout evaluation, before its result may be reported as qualifying."""
    if current_checkpoint_sha256 != freeze.reasoner_checkpoint_sha256:
        raise LifecycleRefusal(
          "the reasoner checkpoint changed since this qualification was frozen (%s -> %s); "
          "the evaluation is not blind and may not be reported as qualifying"
          % (freeze.reasoner_checkpoint_sha256[:12], current_checkpoint_sha256[:12]))
    if current_exemplar_sha256 != freeze.exemplar_library_sha256:
        raise LifecycleRefusal(
          "the exemplar library changed since this qualification was frozen (%s -> %s); the "
          "evaluation is not blind and may not be reported as qualifying"
          % (freeze.exemplar_library_sha256[:12], current_exemplar_sha256[:12]))



@dataclasses.dataclass
class Adaptation:
    """One operational-discovery adaptation of the Reasoner against a specific scroll."""

    adaptation_id: str
    scroll_id: str
    checkpoint_class: str
    parent_checkpoint_id: str
    status: str = "EXPLORATORY"

    def __post_init__(self):
        if self.checkpoint_class not in CHECKPOINT_CLASSES:
            raise LifecycleRefusal("unknown checkpoint class %r" % self.checkpoint_class)
        if self.status not in ADAPTATION_STATUS:
            raise LifecycleRefusal("unknown adaptation status %r" % self.status)

    @property
    def may_cite_as_qualifying(self) -> bool:
        return self.status == "BLESSED_GENERAL"



@dataclasses.dataclass
class MultiScrollSuiteResult:
    scrolls_tested: tuple
    all_passed: bool
    detail: dict = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        if len(set(self.scrolls_tested)) != len(self.scrolls_tested):
            raise LifecycleRefusal("scrolls_tested must be distinct scrolls, not repeats of one")


@dataclasses.dataclass
class QuarantinedImprovement:
    """A scroll-specific Reasoner improvement, isolated until proven general."""

    quarantine_id: str
    source_adaptation: Adaptation
    state: str = "QUARANTINED"

    def __post_init__(self):
        assert_may_promote_to_general(self.source_adaptation.checkpoint_class)
        if self.state not in QUARANTINE_STATES:
            raise LifecycleRefusal("unknown quarantine state %r" % self.state)

    def _advance(self, to_state: str) -> None:
        i, j = QUARANTINE_INDEX[self.state], QUARANTINE_INDEX[to_state]
        if to_state == "PROMOTED_TO_GENERAL" and self.state != "MULTI_SCROLL_SUITE_PASSED":
            raise LifecycleRefusal(
              "%s may only be promoted from MULTI_SCROLL_SUITE_PASSED, not from %s"
              % (self.quarantine_id, self.state))
        if j <= i:
            raise LifecycleRefusal("%s -> %s is not a forward transition" % (self.state, to_state))
        self.state = to_state

    def begin_multi_scroll_suite(self) -> None:
        if self.state != "QUARANTINED":
            raise LifecycleRefusal("can only start the suite from QUARANTINED, not %s" % self.state)
        self._advance("MULTI_SCROLL_SUITE_RUNNING")

    def record_suite_result(self, result: MultiScrollSuiteResult) -> str:
        if self.state != "MULTI_SCROLL_SUITE_RUNNING":
            raise LifecycleRefusal("no suite is running for %s (state %s)"
                                  % (self.quarantine_id, self.state))
        distinct = set(result.scrolls_tested)
        if len(distinct) < MIN_SCROLLS_FOR_PROMOTION:
            self._advance("MULTI_SCROLL_SUITE_FAILED")
            raise LifecycleRefusal(
              "the multi-scroll suite tested %d distinct scroll(s); at least %d are required "
              "to call anything proven general. A suite that only re-tests the scroll the "
              "improvement came from proves the improvement fits its own source, not that it "
              "generalizes." % (len(distinct), MIN_SCROLLS_FOR_PROMOTION))
        if not result.all_passed:
            self._advance("MULTI_SCROLL_SUITE_FAILED")
            return self.state
        self._advance("MULTI_SCROLL_SUITE_PASSED")
        return self.state

    def promote(self) -> Adaptation:
        """Only reachable from MULTI_SCROLL_SUITE_PASSED."""
        self._advance("PROMOTED_TO_GENERAL")
        return Adaptation(
            adaptation_id=self.source_adaptation.adaptation_id + ":promoted",
            scroll_id=self.source_adaptation.scroll_id,
            checkpoint_class=self.source_adaptation.checkpoint_class,
            parent_checkpoint_id=self.source_adaptation.parent_checkpoint_id,
            status="BLESSED_GENERAL",
        )
