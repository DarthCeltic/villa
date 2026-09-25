"""An offline, scripted upstream for demonstrations and browser tests."""
from __future__ import annotations

import json
import os

FIXTURE_ENV = "ARGUS_UPSTREAM_FIXTURE"
SOURCE_ID = "argus-fixture-provider"
REPO = "fixture/provider"
BASE = "f1" * 20
HEAD = "f2" * 20
CONTRACT_FILE = "provider/cli.py"
CONTRACT_TOKENS = ["--plan-only", "--input", "--output"]


def active() -> bool:
    return os.environ.get(FIXTURE_ENV, "").strip() in ("1", "true", "yes")


def source_record() -> dict:
    return {
        "source_id": SOURCE_ID, "endpoint": "https://example.invalid/" + REPO, "repo": REPO, "owner": "fixture",
        "classification": "fixture", "capability_ids": ["fixture_demonstration"], "source_type": "git_repository", "watched_channel": "main",
        "admitted_revision": BASE, "license": "MIT", "auto_promotion_allowed": False, "required_tests": [],
        "check_interval_s": 60, "areas_key": "fixture", "providers": [], "fixture": True,
        "contract": {"files": [{"path": CONTRACT_FILE, "tokens": CONTRACT_TOKENS}]},
    }


def with_fixture(cfg: dict) -> dict:
    """Return `cfg` plus the fixture source; the real sources are untouched."""
    out = dict(cfg)
    out["sources"] = list(cfg.get("sources", [])) + [source_record()]
    ar = dict(cfg.get("areas", {}))
    ar["fixture"] = [{"prefix": "provider/", "area": "fixture.cli", "capability": None, "io_contract": True}]
    out["areas"] = ar
    return out


def fixture_runner(argv: list) -> "tuple[int, str, str]":
    """A scripted `gh api`: repo head, compare, licence and one contract file."""
    path = next((a for a in argv[2:] if not a.startswith("-") and "Accept" not in a and "/" in a), "")
    if path.startswith("repos/%s/commits/" % REPO):
        return 0, json.dumps({"sha": HEAD}), ""
    if path.startswith("repos/%s/compare/" % REPO):
        return 0, json.dumps({"status": "ahead", "total_commits": 1, "ahead_by": 1, "behind_by": 0,
                              "files": [{"filename": CONTRACT_FILE}],
                              "commits": [{"sha": "e" * 40, "commit": {"message": "fixture: add an output option\n"}}]}), ""
    if path.startswith("repos/%s/license" % REPO):
        return 0, json.dumps({"license": {"spdx_id": "MIT"}}), ""
    if path.startswith("repos/%s/contents/" % REPO):
        return 0, " ".join("parser.add_argument('%s')" % t for t in CONTRACT_TOKENS), ""
    return 1, "", "fixture upstream has no answer for %s" % path
