"""The provider update process: discover -> semantic diff -> stage -> contract tests -> receipt -> activate (when compatible) -> retain a rollback pin."""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import threading
import time
from pathlib import Path

from argus.core import update_conveyor as UC
from argus.core import update_store as S
from argus.core import upstream_discovery as D

REPO = Path(__file__).resolve().parents[2]
_CHECK_LOCK = threading.Lock()

class UpdateRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _load_update(source_id: str, revision: str):
    try:
        return S.load_update(source_id, revision)
    except ValueError as exc:
        raise UpdateRefused("BAD_REVISION", str(exc)) from None


WATCH_STATES = ("OPEN", "MERGED_TO_MAIN", "MERGED_TO_NONMAIN_BRANCH", "STAGED", "APPARATUS_ACTIVE",
                "SCIENTIFICALLY_ACTIVE", "QUARANTINED")
_STAGED_STAGES = ("STAGED", "BUILD_PASSED", "SYNTHETIC_CONTROL_PASSED", "REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT", "HELD")


def watch_rows(cfg: dict, observed: dict) -> list:
    """The watchlist as it stands: each pull request in ONE of the seven states, never a bare 'merged'."""
    recs = {r["source_id"]: r for r in D.source_records(cfg)}
    rows = []
    for w in cfg.get("watchlist", []):
        pr = observed.get(w["id"]) or {}
        tracks = w.get("tracks") or "villa-main"
        rec = recs.get(tracks) or {}
        verified = bool(pr.get("ok"))
        if verified:
            merged, base = pr.get("merged"), pr.get("base")
            merge_commit = pr.get("merge_commit")
        else:
            merged = w.get("argus_status") in ("MERGED", "MERGED_TO_MAIN", "MERGED_TO_NONMAIN_BRANCH")
            base = w.get("base_branch") or (rec.get("watched_channel") if merged else None)
            merge_commit = w.get("merge_commit")
        if not merged:
            state, label = "OPEN", None
        elif base == rec.get("watched_channel", "main") and rec.get("channel_kind", "main") == "main":
            state, label = "MERGED_TO_MAIN", None
        else:
            state, label = "MERGED_TO_NONMAIN_BRANCH", "BRANCH_MERGED_NOT_MAIN"
        upd = None
        if merged and merge_commit:
            upd, _ = _load_update(tracks, merge_commit)
        if upd is not None:
            if upd.stage in ("QUARANTINED", "ROLLED_BACK"):
                state = "QUARANTINED"
            elif state == "MERGED_TO_NONMAIN_BRANCH":
                label = "BRANCH_MERGED_STAGED_NOT_MAIN" if upd.stage in _STAGED_STAGES else label
            elif upd.stage in _STAGED_STAGES:
                state = "STAGED"
                for scope, name in (("apparatus", "APPARATUS_ACTIVE"), ("scientific", "SCIENTIFICALLY_ACTIVE")):
                    if (S.active_pin(tracks, scope) or {}).get("revision") == merge_commit:
                        state = name
        rows.append({"id": w["id"], "title": w.get("title"), "state": state, "label": label,
                     "state_source": "OBSERVED" if verified else "CONFIGURED_NOT_VERIFIED",
                     "base_branch": base, "merge_commit": merge_commit, "tracks": tracks, "why": w.get("why"),
                     "adoption_condition": w.get("adoption_condition"), "affects": w.get("affects", []),
                     "argus_status": w.get("argus_status")})
    return rows


def check_watchlist(cfg: dict | None = None, *, runner=D.default_runner) -> dict:
    """Observe every watched pull request from upstream."""
    _require_may_check()
    cfg = cfg or D.load_config()
    out = {}
    for w in cfg.get("watchlist", []):
        if w.get("repo") and w.get("number"):
            out[w["id"]] = D.observe_pr(w["repo"], int(w["number"]), runner)
    S.save_pr_observations(out)
    return out


def _fixture_active() -> bool:
    from argus.core import upstream_fixture as FX
    return FX.active()


