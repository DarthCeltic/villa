"""What each component permits, and therefore what ARGUS may ship, fetch or submit."""
from __future__ import annotations

import dataclasses

REGISTRY_ID = "argus-licence-registry-v1"

KIND = ("CODE", "MODEL_WEIGHTS", "DATA", "TOOL", "LABELS")

FAMILY = (
  "PERMISSIVE",
  "COPYLEFT",
  "NONCOMMERCIAL",
  "UNDECLARED",
  "PROPRIETARY",
)


class LicenceError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Component:
    name: str
    kind: str
    family: str
    spdx: str | None
    source: str
    evidence: str
    note: str = ""

    def __post_init__(self):
        if self.kind not in KIND:
            raise LicenceError("unknown kind %r" % (self.kind,))
        if self.family not in FAMILY:
            raise LicenceError("unknown family %r" % (self.family,))
        if not self.evidence.strip():
            raise LicenceError(
                "%s has no evidence for its licence claim. A licence field with nothing behind "
                "it cannot grant anything." % self.name)


    @property
    def may_run_locally(self) -> bool:
        return self.family != "PROPRIETARY"

    @property
    def may_bundle_in_installer(self) -> bool:
        return self.family == "PERMISSIVE"

    @property
    def may_redistribute(self) -> bool:
        return self.family == "PERMISSIVE"

    @property
    def is_part_of_the_method(self) -> bool:
        """Data is an input, not a method."""
        return self.kind in ("CODE", "TOOL", "MODEL_WEIGHTS")

    @property
    def may_enter_prize_submission(self) -> bool:
        """The prize requires open-sourcing the method under a permissive licence."""
        if not self.is_part_of_the_method:
            return True
        return self.family in ("PERMISSIVE", "COPYLEFT")

    def verdict(self) -> dict:
        return {
          "name": self.name, "kind": self.kind, "family": self.family, "spdx": self.spdx,
          "run_locally": self.may_run_locally,
          "bundle_in_installer": self.may_bundle_in_installer,
          "redistribute": self.may_redistribute,
          "part_of_the_method": self.is_part_of_the_method,
          "prize_submission": self.may_enter_prize_submission,
          "source": self.source, "evidence": self.evidence, "note": self.note,
        }


