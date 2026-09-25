"""`argus run` -- the real pipeline, behind the gates that must pass before it is worth starting."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

from argus.core import (acquisition_identity, compression_provenance, paths,
                        public_identity, side_permit)
from argus.core import route_wiring as RW
from argus.cli import acquire as A
from argus.cli import hardware, manifest, state

DEFAULT_MODEL = None

TARGET_DIR = ("argus", "targets")


def target_paths() -> list:
    d = pathlib.Path(paths.repo(*TARGET_DIR))
    return sorted(d.glob("*.json")) if d.is_dir() else []


DRIVER = ("scripts", "pipeline_driver.py")


def missing_pipeline_inputs() -> list:
    """What `argus run` needs that this checkout does not have, each with what the operator supplies."""
    out = []
    if not target_paths():
        out.append({"missing": "target manifest",
                    "where": "%s/*.json" % "/".join(TARGET_DIR),
                    "operator_supplies": "a JSON file per target with `target` (<scroll>/<segment>), "
                                         "`zarr_base` (the volume URL), `level`, `volume_shape`, "
                                         "`acquisition` and, for a mesh route, `mesh_dir`"})
    if not pathlib.Path(paths.repo(*DRIVER)).is_file():
        out.append({"missing": "pipeline driver",
                    "where": "/".join(DRIVER),
                    "operator_supplies": "the governed driver script (private research tooling, "
                                         "not part of the public build), the ink-detection stages "
                                         "it composes, a detector checkpoint, and a consumed "
                                         "launch authorisation (--authorization-id and "
                                         "--launch-packet)"})
    return out


def _print_public_targets(out) -> None:
    """The bounded public-data runs this build can perform, if any (`argus run <id>`)."""
    from argus.cli import public_pipeline as PP
    found = PP.public_target_paths()
    if found:
        print("public target manifests (argus run <id> --dry-run explains each stage):", file=out)
        for p in found:
            print("  %s" % p.stem, file=out)


def resolve_target(name: str):
    """A target manifest, by file stem or by its declared target string."""
    for p in target_paths():
        if p.stem == name:
            return p
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("target") == name:
            return p
    return None


def public_segment(doc: dict, alias: str):
    """Build the four-part public name, or return None if any part is missing."""
    target = str(doc.get("target") or "")
    if "/" not in target:
        return None
    scroll, upstream = target.split("/", 1)
    volume = str(doc.get("zarr_base") or "").rstrip("/").rsplit("/", 1)[-1]
    if not (scroll and upstream and volume):
        return None
    return public_identity.PublicSegment(
        physical_scroll=scroll, argus_alias=alias, upstream_segment_id=upstream,
        volume_id=volume, pyramid_level=str(doc.get("level", "")),
        source_url=doc.get("zarr_base"))


def readiness(c, root, *, target_url=None) -> dict:
    """Is one component actually usable, per the way IT is acquired?"""
    accepted = state.load_acceptance(root)
    if c.id not in accepted:
        return {"component": c.id, "state": "NOT_ACCEPTED",
                "detail": "its licence (%s) has not been accepted here" % c.spdx,
                "fix": "argus setup --accept %s   (read %s first)" % (c.id, c.licence_url)}
    if c.integrity_mode == "SHA256":
        st = A.state(c, root)
        if st.get("verified"):
            return {"component": c.id, "state": "READY", "detail": "digest verified",
                    "fix": ""}
        return {"component": c.id, "state": "NOT_HELD",
                "detail": "not held and digest-verified (%d bytes of partial transfer)"
                          % st.get("partial_bytes", 0),
                "fix": "argus setup"}
    if c.acquire == "git":
        stem = c.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        tree = pathlib.Path(paths.upstream(stem))
        if tree.is_dir():
            return {"component": c.id, "state": "READY",
                    "detail": "checkout present at %s (pin checked by argus doctor)" % tree,
                    "fix": ""}
        return {"component": c.id, "state": "NOT_CHECKED_OUT",
                "detail": "no checkout at %s" % tree, "fix": "argus setup"}
    if c.acquire == "s3-prefix":
        found = acquisition_identity.find_store(target_url) if target_url else None
        if found:
            return {"component": c.id, "state": "READY",
                    "detail": "a sealed local store for this target exists at %s" % found,
                    "fix": ""}
        return {"component": c.id, "state": "NO_LOCAL_STORE",
                "detail": "no sealed store for this target's volume in any cache root. "
                          "Scoring must never stream from the network.",
                "fix": "acquire the region of interest first; argus doctor shows the roots "
                       "that were searched"}
    if c.acquire == "pip":
        missing = [m for m in c.provides_modules
                   if importlib.util.find_spec(m) is None]
        if not c.provides_modules:
            return {"component": c.id, "state": "UNRESOLVED",
                    "detail": "the manifest does not say which modules this provides, so "
                              "whether it is installed cannot be established",
                    "fix": "add provides_modules to this component in components.json"}
        if missing:
            return {"component": c.id, "state": "NOT_INSTALLED",
                    "detail": "missing modules: %s" % ", ".join(missing),
                    "fix": "uv pip install -e .[volume]"}
        return {"component": c.id, "state": "READY",
                "detail": "all declared modules import", "fix": ""}
    return {"component": c.id, "state": "UNRESOLVED",
            "detail": "acquisition method %r with integrity %s: this project has no way to "
                      "establish that it is present and correct. %s"
                      % (c.acquire, c.integrity_mode, c.integrity_why_unknown or ""),
            "fix": "record a content address for it in argus/cli/components.json, or obtain "
                   "it by hand and pin what you obtained"}


def _refuse(out, code: str, detail: str, fix: str) -> int:
    print("REFUSED: %s" % code, file=out)
    print("  %s" % detail, file=out)
    print("  fix: %s" % fix, file=out)
    return 1


def run(argv, out=sys.stdout) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="argus run", description=__doc__.splitlines()[0])
    ap.add_argument("pipeline", nargs="?", default=None,
                    help="a public target manifest (path or id under argus/public_targets): the "
                         "end-to-end run over public data")
    ap.add_argument("--authorize", metavar="ID", default=None,
                    help="with a public target: stage its pinned checkpoint and issue single-use "
                         "authorisation ID for exactly this run")
    ap.add_argument("--target", default=None,
                    help="a target manifest stem, or the target string it declares")
    ap.add_argument("--list", action="store_true", help="the targets this checkout knows")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="the model key whose declared compression policy applies; there is no default")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every gate and stop before the pipeline")
    ap.add_argument("--no-wait", action="store_true",
                    help="refuse instead of waiting when side work is suspended")
    ap.add_argument("--root", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--authorization-id", default=None,
                    help="single-use launch_authorization_v3 id (required to launch)")
    ap.add_argument("--launch-packet", default=None,
                    help="JSON naming runner, modules, contract, plan, inputs and checkpoint")
    ap.add_argument("--receipt-dir", default=None,
                    help="where refusal receipts are written (default: artifacts/route_refusals)")
    ns, passthrough = ap.parse_known_args(argv)

    if ns.pipeline:
        from argus.cli import public_pipeline as PP
        return PP.run_manifest(ns.pipeline, authorization_id=ns.authorization_id,
                               authorize_as=ns.authorize, dry_run=ns.dry_run, out=out)

    lacking = missing_pipeline_inputs()
    if lacking and not ns.list and ns.target:
        print("REFUSED: PIPELINE_NOT_INSTALLED", file=out)
        print("  argus run cannot launch a pipeline in this checkout. Missing:", file=out)
        for m in lacking:
            print("  - %s at %s" % (m["missing"], m["where"]), file=out)
            print("      you supply: %s" % m["operator_supplies"], file=out)
        print("  nothing was checked or started, and nothing here is a result. "
              "argus doctor shows what this machine can do; argus demo shows the pipeline's "
              "shape.", file=out)
        _print_public_targets(out)
        return 1
    if ns.list or not ns.target:
        for m in lacking:
            print("note: no %s at %s. %s" % (m["missing"], m["where"], m["operator_supplies"]),
                  file=out)
        print("targets known to this checkout:", file=out)
        for p in target_paths():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print("  %-40s UNREADABLE (%s)" % (p.stem, exc), file=out)
                continue
            seg = public_segment(doc, p.stem)
            print("  %-40s %s" % (p.stem, seg.public_name() if seg
                                  else "IDENTITY INCOMPLETE -- cannot be named publicly"),
                  file=out)
        _print_public_targets(out)
        if not ns.target:
            print("\nchoose one: argus run --target <stem>", file=out)
        return 0

    p = resolve_target(ns.target)
    if p is None:
        return _refuse(out, "UNKNOWN_TARGET",
                       "no target manifest in %s matches %r."
                       % (pathlib.Path(paths.repo(*TARGET_DIR)), ns.target),
                       "argus run --list")
    doc = json.loads(p.read_text(encoding="utf-8"))
    seg = public_segment(doc, p.stem)
    if seg is None:
        return _refuse(
            out, "AMBIGUOUS_TARGET_IDENTITY",
            "%s does not carry all four parts of a public name (physical scroll, ARGUS "
            "alias, upstream segment id, volume identity). A run that cannot be named "
            "cannot be reproduced by anybody who reads its output." % p.name,
            "add the missing fields to %s" % p)
    header = "  target: %s" % seg.public_name()
    public_identity.assert_public_safe(header, [seg], where="argus run header")
    print("=" * 78, file=out)
    print(header, file=out)
    print("=" * 78, file=out)

    sel = hardware.select()
    if not sel.get("selected"):
        return _refuse(out, "NO_SUPPORTED_PROFILE", sel.get("detail", ""),
                       "argus doctor")
    profile = sel["profile"]
    if "run" not in profile["supports"]:
        return _refuse(
            out, "PROFILE_CANNOT_RUN",
            "the selected profile is %s, which supports %s. %s"
            % (profile["id"], ", ".join(profile["supports"]), profile["why_refuses"]),
            "run this on a machine with a supported GPU, or use argus demo to see the "
            "pipeline's shape without one")
    print("  profile: %s %s" % (profile["id"], profile["settings"]), file=out)
    for w in sel["warnings"]:
        print("  warning: %s" % w, file=out)

    root = ns.root or state.install_root()
    try:
        components = manifest.load(ns.manifest)
    except manifest.ManifestError as exc:
        return _refuse(out, "MANIFEST_INVALID", str(exc),
                       "correct argus/cli/components.json")
    not_ready = [readiness(c, root, target_url=doc.get("zarr_base"))
                 for c in components if "run" in c.required_for]
    not_ready = [r for r in not_ready if r["state"] != "READY"]
    if not_ready:
        print("REFUSED: COMPONENTS_NOT_READY", file=out)
        for r in not_ready:
            print("  %-22s %-12s %s" % (r["component"], r["state"], r["detail"]), file=out)
            print("       fix: %s" % r["fix"], file=out)
        return 1

    url = doc.get("zarr_base")
    store = acquisition_identity.find_store(url) if url else None
    if store is not None:
        provenance = compression_provenance.from_store(store)
    else:
        provenance = compression_provenance.classify_source(url, None)
        provenance["store"] = None
    verdict = compression_provenance.preflight(ns.model, provenance,
                                               raise_on_refusal=False)
    if verdict["verdict"] == "REFUSE":
        return _refuse(out, "COMPRESSION_PROVENANCE_" + verdict["reason"],
                       verdict["detail"],
                       "declare the model's policy with its evidence in "
                       "argus.core.compression_provenance.POLICIES, or run a checkpoint "
                       "whose policy accepts this source")
    print("  provenance: %s (%s)" % (verdict["verdict"], verdict["reason"]), file=out)

    from argus.core import capability_graph as CG
    route = CG.route(CG.derive(paths.artifact_write_root()))
    if route.get("first_blocked_stage"):
        print("  chain: blocked at %s -- see argus doctor for the evidence rows"
              % route["first_blocked_stage"], file=out)
        if not ns.dry_run:
            return _refuse(
                out, "CHAIN_BLOCKED_AT_" + str(route["first_blocked_stage"]).upper(),
                "the science chain cannot produce the %s stage on this machine, so a run "
                "would stop there. Product readiness and scientific readiness are separate "
                "and neither is asserted here." % route["first_blocked_stage"],
                "argus doctor   (the capability rows name the missing receipt), or "
                "argus run --dry-run to see the full plan anyway")

    if not side_permit.allowed():
        st_p = side_permit.permit_state()
        if ns.no_wait:
            return _refuse(out, "SIDE_WORK_SUSPENDED",
                           "%s -- %s" % (st_p.get("reason"), st_p.get("detail")),
                           "wait, or re-run without --no-wait to have argus wait for you")
        print("  waiting for the side-work permit ...", file=out)
        side_permit.wait_for_permit(announce=lambda d: print("  %s" % d, file=out))

    driver = pathlib.Path(paths.repo("scripts", "pipeline_driver.py"))
    cmd = [sys.executable, str(driver), "--target", ns.target, *passthrough]
    if ns.dry_run:
        print("  DRY RUN. every gate passed. the pipeline would be launched as:", file=out)
        print("    %s" % " ".join(cmd), file=out)
        print("  a real launch additionally requires a consumed launch_authorization_v3 "
              "(--authorization-id + --launch-packet); a dry run consumes nothing", file=out)
        return 0
    if not driver.is_file():
        return _refuse(out, "PIPELINE_MISSING", "no pipeline driver at %s" % driver,
                       "restore scripts/pipeline_driver.py from the repository")

    receipt_dir = (pathlib.Path(ns.receipt_dir) if ns.receipt_dir
                   else paths.artifact_write_root() / "route_refusals")
    try:
        RW.launch_v3(ns.authorization_id, packet=RW.load_launch_packet(ns.launch_packet),
                     receipt_dir=receipt_dir, argv=cmd, stage="argus run")
    except RW.RouteRefusal as exc:
        return _refuse(out, "LAUNCH_AUTHORIZATION_V3", "%s" % exc,
                       "issue a v3 authorisation for exactly this launch "
                       "(argus.core.launch_authorization_v3.issue) and pass --authorization-id "
                       "and --launch-packet")
    print("  launching the governed pipeline ...", file=out)
    return subprocess.run(cmd, cwd=str(paths.repo())).returncode