def _exposure_known(rec: dict) -> bool:
    """Training exposure only applies to model sources; a tool or a release has none to declare."""
    return bool(rec.get("exposure_known", rec["source_type"] != "model_repository"))


def _sources(cfg: dict) -> dict:
    return {r["source_id"]: r for r in D.source_records(cfg)}


def _sha(doc) -> str:
    return S.digest(doc)


def _receipt(kind: str, **fields) -> dict:
    doc = {"kind": kind, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
    doc.setdefault("hashes", {})["receipt_body"] = _sha({k: v for k, v in doc.items() if k != "hashes"})
    return doc


def policy() -> UC.UpdatePolicy:
    return S.load_policy()


def set_policy(mode: str, *, by: str) -> dict:
    p = S.load_policy()
    p.set(mode, from_settings=p.decided)
    return S.save_policy(p, by=by)


def _require_may_check() -> None:
    p = S.load_policy()
    if not p.decided:
        raise UpdateRefused("POLICY_UNSET", "no update policy chosen yet; ARGUS must ask first (nothing was checked)")
    if not p.may_check_upstream():
        raise UpdateRefused("POLICY_NEVER", "the update policy is NEVER: no outbound check was made")


def _check_unlocked(cfg: dict | None = None, *, runner=D.default_runner, http_get=D.default_http_get, sources: list | None = None) -> dict:
    """Observe every source."""
    _require_may_check()
    cfg = cfg or D.load_config()
    found, results = [], []
    for rec in D.source_records(cfg):
        if sources and rec["source_id"] not in sources:
            continue
        obs = D.observe(rec, runner=runner, http_get=http_get)
        S.save_observation(obs)
        row = {"source_id": rec["source_id"], "watch_state": obs.watch_state, "status_line": obs.status_line(),
               "observed_revision": obs.observed_revision, "admitted_revision": rec.get("admitted_revision")}
        rev, base = obs.observed_revision, rec.get("admitted_revision")
        if obs.watch_state == "OK" and D.is_immutable(rev) and rev != base:
            try:
                existing, _ = _load_update(rec["source_id"], rev)
            except UpdateRefused:
                row["refused_revision"] = "not a plain revision name"
                results.append(row)
                continue
            if existing is None:
                u = UC.Update(source_id=rec["source_id"], revision=rev, previous_admitted=base)
                diff = D.semantic_diff(rec, base, rev, cfg, runner) if base else {"available": False, "why": "no admitted revision to diff against"}
                S.save_update(u, {"diff": diff, "detected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                row["new_update"] = rev
                found.append((rec["source_id"], rev))
            else:
                row["known_update"] = rev
        results.append(row)
    problem_snapshot = None
    if not sources or "villa-main" in sources:
        from argus.core import open_problems
        problem_snapshot = open_problems.refresh(runner=runner)
    return {"checked": len(results), "new_updates": len(found), "sources": results,
            "open_problems": problem_snapshot,
            "pull_requests": {k: {kk: v.get(kk) for kk in ("ok", "state", "merged", "base", "merge_commit")}
                              for k, v in check_watchlist(cfg, runner=runner).items()} if not sources else {}}


def check(cfg: dict | None = None, *, runner=D.default_runner, http_get=D.default_http_get, sources: list | None = None) -> dict:
    """Run one update observation at a time, whether it came from the scheduler or a button."""
    if not _CHECK_LOCK.acquire(blocking=False):
        raise UpdateRefused("CHECK_ALREADY_RUNNING", "an upstream check is already running; its result will appear here")
    try:
        return _check_unlocked(cfg, runner=runner, http_get=http_get, sources=sources)
    finally:
        _CHECK_LOCK.release()


def discover_exact(source_id: str, revision: str, cfg: dict | None = None, *, runner=D.default_runner,
                   http_get=D.default_http_get) -> dict:
    """Register ONE named immutable revision of a git source as a DISCOVERED update, with its semantic diff."""
    _require_may_check()
    cfg = cfg or D.load_config()
    rec = _sources(cfg).get(source_id)
    if not rec:
        raise UpdateRefused("UNKNOWN_SOURCE", "no source %r" % source_id)
    if rec["source_type"] != "git_repository" or not D.is_immutable(revision):
        raise UpdateRefused("NOT_EXACT", "an exact revision is a 40-hex git sha of a git_repository source")
    rc, doc, err = D._gh(runner, "repos/%s/commits/%s" % (rec["repo"], revision))
    if rc or not isinstance(doc, dict) or doc.get("sha") != revision:
        raise UpdateRefused("REVISION_NOT_FOUND", "upstream did not resolve %s (%s)" % (revision[:12], (err or "")[:120]))
    reachable, how = D.on_channel(rec, revision, runner)
    if reachable is not True:
        raise UpdateRefused("NOT_ON_WATCHED_CHANNEL", "%s is not reachable from the watched channel %r (%s); a merge "
                            "elsewhere is not a change to this source" % (revision[:12], rec["watched_channel"], how))
    base = rec.get("admitted_revision")
    existing, _ = _load_update(source_id, revision)
    if existing is not None:
        return {"source_id": source_id, "revision": revision, "already_known": True, "stage": existing.stage}
    u = UC.Update(source_id=source_id, revision=revision, previous_admitted=base)
    diff = D.semantic_diff(rec, base, revision, cfg, runner) if base else {"available": False, "why": "no admitted revision to diff against"}
    S.save_update(u, {"diff": diff, "detected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "exact": True})
    return {"source_id": source_id, "revision": revision, "already_known": False, "stage": u.stage,
            "commits": diff.get("commits"), "files_changed": diff.get("files_changed"), "risk": diff.get("risk")}


def _advance(u: UC.Update, to: str, receipt: dict) -> UC.Update:
    return UC.advance(u, to, receipt=receipt)


def stage(source_id: str, revision: str, cfg: dict | None = None, *, runner=D.default_runner, http_get=D.default_http_get) -> dict:
    """DISCOVERED -> CLASSIFIED -> LICENSE_CHECKED -> STAGED -> BUILD_PASSED (adapter contract) -> SYNTHETIC_CONTROL_PASSED (sabotage self-check)."""
    _require_may_check()
    cfg = cfg or D.load_config()
    rec = _sources(cfg).get(source_id)
    if not rec:
        raise UpdateRefused("UNKNOWN_SOURCE", "no source %r" % source_id)
    revision = S.find_revision(source_id, revision) or revision
    u, stored = _load_update(source_id, revision)
    if u is None:
        raise UpdateRefused("UNKNOWN_UPDATE", "no discovered update %s@%s; run a check first" % (source_id, revision[:12]))
    src = D.to_source(rec)
    diff = (stored or {}).get("diff", {})
    steps = []

    def do(to: str, receipt: dict):
        nonlocal u
        u = _advance(u, to, receipt)
        steps.append(to)

    if u.stage == "DISCOVERED":
        do("CLASSIFIED", _receipt("classified", hashes={"diff": _sha(diff)},
                                  upstream_revision=u.revision, risk=diff.get("risk"), impacted=diff.get("impacted_capabilities")))
    if u.stage == "CLASSIFIED":
        lic = D.license_at(rec, u.revision, runner, http_get)
        u.license_at_revision = lic
        expected = src.license
        r = _receipt("license_checked", hashes={"licence": _sha({"at": lic, "expected": expected})},
                     upstream_revision=u.revision, license=lic, expected=expected)
        if lic is None or (expected is not None and lic != expected):
            u.receipts["license_checked"] = r
            u.hold("LICENSE_CHANGED" if lic else "PROVENANCE_INCOMPLETE")
            S.save_update(u, {"diff": diff})
            return {"stage": u.stage, "held_reason": u.held_reason, "steps": steps,
                    "why": "licence at the candidate revision is %r, expected %r" % (lic, expected)}
        do("LICENSE_CHECKED", r)
    if u.stage == "LICENSE_CHECKED":
        cc = D.contract_check(rec, u.revision, runner) if rec["source_type"] == "git_repository" else {"defined": False, "gates": {}, "files": []}
        pinned = {"metadata_only": rec["source_type"] != "git_repository", "contract": cc}
        do("STAGED", _receipt("staged", hashes={"staged": _sha(pinned)}, upstream_revision=u.revision, staged=pinned))
        stored_extra = {"diff": diff, "contract": cc}
        S.save_update(u, stored_extra)
    else:
        stored_extra = {"diff": diff}
    if u.stage == "STAGED":
        cc = ((_load_update(source_id, u.revision)[1]) or {}).get("contract") or {}
        if not cc.get("defined"):
            u.hold("PROVENANCE_INCOMPLETE")
            S.save_update(u, stored_extra)
            return {"stage": u.stage, "held_reason": "PROVENANCE_INCOMPLETE", "steps": steps,
                    "why": "no adapter contract is declared for this source, so compatibility cannot be shown"}
        g = cc["gates"]
        if not (g.get("source_identity") and g.get("api_compatible") and g.get("schemas_parse")):
            u.gates_failed = tuple(k for k, v in g.items() if not v)
            u.stage = "INCOMPATIBLE"
            u.held_reason = "COMPATIBILITY_BROKEN"
            S.save_update(u, stored_extra)
            missing = [(f["path"], [t for t, ok in f["tokens"].items() if not ok]) for f in cc["files"] if not (f["fetched"] and all(f["tokens"].values()))]
            return {"stage": u.stage, "held_reason": u.held_reason, "steps": steps, "missing": missing,
                    "why": "the adapter contract is not satisfied at this revision"}
        do("BUILD_PASSED", _receipt("adapter_contract", hashes={"contract": _sha(cc)}, upstream_revision=u.revision,
                                    gates=g))
    if u.stage == "BUILD_PASSED":
        cc = ((_load_update(source_id, u.revision)[1]) or {}).get("contract") or {}
        if not cc.get("gates", {}).get("sabotage_still_fails"):
            u.hold("COMPATIBILITY_BROKEN")
            S.save_update(u, stored_extra)
            return {"stage": u.stage, "held_reason": "COMPATIBILITY_BROKEN", "steps": steps,
                    "why": "the contract check could not be shown to fail when a required token is removed"}
        do("SYNTHETIC_CONTROL_PASSED", _receipt("sabotage_selfcheck", hashes={"contract": _sha(cc)},
                                                upstream_revision=u.revision, real_data=False,
                                                note="each required token removed in turn must be reported missing"))
    u.gates_passed = tuple(sorted(set(u.gates_passed) | {"source_identity", "immutable_revision", "license_reviewed",
                                                          "api_compatible", "schemas_parse", "sabotage_still_fails"}))
    S.save_update(u, stored_extra)
    return {"stage": u.stage, "steps": steps, "held_reason": u.held_reason,
            "next": "attach a real-data control receipt to make this eligible for scientific use; the apparatus scope may be activated now"}


def run_required_tests(source_id: str, revision: str, cfg: dict | None = None, *, timeout: int = 900, runner=None) -> dict:
    """Run ARGUS's own adapter tests named for this source, and record the result on the update."""
    cfg = cfg or D.load_config()
    rec = _sources(cfg).get(source_id)
    if not rec:
        raise UpdateRefused("UNKNOWN_SOURCE", "no source %r" % source_id)
    revision = S.find_revision(source_id, revision) or revision
    u, stored = _load_update(source_id, revision)
    if u is None:
        raise UpdateRefused("UNKNOWN_UPDATE", "no update %s@%s" % (source_id, revision[:12]))
    tests = [t for t in rec.get("required_tests", []) if (REPO / t).is_file()]
    if not tests:
        res = {"ran": False, "why": "no required adapter tests exist for this source", "passed": None}
    else:
        run = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=str(REPO)))
        p = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests])
        res = {"ran": True, "tests": tests, "passed": p.returncode == 0, "tail": ((p.stdout or "") + (p.stderr or ""))[-400:]}
    S.save_update(u, {"adapter_tests": dict(res, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))})
    return res


