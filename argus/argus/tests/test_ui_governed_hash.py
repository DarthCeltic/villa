"""The UI must approve with the plan's OWN hash."""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "src"
BAD = re.compile(r"approved_plan_sha256\s*:\s*[A-Za-z_][\w.]*\.plan_hash")


def test_no_ui_file_approves_with_the_envelope_hash():
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.ts*") if BAD.search(p.read_text(encoding="utf-8"))]
    assert not offenders, "these approve with plan_hash instead of approvedHash(plan): %s" % offenders


def test_the_helper_prefers_the_plans_own_hash():
    text = (SRC / "lib" / "governed.ts").read_text(encoding="utf-8")
    assert "export function approvedHash" in text and "plan_sha256" in text


def test_every_governed_write_in_the_update_screens_goes_through_the_plan_approve_component():
    for name in ("UpdatesPanel.tsx",):
        text = (SRC / "components" / name).read_text(encoding="utf-8")
        assert "runGoverned(" not in text and "fetch(" not in text, name
        assert "PlanApprove" in text


def test_every_update_plan_approve_action_accepts_the_approval_hash_at_the_transport():
    from argus.service import bff

    text = (SRC / "components" / "UpdatesPanel.tsx").read_text(encoding="utf-8")
    actions = set(re.findall(r'action="(provider\.update\.[^"]+)"', text))
    assert actions
    missing = sorted(name for name in actions if "approved_plan_sha256" not in bff.OPERATIONS[name]["params"])
    assert not missing, "PlanApprove sends approved_plan_sha256 but the transport refuses it for: %s" % missing
