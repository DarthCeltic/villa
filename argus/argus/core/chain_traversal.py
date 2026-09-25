"""Chain CONTINUITY and physical MATERIAL continuity: what may be called a traversal."""
from __future__ import annotations

import hashlib
import json

CONTRACT = "argus-chain-traversal-v1"

MATERIAL_STAGES = ("CT", "SURFACE", "FLATTEN", "SAMPLE", "DETECT", "PACKAGE")

SEGMENT_BOUND_FROM = "SURFACE"

TRAVERSED_STATES = ("PROVEN_ON_HELD_VOXELS", "PROVEN_ON_RECORDS", "SATISFIED_UPSTREAM", "PASS")

BREAKING_STATES = ("REFUSED_CORRECTLY", "REFUSED", "MATERIAL_DISCONTINUITY", "NOT_CONSTRUCTIBLE",
                   "UNKNOWN_REFUSED", "BLOCKED_ON_COMPUTE", "FAIL", "NOT_TESTABLE_HERE",
                   "UNKNOWN")

CONTINUOUS = "CONTINUOUS"
BROKEN = "BROKEN"
NOT_REACHED = "NOT_REACHED_AFTER_BREAK"


class TraversalRefusal(RuntimeError):
    """Raised when something would be called a traversal that is not one."""


def _key(s) -> str:
    return str(s or "").strip().upper().replace("_", "").replace("-", "").replace(" ", "")


def mint_material_id(*, physical_scroll: str, segment: str | None = None) -> str:
    """Physical identity -> id."""
    if not _key(physical_scroll):
        raise TraversalRefusal("a material id needs a physical scroll; an unnamed material "
                               "cannot be shown to be the same material later")
    payload = {"physical_scroll": _key(physical_scroll)}
    if segment is not None:
        if not _key(segment):
            raise TraversalRefusal("segment was given but empty")
        payload["segment"] = _key(segment)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "pm-" + hashlib.sha256(blob).hexdigest()[:20]


def ct_record(*, physical_scroll: str, **fields) -> dict:
    """The ONLY place a material lineage starts."""
    return dict(fields, material_stage="CT", physical_scroll=physical_scroll,
                physical_material_id=mint_material_id(physical_scroll=physical_scroll),
                material_parent_id=None)


def propagate(parent: dict, stage: str, *, segment: str | None = None, **fields) -> dict:
    """Carry the parent's material forward."""
    if stage not in MATERIAL_STAGES:
        raise TraversalRefusal("unknown material stage %r; the lineage is %s"
                               % (stage, MATERIAL_STAGES))
    pi, si = MATERIAL_STAGES.index(parent.get("material_stage")), MATERIAL_STAGES.index(stage)
    if si != pi + 1:
        raise TraversalRefusal("%s cannot follow %s; a skipped stage is a break, not a shortcut"
                               % (stage, parent.get("material_stage")))
    if not parent.get("physical_material_id"):
        raise TraversalRefusal("parent carries no physical_material_id")
    rec = dict(fields, material_stage=stage, physical_scroll=parent.get("physical_scroll"),
               material_parent_id=parent["physical_material_id"])
    if stage == SEGMENT_BOUND_FROM:
        if not segment:
            raise TraversalRefusal("SURFACE must bind a segment")
        rec["segment"] = segment
        rec["physical_material_id"] = mint_material_id(
          physical_scroll=parent["physical_scroll"], segment=segment)
    else:
        if segment is not None and _key(segment) != _key(parent.get("segment")):
            raise TraversalRefusal("segment %r differs from the parent's %r after SURFACE; that is "
                                   "different material" % (segment, parent.get("segment")))
        rec["segment"] = parent.get("segment")
        rec["physical_material_id"] = parent["physical_material_id"]
    return rec


