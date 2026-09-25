"""Everything ARGUS holds, grouped by the scroll it belongs to: public stub.

The operator's local label families, result directories and acquisition receipts are not part of
the public release. This stub keeps the names and response shapes the service, the preflight
planner, the shelf and the status board use, and reports an empty holding: nothing is read from
disk and nothing is attributed to any scroll.
"""
from __future__ import annotations

import re

PUBLIC_NOTE = "the local holdings index is not part of the public release"

#: No label families are indexed in the public build.
LABEL_ROOTS: dict = {}

#: No scroll-id aliases are shipped; a segment prefix is canonicalised by rule (upper-case suffix).
CANONICAL: dict = {}

_SEG = re.compile(r"^(pherc[0-9a-z]+)-(.+)$", re.I)


def segment_asset_roots():
    """No material roots are indexed in the public build."""
    return ()


def scroll_of(segment: str, family: str | None = None) -> str:
    """The scroll a segment belongs to, or UNATTRIBUTED."""
    m = _SEG.match(segment)
    if m:
        raw = m.group(1).lower()
        return raw[:5] + raw[5:].upper() if raw[5:].isalnum() else raw
    return "UNATTRIBUTED"


def physical_segment_of(dirname: str, family: str) -> str:
    """The PHYSICAL segment a label directory describes, stripped of its scroll prefix."""
    m = _SEG.match(dirname)
    return m.group(2) if m else dirname


def pretty(scroll: str) -> str:
    """`PHercFixture1` -> `PHerc Fixture1`."""
    m = re.match(r"^PHerc(\w+)$", scroll)
    return "PHerc %s" % m.group(1) if m else scroll


def build() -> dict:
    """An empty index, in the shape the service returns."""
    return {"scrolls": [],
            "roots_read": {"labels": {}, "assets": {}, "ct_cache": []},
            "nothing_moved": "this is a VIEW over the existing layout. No file is relocated.",
            "note": PUBLIC_NOTE}


def _missing(e: dict) -> list:
    """What this scroll still needs, in plain words."""
    if not e.get("counts", {}).get("physical_segments"):
        return [{"stage": "labels", "text": "nothing held for this scroll yet"}]
    return []


def summary() -> dict:
    """A one-line-per-scroll shape, for a menu (empty in the public build)."""
    idx = build()
    return {
      "scrolls": [],
      "totals": {"physical_scrolls": 0, "physical_segments": 0, "label_representations": 0,
                 "segments_with_labels": 0},
      "nothing_moved": idx["nothing_moved"],
      "note": PUBLIC_NOTE,
    }
