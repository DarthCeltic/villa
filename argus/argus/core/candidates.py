"""Turn a probability plane into ranked candidate regions, carrying everything needed to go back."""
from __future__ import annotations

import hashlib

import numpy as np

CONTRACT_ID = "argus-candidates-v2"
SUPERSEDES = {"argus-candidates-v1": "rank(orientation) bound no segment and no region, took a "
                                     "single depth order, and read a run whose identity was a "
                                     "module constant"}

RANK_STATISTIC = "mean_probability_over_covered_pixels"
MIN_COVERED_PX = 2000
TIE_BREAK = "then by tile origin, row-major, ascending"
DRAWS = 200
SEED = 20260911

BINDING_FIELDS = ("segment", "region", "orientation")


class CandidateRefusal(RuntimeError):
    """Raised rather than returning an empty list that reads like 'nothing was found'."""


def _uncertainty(values: np.ndarray, rng) -> dict:
    """Spread of the tile statistic over resamples of its own pixels."""
    if values.size < 2:
        return {"mean": float(values.mean()) if values.size else None,
                "ci95": None, "why": "too few pixels to resample"}
    draws = rng.choice(values, size=(DRAWS, values.size), replace=True).mean(axis=1)
    return {"mean": float(values.mean()),
            "ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))],
            "draws": DRAWS}


def _region(region, shape) -> tuple:
    """(y0, y1, x0, x1) as ints inside the plane, non-empty."""
    if region is None:
        raise CandidateRefusal(
            "no coordinate region declared. Ranking 'the whole plane' by default is how a search "
            "region gets chosen after the output is seen; declare it (y0, y1, x0, x1).")
    if isinstance(region, dict):
        region = tuple(region.get(k) for k in ("y0", "y1", "x0", "x1"))
    try:
        y0, y1, x0, x1 = (int(v) for v in region)
    except (TypeError, ValueError):
        raise CandidateRefusal("region %r is not (y0, y1, x0, x1) integers" % (region,)) from None
    h, w = shape
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        raise CandidateRefusal("region %r is empty or outside the %dx%d plane" % (region, h, w))
    return (y0, y1, x0, x1)


def assert_candidate_bound(c: dict) -> dict:
    """Refuse a candidate missing its segment, its coordinate region or its orientation."""
    from argus.core import orientation_semantics as OS
    missing = [f for f in BINDING_FIELDS if c.get(f) in (None, "", [], ())]
    if missing:
        raise CandidateRefusal(
            "candidate %r is missing %s. A candidate must bind {segment, coordinate region, "
            "orientation}; one that does not cannot be traced back to the material it is about."
            % (c.get("candidate_id"), ", ".join(missing)))
    reg = c["region"]
    if not (isinstance(reg, (list, tuple)) and len(reg) == 4
            and all(isinstance(v, (int, np.integer)) and not isinstance(v, bool) for v in reg)
            and reg[0] < reg[1] and reg[2] < reg[3]):
        raise CandidateRefusal("candidate %r region %r is not a non-empty (y0, y1, x0, x1)"
                               % (c.get("candidate_id"), reg))
    ext = c.get("extent")
    if ext is not None and not (reg[0] <= ext[0] < ext[1] <= reg[1]
                                and reg[2] <= ext[2] < ext[3] <= reg[3]):
        raise CandidateRefusal("candidate %r extent %r lies outside its declared region %r"
                               % (c.get("candidate_id"), ext, reg))
    if c["orientation"] not in OS.REQUIRED_DEPTH_ORIENTATIONS:
        raise CandidateRefusal("candidate %r orientation %r is not a depth order"
                               % (c.get("candidate_id"), c["orientation"]))
    return c


def _rank_one(run, region, orientation, *, tile_px, limit, model, checkpoint_sha256):
    from argus.core import sealed_blocks as SB
    prob = np.asarray(SB.plane(orientation, "prob", run=run))
    cov = np.asarray(SB.scored_population(orientation, run=run))
    rng = np.random.default_rng(SEED)
    y0, y1, x0, x1 = region
    material_id = run.physical_material_id()

    rows = []
    for ty in range(y0, y1, tile_px):
        for tx in range(x0, x1, tile_px):
            sl = (slice(ty, min(ty + tile_px, y1)), slice(tx, min(tx + tile_px, x1)))
            c = cov[sl]
            n = int(c.sum())
            if n < MIN_COVERED_PX:
                continue
            vals = prob[sl][c]
            rows.append({
              "segment": run.segment,
              "region": [y0, y1, x0, x1],
              "tile": [ty, tx],
              "extent": [sl[0].start, sl[0].stop, sl[1].start, sl[1].stop],
              "orientation": orientation,
              "physical_material_id": material_id,
              "covered_px": n,
              "score": float(vals.mean()),
              "score_statistic": RANK_STATISTIC,
              "uncertainty": _uncertainty(vals, rng),
              "model": model,
              "checkpoint_sha256": checkpoint_sha256,
            })
    rows.sort(key=lambda r: (-r["score"], r["tile"][0], r["tile"][1]))
    for i, r in enumerate(rows[:limit], start=1):
        r["rank"] = i
        r["candidate_id"] = "cand-%s-%s" % (
          orientation[:3], hashlib.sha256(
            ("%s|%s|%s|%s|%s" % (run.segment, list(region), orientation, r["tile"],
                                 RANK_STATISTIC)).encode()).hexdigest()[:10])
        assert_candidate_bound(r)
    return rows


