"""A bounded, identity-aware preview adapter for already-produced Hecate run receipts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import erratum as ERRATUM
from . import paths, scroll_ids

SCHEMA = "argus-hecate-layer-preview-v1"
RECEIPT_SCHEMA = "argus-hecate-control-run-v1"
RECEIPT_FILENAME = "RUN_RECEIPT.json"

PROVIDER_SPACING_UM = {"hecate_24um": 2.4, "hecate_96um": 9.6}

_STATE_WORDS: dict = {}
_EXPOSURE_WORDS: dict = {}


class HecateLayerRefusal(ValueError):
    pass


def _word(table: dict, value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    return table.get(value, value.replace("_", " "))


def _label(exposure: str | None, scientific_state: str | None) -> str:
    """The display label for a run receipt: its state and exposure, derived, never hardcoded."""
    return "%s / %s" % (_word(_STATE_WORDS, scientific_state), _word(_EXPOSURE_WORDS, exposure))


def _receipt_dirs() -> list[Path]:
    """Every ``RUN_RECEIPT.json`` one level under any declared artifact root."""
    seen: set[Path] = set()
    out: list[Path] = []
    for root in paths.artifact_roots():
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/" + RECEIPT_FILENAME)):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(candidate)
    return out


def _load_receipt(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("schema") != RECEIPT_SCHEMA:
        return None
    return body


def _scan() -> list[dict[str, Any]]:
    """Every readable, correctly-schema'd Hecate receipt currently on disk."""
    rows = []
    for path in _receipt_dirs():
        body = _load_receipt(path)
        if body is None:
            continue
        rows.append({"receipt_path": path, "receipt": body})
    return rows


def _relative(path: Path) -> str:
    """Repo-style ``artifacts/...`` path, matching the convention receipts already use for their own declared output paths and what ``/api/file`` expects."""
    resolved = path.resolve()
    for root in paths.artifact_roots():
        try:
            rel = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return "artifacts/" + str(rel).replace("\\", "/")
    return str(resolved).replace("\\", "/")


def _resolve_output_path(raw: str) -> Path:
    """A receipt-declared output path, which is normally repo-relative (``artifacts/...``) but may be an absolute path in a synthetic/test receipt; both resolve to a real Path."""
    p = Path(raw)
    return p if p.is_absolute() else paths.resolve_repo_relative(raw)


def inventory(physical_scroll: str | None = None) -> dict[str, Any]:
    """List the Hecate receipts registered for `physical_scroll` (or every receipt, if None)."""
    canonical = None
    if physical_scroll:
        try:
            canonical = scroll_ids.resolve(physical_scroll)
        except KeyError as exc:
            return {"schema": SCHEMA, "selected_scroll": physical_scroll, "canonical_scroll": None,
                    "available": False, "entries": [], "why": str(exc)}
    rows = _scan()
    entries = []
    registered = []
    for row in rows:
        receipt = row["receipt"]
        registered.append("%s/%s" % (receipt.get("physical_scroll"), receipt.get("acquisition_id")))
        if canonical is not None and receipt.get("physical_scroll") != canonical:
            continue
        entries.append({
            "physical_scroll": receipt.get("physical_scroll"),
            "acquisition_id": receipt.get("acquisition_id"),
            "run_id": receipt.get("run_id"),
            "provider": receipt.get("provider"),
            "exposure": receipt.get("exposure"),
            "scientific_state": receipt.get("scientific_state"),
            "claim_ceiling": receipt.get("claim_ceiling"),
            "label": _label(receipt.get("exposure"), receipt.get("scientific_state")),
            "receipt_path": _relative(row["receipt_path"]),
        })
    why = None
    if not entries:
        why = ("no Hecate run is registered for %s" % canonical) if canonical else "no Hecate runs are registered"
        if registered:
            why += "; registered: %s" % ", ".join(sorted(set(registered)))
    return {"schema": SCHEMA, "selected_scroll": physical_scroll, "canonical_scroll": canonical,
            "available": bool(entries), "entries": entries, "why": why}


