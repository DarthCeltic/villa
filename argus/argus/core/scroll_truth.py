"""One read-only truth payload for the currently selected physical scroll."""
from __future__ import annotations

from argus.core import local_discovery, scroll_status


SCHEMA = "argus-scroll-truth-v1"


def for_scroll(scroll: str, *, inventory: dict | None = None) -> dict:
    route = scroll_status.status(scroll)
    canonical = route.get("scroll")
    inventory = inventory if inventory is not None else local_discovery.scan()
    assets = [
        row for row in inventory.get("assets", [])
        if canonical and row.get("physical_scroll") == canonical
    ]
    counts = {
        "assets": len(assets),
        "renders": sum(1 for row in assets if row.get("kind") == "RENDER"),
        "meshes": sum(1 for row in assets if row.get("kind") == "MESH"),
        "science_records": sum(1 for row in assets if row.get("kind") == "SCIENCE_RECORD"),
        "verified_renders": sum(
            1 for row in assets
            if row.get("kind") == "RENDER" and row.get("status") == "LOCAL_VERIFIED"
        ),
        "viewable_renders": sum(1 for row in assets if row.get("kind") == "RENDER" and row.get("viewable")),
    }
    return {
        "schema": SCHEMA,
        "read_only": True,
        "physical_scroll": canonical,
        "requested": scroll,
        "route": route,
        "local_material": {
            "scanned_utc": inventory.get("scanned_utc"),
            "assets": assets,
            "counts": counts,
            "roots": inventory.get("roots", []),
            "raw_arrays_skipped": bool(inventory.get("limits", {}).get("raw_arrays_skipped")),
            "next": inventory.get("next"),
        },
        "source_precedence": [
            "route: argus.core.scroll_status for the selected physical-scroll journey",
            "science: hash-verified imported closeout summary referenced by route.science_artifacts",
            "local_material: bounded read-only scan of declared artifact, user-data and run roots",
        ],
        "claim_boundary": (
            "Discovery is not import, inference or scientific qualification. Identity-matched "
            "material is visible here; only a hash-verified governed attachment can promote a "
            "private science render into a served evidence view."
        ),
    }
