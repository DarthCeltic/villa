"""Read-only upstream freshness for the ARGUS operator surface."""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
from pathlib import Path

from argus.core import paths


def _git(root: Path, *args: str) -> tuple[int | None, str, str]:
    try:
        p = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", str(exc)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _iso_from_timestamp(path: Path) -> str | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        return None


def _latest_differential() -> tuple[Path | None, dict | None]:
    candidates = []
    for root in paths.artifact_read_roots():
        p = root / "upstream_differential" / "UPSTREAM_DIFFERENTIAL.json"
        if p.is_file():
            candidates.append(p)
    if not candidates:
        return None, None
    p = max(candidates, key=lambda x: x.stat().st_mtime)
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return p, None


def _classify_dirty(lines: list[str]) -> tuple[list[str], list[str]]:
    """Separate install side-effects from files that can change Villa execution."""
    generated, source = [], []
    for line in lines:
        path = line[3:].strip() if len(line) >= 3 else line.strip()
        is_generated = (".egg-info/" in path.replace("\\", "/")
                        or "/__pycache__/" in ("/" + path.replace("\\", "/"))
                        or path.endswith("/agreement.txt")
                        or path == "agreement.txt")
        (generated if is_generated else source).append(line)
    return generated, source


def read() -> dict:
    """Return measured local refs and the last recorded differential, without network I/O."""
    lock_path = paths.repo("argus", "upstream.lock.json")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "schema": "argus-upstream-status-v1",
            "read_only": True,
            "state": "UNKNOWN",
            "checked_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "blocker": "the upstream lock could not be read: %s" % exc,
        }

    declared = ((lock.get("upstream") or {}).get("commit") or "").strip()
    candidate_cfg = lock.get("candidate") or {}
    candidate_ref = str(candidate_cfg.get("ref") or "origin/main").strip()
    checkout = paths.upstream("villa")
    head_rc, head, head_err = _git(checkout, "rev-parse", "HEAD") if checkout.is_dir() else (
        None,
        "",
        "pinned checkout is absent",
    )
    main_rc, main, main_err = _git(checkout, "rev-parse", "origin/main") if checkout.is_dir() else (
        None,
        "",
        "pinned checkout is absent",
    )
    status_rc, porcelain, status_err = (
        _git(checkout, "status", "--porcelain", "--untracked-files=all")
        if checkout.is_dir()
        else (None, "", "pinned checkout is absent")
    )
    subject_rc, subject, subject_err = (
        _git(checkout, "show", "-s", "--format=%cI%x09%s", "origin/main")
        if main
        else (None, "", "remote-tracking ref is absent")
    )

    diff_path, diff = _latest_differential()
    snap = (diff or {}).get("snapshot_freshness") or {}
    ref = (diff or {}).get("git_ref_freshness") or {}
    latest_remote = main or ref.get("origin_main_sha")
    candidate_matches_remote = bool(
        candidate_cfg.get("commit") and latest_remote
        and str(candidate_cfg.get("commit")).strip() == latest_remote
    )
    dirty = [line for line in porcelain.splitlines() if line.strip()]
    dirty_generated, dirty_source = _classify_dirty(dirty)
    pin_matches = bool(declared and head and declared == head)
    remote_known = bool(latest_remote)
    stale_snapshot = bool(diff_path and diff and not (diff.get("refresh_ran_this_session") or {}).get("ran"))

    if not checkout.is_dir() or not pin_matches:
        state = "BLOCKED"
    elif not remote_known:
        state = "UNKNOWN"
    elif stale_snapshot:
        state = "STALE_SNAPSHOT"
    elif dirty_source:
        state = "DIRTY_CHECKOUT"
    else:
        state = "PINNED_WITH_DRIFT"

    return {
        "schema": "argus-upstream-status-v1",
        "read_only": True,
        "state": state,
        "checked_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pin": {
            "declared": declared or None,
            "checkout_head": head or None,
            "matches_checkout": pin_matches,
            "lock_path": str(lock_path),
        },
        "checkout": {
            "path": str(checkout),
            "present": checkout.is_dir(),
            "dirty": bool(dirty),
            "dirty_paths": dirty[:100],
            "dirty_paths_truncated": len(dirty) > 100,
            "dirty_generated_paths": dirty_generated[:100],
            "dirty_source_paths": dirty_source[:100],
            "source_is_clean": not dirty_source,
            "git_error": head_err or status_err or None,
        },
        "remote_tracking": {
            "ref": "origin/main",
            "sha": latest_remote or None,
            "subject": subject or ref.get("origin_main_subject"),
            "commit_date": (subject.split("\t", 1)[0] if "\t" in subject else None)
            or ref.get("origin_main_committed"),
            "known_locally": remote_known,
            "git_error": main_err or subject_err or None,
        },
        "candidate": {
            "ref": candidate_ref,
            "commit": candidate_cfg.get("commit") or None,
            "matches_remote": candidate_matches_remote,
            "materialization": candidate_cfg.get("materialization") or "not declared",
            "review_status": candidate_cfg.get("review_status") or "NOT_REVIEWED",
            "capabilities": candidate_cfg.get("capabilities") or [],
        },
        "differential": {
            "receipt": str(diff_path) if diff_path else None,
            "receipt_written_utc": _iso_from_timestamp(diff_path) if diff_path else None,
            "refresh_ran": (diff.get("refresh_ran_this_session") or {}).get("ran")
            if diff
            else None,
            "refs_last_fetched_utc": ref.get("refs_last_fetched_utc") if diff else None,
            "commits_main_ahead_of_pin": snap.get("commits_main_ahead_of_pin") if diff else None,
            "files_changed_since_pin": (diff.get("capability_diff") or {}).get("files_changed")
            if diff
            else None,
            "basis": (snap.get("count_basis") if diff else None)
            or "last differential receipt; no live fetch is performed here",
        },
        "actions": {
            "automatic_fetch": False,
            "automatic_pin_move": False,
            "next": (
                "refresh the read-only upstream differential when network access is available"
                if stale_snapshot or not remote_known
                else "review changed upstream capabilities before adopting or wiring them"
            ),
        },
    }