def _orientation_primary(paired: dict[str, Any]) -> tuple[str, str]:
    """Which frozen orientation the receipt's own ``construction_trust`` names as primary."""
    trust = str((paired or {}).get("construction_trust") or "")
    if "FORWARD_ONLY" in trust:
        return "forward", "reverse"
    if "REVERSE_ONLY" in trust:
        return "reverse", "forward"
    return "forward", "reverse"


def _output_ref(images: dict[str, Any], key: str) -> dict[str, Any] | None:
    ref = (images or {}).get(key)
    if not ref or not ref.get("path"):
        return None
    resolved = _resolve_output_path(ref["path"])
    if not resolved.is_file():
        raise HecateLayerRefusal(
            "HECATE_OUTPUT_MISSING: the receipt declares a %s image output (%s) that is not "
            "on disk" % (key, ref["path"]))
    return {"path": ref["path"], "sha256": ref.get("sha256"), "orientation": key}


def _volume_ref(outputs: dict[str, Any], key: str, receipt_dir: Path | None = None,
                disclosures: list | None = None) -> dict[str, Any] | None:
    ref = (outputs or {}).get(key)
    if not ref or not ref.get("path"):
        return None
    resolved = _resolve_output_path(ref["path"])
    if not (resolved / ".zarray").is_file():
        err = ERRATUM.declared_output_not_committed(receipt_dir, ref["path"], ref.get("manifest_sha256")) if receipt_dir else None
        if err:
            if disclosures is not None:
                disclosures.append({"output": key, "path": ref["path"], "manifest_sha256": ref.get("manifest_sha256"),
                                    "status": "NOT_COMMITTED", "erratum": err})
            return None
        raise HecateLayerRefusal(
            "HECATE_OUTPUT_MISSING: the receipt declares %s (%s) but it is not on disk"
            % (key, ref["path"]))
    return {"path": ref["path"], "orientation": "forward" if key == "forward_3d" else "reverse",
            "file_count": len(ref.get("files") or []), "manifest_sha256": ref.get("manifest_sha256")}


def _checkpoint_sha256(provider_id: str | None) -> str | None:
    if not provider_id:
        return None
    from argus.core import science_candidates
    row = next((c for c in science_candidates.inventory()["candidates"] if c["id"] == provider_id), None)
    return ((row or {}).get("artifact") or {}).get("sha256")


