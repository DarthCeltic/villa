"""Assemble a PROPOSED READING from accepted stroke hypotheses, and never from anything else."""
from __future__ import annotations

import hashlib
import json

CONTRACT = "argus-text-assembly-v1"

OUTPUT_PLANE = "PROPOSED_READING"

FORBIDDEN_PLANES = ("PHYSICAL_INK_EVIDENCE", "STROKE_HYPOTHESIS", "RECONSTRUCTED_STRUCTURE")

ACCEPTED = "ACCEPTED"
ADMISSIBLE_STATES = (ACCEPTED,)
INADMISSIBLE_STATES = ("PROPOSED", "PENDING", "UNDER_REVIEW", "REJECTED", "WITHDRAWN",
                       "MACHINE_PROPOSED", "UNKNOWN")

GAP = "█"

GAP_RULE = (
  "a position with no supporting accepted stroke is emitted as a GAP. It is never filled, not "
  "even with the most probable letter marked uncertain, because the mark travels less well than "
  "the letter does: quoted once without its markup, a guessed character is indistinguishable "
  "from a read one."
)


class AssemblyRefusal(RuntimeError):
    """Raised when an assembly would present interpretation as measurement."""


def _sha(obj) -> str:
    return hashlib.sha256(
      json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def assert_controls_passed(physical_controls: dict) -> dict:
    """Refuse outright if the physical evidence did not pass its controls."""
    if not isinstance(physical_controls, dict) or not physical_controls:
        raise AssemblyRefusal(
          "no physical control results were supplied. An assembly with no controls beneath it "
          "cannot be distinguished from one whose controls failed, and the contract treats the "
          "second as hallucination. UNDECLARED is a refusal here, not a presumption.")
    failed = sorted(k for k, v in physical_controls.items() if not v)
    if failed:
        raise AssemblyRefusal(
          "refusing to assemble a reading over FAILED physical controls: %s.\n"
          "A language-plausible answer on a failed physical control is READER_HALLUCINATION by "
          "the frozen contract's definition. Emitting it with a warning is not a lesser version "
          "of this refusal -- fluent Greek beside a probability map reads as corroboration, and "
          "the warning is the part that gets dropped." % ", ".join(failed))
    return {"controls_passed": sorted(physical_controls), "count": len(physical_controls)}


def assert_sealed_promoted(obj) -> None:
    """A sealed review task is consumed by a reading only once two named people have reviewed it."""
    from argus.core import sealed_review_queue as SQ
    try:
        SQ.assert_promoted(obj)
    except SQ.UnreviewedTaskRefused as exc:
        raise AssemblyRefusal(str(exc)) from exc


def assert_stroke_admissible(stroke: dict) -> dict:
    """A stroke enters an assembly only if a person accepted it."""
    if not isinstance(stroke, dict):
        raise AssemblyRefusal("a stroke hypothesis must be a mapping; got %r"
                              % type(stroke).__name__)
    assert_sealed_promoted(stroke)
    sid = stroke.get("stroke_id")
    if not sid:
        raise AssemblyRefusal(
          "a stroke hypothesis with no stroke_id cannot be traced back to the review that "
          "accepted it, and an untraceable character is prose.")
    state = str(stroke.get("review_state") or "UNKNOWN").strip().upper()
    if state not in ADMISSIBLE_STATES:
        raise AssemblyRefusal(
          "stroke %s is %s. Only %s strokes may be assembled: a proposed or pending hypothesis "
          "is not weaker evidence, it is not evidence. Assembling one would let the reading "
          "decide which strokes exist." % (sid, state, ACCEPTED))
    if not stroke.get("accepted_by"):
        raise AssemblyRefusal(
          "stroke %s claims state %s with no accepted_by. A state with no reviewer behind it is "
          "a field somebody set." % (sid, state))
    if str(stroke.get("accepted_by_class") or "").strip().upper() in ("MODEL", "MACHINE", "AI"):
        raise AssemblyRefusal(
          "stroke %s was accepted by a %s. A model accepting a model's proposal is agreement "
          "between two models, and the reading assembled from it would describe that agreement."
          % (sid, stroke.get("accepted_by_class")))
    return {"stroke_id": sid, "state": state}


def assemble(*, positions, physical_controls, region_id, evidence_plane_sha256=None) -> dict:
    """Build a proposed reading."""
    if not region_id:
        raise AssemblyRefusal("an assembly must name the region it reads; an unlocated reading "
                              "cannot be checked against anything.")
    if not isinstance(positions, (list, tuple)):
        raise AssemblyRefusal("positions must be an ordered sequence; order is the reading.")
    assert_sealed_promoted([region_id, positions])

    control_record = assert_controls_passed(physical_controls)

    chars, gaps, supported, used = [], 0, 0, []
    for i, pos in enumerate(positions):
        if not isinstance(pos, dict):
            raise AssemblyRefusal("position %d is %r, not a mapping" % (i, type(pos).__name__))
        strokes = pos.get("strokes") or []
        if not strokes:
            chars.append({"index": i, "char": GAP, "gap": True, "strokes": []})
            gaps += 1
            continue
        checked = [assert_stroke_admissible(s) for s in strokes]
        ch = pos.get("char")
        if not ch:
            raise AssemblyRefusal(
              "position %d has %d accepted stroke(s) and no character. A supported slot with no "
              "reading is not a gap -- it is an assembly that lost its own output, and emitting "
              "it as a gap would hide accepted evidence." % (i, len(checked)))
        if len(str(ch)) != 1:
            raise AssemblyRefusal(
              "position %d proposes %r. One slot carries one character; a multi-character slot "
              "is a reconstruction, which belongs to a different output plane." % (i, ch))
        ids = [c["stroke_id"] for c in checked]
        used.extend(ids)
        chars.append({"index": i, "char": str(ch), "gap": False, "strokes": ids})
        supported += 1

    text = "".join(c["char"] for c in chars)
    rec = {
      "contract": CONTRACT,
      "output_plane": OUTPUT_PLANE,
      "region_id": region_id,
      "text": text,
      "characters": chars,
      "slots": len(chars),
      "supported": supported,
      "gaps": gaps,
      "gap_marker": GAP,
      "gap_rule": GAP_RULE,
      "stroke_ids_used": sorted(set(used)),
      "physical_controls": control_record,
      "evidence_plane_sha256": evidence_plane_sha256,
      "what_this_is": "an interpretation of accepted strokes.",
      "what_this_is_not": [
        "a measurement",
        "evidence that ink is present",
        "a claim that any character is on the papyrus",
        "input to the physical ink probability, which this module cannot write",
      ],
      "may_not_write_planes": list(FORBIDDEN_PLANES),
      "no_merged_confidence": "there is no single number spanning physical evidence and "
                              "linguistic plausibility. The whole risk this module is built "
                              "against is the second raising the apparent value of the first.",
    }
    rec["assembly_sha256"] = _sha({k: v for k, v in rec.items()
                                   if k not in ("assembly_sha256",)})
    return rec


def from_glyph_annotations(cells: list, *, region_id: str) -> dict:
    """A PROPOSED_READING worksheet from reviewed board cells and their HUMAN letter judgments."""
    if not region_id:
        raise AssemblyRefusal("an assembly must name the region it reads")
    assert_sealed_promoted([region_id, cells])
    chars, gaps, supported, used = [], 0, 0, []
    for i, cell in enumerate(cells):
        g = (cell or {}).get("glyph") or {}
        ch = g.get("char")
        if not ch:
            chars.append({"index": i, "cell_id": (cell or {}).get("cell_id"), "char": GAP,
                          "gap": True, "strokes": []})
            gaps += 1
            continue
        stroke = {"stroke_id": g.get("task_id"), "review_state": ACCEPTED,
                  "accepted_by": ", ".join(g.get("accepted_by") or []),
                  "accepted_by_class": "HUMAN"}
        checked = assert_stroke_admissible(stroke)
        if len(str(ch)) != 1:
            raise AssemblyRefusal("cell %s proposes %r; one slot carries one character"
                                  % ((cell or {}).get("cell_id"), ch))
        used.append(checked["stroke_id"])
        chars.append({"index": i, "cell_id": (cell or {}).get("cell_id"), "char": str(ch),
                      "gap": False, "strokes": [checked["stroke_id"]],
                      "accepted_by": list(g.get("accepted_by") or [])})
        supported += 1
    rec = {
      "contract": CONTRACT,
      "output_plane": OUTPUT_PLANE,
      "region_id": region_id,
      "text": "".join(c["char"] for c in chars),
      "characters": chars,
      "slots": len(chars),
      "supported": supported,
      "gaps": gaps,
      "gap_marker": GAP,
      "gap_rule": GAP_RULE,
      "stroke_ids_used": sorted(set(used)),
      "physical_controls": {"basis": "HUMAN_REVIEW_OF_CANDIDATE_REGIONS",
                            "detector_control": "NOT_APPLICABLE",
                            "why": "every letter is a person-agreed judgment of a person-accepted "
                                   "region; no model or language prior produced a character"},
      "reading_order_established": False,
      "reading_order_note": "slots follow the board's row-major layout. No line detection or "
                            "reading-direction judgment has been made.",
      "what_this_is": "an interpretation of accepted regions by named people.",
      "what_this_is_not": ["a measurement", "evidence that ink is present",
                           "a claim that any character is on the papyrus",
                           "a detector result", "a reading of an unread scroll"],
      "may_not_write_planes": list(FORBIDDEN_PLANES),
    }
    rec["assembly_sha256"] = _sha({k: v for k, v in rec.items() if k != "assembly_sha256"})
    return rec


def render_for_display(assembly: dict) -> dict:
    """What a UI may show."""
    if assembly.get("output_plane") != OUTPUT_PLANE:
        raise AssemblyRefusal("refusing to render %r as a reading" % assembly.get("output_plane"))
    return {
      "plane": OUTPUT_PLANE,
      "text": assembly["text"],
      "slots": assembly["slots"],
      "supported": assembly["supported"],
      "gaps": assembly["gaps"],
      "badge": "PROPOSED READING - not a measurement",
      "must_render_gaps": "the gap marker may not be stripped, collapsed or replaced with a "
                          "space. A reading with its gaps removed reads as continuous text.",
      "must_not_render": ["a combined confidence", "this text beside an ink probability as "
                          "though they were the same kind of claim"],
    }


def selftest() -> bool:
    ok = []

    def ck(n, c):
        ok.append((n, bool(c)))

    good_stroke = {"stroke_id": "s1", "review_state": "ACCEPTED",
                   "accepted_by": "reviewer-a", "accepted_by_class": "HUMAN"}
    controls = {"permutation_null": True, "planted_ink": True, "detection_floor": True}

    a = assemble(positions=[{"char": "α", "strokes": [good_stroke]},
                            {},
                            {"char": "β", "strokes": [dict(good_stroke, stroke_id="s2")]}],
                 physical_controls=controls, region_id="R1")
    ck("a supported slot emits its character", a["characters"][0]["char"] == "α")
    ck("an unsupported slot emits a GAP and is never filled",
       a["characters"][1]["gap"] and a["characters"][1]["char"] == GAP)
    ck("the gap appears in the text rather than being dropped", GAP in a["text"])
    ck("every character names its supporting strokes",
       a["characters"][0]["strokes"] == ["s1"])
    ck("counts are reported and add up",
       a["supported"] == 2 and a["gaps"] == 1 and a["slots"] == 3)
    ck("the assembly is hashed", len(a["assembly_sha256"]) == 64)
    ck("it declares the plane it writes", a["output_plane"] == OUTPUT_PLANE)
    ck("it cannot write the physical plane",
       "PHYSICAL_INK_EVIDENCE" in a["may_not_write_planes"])
    ck("there is no merged confidence field",
       not any(k for k in a if "confidence" in k and k != "no_merged_confidence"))

    for bad_state in ("PROPOSED", "PENDING", "REJECTED", "UNKNOWN"):
        try:
            assemble(positions=[{"char": "α",
                                 "strokes": [dict(good_stroke, review_state=bad_state)]}],
                     physical_controls=controls, region_id="R1")
            ck("a %s stroke is refused" % bad_state, False)
        except AssemblyRefusal:
            ck("a %s stroke is refused" % bad_state, True)

    try:
        assemble(positions=[{"char": "α",
                             "strokes": [dict(good_stroke, accepted_by_class="MODEL")]}],
                 physical_controls=controls, region_id="R1")
        ck("a model-accepted stroke is refused", False)
    except AssemblyRefusal as e:
        ck("a model-accepted stroke is refused", "two models" in str(e))

    try:
        assemble(positions=[{"char": "α", "strokes": [good_stroke]}],
                 physical_controls=dict(controls, permutation_null=False), region_id="R1")
        ck("a FAILED physical control refuses the whole assembly", False)
    except AssemblyRefusal as e:
        ck("a FAILED physical control refuses the whole assembly",
           "READER_HALLUCINATION" in str(e))

    try:
        assemble(positions=[{"char": "α", "strokes": [good_stroke]}],
                 physical_controls={}, region_id="R1")
        ck("absent controls are a refusal, not a presumption", False)
    except AssemblyRefusal:
        ck("absent controls are a refusal, not a presumption", True)

    try:
        assemble(positions=[{"strokes": [good_stroke]}], physical_controls=controls,
                 region_id="R1")
        ck("a supported slot with no character is refused, not turned into a gap", False)
    except AssemblyRefusal as e:
        ck("a supported slot with no character is refused, not turned into a gap",
           "lost its own output" in str(e))

    try:
        assemble(positions=[{"char": "αβ", "strokes": [good_stroke]}],
                 physical_controls=controls, region_id="R1")
        ck("a multi-character slot is refused as a reconstruction", False)
    except AssemblyRefusal:
        ck("a multi-character slot is refused as a reconstruction", True)

    try:
        assemble(positions=[], physical_controls=controls, region_id="")
        ck("an unlocated reading is refused", False)
    except AssemblyRefusal:
        ck("an unlocated reading is refused", True)

    d = render_for_display(a)
    ck("the display keeps the plane badge", "PROPOSED READING" in d["badge"])
    ck("the display forbids stripping gaps", "may not be stripped" in d["must_render_gaps"])

    for n, g in ok:
        print("  %-4s %s" % ("ok" if g else "FAIL", n))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    print(json.dumps({"contract": CONTRACT, "output_plane": OUTPUT_PLANE,
                      "admissible_states": list(ADMISSIBLE_STATES),
                      "gap_marker": GAP, "gap_rule": GAP_RULE}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