def attach_real_data_control(source_id: str, revision: str, receipt_doc: dict, *, by: str) -> dict:
    """A person supplies a receipt for a control run on REAL scroll data."""
    revision = S.find_revision(source_id, revision) or revision
    u, stored = _load_update(source_id, revision)
    if u is None:
        raise UpdateRefused("UNKNOWN_UPDATE", "no update %s@%s" % (source_id, revision[:12]))
    if u.stage != "SYNTHETIC_CONTROL_PASSED":
        raise UpdateRefused("WRONG_STAGE", "%s is %s; a real-data control attaches after SYNTHETIC_CONTROL_PASSED" % (revision[:12], u.stage))
    if not receipt_doc.get("real_data") or not receipt_doc.get("hashes"):
        raise UpdateRefused("NOT_A_REAL_DATA_RECEIPT", "the receipt must state real_data=True and carry hashes")
    r = dict(receipt_doc, attached_by=by, upstream_revision=revision)
    u = _advance(u, "REAL_DATA_CONTROL_PASSED", r)
    S.save_update(u, {})
    return {"stage": u.stage}


def readiness(u: UC.Update, rec: dict) -> dict:
    """What activation is possible for each scope, and exactly what is missing."""
    out = {}
    ok_ap = u.stage in ("SYNTHETIC_CONTROL_PASSED", "REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT")
    out["apparatus"] = {"allowed": ok_ap, "missing": [] if ok_ap else ["pass the adapter contract and the sabotage self-check"]}
    miss = []
    if u.stage not in ("REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT"):
        miss.append("a real-data control receipt")
    if not D.is_immutable(u.revision):
        miss.append("an immutable revision")
    if rec.get("license") is None:
        miss.append("a declared licence")
    if not _exposure_known(rec):
        miss.append("a declared training-exposure manifest")
    if rec.get("fixture"):
        miss.append("FIXTURE_UPSTREAM: a scripted demonstration source can never be used for scientific runs")
    out["scientific"] = {"allowed": not miss and u.stage in ("REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT"), "missing": miss}
    return out


