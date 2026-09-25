"""A Workspace: the scope a person or an agent is working in, persisted as a manifest."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import re

from argus.core.safe_names import is_plain_file_name
import time
from pathlib import Path

from argus.core.actions import STATE, Refused
from argus.core import paths, storage_catalog

WORKSPACES = STATE / "workspaces"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")

THEME_FIELDS = ("civilization", "palette")


def _path(slug: str) -> Path:
    """A workspace is named by its slug and nothing else: a slug is never a path, so a value that is not one never reaches the filesystem."""
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or not is_plain_file_name(slug):
        raise Refused("BAD_SLUG", "a workspace slug is lowercase letters, digits, dash or underscore, 2-63 characters")
    return WORKSPACES / ("%s.json" % slug)


def list_workspaces() -> list:
    if not WORKSPACES.is_dir():
        return []
    out = []
    for p in sorted(WORKSPACES.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def get(slug: str) -> dict | None:
    try:
        p = _path(slug)
    except Refused:
        return None
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create(slug: str, *, scroll: str, objective: str, actor: str,
           civilization: str | None = None, palette: str | None = None) -> dict:
    if not SLUG.fullmatch(slug or "") or not is_plain_file_name(slug):
        raise Refused("BAD_SLUG",
                      "a workspace slug is lowercase letters, digits, dash or underscore, "
                      "2-63 characters. It becomes a filename and a URL")
    if not scroll:
        raise Refused("NO_SCROLL", "a workspace is scoped to one scroll; say which")
    existing = get(slug)
    if existing:
        if existing.get("scroll") != scroll:
            raise Refused("SLUG_TAKEN",
                          "workspace %r already exists and is scoped to %s"
                          % (slug, existing.get("scroll")))
        return existing
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    rec = {
        "schema": "argus-workspace-v1", "slug": slug, "scroll": scroll,
        "objective": objective or "unstated",
        "created_by": actor,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "appearance": {"civilization": civilization, "palette": palette,
                       "note": ("appearance only. It changes how ARGUS looks and never what "
                                "it holds, scopes, or reports")},
        "scope_note": ("this workspace SELECTS from real holdings. It owns no evidence, "
                       "copies nothing, and deleting it loses a bookmark rather than a "
                       "receipt"),
        "pinned": {"runs": [], "models": [], "candidates": []},
    }
    tmp = _path(slug).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(_path(slug)))
    return rec


def readiness(slug: str, *, fragments_root: Path | None = None) -> dict:
    """What this workspace can actually do right now, from what is on disk."""
    ws = get(slug)
    if not ws:
        raise Refused("NO_WORKSPACE", "no workspace named %r" % slug)
    if fragments_root is None:
        staged = sorted(storage_catalog.local_fragment_paths(
            fallback_root=paths.science_data("fragments")))
    else:
        frags = Path(fragments_root)
        staged = sorted(p.name for p in frags.iterdir() if p.is_dir()) \
            if frags.is_dir() else []
    runs = paths.runs()
    run_dirs = sorted((p.name for p in runs.iterdir() if p.is_dir()), reverse=True)[:12] \
        if runs.is_dir() else []
    blockers = []
    if not staged:
        blockers.append({"blocker": "no fragment staged", "action": "run an ingest plan"})
    from argus.core import canonical_io
    ckpt = Path(canonical_io.CKPT)
    if not ckpt.is_file():
        blockers.append({"blocker": "canonical checkpoint absent",
                         "action": "stage ink_canonical_2um"})
    return {
        "workspace": ws["slug"], "scroll": ws["scroll"], "objective": ws["objective"],
        "staged_fragments": staged,
        "recent_runs": run_dirs,
        "blockers": blockers,
        "next_step": (blockers[0]["action"] if blockers
                      else "nothing on disk blocks this scope"),
        "every_line_is_a_filesystem_fact": True,
    }
