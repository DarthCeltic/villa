"""The Install tiers screen reads one route, shows repair as a plan, and cannot change anything."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui" / "src"
PANEL = (UI / "components" / "InstallTiersPanel.tsx").read_text(encoding="utf-8")
SYSTEM = (UI / "screens" / "SystemScreen.tsx").read_text(encoding="utf-8")


def test_the_panel_reads_the_single_status_route():
    assert '"/api/install_tiers"' in PANEL
    assert len(re.findall(r"usePoll<", PANEL)) == 1


def test_the_panel_has_no_write_path():
    for forbidden in ("PlanApprove", "governed", "postJson", "method: \"POST\"", "fetch("):
        assert forbidden not in PANEL
    assert "Read-only check" in PANEL and "without asking a standard user to copy commands" in PANEL


def test_the_standard_user_screen_never_renders_raw_install_commands():
    assert "{s.command}" not in PANEL
    assert "Exact operator instructions are retained in the signed plan receipt" in PANEL


def test_an_unknown_probe_is_never_drawn_as_ready():
    assert 'state === "READY") return "var(--status-certified)"' in PANEL
    assert 'if (state === "UNKNOWN") return "var(--ink-dim)"' in PANEL


def test_a_tag_only_pin_is_labelled_as_not_for_scientific_use():
    assert "TAG_ONLY_DIGEST_NOT_RESOLVED" in PANEL and "not usable for scientific runs" in PANEL


def test_the_tab_is_registered_on_the_system_screen():
    assert 'id: "install"' in SYSTEM and "InstallTiersPanel" in SYSTEM
    assert 'tab === "install"' in SYSTEM
