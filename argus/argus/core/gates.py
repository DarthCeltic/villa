"""VIGILES: the gates between raw CT and a candidate anyone should believe.

Public build. The ladder keeps its shape and every gate name, so the service route and the
interface that render it keep working. The evidence the operator's own gates read (canvas
provenance, model parity, training exposure, controls and selection records) is not part of
the public release, so each of those gates reports SHUT with a plain reason and a concrete
thing that would open it. Nothing here fabricates a verdict, and nothing here submits.
"""
from __future__ import annotations

from argus.core import paths, storage_catalog

OPEN, SHUT, BLOCKED_UPSTREAM = "OPEN", "SHUT", "BLOCKED_UPSTREAM"

NOT_PUBLIC = "not part of the public release"


def _gate(name, state, why, satisfied_by, evidence=None, **extra):
    return dict({"gate": name, "state": state, "why": why,
                 "satisfied_by": satisfied_by, "evidence": evidence}, **extra)


def _after(name, prev, upstream, why, satisfied_by):
    """A gate that is BLOCKED_UPSTREAM behind a shut predecessor, and SHUT otherwise."""
    if prev["state"] != OPEN:
        return _gate(name, BLOCKED_UPSTREAM, "%s is not open" % upstream,
                     "open %s first" % upstream)
    return _gate(name, SHUT, why, satisfied_by, evidence={"public_build": NOT_PUBLIC})


def geometry() -> dict:
    """Is there a surface whose geometry we can stand on?"""
    fragment_paths = storage_catalog.local_fragment_paths(
        fallback_root=paths.science_data("fragments"))
    staged = sorted(fragment_paths)
    masked = [name for name, root in fragment_paths.items()
              if (root / "mask.png").is_file()]
    if not masked:
        return _gate("geometry", SHUT,
                     "no staged fragment carries a validity mask, so no surface has a "
                     "defensible extent",
                     "stage a fragment with its mask.png, or record an "
                     "ABSENT_BY_DECLARATION citing the upstream listing",
                     evidence={"staged": staged})
    return _gate("geometry", OPEN,
                 "%d staged fragment(s) carry a verified validity mask" % len(masked),
                 None, evidence={"with_mask": masked})


def canvas() -> dict:
    """Is the canvas we score on provably the canvas the publisher declared?"""
    return _gate("canvas", SHUT,
                 "no canvas-provenance record is read in this build; the operator's canvas "
                 "records are %s" % NOT_PUBLIC,
                 "record a canvas-provenance check for your own data",
                 evidence={"public_build": NOT_PUBLIC})


def depth(prev) -> dict:
    """Is the depth window placed without consulting a label, and is polarity controlled?"""
    return _after("depth", prev, "geometry",
                  "no frozen plan recording a label-free depth window is read in this build",
                  "freeze a plan that places the depth window without consulting a label")


def representation(prev) -> dict:
    """Is the model being fed at the pitch and depth extent it was trained for?"""
    return _after("representation", prev, "depth",
                  "no strict-load parity receipt for a model is read in this build",
                  "record a strict-load parity receipt for the model you intend to use")


def exposure(prev) -> dict:
    """Do we know what the model trained on, and is the target excluded from it?"""
    return _after("exposure", prev, "representation",
                  "no training-exposure record for a model is read in this build",
                  "resolve the model's training set by dataset root before scoring anything "
                  "with it")


def controls(prev) -> dict:
    """Have the controls that make a number mean something actually run?"""
    return _after("controls", prev, "exposure",
                  "no matched-scope control run is read in this build",
                  "run the matched-scope controls your frozen plan declares")


def specificity(prev) -> dict:
    """Does anything clear a frozen selection floor, with a valid interval?"""
    return _after("specificity", prev, "controls",
                  "no selection result against a frozen floor is read in this build",
                  "declare the selection floor before any result exists, then record the "
                  "result against it")


def candidate(prev) -> dict:
    if prev["state"] != OPEN:
        return _gate("candidate", BLOCKED_UPSTREAM,
                     "nothing has cleared the selection floor",
                     "clear specificity first")
    n = 0
    for _r in paths.artifact_roots():
        _c = _r / "candidates"
        if _c.is_dir():
            n += len(list(_c.iterdir()))
    return _gate("candidate", OPEN if n else SHUT,
                 "%d candidate object(s)" % n if n else "no candidate has been produced",
                 None if n else ("complete the declared qualification and operator-authorisation "
                                 "gates before any target action is considered"),
                 evidence={"n": n})


def submission(prev) -> dict:
    """Deliberately last, and deliberately far."""
    return _gate("submission", BLOCKED_UPSTREAM if prev["state"] != OPEN else SHUT,
                 ("no candidate exists, and a candidate is not a submission" if prev["state"]
                  != OPEN else
                  "a candidate exists, and submission still requires human review"),
                 "operator decision only. Nothing here submits, and nothing may",
                 evidence={"automated_submission": "never"})


def evaluate() -> dict:
    g = geometry()
    c = canvas()
    d = depth(g)
    r = representation(d)
    e = exposure(r)
    ct = controls(e)
    sp = specificity(ct)
    cd = candidate(sp)
    sb = submission(cd)
    gates = [g, c, d, r, e, ct, sp, cd, sb]
    first_shut = next((x for x in gates if x["state"] != OPEN), None)
    return {
        "schema": "argus-vigiles-gates-v1",
        "gates": gates,
        "n_open": sum(1 for x in gates if x["state"] == OPEN),
        "n_total": len(gates),
        "first_closed": (first_shut or {}).get("gate"),
        "next_action": (first_shut or {}).get("satisfied_by"),
        "rule": ("a later gate whose predecessor is shut reports BLOCKED_UPSTREAM rather than "
                 "its own verdict. Evaluating specificity on uncertified geometry produces a "
                 "number about nothing"),
        "every_verdict_is_read_from_disk": True,
        "public_build": "the operator's gate evidence is %s; the gates that would read it "
                        "report SHUT rather than a fabricated verdict" % NOT_PUBLIC,
    }