def activate(source_id: str, revision: str, scope: str, *, by: str, cfg: dict | None = None) -> dict:
    if scope not in S.SCOPES:
        raise UpdateRefused("BAD_SCOPE", "scope must be one of %s" % (S.SCOPES,))
    cfg = cfg or D.load_config()
    rec = _sources(cfg).get(source_id)
    if not rec:
        raise UpdateRefused("UNKNOWN_SOURCE", "no source %r" % source_id)
    if rec.get("channel_kind", "main") != "main":
        raise UpdateRefused("NON_MAIN_CHANNEL", "source %s watches the non-main channel %r; a revision merged there is "
                            "recorded and staged but is never activated" % (source_id, rec.get("watched_channel")))
    revision = S.find_revision(source_id, revision) or revision
    u, stored = _load_update(source_id, revision)
    if u is None:
        raise UpdateRefused("UNKNOWN_UPDATE", "no update %s@%s" % (source_id, revision[:12]))
    if not S.load_policy().decided:
        raise UpdateRefused("POLICY_UNSET", "no update policy chosen yet")
    if u.stage in ("HELD", "INCOMPATIBLE", "QUARANTINED", "ROLLED_BACK"):
        raise UpdateRefused("UPDATE_%s" % u.stage, "%s is %s (%s) and cannot be activated" % (revision[:12], u.stage, u.held_reason))
    r = readiness(u, rec)[scope]
    if not r["allowed"]:
        raise UpdateRefused("NOT_READY", "scope %s not allowed: missing %s" % (scope, "; ".join(r["missing"])))
    receipt = _receipt("activation", hashes={"update_record": (stored or {}).get("record_sha256") or _sha(u.__dict__)},
                       source_id=source_id, revision=revision, scope=scope, by=by, operator_promoted=True,
                       previous=(S.active_pin(source_id, scope) or {}).get("revision") or rec.get("admitted_revision"))
    if scope == "scientific":
        if u.stage == "REAL_DATA_CONTROL_PASSED":
            src = dataclasses.replace(D.to_source(rec), auto_promotion_allowed=True)
            u = UC.promote(u, src, UC.UpdatePolicy(mode="AUTOMATIC"),
                           license_now=u.license_at_revision if u.license_at_revision else rec.get("license"),
                           science_contract_changed=False, coordinate_schema_changed=False,
                           exposure_known=_exposure_known(rec), destination_busy=False)
            if u.stage == "HELD":
                S.save_update(u, {})
                raise UpdateRefused("HELD_" + str(u.held_reason), "promotion held: %s" % u.held_reason)
        if u.stage == "ADMITTED":
            u = _advance_to_current(u, receipt)
    before = S.pin_set(D.source_records(cfg))
    pin = S.set_active(source_id, scope, revision, by=by, receipt_sha256=receipt["hashes"]["receipt_body"],
                       note="activated for %s runs" % scope)
    S.save_update(u, {"activation_receipt_%s" % scope: receipt})
    return {"activated": True, "scope": scope, "pin": pin, "receipt": receipt,
            "existing_run_pins_unchanged": True, "pin_set_before": before}