def assess_material(records) -> dict:
    """Refuse-grade verdict on a CT->...->PACKAGE lineage."""
    recs = list(records)
    problems = []
    first_break = None
    stages = [r.get("material_stage") for r in recs]
    if stages != list(MATERIAL_STAGES[:len(stages)]) or not recs:
        problems.append("lineage stages %s are not the ordered prefix of %s" % (stages,
                                                                                 MATERIAL_STAGES))
        first_break = 0 if not recs else next(
          (i for i, s in enumerate(stages) if i >= len(MATERIAL_STAGES) or s != MATERIAL_STAGES[i]),
          len(stages))
    for i, r in enumerate(recs):
        if first_break is not None and i >= first_break:
            break
        mid = r.get("physical_material_id")
        if not mid:
            problems.append("stage %d (%s) carries no physical_material_id" % (i, stages[i]))
            first_break = i
            break
        if i == 0:
            if r.get("material_parent_id") is not None:
                problems.append("CT must start the lineage (no parent)")
                first_break = 0
                break
            expect = mint_material_id(physical_scroll=r.get("physical_scroll"))
        else:
            prev = recs[i - 1]
            if r.get("material_parent_id") != prev.get("physical_material_id"):
                problems.append("stage %d (%s) names parent %r but the previous stage is %r"
                                % (i, stages[i], r.get("material_parent_id"),
                                   prev.get("physical_material_id")))
                first_break = i
                break
            if _key(r.get("physical_scroll")) != _key(recs[0].get("physical_scroll")):
                problems.append("stage %d (%s) is on %r; the lineage started on %r"
                                % (i, stages[i], r.get("physical_scroll"),
                                   recs[0].get("physical_scroll")))
                first_break = i
                break
            if stages[i] == SEGMENT_BOUND_FROM:
                expect = mint_material_id(physical_scroll=recs[0].get("physical_scroll"),
                                          segment=r.get("segment"))
            else:
                expect = prev.get("physical_material_id")
        if mid != expect:
            problems.append("MATERIAL_DISCONTINUITY at stage %d (%s): id %s, expected %s"
                            % (i, stages[i], mid, expect))
            first_break = i
            break
    return {"contract": CONTRACT, "continuous": not problems,
            "verdict": CONTINUOUS if not problems else "MATERIAL_DISCONTINUITY",
            "first_break_index": first_break,
            "first_break_stage": (stages[first_break] if first_break is not None
                                  and first_break < len(stages) else None),
            "problems": problems,
            "material_ids": [r.get("physical_material_id") for r in recs]}


def assess_traversal(stage_records, *, declared_stages, material_key: str = "physical_material_id"
                     ) -> dict:
    """Continuity over a declared ordered chain."""
    declared = list(declared_stages)
    by_name = {}
    dup = []
    for r in stage_records:
        n = r.get("stage")
        if n in by_name:
            dup.append(n)
        by_name.setdefault(n, r)
    order_seen = [r.get("stage") for r in stage_records]
    undeclared = [n for n in order_seen if n not in declared]
    present_in_order = [n for n in dict.fromkeys(order_seen) if n in declared]
    expected_order = [n for n in declared if n in by_name]
    out_of_order = {a for a, b in zip(present_in_order, expected_order) if a != b}

    rows, broken_at, why = [], None, None
    first_material = None
    for i, name in enumerate(declared):
        rec = by_name.get(name)
        if broken_at is not None:
            rows.append({"stage": name, "own_state": (rec or {}).get("state"),
                         "traversal": NOT_REACHED,
                         "why": "after the break at %s; its own state cannot count"
                                % declared[broken_at]})
            continue
        problem = None
        if rec is None:
            problem = "MISSING: no record for a declared stage"
        elif name in dup:
            problem = "DUPLICATE: two records for one stage"
        elif name in out_of_order:
            problem = "OUT_OF_ORDER"
        elif rec.get("state") not in TRAVERSED_STATES:
            problem = "%s is not a traversal state" % rec.get("state")
        else:
            mid = rec.get(material_key)
            if mid:
                if first_material is None:
                    first_material = mid
                elif mid != first_material:
                    problem = "MATERIAL_DISCONTINUITY: %s != %s" % (mid, first_material)
        if problem:
            broken_at, why = i, problem
            rows.append({"stage": name, "own_state": (rec or {}).get("state"),
                         "traversal": BROKEN, "why": problem})
        else:
            rows.append({"stage": name, "own_state": rec.get("state"), "traversal": "TRAVERSED"})
    if undeclared and broken_at is None:
        broken_at, why = len(declared), "UNDECLARED stage record(s): %s" % undeclared
    traversed = [r["stage"] for r in rows if r["traversal"] == "TRAVERSED"]
    return {
      "contract": CONTRACT,
      "verdict": CONTINUOUS if broken_at is None else BROKEN,
      "continuous": broken_at is None,
      "broken_at_index": broken_at,
      "broken_at_stage": declared[broken_at] if broken_at is not None
                                                and broken_at < len(declared) else None,
      "why": why,
      "traversed_prefix": traversed,
      "traversed_count": len(traversed),
      "declared_count": len(declared),
      "rows": rows,
      "rule": "a refused, missing, unknown, duplicated, out-of-order or other-material stage ends "
              "the traversal; later stages are NOT_REACHED whatever their own records say",
    }


def assert_continuous(stage_records, *, declared_stages) -> dict:
    r = assess_traversal(stage_records, declared_stages=declared_stages)
    if not r["continuous"]:
        raise TraversalRefusal("chain is not continuous: broken at %s (%s); traversed %d of %d"
                               % (r["broken_at_stage"], r["why"], r["traversed_count"],
                                  r["declared_count"]))
    return r