def rank(*positional, segment: str | None = None, region=None, orientations=None, run=None,
         tile_px: int = 640, limit: int = 50, model: str | None = None,
         checkpoint_sha256: str | None = None) -> dict:
    """Ranked candidate regions for ONE segment, ONE declared region, BOTH depth orientations."""
    from argus.core import orientation_semantics as OS
    from argus.core import sealed_blocks as SB

    if positional:
        raise CandidateRefusal(
            "rank() takes no positional orientation. The single-orientation signature read one "
            "depth order of a run pinned to one segment; pass segment=, region= and "
            "orientations=%r." % (OS.REQUIRED_DEPTH_ORIENTATIONS,))
    try:
        oris = OS.require_both_orientations(orientations)
    except OS.OrientationSemanticsViolation as e:
        raise CandidateRefusal(str(e)) from None
    if not segment or not str(segment).strip():
        raise CandidateRefusal("no segment declared. A candidate that cannot name its segment "
                               "cannot be traced to material.")
    sealed = run if run is not None else SB.default_run()
    if str(segment) != sealed.segment:
        raise CandidateRefusal(
            "segment %r was declared, but the sealed run's own record says %r (%s). Ranking one "
            "segment's plane under another's name is a known material identity seam."
            % (segment, sealed.segment, sealed.manifest_path))
    shape = SB.plane(oris[0], "prob", run=sealed).shape
    reg = _region(region, shape)

    by_orientation, considered = {}, {}
    for o in oris:
        rows = _rank_one(sealed, reg, o, tile_px=tile_px, limit=limit, model=model,
                         checkpoint_sha256=checkpoint_sha256)
        considered[o] = len(rows)
        by_orientation[o] = rows[:limit]
    if not any(considered.values()):
        raise CandidateRefusal(
            "no tile in region %r carries at least %d covered pixels in either orientation. That "
            "is a refusal, not an empty result: 'nothing was found' and 'nothing could be "
            "examined' are different statements and only one of them is about ink."
            % (list(reg), MIN_COVERED_PX))

    return {
      "contract": CONTRACT_ID,
      "supersedes": SUPERSEDES,
      "binding": {"segment": sealed.segment, "scroll": sealed.scroll, "region": list(reg),
                  "orientations": list(oris),
                  "physical_material_id": sealed.physical_material_id(),
                  "seal_record": str(sealed.manifest_path),
                  "seal_manifest_sha256": sealed.manifest_sha256,
                  "identity_source": "the seal record, not this module"},
      "frozen_rule": {"statistic": RANK_STATISTIC, "tile_px": tile_px,
                      "min_covered_px": MIN_COVERED_PX, "tie_break": TIE_BREAK,
                      "draws": DRAWS, "seed": SEED},
      "tiles_considered": considered,
      "by_orientation": by_orientation,
      "no_gpu": "ranked from saved arrays; no model was loaded",
      "what_a_high_rank_means": "this tile scored highest under the frozen statistic. It is a "
                                "place to look, not a finding, and a ranking never qualifies the "
                                "detector that produced the plane.",
      "uncertainty_is_not_confidence_in_ink": "the interval is the spread of the statistic over "
                                              "resamples of the tile's own pixels. A tile can "
                                              "be tightly estimated and mean nothing.",
      "orientations_are_never_merged": "forward and reversed are ranked separately. A "
                                       "forward/reverse comparison measures depth-order "
                                       "sensitivity and never which physical face carries ink.",
    }


def to_review_tasks(ranked: dict, *, limit: int = 20) -> list:
    """Hand candidates to the Review Lab as tasks."""
    from argus.core import orientation_semantics as OS
    if "by_orientation" not in ranked:
        raise CandidateRefusal("ranked result has no by_orientation block; it predates the "
                               "{segment, region, orientation} binding and is refused")
    try:
        OS.require_both_orientations(list(ranked["by_orientation"]))
    except OS.OrientationSemanticsViolation as e:
        raise CandidateRefusal(str(e)) from None
    out = []
    for _o, cands in ranked["by_orientation"].items():
        for c in cands[:limit]:
            assert_candidate_bound(c)
            out.append({
              "task_type": "candidate_is_ink",
              "candidate_id": c["candidate_id"],
              "segment": c["segment"],
              "region": c["region"],
              "orientation": c["orientation"],
              "extent": c["extent"],
              "physical_material_id": c.get("physical_material_id"),
              "covered_px": c["covered_px"],
              "provenance": {"model": c.get("model"), "checkpoint": c.get("checkpoint_sha256"),
                             "statistic": ranked["frozen_rule"]["statistic"]},
              "score_withheld_from_reviewer": True,
              "why_withheld": "a reviewer who can see the ranking is not an independent "
                              "observation of the region.",
            })
    return out




