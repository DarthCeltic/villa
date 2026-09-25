"""`argus identify` -- say what a scroll volume or segment IS, from the data itself, and what the route from there looks like."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _zarr_evidence(root: pathlib.Path) -> dict:
    ev = {"names": [{"kind": "zarr_name", "text": str(root).replace("\\", "/")}], "scroll_hints": []}
    if root.parent.name:
        ev["scroll_hints"].append({"kind": "the folder holding the store", "text": root.parent.parent.name
                                   if root.parent.name == "volumes" else root.parent.name})
    z = root / "0" / ".zarray"
    try:
        h = json.loads(z.read_text(encoding="utf-8"))
        ev["array"] = {"shape": h.get("shape"), "chunks": h.get("chunks"), "dtype": h.get("dtype")}
    except (OSError, ValueError):
        pass
    return ev


def gather(target: str) -> tuple:
    """(kind, evidence) for a URL, a tifxyz directory or a local zarr store."""
    if "://" in target:
        return "url", {"names": [{"kind": "url", "text": target}], "scroll_hints": []}
    p = pathlib.Path(target)
    if not p.exists():
        raise ValueError("%s does not exist" % target)
    if p.is_dir() and (p / "meta.json").is_file() and (p / "x.tif").is_file():
        from argus.core import segment_import as SI
        from argus.core import native_3d_provider as N3P
        declared = None
        src = p / N3P.VOLUME_SOURCE_FILE
        if src.is_file():
            declared = N3P._read_source_line(src)
        return "tifxyz", SI.gather_evidence(p.resolve(), declared)
    if p.is_dir() and (p.name.endswith(".zarr") or (p / ".zattrs").is_file() or (p / ".zgroup").is_file()):
        return "zarr", _zarr_evidence(p.resolve())
    raise ValueError("%s is not a tifxyz directory, a zarr store or a URL" % target)


def identify(target: str, *, route: bool = True) -> dict:
    from argus.core import official_identity as OI
    kind, ev = gather(target)
    res = OI.identify_volume(ev)
    out = {"target": target, "kind": kind, **res}
    if route and res["state"] == "IDENTIFIED":
        from argus.core import scroll_status as SS
        st = SS.status(res["identity"]["scroll"])
        out["route"] = {
            "scroll": res["identity"]["scroll"], "status": st.get("status"),
            "refusal": st.get("refusal"), "step_counts": st.get("step_counts"),
            "next_action": st.get("next_action"), "blocker": st.get("blocker"),
            "stages": [{"n": s["n"], "id": s["id"], "state": s["state"], "why": s.get("why"),
                        "code": s.get("code")} for s in st.get("steps") or []],
            "read_only": True}
    return out


def _print(o: dict, out) -> None:
    print("target      %s  (%s)" % (o["target"], o["kind"]), file=out)
    if o["state"] != "IDENTIFIED":
        print("REFUSED     %s" % "; ".join(o["reasons"]), file=out)
        for u in o["evidence_used"]:
            print("  seen      %s: %s" % (u["evidence"], u["value"]), file=out)
        return
    i = o["identity"]
    print("IDENTIFIED  %s  confidence %s  (%s)" % (i["scroll"], o["confidence"], o.get("basis")), file=out)
    print("  volume    %s   scan %s   %s um  %s keV" % (i["volume_id"], i["scan_id"],
                                                        i["pixel_size_um"], i["energy_kev"]), file=out)
    print("  store     %s" % i["source_url"], file=out)
    print("  prizes    %s" % (", ".join(i["prizes"]) or "none"), file=out)
    for u in o["evidence_used"]:
        print("  evidence  %s: %s" % (u["evidence"], u["value"]), file=out)
    r = o.get("route")
    if r:
        print("route for %s (read-only; nothing was run)" % r["scroll"], file=out)
        for s in r["stages"]:
            print("  %2d %-20s %-11s %s" % (s["n"], s["id"], s["state"], (s.get("why") or "")[:110]), file=out)
        na = r.get("next_action") or {}
        print("  next      %s: %s" % (na.get("label"), (na.get("why") or "")[:160]), file=out)


def run(argv, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog="argus identify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-route", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        o = identify(a.target, route=not a.no_route)
    except ValueError as e:
        print("argus identify: %s" % e, file=out)
        return 2
    if a.json:
        print(json.dumps(o, indent=1, sort_keys=True, default=str), file=out)
    else:
        _print(o, out)
    return 0 if o["state"] == "IDENTIFIED" else 1
