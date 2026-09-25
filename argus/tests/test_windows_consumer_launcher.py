from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_double_click_launchers_are_repo_relative() -> None:
    start = (ROOT / "Start-ARGUS.cmd").read_text(encoding="utf-8")
    stop = (ROOT / "Stop-ARGUS.cmd").read_text(encoding="utf-8")

    assert '"%~dp0scripts\\Start-ARGUS.ps1"' in start
    assert '"%~dp0scripts\\Stop-ARGUS.ps1"' in stop
    assert re.search(r"[A-Za-z]:\\", start + stop) is None


def test_start_launcher_is_bounded_and_uses_the_governed_stack() -> None:
    script = (ROOT / "scripts" / "Start-ARGUS.ps1").read_text(encoding="utf-8")

    assert '[ValidateRange(30, 1800)]' in script
    assert '[ValidateRange(15, 300)]' in script
    assert '@("compose", "up", "-d")' in script
    assert '"--build"' in script
    assert '$UiUrl = "http://127.0.0.1:8792"' in script
    assert '$UiHealthUrl = "$UiUrl/health"' in script
    assert "http://127.0.0.1:18787/api/ready" in script
    assert "http://127.0.0.1:18787/api/health" not in script
    assert "Start-Process $UiUrl" in script
    assert "Start-Sleep -Seconds 2" in script
    assert "Invoke-Expression" not in script
    assert "docker compose down -v" not in script


def test_stop_launcher_preserves_named_state() -> None:
    script = (ROOT / "scripts" / "Stop-ARGUS.ps1").read_text(encoding="utf-8")

    assert ".Source compose down" in script
    assert "compose down -v" not in script
    assert "were preserved" in script
