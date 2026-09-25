"""The seven execution capabilities on THREE independent axes, never collapsed."""
from __future__ import annotations

import dataclasses
from typing import Iterable

VOCAB_ID = "argus-capability-vocabulary-v2"

ROUTE = ("CT", "Surface", "Flatten", "Sample", "Detect", "Review")

AVAILABILITY = ("INSTALLED", "NOT_INSTALLED", "DEGRADED")
OPERATIONAL_VERIFICATION = ("CONTROL_PASSED", "TESTED", "UNTESTED", "FAILED")
SCIENTIFIC_ADMISSIBILITY = ("ADMISSIBLE", "RESEARCH_ONLY", "PLUMBING_ONLY", "UNQUALIFIED",
                            "NOT_APPLICABLE")


class VocabularyError(ValueError):
    """Raised when a capability would have to be described by a guess."""


@dataclasses.dataclass(frozen=True)
class Capability:
    """One execution capability."""

    key: str
    name: str
    does: str
    implementation: str | None
    produces_stage: str | None
    needs_stage: str | None
    scientific: bool
    why_it_is_not_the_renderer: str

    def __post_init__(self):
        for st in (self.produces_stage, self.needs_stage):
            if st is not None and st not in ROUTE:
                raise VocabularyError("unknown route stage %r" % (st,))


CAPABILITIES = (
  Capability(
    key="volume_viewer", name="Volume viewer",
    does="Displays raw CT and imagery that already exists on disk.",
    implementation="ARGUS plane service + PlaneViewer",
    produces_stage=None, needs_stage="CT", scientific=False,
    why_it_is_not_the_renderer="showing a CT plane reads bytes that already exist; it "
                               "creates no new scientific representation"),
  Capability(
    key="surface_geometry_viewer", name="Surface geometry viewer",
    does="Displays meshes, TIFXYZ and surface artifacts that already exist.",
    implementation="argus.core.meshview + MeshCanvas",
    produces_stage=None, needs_stage="Surface", scientific=False,
    why_it_is_not_the_renderer="drawing a lattice computed elsewhere is not producing a "
                               "surface"),
  Capability(
    key="flattening_engine", name="Flattening engine",
    does="Converts suitable surface geometry into a 2D/UV coordinate layout.",
    implementation="volume-cartographer flattening (external, GPL-3.0)",
    produces_stage="Flatten", needs_stage="Surface", scientific=True,
    why_it_is_not_the_renderer="it rearranges coordinates; it never touches the volume"),
  Capability(
    key="ct_to_surface_sampler", name="CT-to-surface sampler",
    does="Samples raw CT along qualified TIFXYZ coordinates to build the detector's "
         "surface/depth stack.",
    implementation="vc_render_tifxyz (external, GPL-3.0)",
    produces_stage="Sample", needs_stage="Flatten", scientific=True,
    why_it_is_not_the_renderer="this IS the operation people were calling the renderer. It "
                               "reads the volume and writes new scientific data. "
                               "Mechanical admission is not "
                               "scientific admissibility and never becomes it."),
  Capability(
    key="physical_pitch_adapter", name="Physical pitch adapter",
    does="Converts sampled data into the detector's declared physical input contract.",
    implementation="argus.core.physical_resample",
    produces_stage=None, needs_stage="Sample", scientific=True,
    why_it_is_not_the_renderer="it resamples an existing stack to a declared pitch; it "
                               "creates no new measurement"),
  Capability(
    key="ink_inference_engine", name="Ink inference engine",
    does="Runs a detector checkpoint over an adapted stack.",
    implementation="argus detector runtime",
    produces_stage="Detect", needs_stage="Sample", scientific=True,
    why_it_is_not_the_renderer="a detector consumes the sampler's output. Whether it runs, "
                               "whether it reproduces a reference, and whether its output is "
                               "admissible are three separate facts"),
  Capability(
    key="surface_tracing", name="Surface tracing and segmentation",
    does="Finds the sheet in the volume and produces the geometry everything downstream "
         "depends on.",
    implementation="external surface-tracing tools (upstream)",
    produces_stage="Surface", needs_stage="CT", scientific=True,
    why_it_is_not_the_renderer="tracing decides WHERE the sheet is; the sampler reads the "
                               "volume along a trace that already exists. Confusing them "
                               "hides where each surface came from"),
  Capability(
    key="storage_hydration", name="Storage and hydration",
    does="Streams, caches, hydrates and evicts large CT stores so a volume larger than "
         "local disk can be read.",
    implementation="argus storage conveyor + fsspec/zarr chunk streaming",
    produces_stage=None, needs_stage="CT", scientific=False,
    why_it_is_not_the_renderer="moving bytes changes no measurement; but a wrong chunk "
                               "silently substituted would change every measurement, which "
                               "is why it is a capability and not a detail"),
  Capability(
    key="updater_component_manager", name="Updater and component manager",
    does="Fetches, pins, quarantines and promotes external components under governance.",
    implementation="argus.core.update_conveyor",
    produces_stage=None, needs_stage=None, scientific=False,
    why_it_is_not_the_renderer="it decides WHICH renderer you have. An updater that promoted "
                               "silently would change a frozen run underneath itself, which "
                               "is the failure this capability exists to prevent"),
  Capability(
    key="review_and_provenance", name="Review and provenance",
    does="Lets people inspect candidates, compare layers and promote supervision through the "
         "governed Review Lab states.",
    implementation="argus.core.review_lab",
    produces_stage="Review", needs_stage="Detect", scientific=False,
    why_it_is_not_the_renderer="it governs judgement about output; it produces none"),
)

