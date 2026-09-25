"""`argus setup` -- acquire and verify everything, after saying what it is and what it costs."""
from __future__ import annotations

import sys

from argus.core import side_permit
from argus.cli import acquire as A
from argus.cli import hardware, manifest, state

BAR = "-" * 78


def _profile_report(sel: dict, out) -> None:
    m = sel["measured"]
    print(BAR, file=out)
    print("MACHINE", file=out)
    print(BAR, file=out)
    print("  platform      : %s, python %s" % (m["platform"], m["python"]), file=out)
    print("  cpu cores     : %s" % (m["cpu_count"] or "UNKNOWN"), file=out)
    print("  free RAM      : %s"
          % ("%.1f GiB" % m["free_ram_gib"] if m["free_ram_gib"] is not None
             else "UNKNOWN -- " + str(m["free_ram_why_unknown"])), file=out)
    for d in m["disks"]:
        print("  disk %-10s: %s" % (d["volume"],
                                    "%.1f GiB free of %.1f GiB" % (d["free_gib"], d["total_gib"])
                                    if d["free_gib"] is not None
                                    else "UNKNOWN -- " + str(d["why_unknown"])), file=out)
    g = m["gpu"]
    if g["status"] == "PRESENT":
        for d in g["devices"]:
            cc = ("compute %.1f" % d["compute_capability"]) if d["compute_capability"] \
                else "compute UNKNOWN (%s)" % d["compute_capability_why_unknown"]
            print("  gpu           : %s, %s MB VRAM, %s"
                  % (d["name"], int(d["vram_mb"]) if d["vram_mb"] else "UNKNOWN", cc), file=out)
    else:
        print("  gpu           : %s -- %s" % (g["status"], g["why"]), file=out)
    print(file=out)
    print(BAR, file=out)
    print("PROFILE", file=out)
    print(BAR, file=out)
    if not sel["selected"]:
        print("  NONE SUPPORTED. %s" % sel["detail"], file=out)
        for r in sel["rejected"]:
            print("    %-10s unmet: %s" % (r["profile"], "; ".join(r["unmet"])), file=out)
        return
    p = sel["profile"]
    print("  selected      : %s%s" % (p["id"], "  (first-class, not a fallback)"
                                      if p["first_class"] else ""), file=out)
    print("  what it does  : %s" % ", ".join(p["supports"]), file=out)
    if p["refuses"]:
        print("  what it will NOT do: %s" % ", ".join(p["refuses"]), file=out)
        print("      because   : %s" % p["why_refuses"], file=out)
    print("  settings      : %s" % p["settings"], file=out)
    if sel["amp_override"] is False:
        print("  amp           : FORCED OFF", file=out)
    for w in sel["warnings"]:
        print("  warning       : %s" % w, file=out)
    for r in sel["rejected"]:
        print("  not selected  : %-9s -- %s" % (r["profile"], "; ".join(r["unmet"])), file=out)