COMPONENTS = (
  Component(
    name="ARGUS", kind="CODE", family="PERMISSIVE", spdx="Apache-2.0",
    source="this repository",
    evidence="operator decision, 2026-09-10. Sole copyright holder; LICENSE carries the "
             "canonical Apache-2.0 text with the copyright line named in LICENSE. The prior MIT "
             "LICENSE is preserved at LICENSE.MIT.superseded rather than deleted, so the "
             "change is visible instead of silent.",
    note="THE GRANT COVERS ARGUS-OWNED CODE ONLY. It does not reach scan-derived artifacts "
         "(which inherit CC BY-NC 4.0 from the data terms), ink labels, or any model "
         "checkpoint -- model weights keep their own terms. A permissive "
         "licence on the code that produced an artifact does not launder the artifact's "
         "source terms. See NOTICE."),
  Component(
    name="kaggle (official python client)", kind="TOOL", family="PERMISSIVE",
    spdx="Apache-2.0", source="PyPI `kaggle` 2.2.4, installed into the project virtual environment only",
    evidence="package metadata: 'License :: OSI Approved :: Apache Software License', "
             "Copyright 2018 Kaggle Inc. Resolved version read from importlib.metadata at "
             "install time, not typed from documentation.",
    note="AN OPTIONAL DEPENDENCY, not a requirement. ARGUS must import, test and refuse "
         "without it: a correctness gate that needs a cloud account passes only for whoever "
         "holds one. Its credentials file is never read, hashed, logged or committed by "
         "ARGUS, and account identifiers are redacted out of every receipt."),
  Component(
    name="vc_render_tifxyz / volume-cartographer", kind="TOOL", family="COPYLEFT",
    spdx="GPL-3.0", source="ScrollPrize/villa",
    evidence="volume-cartographer/LICENSE (the villa repository ROOT LICENSE is MIT; "
             "GPL-3.0 applies only to the volume-cartographer subtree)",
    note="run as an EXTERNAL subprocess and never incorporated. That boundary is a choice "
         "ARGUS made, not a claim that copying GPL code is forbidden. If a build is ever "
         "distributed, the source-offer obligation attaches."),
  Component(
    name="hengck23 solution code", kind="CODE", family="PERMISSIVE", spdx="MIT",
    source="github.com/hengck23/solution-vesuvius-challenge-ink-detection",
    evidence="repository declares MIT"),
  Component(
    name="Third-party model weights (no declared licence)", kind="MODEL_WEIGHTS",
    family="UNDECLARED", spdx=None,
    source="any published checkpoint whose publisher declares no licence",
    evidence="a checkpoint with no declared licence is recorded as UNDECLARED; checked per "
             "checkpoint, not assumed",
    note="Undeclared weights cannot be redistributed and cannot enter a submission that "
         "must be open-sourced permissively. A permissive licence on the code that loads "
         "them does not cover the weights."),
  Component(
    name="Herculaneum CT volumes", kind="DATA", family="NONCOMMERCIAL", spdx="CC BY-NC 4.0",
    source="vesuvius-challenge-open-data S3",
    evidence="Vesuvius Challenge data terms",
    note="fetched from source at run time and never redistributed. The installer must not "
         "bundle a byte of it."),
  Component(
    name="ink_9um labels", kind="LABELS", family="UNDECLARED", spdx=None,
    source="hf://buckets/scrollprize/datasets/ink_9um",
    evidence="the dataset README declares no licence; the underlying CT is CC BY-NC 4.0",
    note="treated as non-redistributable. Publicly fetchable is not the same as "
         "redistributable, and the two are constantly confused."),
  Component(
    name="timm / torch / OpenCV / zarr", kind="CODE", family="PERMISSIVE",
    spdx="Apache-2.0 / BSD-3-Clause",
    source="PyPI",
    evidence="package metadata",
    note="fetched by the installer from authoritative sources with hashes recorded"),
  Component(
    name="scikit-learn (test only)", kind="CODE", family="PERMISSIVE",
    spdx="BSD-3-Clause",
    source="PyPI scikit-learn",
    evidence="declared 2026-09-11 in pyproject [project.optional-dependencies] test; "
             "installed version 1.9.1; BSD-3-Clause is permissive and checked, not assumed",
    note="used ONLY to prove the canonical metric against an independent implementation. Not "
         "bundled, not redistributed, and not part of any submission."),
)


def manifest() -> dict:
    rows = [c.verdict() for c in COMPONENTS]
    blockers = [r for r in rows if not r["prize_submission"]]
    return {
      "contract": REGISTRY_ID,
      "components": rows,
      "release_rules": {
        "bundle": [r["name"] for r in rows if r["bundle_in_installer"]],
        "fetch_at_runtime_never_bundle": [r["name"] for r in rows
                                          if not r["bundle_in_installer"]
                                          and r["run_locally"]],
        "external_subprocess_only": [r["name"] for r in rows if r["family"] == "COPYLEFT"],
        "public_build_is_an_allowlist": "the release is assembled from a list of what MAY "
                                        "ship, never by deleting private files from a full "
                                        "internal build. A deletion pass fails silently the "
                                        "first time someone adds a file.",
      },
      "prize_submission_blockers": [
        {"name": r["name"], "family": r["family"], "why": r["note"]} for r in blockers],
      "submission_consequence": (
        "model weights with an undeclared licence cannot enter a prize submission: a "
        "submission has to open-source the method and undeclared weights carry no grant."
        if any(r["kind"] == "MODEL_WEIGHTS" and not r["prize_submission"] for r in rows)
        else "no weights currently block a submission"),
      "unknown_is_a_refusal": "UNDECLARED components are refused in every column that requires "
                              "a grant. The absence of a licence is not a gap for the packager "
                              "to fill in.",
    }