def _advance_to_current(u: UC.Update, receipt: dict) -> UC.Update:
    return UC.advance(u, "CURRENT", receipt=receipt)


def rollback(source_id: str, scope: str, *, by: str, cfg: dict | None = None) -> dict:
    if scope not in S.SCOPES:
        raise UpdateRefused("BAD_SCOPE", "scope must be one of %s" % (S.SCOPES,))
    active = S.active_pin(source_id, scope)
    if not active:
        raise UpdateRefused("NOTHING_ACTIVE", "no activated pin for %s/%s; the shipped revision is already in use" % (source_id, scope))
    slots = (S.load_pins().get(source_id) or {}).get(scope) or {}
    if slots.get("previous"):
        restored = S.restore_previous(source_id, scope, by=by)
    else:
        S.clear_active(source_id, scope, by=by)
        rec = _sources(cfg or D.load_config()).get(source_id) or {}
        restored = {"revision": rec.get("admitted_revision"), "note": "the shipped baseline in config/upstream_sources.json"}
    u, stored = _load_update(source_id, active["revision"])
    if u is not None:
        u.previous_admitted = restored["revision"]
        u.stage = "QUARANTINED"
        u.notes = "rolled back by %s; preserved, not deleted" % by
        S.save_update(u, {"rolled_back_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return {"rolled_back": True, "scope": scope, "restored": restored, "quarantined": active["revision"]}


def _gate_rows(u: UC.Update, stored: dict) -> list:
    seq = UC.CONVEYOR_SEQUENCE
    cur = seq.index(u.stage) if u.stage in seq else -1
    rows = []
    for st in seq[:-2]:
        rows.append({"gate": st, "state": "PASSED" if (u.stage in seq and seq.index(st) <= cur) else "NOT_RUN"})
    return rows


def status(cfg: dict | None = None) -> dict:
    """Everything the Updates screen shows, from one source: policy, sources, updates, gates, pins."""
    cfg = cfg or D.load_config()
    recs = {r["source_id"]: r for r in D.source_records(cfg)}
    pol = S.load_policy()
    obs_all = S.load_observations()
    checked_times = sorted(
        str(row.get("checked_utc")) for row in obs_all.values()
        if isinstance(row, dict) and row.get("checked_utc")
    )
    pins = S.load_pins()
    updates = []
    for stored in S.list_updates():
        d = stored["update"]
        rec = recs.get(d["source_id"])
        if not rec:
            continue
        u, _ = _load_update(d["source_id"], d["revision"])
        diff = stored.get("diff") or {}
        tests = stored.get("adapter_tests") or {}
        updates.append({
            "source_id": d["source_id"], "revision": d["revision"], "stage": u.stage, "held_reason": u.held_reason,
            "admitted_revision": rec.get("admitted_revision"), "providers": rec.get("providers", []),
            "diff": {k: diff.get(k) for k in ("available", "why", "commits", "files_changed", "truncated", "risk",
                                              "impacted_capabilities", "io_contract_touched", "licence_touched",
                                              "dependencies_touched", "by_area", "notable_commits", "io_contract_paths")},
            "contract": {"defined": (stored.get("contract") or {}).get("defined"),
                         "gates": (stored.get("contract") or {}).get("gates")},
            "adapter_tests": {"ran": tests.get("ran"), "passed": tests.get("passed")},
            "gates": _gate_rows(u, stored),
            "tested": {"contract": bool((stored.get("contract") or {}).get("gates", {}).get("api_compatible")),
                       "sabotage_selfcheck": bool((stored.get("contract") or {}).get("gates", {}).get("sabotage_still_fails")),
                       "adapter_tests": tests.get("passed") if tests.get("ran") else None,
                       "real_data_control": u.stage in ("REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT")},
            "readiness": readiness(u, rec),
            "record_sha256": stored.get("record_sha256"),
        })
    srcs = []
    for sid, rec in recs.items():
        o = obs_all.get(sid) or {}
        ob = UC.Observation(sid, o.get("watch_state", "NOT_CHECKED"), o.get("checked_utc"), o.get("observed_revision"),
                            o.get("etag"), bool(o.get("fetched")), o.get("error"))
        srcs.append({"source_id": sid, "endpoint": rec["endpoint"], "type": rec["source_type"], "classification": rec["classification"],
                     "channel": rec["watched_channel"], "admitted_revision": rec.get("admitted_revision"),
                     "pin_status": "IMMUTABLE" if D.is_immutable(rec.get("admitted_revision")) else "NO_IMMUTABLE_PIN",
                     "license": rec.get("license"), "providers": rec.get("providers", []),
                     "watch_state": ob.watch_state, "status_line": ob.status_line(), "up_to_date_claim_allowed": ob.is_up_to_date_claim_allowed,
                     "observed_revision": ob.observed_revision, "checked_utc": ob.checked_utc,
                     "active_pins": {sc: ((pins.get(sid) or {}).get(sc) or {}).get("active") for sc in S.SCOPES},
                     "rollback_available": {sc: bool(((pins.get(sid) or {}).get(sc) or {}).get("previous")) for sc in S.SCOPES},
                     "note": rec.get("note") or rec.get("runtime_pin_note")})
    return {"schema": "argus-provider-updates-status-v1", "policy": {"mode": pol.mode, "decided_utc": pol.decided_utc,
            "prompt": None if pol.decided else pol.prompt()}, "sources": srcs, "updates": updates,
            "watchlist": watch_rows(cfg, S.load_pr_observations()),
            "last_checked_utc": checked_times[-1] if checked_times else None,
            "upstream_mode": "FIXTURE" if _fixture_active() else "LIVE",
            "rule": "existing runs stay pinned; an activated revision only changes what NEW runs use"}


def lifecycle_summary(cfg: dict | None = None) -> dict:
    """The provider lifecycle in one small, offline-safe record: what is staged, what is active, what can be rolled back."""
    try:
        st = status(cfg)
    except Exception as exc:
        return {"state": "UNKNOWN", "why": "the provider store could not be read: %s" % type(exc).__name__}
    staged = [{"source_id": u["source_id"], "revision": u["revision"], "stage": u["stage"], "held_reason": u["held_reason"]}
              for u in st["updates"] if u["stage"] != "CURRENT"]
    active = {s["source_id"]: {sc: rev for sc, rev in (s["active_pins"] or {}).items() if rev} for s in st["sources"]}
    rollback = sorted(s["source_id"] for s in st["sources"] if any((s["rollback_available"] or {}).values()))
    held = [u for u in staged if u["stage"] == "HELD"]
    decided = bool(st["policy"]["decided_utc"])
    return {"state": "DEGRADED" if held else "OK", "policy": st["policy"]["mode"], "policy_decided": decided,
            "upstream_mode": st["upstream_mode"], "sources": len(st["sources"]), "staged": staged, "held": len(held),
            "active_pins": {k: v for k, v in active.items() if v}, "rollback_available": rollback,
            "next": None if not held else "a staged update is HELD: open Updates to read why and to release or drop it",
            "rule": st["rule"]}