BY_KEY = {c.key: c for c in CAPABILITIES}

FORBIDDEN_PHRASES = ("renderer installed", "renderer not installed", "renderer: installed",
                     "renderer missing", "the renderer is installed")

AGGREGATES = ("USABLE_AS_EVIDENCE", "USABLE_FOR_RESEARCH", "RUNS_BUT_NOT_ADMISSIBLE",
              "RUNS_UNVERIFIED", "BROKEN", "NOT_AVAILABLE", "DISPLAY_ONLY")


@dataclasses.dataclass(frozen=True)
class CapabilityState:
    """What is true about one capability on this machine, on all three axes."""

    key: str
    availability: str
    operational_verification: str
    scientific_admissibility: str
    verification_label: str | None = None
    version_or_hash: str | None = None
    license: str | None = None
    source: str | None = None
    last_successful_test_utc: str | None = None
    detail: str | None = None

    def __post_init__(self):
        if self.key not in BY_KEY:
            raise VocabularyError("unknown capability %r" % (self.key,))
        if self.availability not in AVAILABILITY:
            raise VocabularyError("unknown availability %r" % (self.availability,))
        if self.operational_verification not in OPERATIONAL_VERIFICATION:
            raise VocabularyError("unknown operational_verification %r"
                                  % (self.operational_verification,))
        if self.scientific_admissibility not in SCIENTIFIC_ADMISSIBILITY:
            raise VocabularyError("unknown scientific_admissibility %r"
                                  % (self.scientific_admissibility,))
        if (self.operational_verification in ("CONTROL_PASSED", "TESTED")
                and self.availability == "NOT_INSTALLED"):
            raise VocabularyError(
                "%s reports %s while NOT_INSTALLED. Something absent cannot have been run."
                % (self.key, self.operational_verification))
        if (self.operational_verification == "CONTROL_PASSED"
                and not self.last_successful_test_utc):
            raise VocabularyError(
                "%s claims CONTROL_PASSED with no successful test recorded. Verification is "
                "a measurement, not a setting." % self.key)
        if (self.scientific_admissibility == "ADMISSIBLE"
                and self.operational_verification != "CONTROL_PASSED"):
            raise VocabularyError(
                "%s claims ADMISSIBLE without a passed control. Admissibility is downstream "
                "of verification, never a substitute for it." % self.key)
        if (self.scientific_admissibility != "NOT_APPLICABLE"
                and not BY_KEY[self.key].scientific
                and self.scientific_admissibility != "UNQUALIFIED"):
            raise VocabularyError(
                "%s is not a scientific capability, so its admissibility is NOT_APPLICABLE. "
                "Giving a viewer a scientific verdict invites it to be cited as one."
                % self.key)

    @property
    def capability(self) -> Capability:
        return BY_KEY[self.key]


    def aggregate(self) -> dict:
        """One verdict, with the exact rule that produced it."""
        c = self.capability
        a, o, s = (self.availability, self.operational_verification,
                   self.scientific_admissibility)
        if a == "NOT_INSTALLED":
            return {"verdict": "NOT_AVAILABLE",
                    "rule": "availability == NOT_INSTALLED"}
        if o == "FAILED":
            return {"verdict": "BROKEN",
                    "rule": "operational_verification == FAILED"}
        if a == "DEGRADED":
            return {"verdict": "RUNS_UNVERIFIED",
                    "rule": "availability == DEGRADED (present but impaired)"}
        if not c.scientific:
            return {"verdict": "DISPLAY_ONLY",
                    "rule": "capability.scientific is False, so no scientific verdict "
                            "applies; it shows existing bytes"}
        if o == "UNTESTED":
            return {"verdict": "RUNS_UNVERIFIED",
                    "rule": "availability == INSTALLED and operational_verification == "
                            "UNTESTED"}
        if s == "ADMISSIBLE":
            return {"verdict": "USABLE_AS_EVIDENCE",
                    "rule": "scientific_admissibility == ADMISSIBLE and "
                            "operational_verification == CONTROL_PASSED"}
        if s == "RESEARCH_ONLY":
            return {"verdict": "USABLE_FOR_RESEARCH",
                    "rule": "scientific_admissibility == RESEARCH_ONLY"}
        return {"verdict": "RUNS_BUT_NOT_ADMISSIBLE",
                "rule": "operational_verification in (CONTROL_PASSED, TESTED) and "
                        "scientific_admissibility in (PLUMBING_ONLY, UNQUALIFIED)"}

    @property
    def may_produce_evidence(self) -> bool:
        """The only boolean this module exposes, and it is derived from the aggregate."""
        return self.aggregate()["verdict"] in ("USABLE_AS_EVIDENCE", "DISPLAY_ONLY")

    def human_status(self) -> str:
        """Level 1 language."""
        c = self.capability
        if self.availability == "NOT_INSTALLED":
            return "Not available — %s is not installed" % c.name
        if self.availability == "DEGRADED":
            return "Action needed — %s is installed but impaired" % c.name
        if self.operational_verification == "FAILED":
            return "%s runs, but a check did not pass" % c.name
        if self.operational_verification == "UNTESTED":
            return "%s is installed but has not been checked" % c.name
        if not c.scientific:
            return "%s is installed and checked" % c.name
        if self.scientific_admissibility == "ADMISSIBLE":
            return "%s is checked and admissible as evidence" % c.name
        if self.scientific_admissibility == "RESEARCH_ONLY":
            return "%s is checked; research use only" % c.name
        if self.scientific_admissibility == "PLUMBING_ONLY":
            return ("%s is checked, but no scientific route admits its output yet" % c.name)
        return "%s is checked but not yet qualified" % c.name

    def as_dict(self) -> dict:
        c = self.capability
        agg = self.aggregate()
        return {
          "key": self.key, "name": c.name, "does": c.does,
          "implementation": c.implementation,
          "produces_stage": c.produces_stage, "needs_stage": c.needs_stage,
          "scientific": c.scientific,
          "availability": self.availability,
          "operational_verification": self.operational_verification,
          "verification_label": self.verification_label,
          "scientific_admissibility": self.scientific_admissibility,
          "axes_are_independent": True,
          "aggregate": agg["verdict"], "aggregate_rule": agg["rule"],
          "may_produce_evidence": self.may_produce_evidence,
          "version_or_hash": self.version_or_hash, "license": self.license,
          "source": self.source,
          "last_successful_test_utc": self.last_successful_test_utc,
          "human_status": self.human_status(),
          "detail": self.detail,
        }


