"""Resume / seam-consistency planning for ARGUS's own multi-part Villa provider runs."""
from __future__ import annotations

from typing import Iterable

CONTRACT = "argus-tiled-run-resume-v1"


class TiledRunRefusal(ValueError):
    """Raised only when the given receipts cannot honestly be resumed -- never a silent guess."""


_SEAM_CRITICAL_FLAGS = {
    "vesuvius.predict": ("--model-path", "--input-format", "--overlap", "--patch-size",
                         "--normalization", "--tta-type", "--scroll-id", "--segment-id",
                         "--energy", "--resolution"),
    "vesuvius.blend_logits": ("--sigma-scale", "--chunk-size", "--compression-level"),
    "vesuvius.blend_and_finalize": ("--sigma-scale", "--chunk-size", "--compression-level",
                                    "--mode", "--threshold-value"),
    "vesuvius.finalize_outputs": ("--mode", "--threshold-value", "--chunk-size"),
}

_NUM_PARTS_FLAG = "--num-parts"
_PART_ID_FLAG = "--part-id"

TILED_CAPABILITIES = tuple(sorted(_SEAM_CRITICAL_FLAGS))


def _argv_flags(argv: list) -> dict:
    """``[\"--a\", \"1\", \"--flag\", \"--b\", \"2\"]`` -> ``{\"--a\": \"1\", \"--flag\": True, \"--b\": \"2\"}``."""
    flags: dict = {}
    i = 0
    while i < len(argv):
        tok = str(argv[i])
        if tok.startswith("--"):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not str(nxt).startswith("--"):
                flags[tok] = str(nxt)
                i += 2
            else:
                flags[tok] = True
                i += 1
        else:
            i += 1
    return flags


def plan_resume(capability_id: str, num_parts: int, receipts: Iterable[dict]) -> dict:
    """Which part_ids of one declared tiled run are DONE, which are MISSING, and whether every completed part agrees on the seam-critical tiling parameters."""
    if capability_id not in _SEAM_CRITICAL_FLAGS:
        raise TiledRunRefusal(
            "%r is not one of the tiled inference capabilities this module plans resume for: %s"
            % (capability_id, list(TILED_CAPABILITIES)))
    if not isinstance(num_parts, int) or isinstance(num_parts, bool) or num_parts < 1:
        raise TiledRunRefusal("num_parts must be a positive integer, got %r" % (num_parts,))

    seam_flags = _SEAM_CRITICAL_FLAGS[capability_id]
    done: dict = {}
    reference_params: dict | None = None
    reference_part_id: int | None = None

    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("schema") != "argus-villa-provider-receipt-v1":
            raise TiledRunRefusal("every receipt must be a real argus-villa-provider-receipt-v1 record")
        if receipt.get("capability_id") != capability_id:
            raise TiledRunRefusal(
                "receipt for capability_id=%r does not belong to this %r resume plan"
                % (receipt.get("capability_id"), capability_id))

        flags = _argv_flags(receipt.get("argv") or [])
        declared_num_parts = flags.get(_NUM_PARTS_FLAG)
        if declared_num_parts is not None and str(declared_num_parts) != str(num_parts):
            raise TiledRunRefusal(
                "receipt declares --num-parts=%s but this resume plan is for num_parts=%d -- a "
                "run cannot be resumed against a different shard count without re-tiling every "
                "part, which would move every seam" % (declared_num_parts, num_parts))

        part_id_raw = flags.get(_PART_ID_FLAG)
        if part_id_raw is None or part_id_raw is True:
            raise TiledRunRefusal("receipt has no --part-id value in its argv; not a tiled-run part")
        try:
            part_id = int(part_id_raw)
        except (TypeError, ValueError):
            raise TiledRunRefusal("receipt's --part-id=%r is not an integer" % (part_id_raw,))
        if not (0 <= part_id < num_parts):
            raise TiledRunRefusal(
                "receipt's part_id=%d is outside the declared 0..%d range" % (part_id, num_parts - 1))

        if receipt.get("status") != "OK":
            continue

        params = {flag: flags.get(flag) for flag in seam_flags}
        if reference_params is None:
            reference_params, reference_part_id = params, part_id
        elif params != reference_params:
            mismatched = {flag: (reference_params[flag], params[flag])
                          for flag in seam_flags if reference_params[flag] != params[flag]}
            raise TiledRunRefusal(
                "part_id=%d used different tiling parameters than the already-completed "
                "part_id=%d -- resuming would stitch mismatched tiles into a real seam: %s"
                % (part_id, reference_part_id, mismatched))

        done[part_id] = receipt

    missing = [i for i in range(num_parts) if i not in done]
    return {
        "schema": CONTRACT,
        "capability_id": capability_id,
        "num_parts": num_parts,
        "done_part_ids": sorted(done),
        "missing_part_ids": missing,
        "complete": not missing,
        "next_part_id": missing[0] if missing else None,
        "seam_parameters_consistent": True,
        "reference_parameters": reference_params,
    }
