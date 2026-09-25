"""Every row on the /leaderboard board must satisfy its own published schema."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "docs/public/evidence_products/SCHEMA.json").read_text(encoding="utf-8"))
DATA = json.loads(
    (ROOT / "argus/ui/src/data/evidenceProducts.json").read_text(encoding="utf-8"))


def test_schema_declares_a_kind_enum_and_required_fields():
    assert "kind" in SCHEMA["properties"]
    assert len(SCHEMA["properties"]["kind"]["enum"]) >= 1
    assert set(SCHEMA["required"]) <= set(SCHEMA["properties"])


def test_every_row_has_every_required_field():
    required = set(SCHEMA["required"])
    for row in DATA:
        missing = required - row.keys()
        assert not missing, "%r is missing %r" % (row.get("name", "<unnamed>"), missing)


def test_every_row_declares_no_field_outside_the_schema():
    allowed = set(SCHEMA["properties"])
    for row in DATA:
        extra = set(row) - allowed
        assert not extra, "%r declares undeclared field(s) %r" % (row.get("name"), extra)


def test_every_kind_is_one_of_the_declared_enum_values():
    enum = set(SCHEMA["properties"]["kind"]["enum"])
    for row in DATA:
        assert row["kind"] in enum, "%r has kind %r, not one of %r" % (
            row.get("name"), row.get("kind"), sorted(enum))


def test_every_evidence_url_looks_like_something_reachable_not_a_bare_claim():
    for row in DATA:
        url = row["evidence_url"]
        assert url and (
            "/" in url or "://" in url
        ), "%r's evidence_url %r is not a reachable path or URL" % (row.get("name"), url)


def test_a_scientific_result_row_would_need_the_real_gate_named_in_its_schema():
    """Not a runtime check of the gate itself -- a reminder, enforced at review time, that SCIENTIFIC RESULT is not a free label."""
    for row in DATA:
        if row["kind"] == "SCIENTIFIC RESULT":
            haystack = (row["detail"] + " " + row["evidence_url"]).lower()
            assert "gate" in haystack or "scroll_generalization" in haystack, (
                "%r claims SCIENTIFIC RESULT without naming the gate it passed" % row.get("name"))
