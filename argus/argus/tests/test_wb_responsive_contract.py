"""The Workbench's width classes are one contract: the code (lib/wbLayout.ts) and the stylesheet (theme/wbresponsive.css) must agree, and no rule may make a panel decision from the browser's width again."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui" / "src"


def _consts() -> tuple[int, int]:
    src = (UI / "lib" / "wbLayout.ts").read_text(encoding="utf-8")
    return int(re.search(r"export const WIDE_MIN = (\d+);", src).group(1)), int(re.search(r"export const MEDIUM_MIN = (\d+);", src).group(1))


def test_the_stylesheet_uses_the_same_three_thresholds_as_the_code():
    wide, medium = _consts()
    css = (UI / "theme" / "wbresponsive.css").read_text(encoding="utf-8")
    assert ".bench-work { container: wb / inline-size; }" in css
    assert "@container wb (min-width: %dpx)" % wide in css
    assert "@container wb (min-width: %dpx) and (max-width: %dpx)" % (medium, wide - 1) in css
    assert "@container wb (max-width: %dpx)" % (medium - 1) in css


def test_no_workbench_panel_rule_decides_from_the_browsers_width():
    """The panel widths, the overlay and the sheet are container rules."""
    offenders = []
    for name in ("workbench.css", "shell.css", "bench.css"):
        css = (UI / "theme" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"@media[^{]*\{", css):
            depth, i = 1, m.end()
            while i < len(css) and depth:
                depth += {"{": 1, "}": -1}.get(css[i], 0)
                i += 1
            block = css[m.end():i]
            if re.search(r"\.wb-(body|drawer|inspector|panel)\b", block):
                offenders.append("%s: %s" % (name, m.group(0).strip()))
    assert not offenders, offenders
