from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import check_saved_mesh, write_receipt


def _print_and_exit(report) -> int:
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.status == "PASS" else 2 if report.status == "FAIL" else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidence-gate")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="fast DECLARED check: trust an already-computed evidence JSON")
    check.add_argument("--mesh", required=True, type=Path,
                       help="local saved evidence JSON; no live mesh is fetched")
    check.add_argument("--volume-url", required=True,
                       help="volume identity or URL; remote URLs are refused, never fetched")
    check.add_argument("--receipt", type=Path)

    measure = sub.add_parser(
        "measure", help="real MEASURED check: run ARGUS's own geometry gates on a real "
                        "tifxyz mesh (requires the argus package and tifffile)")
    measure.add_argument("--manifest", required=True, type=Path,
                         help='JSON: {"mesh_dir": "...", "acquisition": {...}, '
                              '"volume_shape": [z,y,x], "volume_url": "...", '
                              '"orientation_provenance": "AS_WRITTEN|REVERSED", ...}')
    measure.add_argument("--allow-network", action="store_true",
                         help="permit bounded, byte-range reads against a remote volume_url "
                              "(a local file:// volume is always read; without this flag a "
                              "remote volume_url is declared but not fetched)")
    measure.add_argument("--volume-only", action="store_true",
                         help="chunk-identity probe of a CT volume alone (no mesh, no orientation): "
                              "takes volume_url from the manifest and needs --probe-box")
    measure.add_argument("--probe-box", metavar="z0:z1:y0:y1:x0:x1",
                         help='half-open voxel box on the manifest "level" (default 0); '
                              "required with --volume-only")
    measure.add_argument("--receipt", type=Path)

    args = parser.parse_args(argv)
    full_argv = ["evidence-gate", *sys.argv[1:]]

    if args.command == "check":
        report = check_saved_mesh(args.mesh, args.volume_url,
                                  command="evidence-gate check", argv=full_argv)
        if args.receipt:
            write_receipt(report, args.receipt)
        return _print_and_exit(report)

    if args.command == "measure":
        from .measure import measure_mesh, parse_probe_box, probe_volume
        spec = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.volume_only:
            if not args.probe_box or not spec.get("volume_url"):
                parser.error("--volume-only needs --probe-box and a volume_url in the manifest")
            try:
                box = parse_probe_box(args.probe_box)
            except ValueError as exc:
                parser.error(str(exc))
            report = probe_volume(spec["volume_url"], box, allow_network=args.allow_network,
                                  level=int(spec.get("level", 0)), argv=full_argv)
            if args.receipt:
                write_receipt(report, args.receipt)
            return _print_and_exit(report)
        mesh_dir = Path(spec["mesh_dir"])
        if not mesh_dir.is_absolute():
            mesh_dir = (args.manifest.parent / mesh_dir).resolve()
        report = measure_mesh(
            mesh_dir, spec["acquisition"],
            volume_shape=spec.get("volume_shape"),
            volume_origin_zyx=tuple(spec.get("volume_origin_zyx", (0.0, 0.0, 0.0))),
            coord_scale=float(spec.get("coord_scale", 1.0)),
            volume_url=spec.get("volume_url"), allow_network=args.allow_network,
            chunk_shape=tuple(spec.get("chunk_shape", (128, 128, 128))),
            fill_value=float(spec.get("fill_value", 0.0)),
            orientation_provenance=spec.get("orientation_provenance"),
            orientation_control=spec.get("orientation_control"),
            command="evidence-gate measure", argv=full_argv)
        if args.receipt:
            write_receipt(report, args.receipt)
        return _print_and_exit(report)

    return 2