def _accept_interactively(c, out, interactive: bool) -> bool:
    print(BAR, file=out)
    print("LICENCE -- read this before it is fetched", file=out)
    print(BAR, file=out)
    print(c.licence_display(), file=out)
    if not interactive:
        print("  ACCEPTANCE REQUIRED. This session is not interactive, so nothing is "
              "assumed.", file=out)
        print("  fix: argus setup --accept %s" % c.id, file=out)
        return False
    try:
        answer = input("  accept these terms for %s? [y/N] " % c.id).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def run(argv, out=sys.stdout) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="argus setup", description=__doc__.splitlines()[0])
    ap.add_argument("--plan", action="store_true",
                    help="show what would happen and write nothing")
    ap.add_argument("--accept", action="append", default=[], metavar="COMPONENT_ID",
                    help="record acceptance of one component's licence (repeatable). "
                         "There is deliberately no --accept-all: the point of the prompt is "
                         "that somebody looked at each one")
    ap.add_argument("--profile", default=None,
                    help="override the measured profile. Recorded as an override, because a "
                         "hand-picked profile that the hardware does not support is a "
                         "decision, not a measurement")
    ap.add_argument("--manifest", default=None, help="an alternative component manifest")
    ap.add_argument("--root", default=None, help="where components land (default: the "
                                                 "declared cache root)")
    ap.add_argument("--no-wait", action="store_true",
                    help="refuse immediately instead of waiting when side work is suspended")
    ns = ap.parse_args(argv)

    try:
        components = manifest.load(ns.manifest)
    except manifest.ManifestError as exc:
        print("REFUSED: the component manifest did not load.\n  %s" % exc, file=out)
        print("  fix: correct argus/cli/components.json. A manifest that cannot be trusted "
              "is not partially used.", file=out)
        return 2

    measured = hardware.probe()
    sel = hardware.select(measured)
    if ns.profile:
        sel["selected"] = ns.profile
        sel["profile"] = hardware.PROFILES.get(ns.profile)
        sel["operator_override"] = True
        if sel["profile"] is None:
            print("REFUSED: unknown profile %r. Declared profiles: %s"
                  % (ns.profile, ", ".join(sorted(hardware.PROFILES))), file=out)
            return 2
    _profile_report(sel, out)

    root = ns.root or state.install_root()
    accepted = state.load_acceptance(root)

    for cid in ns.accept:
        c = manifest.by_id(components, cid)
        if c is None:
            print("REFUSED: no component %r in the manifest." % cid, file=out)
            return 2
        print(BAR, file=out)
        print("ACCEPTING", file=out)
        print(c.licence_display(), file=out)
        if ns.plan:
            print("  --plan: not recorded.", file=out)
            continue
        state.record_acceptance(c, root)
        accepted[c.id] = c.acceptance_fingerprint()
        print("  recorded.", file=out)

    interactive = sys.stdin is not None and sys.stdin.isatty() and not ns.plan

    print(file=out)
    print(BAR, file=out)
    print("COMPONENTS", file=out)
    print(BAR, file=out)
    results, blocked = [], 0
    for c in components:
        st = A.state(c, root)
        if st.get("verified"):
            print("  [held    ] %-22s already verified; nothing fetched" % c.id, file=out)
            results.append({"component": c.id, "status": A.ALREADY_PRESENT})
            continue
        if c.id not in accepted:
            if _accept_interactively(c, out, interactive) and not ns.plan:
                state.record_acceptance(c, root)
                accepted[c.id] = c.acceptance_fingerprint()
            else:
                results.append(A.gate(c, accepted=accepted) or {})
                blocked += 1
                continue
        if ns.plan:
            g = A.gate(c, accepted=accepted)
            if g:
                print("  [would refuse] %-18s %s" % (c.id, g["reason"]), file=out)
                print("       %s" % g["detail"], file=out)
                print("       fix: %s" % g["fix"], file=out)
                results.append(g)
                blocked += 1
            else:
                print("  [would fetch ] %-18s %s" % (c.id, c.url), file=out)
                results.append({"component": c.id, "status": "PLANNED"})
            continue

        if not side_permit.allowed():
            st_p = side_permit.permit_state()
            if ns.no_wait:
                print("  [suspended] side work is not permitted (%s). Nothing was fetched."
                      % st_p.get("reason"), file=out)
                return 3
            side_permit.wait_for_permit(
                announce=lambda d: print("  [waiting  ] %s" % d, file=out))

        res = A.acquire(c, root, accepted=accepted, fetch=A.http_fetcher())
        results.append(res)
        if res["status"] == A.REFUSED:
            blocked += 1
            print("  [REFUSED ] %-22s %s" % (c.id, res["reason"]), file=out)
            print("       %s" % res["detail"], file=out)
            print("       fix: %s" % res["fix"], file=out)
        elif res["status"] == A.MANUAL:
            print("  [manual  ] %-22s %s" % (c.id, res["detail"]), file=out)
            print("       fix: %s" % res["fix"], file=out)
        else:
            print("  [%-8s] %-22s %s" % (res["status"].lower(), c.id, res["detail"]), file=out)

    print(file=out)
    print(BAR, file=out)
    print("  %d component(s) blocked. `argus doctor` lists every check and its fix."
          % blocked if blocked else "  every component is accounted for. Next: argus demo",
          file=out)
    return 1 if blocked else 0
