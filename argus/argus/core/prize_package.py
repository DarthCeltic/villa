"""Assemble a submission package OF A DECLARED KIND."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import pathlib

CONTRACT_ID = "argus-prize-package-v2"

REPO = pathlib.Path(_argus_public_path('repo', ''))

PACKAGE_KINDS = ("PROGRESS_TOOLING", "FIRST_LETTERS_DISCOVERY", "PARIS4_TITLE", "GRAND_PRIZE")

CLAIM_CLASSES = ("PIPELINE_PASSED", "KNOWN_DOMAIN_CONTROL_READ",
                 "EXPLORATORY_TARGET_SIGNAL", "QUALIFIED_CROSS_SCROLL_DETECTOR",
                 "TOOLING_CONTRIBUTION", "HONEST_REFUSAL",
                 "DIRECT_VISIBLE_INK", "SCROLL_SPECIFIC_DETECTOR_READ")

UNREAD_SCROLL_CLASS = "QUALIFIED_CROSS_SCROLL_DETECTOR"

DETECTOR_BACKED_CLASSES = ("QUALIFIED_CROSS_SCROLL_DETECTOR", "SCROLL_SPECIFIC_DETECTOR_READ",
                           "EXPLORATORY_TARGET_SIGNAL", "KNOWN_DOMAIN_CONTROL_READ")

MIN_LEGIBLE_LETTERS = 10
MAX_REGION_CM2 = 4.0

MIN_READABLE_COVERAGE = 0.90

_DISCOVERY_CLASSES = ("DIRECT_VISIBLE_INK", "SCROLL_SPECIFIC_DETECTOR_READ",
                      "QUALIFIED_CROSS_SCROLL_DETECTOR")

KIND_POLICY = {
  "PROGRESS_TOOLING": {
    "what": "a pipeline, fix, dataset, diagnostic or documented refusal. Not a reading.",
    "headline_gate": False,
    "why_no_headline_gate": "nothing here is credited to a detector, so whether any detector "
                            "generalises across scrolls is not a fact about this package. "
                            "Refusing a dataset tool because NO_QUALIFIED_DETECTOR stands is "
                            "refusing it for someone else's reason.",
    "licence_declaration": "SHIPPED_ONLY",
    "allowed_classes": ("TOOLING_CONTRIBUTION", "HONEST_REFUSAL", "PIPELINE_PASSED",
                        "KNOWN_DOMAIN_CONTROL_READ"),
    "primary_classes": ("TOOLING_CONTRIBUTION", "HONEST_REFUSAL"),
    "unread_scroll_classes": (),
    "required_evidence": (),
    "one_of_evidence": ("operational_pipeline_proof", "real_data_control",
                        "bug_fix_before_after", "usability_improvement",
                        "standard_format_support", "honest_refusal"),
    "extra_rules": (),
  },
  "FIRST_LETTERS_DISCOVERY": {
    "what": "ten legible letters inside one 4 cm^2 region of an eligible unread scroll.",
    "headline_gate": True,
    "why_no_headline_gate": None,
    "licence_declaration": "SHIPPED_AND_CREDITED",
    "allowed_classes": ("DIRECT_VISIBLE_INK", "SCROLL_SPECIFIC_DETECTOR_READ",
                        "QUALIFIED_CROSS_SCROLL_DETECTOR", "KNOWN_DOMAIN_CONTROL_READ",
                        "HONEST_REFUSAL"),
    "primary_classes": _DISCOVERY_CLASSES,
    "unread_scroll_classes": _DISCOVERY_CLASSES,
    "required_evidence": ("target_scroll", "tifxyz", "static_programmatic_image", "scale_bar",
                          "letter_dimensions", "row_annotation", "false_positive_controls",
                          "held_out_validation"),
    "one_of_evidence": (),
    "extra_rules": ("legible_letters >= %d" % MIN_LEGIBLE_LETTERS,
                    "0 < region_area_cm2 <= %.1f" % MAX_REGION_CM2,
                    "training_overlap_segments == 0, stated",
                    "target_scroll != PHercParis4 (that is the PARIS4_TITLE lane)"),
  },
  "PARIS4_TITLE": {
    "required_target_scroll": "PHercParis4",
    "what": "the title region of PHercParis4, on any qualifying scan including 2.4 um.",
    "headline_gate": True,
    "why_no_headline_gate": None,
    "licence_declaration": "SHIPPED_AND_CREDITED",
    "allowed_classes": ("DIRECT_VISIBLE_INK", "SCROLL_SPECIFIC_DETECTOR_READ",
                        "QUALIFIED_CROSS_SCROLL_DETECTOR", "KNOWN_DOMAIN_CONTROL_READ",
                        "HONEST_REFUSAL"),
    "primary_classes": _DISCOVERY_CLASSES,
    "unread_scroll_classes": _DISCOVERY_CLASSES,
    "required_evidence": ("title_region_context", "tifxyz", "static_programmatic_image",
                          "scale_bar", "letter_dimensions", "method",
                          "false_positive_controls", "held_out_validation",
                          "exposure_accounting"),
    "one_of_evidence": (),
    "extra_rules": ("target_scroll == PHercParis4",
                    "training_overlap_segments == 0, stated"),
  },
  "GRAND_PRIZE": {
    "what": "a complete, readable recto surface of one scroll.",
    "headline_gate": True,
    "why_no_headline_gate": None,
    "licence_declaration": "SHIPPED_AND_CREDITED",
    "allowed_classes": ("DIRECT_VISIBLE_INK", "SCROLL_SPECIFIC_DETECTOR_READ",
                        "QUALIFIED_CROSS_SCROLL_DETECTOR", "KNOWN_DOMAIN_CONTROL_READ",
                        "HONEST_REFUSAL"),
    "primary_classes": _DISCOVERY_CLASSES,
    "unread_scroll_classes": _DISCOVERY_CLASSES,
    "required_evidence": ("target_scroll", "complete_recto_surface", "column_ordered_tifxyz",
                          "static_programmatic_images", "whole_scroll_banner",
                          "vc3d_integration", "reproducibility"),
    "one_of_evidence": (),
    "extra_rules": ("readable_coverage_fraction >= %.2f" % MIN_READABLE_COVERAGE,
                    "documented_human_effort_hours > 0",
                    "training_overlap_segments == 0, stated"),
  },
}

REQUIREMENT_SCOPE = (
  {"requirement": "evidence_chain", "applies_to": PACKAGE_KINDS,
   "why": "a package that points at evidence it cannot produce is worse than one with fewer "
          "claims. Nothing about the kind of package changes that."},
  {"requirement": "claim_class", "applies_to": PACKAGE_KINDS,
   "why": "an unlabelled claim defaults to the strongest reading in the reader's head. The "
          "VOCABULARY is per kind, because the honest label for a dataset tool is not on the "
          "same list as the honest label for a reading."},
  {"requirement": "licence_declaration", "applies_to": PACKAGE_KINDS,
   "scope": "shipped components for PROGRESS_TOOLING; shipped AND credited for the discovery "
            "kinds",
   "why": "an UNDECLARED grant blocks redistribution always, and blocks submission of a METHOD "
          "that depends on it. It must not block a package that neither ships nor credits it."},
  {"requirement": "redistribution", "applies_to": PACKAGE_KINDS,
   "scope": "shipped components only",
   "why": "a copyleft tool invoked as an external subprocess is not redistributed by being "
          "named in a method section."},
  {"requirement": "exposure", "applies_to": PACKAGE_KINDS,
   "scope": "only when a claim credits a detector; a credited detector's recorded exposure to its target is "
            "disqualifying for FIRST_LETTERS_DISCOVERY and GRAND_PRIZE, and is an ACCOUNTING "
            "requirement rather than a disqualification inside PARIS4_TITLE",
   "why": "a submission cannot present a scroll the model was trained from as evidence of "
          "generalisation. Paris4's own lane is not a generalisation claim, so the same fact "
          "obliges disclosure there instead of refusal."},
  {"requirement": "headline", "applies_to": ("FIRST_LETTERS_DISCOVERY", "PARIS4_TITLE",
                                             "GRAND_PRIZE"),
   "scope": "exempt where every unread-scroll claim rests on DIRECT_VISIBLE_INK, or on a "
            "SCROLL_SPECIFIC_DETECTOR_READ whose disjoint train/validation/hunt contract holds",
   "why": "NO_QUALIFIED_DETECTOR is a fact about CROSS-SCROLL generalisation. Requiring it of "
          "a method that never claimed to generalise is requiring the wrong evidence; "
          "requiring it of a method that DOES claim to generalise is the whole point."},
  {"requirement": "kind_requirements", "applies_to": PACKAGE_KINDS,
   "why": "each prize asks for evidence the others do not. This is also what keeps the kinds "
          "mutually exclusive in practice rather than on paper."},
)


class PackageRefused(RuntimeError):
    """Raised with every reason at once."""


def _policy(package_kind: str) -> dict:
    if package_kind not in KIND_POLICY:
        raise PackageRefused(
            "package_kind %r is not one of %s. This module cannot judge a package it has not "
            "been told the purpose of, and there is no default: a default is how a tooling "
            "package silently gets judged as a discovery claim."
            % (package_kind, list(PACKAGE_KINDS)))
    return KIND_POLICY[package_kind]


def _evidence_chain(claims: list) -> list:
    """Unchanged from v1, and correct in v1: every claim cites a receipt that resolves and still hashes."""
    from argus.core.receipts import sha_file
    problems = []
    for c in claims:
        rel = c.get("receipt")
        if not rel:
            problems.append("claim %r cites no receipt" % c.get("id", "?"))
            continue
        from argus.core import paths as _paths
        p = _paths.resolve_repo_relative(rel)
        if not p.is_file():
            problems.append("claim %r cites a receipt that is not on disk: %s"
                            % (c.get("id", "?"), rel))
            continue
        want = c.get("receipt_sha256")
        if want and sha_file(p) != want:
            problems.append("claim %r cites %s whose hash no longer matches -- the package "
                            "would point at evidence it cannot produce"
                            % (c.get("id", "?"), rel))
    return problems


def _licences(package_kind: str, ev: dict) -> list:
    """Scoped to what this package actually SHIPS, plus what a discovery package CREDITS."""
    from argus.core import licence_registry as LR
    ships = [str(x) for x in (ev.get("ships") or [])]
    credits = [str(x) for x in (ev.get("credits") or [])]
    declare_credited = _policy(package_kind)["licence_declaration"] == "SHIPPED_AND_CREDITED"

    by_name = {c["name"]: c for c in LR.manifest()["components"]}
    problems = []
    for name in sorted(set(ships) | set(credits)):
        comp = by_name.get(name)
        if comp is None:
            problems.append("%r is %s by this package but is not in the licence registry. An "
                            "unregistered component is UNDECLARED by default -- failing open "
                            "here would make the registry optional."
                            % (name, "shipped" if name in ships else "credited"))
            continue
        shipped = name in ships
        credited = name in credits
        if comp["family"] == "UNDECLARED" and (shipped or (credited and declare_credited)):
            problems.append("%s carries NO licence grant and is %s by this package. UNDECLARED "
                            "is a refusal, not a gap for the packager to fill in."
                            % (name, "shipped" if shipped else "credited as part of the method"))
        if shipped and not comp["redistribute"]:
            problems.append("%s is SHIPPED in this package and may not be redistributed (%s). "
                            "Crediting it as an external tool would be fine; including it is "
                            "not." % (name, comp["spdx"] or comp["family"]))
    return problems


def _is_detector_backed(claim: dict) -> bool:
    return bool(claim.get("credits_detector")
                or claim.get("claim_class") in DETECTOR_BACKED_CLASSES)


def _exposure(package_kind: str, claims: list, ev: dict) -> list:
    """Fires only where a detector is actually credited."""
    from argus.core import project_state as PS
    backed = [c for c in claims if _is_detector_backed(c)]
    if not backed:
        return []

    problems = []
    decl = ev.get("detector_exposure") or {}
    exposed = [str(s) for s in (decl.get("exposed_scrolls") or [])]
    target = ev.get("target_scroll")
    if not decl:
        problems.append(
          "claim(s) %s credit a detector but the package declares no `detector_exposure`. "
          "Unrecorded exposure is a refusal: a package cannot show that a model had not seen "
          "what it is credited with reading by not saying."
          % ", ".join(repr(c.get("id", "?")) for c in backed))
    elif target and target in exposed and package_kind != "PARIS4_TITLE":
        problems.append(
          "the credited detector's own exposure list contains %s, which is the scroll it is "
          "credited with reading. A model cannot be evidence of generalisation to material it "
          "was trained from." % target)

    return problems


def _claim_classes(package_kind: str, claims: list) -> list:
    """Vocabulary, primary claim, and who may speak about an unread scroll -- all per kind."""
    pol = _policy(package_kind)
    allowed = pol["allowed_classes"]
    licensed = pol["unread_scroll_classes"]
    problems = []
    for c in claims:
        k = c.get("claim_class")
        if k not in CLAIM_CLASSES:
            problems.append("claim %r has class %r, which is not one of %s. An unlabelled "
                            "claim defaults to the strongest reading in the reader's head."
                            % (c.get("id", "?"), k, list(CLAIM_CLASSES)))
            continue
        if k not in allowed:
            problems.append("claim %r is classed %r, which a %s package does not admit "
                            "(admits %s). Passing one kind must never satisfy another."
                            % (c.get("id", "?"), k, package_kind, list(allowed)))
        if c.get("about_unread_scroll"):
            if not licensed:
                problems.append(
                  "claim %r is about ink in an unread scroll, and a %s package may make no such "
                  "claim at any class. The pipeline running is evidence about the pipeline."
                  % (c.get("id", "?"), package_kind))
            elif k not in licensed:
                problems.append("claim %r is about an unread scroll but is classed %r. In a %s "
                                "package only %s licenses that."
                                % (c.get("id", "?"), k, package_kind, list(licensed)))

    if not any(c.get("claim_class") in pol["primary_classes"] for c in claims):
        problems.append("a %s package must carry at least one claim classed %s. A package with "
                        "no claim of the sort its kind exists to make is not that kind of "
                        "package." % (package_kind, list(pol["primary_classes"])))
    return problems


def _scroll_specific_contract(ev: dict) -> list:
    """The price of skipping the headline gate: prove the method is scroll-specific."""
    m = ev.get("method") or {}
    problems = []
    if not m.get("scroll_specific"):
        problems.append("the method does not declare itself scroll-specific, so the headline "
                        "cross-scroll gate is the one that applies to it.")
    sets = {}
    for key in ("train_segments", "validation_segments", "hunt_segments"):
        vals = [str(x) for x in (m.get(key) or [])]
        if not vals:
            problems.append("method declares no %s. A scroll-specific contract with an empty "
                            "%s is not a contract." % (key, key))
        sets[key] = set(vals)
    pairs = (("train_segments", "validation_segments"), ("train_segments", "hunt_segments"),
             ("validation_segments", "hunt_segments"))
    for a, b in pairs:
        both = sorted(sets[a] & sets[b])
        if both:
            problems.append("%s and %s overlap on %s. Disjointness is the entire content of "
                            "the claim -- a segment on both sides is a detector scored on its "
                            "own training material." % (a, b, both))
    return problems


def _headline(package_kind: str, claims: list, ev: dict) -> tuple:
    """Returns (problems, scope) -- the cross-scroll gate, scoped and exemptible."""
    from argus.core import project_state as PS
    pol = _policy(package_kind)
    if not pol["headline_gate"]:
        return [], {"applies": False, "why": pol["why_no_headline_gate"]}

    classes = [c.get("claim_class") for c in claims]
    reading = [c for c in claims
               if c.get("claim_class") in _DISCOVERY_CLASSES or c.get("about_unread_scroll")]
    problems = []

    if reading and all(c.get("claim_class") == "DIRECT_VISIBLE_INK" for c in reading):
        return [], {"applies": True, "exempt": True, "route": "DIRECT_VISIBLE_INK",
                    "why": "the ink is directly visible in a static programmatic image. No "
                           "detector is part of the argument."}

    if "SCROLL_SPECIFIC_DETECTOR_READ" in classes \
            and "QUALIFIED_CROSS_SCROLL_DETECTOR" not in classes:
        contract = _scroll_specific_contract(ev)
        if not contract:
            return [], {"applies": True, "exempt": True,
                        "route": "SCROLL_SPECIFIC_DETECTOR_READ",
                        "why": "the submitted method is explicitly scroll-specific and its "
                               "train/validation/hunt sets are disjoint. Universal cross-scroll "
                               "qualification is not the evidence this method claims."}
        problems.extend(contract)

    headline = PS.detector_state()
    if headline.get("state") != "QUALIFIED_CROSS_SCROLL_DETECTOR":
        problems.append(
          "the project headline is %s, and this package rests on a detector presented as "
          "general. No unread-scroll reading may appear on that basis while that stands, "
          "whatever any AUC says." % headline.get("state"))
    return problems, {"applies": True, "exempt": False,
                      "headline_state": headline.get("state")}


def _stated_zero_overlap(ev: dict) -> list:
    """`training_overlap_segments` must be present and zero."""
    if "training_overlap_segments" not in ev:
        return ["the package does not state `training_overlap_segments`. Unstated overlap is "
                "not zero overlap -- a submitted region that overlaps training data is the "
                "failure this requirement exists to catch."]
    n = ev.get("training_overlap_segments")
    if n != 0:
        return ["the package states %r training-overlap segment(s). No submitted region may "
                "overlap training data." % (n,)]
    return []


def _kind_requirements(package_kind: str, ev: dict) -> list:
    """The evidence that kind's prize rules ask for, and nothing another kind's rules ask for."""
    pol = _policy(package_kind)
    problems = []
    for key in pol["required_evidence"]:
        if not ev.get(key):
            problems.append("a %s package requires evidence %r, which is absent or empty."
                            % (package_kind, key))
    one_of = pol["one_of_evidence"]
    if one_of and not any(ev.get(k) for k in one_of):
        problems.append("a %s package must carry at least one of %s. A package that "
                        "demonstrates nothing is not a contribution."
                        % (package_kind, list(one_of)))

    if package_kind == "FIRST_LETTERS_DISCOVERY":
        letters = ev.get("legible_letters")
        if not isinstance(letters, int) or letters < MIN_LEGIBLE_LETTERS:
            problems.append("First Letters needs %d legible letters; the package states %r."
                            % (MIN_LEGIBLE_LETTERS, letters))
        area = ev.get("region_area_cm2")
        if not isinstance(area, (int, float)) or not 0 < float(area) <= MAX_REGION_CM2:
            problems.append("First Letters needs those letters inside ONE region of at most "
                            "%.1f cm^2; the package states %r. Ten letters spread over a whole "
                            "segment is a different, weaker claim."
                            % (MAX_REGION_CM2, area))
        reserved = KIND_POLICY["PARIS4_TITLE"].get("required_target_scroll")
        if reserved and str(ev.get("target_scroll")) == reserved:
            problems.append("%s has its own lane. Assemble this as PARIS4_TITLE, which admits "
                            "any qualifying scan of it including 2.4 um and requires exposure "
                            "accounting that generic First Letters does not." % reserved)
        problems.extend(_stated_zero_overlap(ev))

    elif package_kind == "PARIS4_TITLE":
        want = KIND_POLICY["PARIS4_TITLE"].get("required_target_scroll")
        if want and str(ev.get("target_scroll")) != want:
            problems.append("PARIS4_TITLE is the %s title lane; this package targets %r. Use "
                            "FIRST_LETTERS_DISCOVERY for any other scroll."
                            % (want, ev.get("target_scroll")))
        problems.extend(_stated_zero_overlap(ev))

    elif package_kind == "GRAND_PRIZE":
        cov = ev.get("readable_coverage_fraction")
        if not isinstance(cov, (int, float)) or float(cov) < MIN_READABLE_COVERAGE:
            problems.append("the Grand Prize needs readable coverage of at least %.2f of the "
                            "recto surface; the package states %r. A produced surface is not a "
                            "read one." % (MIN_READABLE_COVERAGE, cov))
        hours = ev.get("documented_human_effort_hours")
        if not isinstance(hours, (int, float)) or float(hours) <= 0:
            problems.append("the Grand Prize requires DOCUMENTED human effort; the package "
                            "states %r hours. Undocumented effort cannot be reviewed."
                            % (hours,))
        problems.extend(_stated_zero_overlap(ev))

    return problems


def _publication() -> dict:
    """The posture, recorded in every result and written into every assembled package."""
    from argus.core import publication_lock as PL
    return PL.posture()


def posture() -> dict:
    """The claim posture any packet built beside this module must carry, without judging a package."""
    return {
      "contract": CONTRACT_ID,
      "claim_classes": list(CLAIM_CLASSES),
      "honest_refusal_class": "HONEST_REFUSAL",
      "unread_scroll_class": UNREAD_SCROLL_CLASS,
      "package_kinds": list(PACKAGE_KINDS),
      "prize_eligibility_claimed": False,
      "unread_scroll_reading_claimed": False,
      "unread_scroll_rule": "a statement about an unread scroll needs a claim class that licenses "
                            "one (%s, or a scoped route that proves its own contract). A packet of "
                            "human review records holds none, so it makes no such statement."
                            % UNREAD_SCROLL_CLASS,
      "publication": _publication(),
      "never_submits": "assembling a packet is not submitting or publishing it; this module has no "
                       "upload path and publication_lock has no override flag.",
    }


def _volume_identity(package_kind: str, ev: dict, registry, registry_source) -> list:
    """PACKAGE_EXPORT checkpoint of `volume_id_gate`."""
    if package_kind == "PROGRESS_TOOLING":
        return []
    from argus.core import volume_id_gate as VG
    try:
        VG.before_package_export(scroll=ev.get("target_scroll"),
                                 declared_volume_id=ev.get("volume_id"),
                                 package_volume_ids=ev.get("package_volume_ids"),
                                 registry=registry, registry_source=registry_source)
    except VG.VolumeIdRefusal as e:
        return ["volume id not verified at package export: %s" % e]
    return []


def assemble(claims: list, *, package_kind: str, out_dir, evidence: dict = None,
             screenshots=None, dry_run: bool = True, volume_registry=None,
             volume_registry_source: str | None = None) -> dict:
    """Build a package of `package_kind`, or refuse with every applicable reason at once."""
    ev = dict(evidence or {})
    pol = _policy(package_kind)

    headline_problems, headline_scope = _headline(package_kind, claims, ev)
    blockers = {
      "evidence_chain": _evidence_chain(claims),
      "claim_class": _claim_classes(package_kind, claims),
      "licence": _licences(package_kind, ev),
      "exposure": _exposure(package_kind, claims, ev),
      "headline": headline_problems,
      "kind_requirements": _kind_requirements(package_kind, ev),
      "volume_identity": _volume_identity(package_kind, ev, volume_registry,
                                          volume_registry_source),
    }

    total = sum(len(v) for v in blockers.values())
    result = {
      "contract": CONTRACT_ID,
      "package_kind": package_kind,
      "package_kind_is": pol["what"],
      "assembled": False,
      "dry_run": dry_run,
      "blockers": {k: v for k, v in blockers.items() if v},
      "blocker_count": total,
      "claims_offered": len(claims),
      "headline_gate": headline_scope,
      "requirement_scope": [dict(r) for r in REQUIREMENT_SCOPE],
      "publication": _publication(),
      "never_submits": "this module has no upload, no external POST, no release call, for any "
                       "kind. Assembling is not publishing, and publication_lock refuses that "
                       "separately with no override flag.",
      "kinds_are_exclusive": "satisfying one package kind does not satisfy another. Each kind "
                             "requires evidence and admits claim classes the others do not.",
    }
    if package_kind == "PROGRESS_TOOLING":
        result["why_refusal_is_expected_today"] = (
          "it is NOT expected for this kind. A tooling package credits no detector, so the "
          "headline and exposure standing facts do not bear on it. If this refuses, the reason "
          "is in this package.")
    else:
        result["why_refusal_is_expected_today"] = (
          "a discovery package that rests on a detector presented as general is refused while "
          "the headline is NO_QUALIFIED_DETECTOR. That is the module working as designed "
          "rather than failing.")

    if total:
        result["refused_because"] = ("all applicable reasons are reported together. Fixing one "
                                     "blocker at a time costs a round trip per blocker.")
        return result

    out = pathlib.Path(out_dir)
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)
        (out / "CLAIMS.json").write_text(json.dumps(claims, indent=1), encoding="utf-8")
        (out / "PACKAGE_KIND.json").write_text(
            json.dumps({"contract": CONTRACT_ID, "package_kind": package_kind,
                        "is": pol["what"], "evidence": ev,
                        "headline_gate": headline_scope}, indent=1, default=str),
            encoding="utf-8")
        (out / "PUBLICATION.json").write_text(
            json.dumps(result["publication"], indent=1), encoding="utf-8")
    result.update({"assembled": True, "out_dir": out.as_posix(),
                   "screenshots": list(screenshots or [])})
    return result