def preview(physical_scroll: str, acquisition_id: str) -> dict[str, Any]:
    """The one gate."""
    if not physical_scroll or not str(physical_scroll).strip():
        raise HecateLayerRefusal("physical_scroll is required")
    if not acquisition_id or not str(acquisition_id).strip():
        raise HecateLayerRefusal("acquisition_id is required")
    try:
        canonical = scroll_ids.resolve(physical_scroll)
    except KeyError as exc:
        raise HecateLayerRefusal(str(exc)) from None

    rows = _scan()
    scroll_matches = [r for r in rows if r["receipt"].get("physical_scroll") == canonical]
    row = next((r for r in scroll_matches if r["receipt"].get("acquisition_id") == acquisition_id), None)
    if row is None:
        registered = sorted({"%s/%s" % (r["receipt"].get("physical_scroll"), r["receipt"].get("acquisition_id"))
                             for r in rows})
        if scroll_matches:
            other_ids = sorted({r["receipt"].get("acquisition_id") for r in scroll_matches})
            raise HecateLayerRefusal(
                "HECATE_ACQUISITION_MISMATCH: %s has a registered Hecate run, but not for "
                "acquisition %r; registered acquisition(s) for %s: %s"
                % (canonical, acquisition_id, canonical, ", ".join(other_ids)))
        if registered:
            raise HecateLayerRefusal(
                "HECATE_IDENTITY: no Hecate output for %s -- only %s ha%s a registered run"
                % (canonical, ", ".join(registered), "s" if len(registered) == 1 else "ve"))
        raise HecateLayerRefusal("HECATE_IDENTITY: no Hecate output for %s -- no Hecate run is "
                                 "registered at all" % canonical)

    receipt = row["receipt"]
    outputs = receipt.get("outputs") or {}
    images = outputs.get("images") or {}
    paired = receipt.get("paired_orientation_control") or {}
    primary_key, secondary_key = _orientation_primary(paired)

    primary_image = _output_ref(images, primary_key)
    secondary_image = _output_ref(images, secondary_key)
    if primary_image is None:
        raise HecateLayerRefusal(
            "HECATE_OUTPUT_MISSING: the receipt declares no %s image output" % primary_key)
    volume_disclosures: list = []
    receipt_dir = Path(row["receipt_path"]).parent if row.get("receipt_path") else None
    primary_volume = _volume_ref(outputs, "%s_3d" % primary_key, receipt_dir, volume_disclosures)
    secondary_volume = _volume_ref(outputs, "%s_3d" % secondary_key, receipt_dir, volume_disclosures)

    exposure = receipt.get("exposure")
    scientific_state = receipt.get("scientific_state")
    provider = receipt.get("provider")
    return {
        "schema": SCHEMA,
        "physical_scroll": canonical,
        "acquisition_id": receipt.get("acquisition_id"),
        "run_id": receipt.get("run_id"),
        "segment": receipt.get("segment"),
        "provider": provider,
        "provider_revision": receipt.get("provider_revision"),
        "checkpoint_sha256": _checkpoint_sha256(provider),
        "spacing_um": PROVIDER_SPACING_UM.get(provider or ""),
        "runtime_receipt": receipt.get("runtime_receipt"),
        "receipt_path": _relative(row["receipt_path"]),
        "orientation": {
            "primary": primary_key,
            "secondary": secondary_key,
            "decision_rule": paired.get("decision_rule"),
            "construction_trust": paired.get("construction_trust"),
            "metrics": {primary_key: paired.get(primary_key), secondary_key: paired.get(secondary_key)},
        },
        "image": {"primary": primary_image, "secondary": secondary_image},
        "volume_3d": {"primary": primary_volume, "secondary": secondary_volume},
        "volume_3d_disclosures": volume_disclosures,
        "exposure": exposure,
        "scientific_state": scientific_state,
        "claim_ceiling": receipt.get("claim_ceiling"),
        "eligible_for_automatic_routing": receipt.get("eligible_for_automatic_routing"),
        "label": _label(exposure, scientific_state),
        "not_a_reading": "This is a display of an already-recorded control run, not "
                        "a reading, a qualified detector, or a generalization claim.",
    }


def render_3d_slice(physical_scroll: str, acquisition_id: str, orientation: str, z: int
                    ) -> tuple[bytes, dict[str, Any]]:
    """Render one z-slice of a declared 3-D output as a grayscale PNG."""
    info = preview(physical_scroll, acquisition_id)
    if orientation not in ("primary", "secondary"):
        raise HecateLayerRefusal("orientation must be 'primary' or 'secondary'")
    volume = info["volume_3d"].get(orientation)
    if volume is None:
        raise HecateLayerRefusal(
            "HECATE_NO_3D: no 3-D output is declared for the %s orientation of %s/%s"
            % (orientation, info["physical_scroll"], info["acquisition_id"]))
    resolved = _resolve_output_path(volume["path"])
    try:
        import zarr
        arr = zarr.open(str(resolved), mode="r")
    except Exception as exc:
        raise HecateLayerRefusal("HECATE_3D_UNREADABLE: %s" % type(exc).__name__) from exc
    depth = arr.shape[0]
    if not (0 <= z < depth):
        raise HecateLayerRefusal("HECATE_3D_Z_RANGE: z must be within [0, %d)" % depth)
    import numpy as np
    plane = np.asarray(arr[z])
    if plane.ndim != 2:
        raise HecateLayerRefusal("HECATE_3D_INVALID: expected a 2-D [y, x] slice")
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(plane.astype("uint8"), mode="L").save(buf, format="PNG")
    return buf.getvalue(), {
        "schema": SCHEMA, "physical_scroll": info["physical_scroll"],
        "acquisition_id": info["acquisition_id"], "orientation": orientation,
        "z": z, "depth": int(depth), "label": info["label"],
        "not_a_reading": info["not_a_reading"],
    }