def to_review_lab_tasks(ranked: dict, *, limit: int = 20) -> list:
    """`rank()`'s uncertainty-ranked tiles as real `argus.core.review_lab.Task` objects -- the object the SERVED pipeline actually answers, not the plain, un-answerable dict `to_review_tasks()` returns..."""
    from argus.core import review_lab as RL

    src = ranked["binding"]
    cand_tasks = to_review_tasks(ranked, limit=limit)
    out = []
    for c in cand_tasks:
        binding = {
          "source_volume": "%s (scroll %s)" % (c["segment"], src.get("scroll")),
          "surface_or_candidate_id": c["candidate_id"],
          "physical_coordinates": {"region": c["region"], "extent": c["extent"]},
          "source_hashes": {"seal_manifest_sha256": src.get("seal_manifest_sha256"),
                            "seal_record": src.get("seal_record")},
          "displayed_assets": {"tile_extent": c["extent"], "covered_px": c["covered_px"]},
          "camera_and_orientation": {"orientation": c["orientation"]},
          "producing_model_or_tool": c["provenance"].get("model") or "unspecified",
          "prompt_version": "argus-review-lab-v1/JUDGE_INK_CANDIDATE/%s" % CONTRACT_ID,
          "license_state": "inherits the sealed run's own licence terms (material %s); not "
                          "re-derived here" % src.get("physical_material_id"),
          "exposure_state": "ranked from a sealed probability plane; the reviewer sees no "
                            "score, only the tile itself",
          "selected_because": "ranked highest under the frozen uncertainty statistic (%s)"
                              % c["provenance"].get("statistic"),
          "confidence_shown_to_reviewer": False,
        }
        out.append(RL.Task(
          task_id=RL.new_task_id(c["candidate_id"]), task_type="JUDGE_INK_CANDIDATE",
          binding=binding,
          permitted_answers=("INK", "NOT_INK", "CANNOT_TELL")))
    return out


def plan_review_tasks(ranked: dict, target_dir, *, limit: int = 20) -> dict:
    """What `export_review_tasks(..., merge_existing=True)` WOULD write, without writing anything."""
    import json
    import pathlib

    target_dir = pathlib.Path(target_dir)
    tasks = to_review_lab_tasks(ranked, limit=limit)
    existing = []
    p = target_dir / "REVIEW_TASKS.json"
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8")).get("tasks", [])
        except (OSError, ValueError):
            existing = []
    have = {(t.get("binding") or {}).get("surface_or_candidate_id") for t in existing}
    new = [t for t in tasks if t.binding["surface_or_candidate_id"] not in have]
    return {
      "target_dir": str(target_dir),
      "existing_tasks_kept": len(existing),
      "candidates_ranked": len(tasks),
      "tasks_to_add": len(new),
      "already_have_a_task": len(tasks) - len(new),
      "would_add": [{"candidate_id": t.binding["surface_or_candidate_id"],
                     "extent": t.binding["physical_coordinates"]["extent"],
                     "orientation": t.binding["camera_and_orientation"]["orientation"]}
                    for t in new],
      "score_shown": False,
      "what_this_is": "places for a person to look, ranked under a frozen rule. Not findings, and "
                      "not a detector qualification.",
    }


def export_review_tasks(ranked: dict, target_dir, *, limit: int = 20,
                        merge_existing: bool = False):
    """Write `rank()`'s uncertainty-ranked tiles as `REVIEW_TASKS.json` for one served target -- the exact file `GET /api/unroll` reads and `POST /ui/review/answer` records durable answers beside..."""
    import json
    import pathlib

    from argus.core import review_lab as RL

    target_dir = pathlib.Path(target_dir)
    tasks = to_review_lab_tasks(ranked, limit=limit)
    records = [t.as_record() for t in tasks]
    p = target_dir / "REVIEW_TASKS.json"
    prior_payload = None
    if merge_existing and p.is_file():
        prior_payload = json.loads(p.read_text(encoding="utf-8"))
        have = {(t.get("binding") or {}).get("surface_or_candidate_id")
                for t in prior_payload.get("tasks", [])}
        records = list(prior_payload.get("tasks", [])) + [
            r for r in records if r["binding"]["surface_or_candidate_id"] not in have]
    payload = dict(prior_payload or {})
    payload.update({
      "schema": "argus-review-tasks-v1",
      "contract": RL.CONTRACT_ID,
      "source_contract": ranked["contract"],
      "generated_from": "argus.core.candidates.rank + to_review_lab_tasks -- uncertainty "
                        "ranking over a sealed probability plane, wired to the served review "
                        "pipeline rather than only to tests",
      "counts": dict((prior_payload or {}).get("counts") or {}, tasks=len(records)),
      "tasks": records,
    })
    target_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return p
