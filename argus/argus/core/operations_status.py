"""The five operational surfaces, assembled once so the interface and the console agree."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import subprocess
import time

CONTRACT = "argus-operations-status-v1"

SURFACES = ("resources", "updates", "downloads", "public_release", "private_campaign",
            "detector")

_CACHE: dict = {}
_TTL_S = 20.0


def _cached(key, fn, ttl=_TTL_S):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _headline(state: str, text: str, **rest) -> dict:
    out = {"state": state, "headline": text}
    out.update(rest)
    return out



def resources() -> dict:
    from argus.core import job_preflight as JP
    from argus.core import owned_process_tree as OPT

    def build():
        f = JP.floors()
        spec = JP.JobSpec(name="status", input_path="", roi=None, output_path=_argus_public_path('anchor', ''))
        fc = JP.resource_forecast(spec)
        try:
            audit = OPT.audit()
        except Exception as exc:
            audit = {"leases": 0, "rows": [], "orphans": [],
                     "error": "%s: %s" % (type(exc).__name__, exc)}

        orphans = audit.get("orphans", [])
        held = sum(o.get("orphan_working_set_mb", 0) for o in orphans)
        measured = bool(f.get("measured"))

        if orphans:
            s = _headline("ORPHANED_WORK",
                          "%d job(s) left processes holding %d MB" % (len(orphans), held))
        elif not measured:
            s = _headline("NOT_MEASURED",
                          "no measured floors: run a job under measurement first")
        else:
            short = [k for k, need in (("ram_free_mb", f.get("ram_floor_mb")),
                                       ("disk_free_mb", f.get("disk_floor_mb")),
                                       ("vram_free_mb", f.get("vram_floor_mb")))
                     if need and fc.get(k) is not None and fc[k] < need]
            s = (_headline("BELOW_FLOOR", "%s below the measured floor" % ", ".join(short))
                 if short else
                 _headline("CLEAR", "free resources are above every measured floor"))

        s.update({
          "floors_measured": measured,
          "floors": f,
          "free": {k: fc.get(k) for k in ("ram_free_mb", "ram_total_mb", "disk_free_mb",
                                          "vram_free_mb", "vram_total_mb")},
          "leases": audit.get("leases", 0),
          "orphan_jobs": [o.get("job") for o in orphans],
          "orphan_working_set_mb": held,
          "why_floors_are_measured": "a floor invented at a keyboard is a number that fails in "
                                     "the direction nobody tested. These come from a sealed "
                                     "run's own peaks.",
        })
        return s

    return _cached("resources", build)



def updates() -> dict:
    from argus.core import provider_updates as PU

    def build():
        rec = PU.status()
        rows = rec.get("sources", [])
        candidates = [r for r in rec.get("updates", []) if r.get("stage") != "CURRENT"]
        unavailable = [r.get("source_id") for r in rows
                       if r.get("watch_state") not in ("OK", "NOT_CHECKED")]
        unchecked = [r.get("source_id") for r in rows
                     if r.get("watch_state") == "NOT_CHECKED"]
        checked = [r for r in rows if r.get("checked_utc")]
        if unavailable:
            s = _headline("DEGRADED",
                          "%d source(s) could not be checked; no update was activated" % len(unavailable))
        elif not checked:
            s = _headline("NOT_CHECKED", "upstream has not been checked on this machine yet")
        elif candidates:
            s = _headline("DRIFTED",
                          "%d candidate revision(s) observed; nothing was activated" % len(candidates))
        else:
            s = _headline("IN_SYNC", "every tracked source matches its pin")
        s.update({
          "checked_utc": rec.get("last_checked_utc"),
          "policy": (rec.get("policy") or {}).get("mode"),
          "sources": [{"key": r.get("source_id"), "kind": r.get("type"),
                       "state": r.get("watch_state"), "stable": r.get("admitted_revision"),
                       "candidate": r.get("observed_revision"),
                       "checked_utc": r.get("checked_utc")} for r in rows],
          "candidate_updates": len(candidates),
          "unavailable": unavailable,
          "unchecked": unchecked,
          "nothing_was_promoted": "automatic policy observes upstream metadata only. Staging, "
                                  "adapter tests, downloads and activation remain governed; "
                                  "scientific activation also requires real-data evidence.",
        })
        return s

    return _cached("updates", build)



def downloads() -> dict:
    from argus.core import content_store as CS

    def build():
        try:
            rec = CS.as_record()
        except Exception as exc:
            return _headline("UNREADABLE", "the content index could not be read",
                             why="%s: %s" % (type(exc).__name__, exc))
        held = rec.get("objects_held", 0)
        gb = rec.get("bytes_held", 0) / float(1 << 30)
        s = (_headline("EMPTY", "nothing is held in the content store yet") if not held else
             _headline("HELD", "%d object(s), %.2f GB, held once each" % (held, gb)))
        s.update({k: rec.get(k) for k in ("objects_indexed", "objects_held", "bytes_held",
                                          "derived_here", "upstream_refetchable",
                                          "evictable_bytes")})
        s["derived_is_never_evicted"] = ("an artifact produced here has no upstream to "
                                         "re-download it from.")
        return s

    return _cached("downloads", build)



def _tracked_files() -> list:
    from argus.core import paths
    try:
        r = subprocess.run(["git", "-C", str(paths.repo()), "ls-files"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return []
        return [x for x in r.stdout.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def public_release() -> dict:
    from argus.core import licence_resolver as LR
    from argus.core import release_exporter as RE

    def build():
        files = _tracked_files()
        rows = [RE.classify(p) for p in files]
        by_class = {c: sum(1 for r in rows if r["class"] == c) for c in RE.CLASSES}
        unclassified = sum(1 for r in rows if r["class"] is None)
        publishable = sum(1 for r in rows if r["publishable"])

        try:
            lic = LR.register()
            undeclared = [x["component"] for x in lic["rows"] if not x["ok"]]
        except Exception:
            undeclared = ["UNKNOWN"]

        if not files:
            s = _headline("NOT_MEASURED", "the tracked file list could not be read")
        elif undeclared:
            s = _headline("BLOCKED",
                          "%d component(s) carry no declared licence" % len(undeclared))
        else:
            s = _headline("READY_BUT_UNPUBLISHED",
                          "%d of %d tracked files would publish; the rest stay home"
                          % (publishable, len(files)))
        s.update({
          "tracked_files": len(files),
          "publishable": publishable,
          "withheld": len(files) - publishable,
          "by_class": by_class,
          "unclassified": unclassified,
          "undeclared_licences": undeclared,
          "publishing_is_not_automatic": "this counts what a publish WOULD include. Publishing "
                                         "is the operator's action and nothing here performs it.",
          "fail_closed": "a path matching no allow rule is withheld. Withholding something "
                         "publishable is recoverable; publishing something private is not.",
        })
        return s

    return _cached("public_release", build, ttl=120.0)



STANDING_GATES = (
  ("NO_QUALIFIED_DETECTOR", "CLOSED",
   "the public build ships no qualified detector. A detector's output becomes evidence only "
   "after it passes a declared cross-scroll gate."),
  ("BLIND_HUNT", "CLOSED",
   "no protected blind target is accessed. Not gated on a result; gated on authorisation."),
  ("CROSS_SCROLL_QUALIFICATION", "NOT_ESTABLISHED",
   "no cross-scroll qualification result ships with the public build. Qualification needs "
   "independently labelled scrolls held out from training, and it is established by running "
   "it, not by describing it."),
  ("PUBLICATION", "OPERATOR_ONLY",
   "nothing is published, posted, messaged or merged from here."),
)


def private_campaign() -> dict:
    from argus.core import authorship as AU

    def build():
        closed = [g for g in STANDING_GATES if g[1] in ("CLOSED", "BLOCKED_BY_DATA")]
        s = _headline("NO_QUALIFIED_DETECTOR",
                      "%d standing gate(s) closed; campaign state is not part of the public release"
                      % len(closed))
        try:
            auth = AU.as_record()
        except Exception as exc:
            auth = {"outstanding": None, "why": "%s: %s" % (type(exc).__name__, exc)}
        s.update({
          "authorship_debt": {
            "commits_crediting_a_tool": auth.get("outstanding"),
            "of_total": AU.HISTORICAL_DEBT["commits_total"],
            "all_pushed": AU.HISTORICAL_DEBT["all_pushed"],
            "new_commits_are_refused": "a tracked commit-msg hook now refuses one, so the count "
                                       "cannot grow.",
            "clearing_it_needs_authorisation": AU.HISTORICAL_DEBT["why_not_fixed_here"],
          },
          "gates": [{"gate": g, "state": st, "why": why} for g, st, why in STANDING_GATES],
          "probability_is_display_only": "a probability above 0.5 is a display threshold and "
                                         "never a scientific criterion.",
          "operational_is_not_scientific": "reproducing a released model proves the route runs. "
                                           "It establishes nothing about ink and contributes "
                                           "nothing to qualification.",
        })
        return s

    return _cached("private_campaign", build, ttl=600.0)



def all_surfaces() -> dict:
    out = {"contract": CONTRACT,
           "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "surfaces": {}}
    for name in SURFACES:
        try:
            out["surfaces"][name] = globals()[name]()
        except Exception as exc:
            out["surfaces"][name] = _headline(
              "ERROR", "this surface could not be computed",
              why="%s: %s" % (type(exc).__name__, exc))
    out["worst_state"] = _worst([v["state"] for v in out["surfaces"].values()])
    return out


_SEVERITY = ("ERROR", "ROADMAP_RECONCILIATION_FAILED", "ORPHANED_WORK", "BELOW_FLOOR",
             "BLOCKED", "PREREQUISITES_NOT_MET", "UNREADABLE",
             "NO_QUALIFIED_DETECTOR", "NOT_MEASURED", "NOT_CHECKED", "DRIFTED",
             "READY_BUT_UNPUBLISHED", "EMPTY", "HELD", "JOINED", "IN_SYNC", "CLEAR")


def _worst(states) -> str:
    for s in _SEVERITY:
        if s in states:
            return s
    return "UNKNOWN"



def detector() -> dict:
    """The detector head: not part of the public release."""
    return _headline(
      "NOT_MEASURED", "the detector lineage is not part of the public release",
      current_detector_head=None, last_terminal_lineage_node=None, last_terminal_state=None,
      live_contracts=[], pending_reruns=[], refused_lanes=[], open_pending=[],
      successor_prerequisites=None, training_authorised=False,
      successor_launch_authorised=False, next_machine_action=None,
      nothing_is_authorised="the public build reports no detector lineage. Authorising a launch "
                            "is an operator act and no consumer of this surface performs one.")