def route_status(states: Iterable[CapabilityState], *, have_stages: Iterable[str]) -> list:
    """The CT -> Surface -> Flatten -> Sample -> Detect -> Review strip, per stage."""
    have = set(have_stages)
    by_produces = {s.capability.produces_stage: s for s in states
                   if s.capability.produces_stage}
    out = []
    for stage in ROUTE:
        if stage in have:
            out.append({"stage": stage, "state": "available",
                        "label": "%s available" % stage, "blocking_capability": None})
            continue
        st = by_produces.get(stage)
        if st is None:
            out.append({"stage": stage, "state": "not_available",
                        "label": "Not available", "blocking_capability": None})
            continue
        agg = st.aggregate()["verdict"]
        if agg == "NOT_AVAILABLE":
            state, label = ("action_needed",
                            "Action needed — install and qualify %s" % st.capability.name)
        elif agg in ("USABLE_AS_EVIDENCE", "USABLE_FOR_RESEARCH", "DISPLAY_ONLY"):
            state, label = "ready_to_run", "Ready to run %s" % st.capability.name
        elif agg == "BROKEN":
            state, label = "action_needed", "%s failed a check" % st.capability.name
        else:
            state, label = ("unqualified",
                            "%s runs but is not yet qualified" % st.capability.name)
        out.append({"stage": stage, "state": state, "label": label,
                    "blocking_capability": st.key,
                    "blocking_capability_name": st.capability.name,
                    "aggregate": agg, "human_status": st.human_status()})
    return out


def next_operation(states: Iterable[CapabilityState], *, have_stages: Iterable[str]) -> dict:
    """The single next thing, named by the capability that actually produces it."""
    rs = route_status(states, have_stages=have_stages)
    have = [r for r in rs if r["state"] == "available"]
    todo = [r for r in rs if r["state"] != "available"]
    if not todo:
        return {"complete": True, "headline": "Every stage of this route has an artifact",
                "route": rs}
    nxt = todo[0]
    last = have[-1]["stage"] if have else None
    if nxt["state"] == "action_needed":
        head = "%s available — next: install and qualify %s" % (
            last or "Nothing", nxt.get("blocking_capability_name", "the next stage"))
    elif nxt["state"] == "unqualified":
        head = "%s available — next: qualify %s" % (
            last or "Nothing", nxt["blocking_capability_name"])
    elif nxt["blocking_capability"]:
        head = "%s available — next: run %s" % (last or "Nothing",
                                                nxt["blocking_capability_name"])
    else:
        head = "%s available — next stage has no capability registered" % (last,)
    return {"complete": False, "headline": head, "stage": nxt["stage"],
            "blocking_capability": nxt["blocking_capability"],
            "state": nxt["state"], "route": rs}


def check_copy(text: str) -> list:
    """Forbidden generic-renderer phrases present in a string."""
    low = text.lower()
    return [p for p in FORBIDDEN_PHRASES if p in low]
