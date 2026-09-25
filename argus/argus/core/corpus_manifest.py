"""Label-corpus manifest: not part of the public ARGUS release.

The operator's build binds each held label set to its scroll, segment, acquisition, depth handling and
proven label authority. Which label sets exist, their authority and their preprocessing are private
research holdings and are not shipped. This module keeps the same interface with an empty inventory, so
every surface that reads it reports the truth for this build: no label corpus is present, and no scroll
is proven to carry human-only ground truth here.
"""
from __future__ import annotations

CONTRACT = "argus-corpus-manifest-v1"

HUMAN_ONLY = "HUMAN_ONLY"
DERIVED_MIXED = "DERIVED_MIXED"
MACHINE_GATED = "MACHINE_GATED"
UNKNOWN = "UNKNOWN"

#: Empty in the public release: no label set, cache layout or depth handling ships with it.
CACHE_DIR: dict = {}
LABEL_AUTHORITY: dict = {}
DEPTH_TRANSFORM: dict = {}
SCROLL_LABEL_INVENTORY: dict = {}
REQUIRED_CLEAN_SCROLLS = 3

WHY = "no label corpus ships with the public release; bring your own labels and record their authority"


class ManifestRefusal(RuntimeError):
    """A binding that cannot be proven is refused, never defaulted."""


def bind(scroll: str, segment: str) -> dict:
    raise ManifestRefusal("no label authority recorded for %r: %s" % (scroll, WHY))


def bind_all(scrolls) -> dict:
    return {"rows": [], "problems": [], "why": WHY}


def qualification_status() -> dict:
    return {
      "status": "BLOCKED_INSUFFICIENT_PROVEN_HUMAN_GROUND_TRUTH_SCROLLS",
      "proven_clean_scrolls": 0,
      "proven_clean_names": [],
      "required_clean_scrolls": REQUIRED_CLEAN_SCROLLS,
      "by_authority": {},
      "scrolls_examined": 0,
      "any_byte_separable_human_subset": False,
      "precise_wording": "No label corpus is present in this build, so no scroll is proven to carry "
                         "human-only ground truth here.",
      "why": WHY,
    }


def selftest() -> bool:
    q = qualification_status()
    return not SCROLL_LABEL_INVENTORY and q["proven_clean_scrolls"] == 0


def main(argv=None) -> int:
    import json
    print(json.dumps(qualification_status(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
