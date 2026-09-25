"""`argus acquire --dry-run` -- enumerate a volume acquisition and fetch NOTHING."""
from __future__ import annotations

import json
import pathlib
import sys

from argus.core import paths
from argus.core import route_wiring as RW
from argus.core import volume_acquisition as VA


def run(argv, out=sys.stdout) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="argus acquire", description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="volume URL on either official mirror")
    ap.add_argument("--scroll", required=True, help="physical scroll the volume belongs to")
    ap.add_argument("--volume-id", required=True, help="the OFFICIAL 14-digit volume id")
    ap.add_argument("--phase", required=True, choices=sorted(VA.PHASES))
    ap.add_argument("--array-path", default="", help="pyramid level inside the volume, e.g. 0")
    ap.add_argument("--roi", default=None, help="z0,z1,y0,y1,x0,x1 (exact; never clamped)")
    ap.add_argument("--zarray", default=None, help="LOCAL .zarray file for A1/A2 planning")
    ap.add_argument("--byte-ceiling", type=int, default=None)
    ap.add_argument("--receipt-dir", default=None,
                    help="where refusal receipts go (default: artifacts/route_refusals)")
    ap.add_argument("--dry-run", action="store_true",
                    help="REQUIRED. Enumerate the job from local metadata; zero requests")
    a = ap.parse_args(argv)
    receipt_dir = (pathlib.Path(a.receipt_dir) if a.receipt_dir
                   else paths.artifact_write_root() / "route_refusals")
    try:
        if not a.dry_run:
            RW.write_refusal("ACQUISITION_DRY_RUN", stage="argus acquire", receipt_dir=receipt_dir,
                             reasons=["this verb performs dry runs only; a real fetch goes "
                                      "through volume_acquisition.acquire() under a consumed "
                                      "launch_authorization_v3, which no flag can supply"])
            print(json.dumps({"refused": True, "gate": "ACQUISITION_DRY_RUN",
                              "why": "--dry-run is required; this verb never fetches"},
                             indent=1), file=out)
            return 2
        meta = None
        if a.zarray:
            meta = json.loads(pathlib.Path(a.zarray).read_text(encoding="utf-8"))
        plan = RW.acquire_dry_run(receipt_dir=receipt_dir, scroll=a.scroll,
                                  volume_id=a.volume_id, url=a.url, phase=a.phase,
                                  array_path=a.array_path, roi=a.roi, zarray_meta=meta,
                                  byte_ceiling=a.byte_ceiling)
    except RW.RouteRefusal as exc:
        print(json.dumps({"refused": True, "gate": exc.gate, "receipt": str(exc.receipt_path),
                          "why": str(exc)}, indent=1), file=out)
        return 2
    show = dict(plan)
    show["keys"] = plan["keys"][:5] + (["..."] if len(plan["keys"]) > 5 else [])
    print(json.dumps(show, indent=1, default=str), file=out)
    return 0
