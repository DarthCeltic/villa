"""Discover upstream changes, diff them semantically, and check adapter contracts."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Callable

from argus.core import update_conveyor as UC

SOURCES_PATH = Path(__file__).resolve().parents[2] / "config" / "upstream_sources.json"

Runner = Callable[[list], "tuple[int, str, str]"]
HttpGet = Callable[[str], "tuple[int, str]"]
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def default_runner(argv: list, timeout: int = 90) -> "tuple[int, str, str]":
    from argus.core import upstream_fixture as FX
    if FX.active():
        return FX.fixture_runner(argv)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "executable not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def default_http_get(url: str) -> "tuple[int, str]":
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "argus-update-watch"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, repr(e)[:200]


def load_config(path: Path | None = None) -> dict:
    cfg = json.loads((path or SOURCES_PATH).read_text(encoding="utf-8"))
    from argus.core import upstream_fixture as FX
    return FX.with_fixture(cfg) if FX.active() and path is None else cfg


def source_records(cfg: dict | None = None) -> list:
    return list((cfg or load_config())["sources"])


def to_source(rec: dict) -> UC.Source:
    return UC.Source(
        source_id=rec["source_id"], endpoint=rec["endpoint"], owner=rec["owner"],
        classification=rec["classification"], capability_ids=tuple(rec["capability_ids"]),
        source_type=rec["source_type"], watched_channel=rec["watched_channel"],
        admitted_revision=rec.get("admitted_revision"), license=rec.get("license"),
        auto_promotion_allowed=bool(rec.get("auto_promotion_allowed")),
        required_tests=tuple(rec.get("required_tests", ())), check_interval_s=int(rec.get("check_interval_s", 3600)))


def is_immutable(rev: str | None) -> bool:
    return isinstance(rev, str) and bool(rev) and bool(SHA_RE.fullmatch(rev) or DIGEST_RE.fullmatch(rev))


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _watch_state(rc: int, err: str) -> str:
    low = (err or "").lower()
    if "rate limit" in low or "429" in low or "secondary rate" in low:
        return "RATE_LIMITED"
    if rc in (124, 127) or "could not resolve" in low or "no such host" in low or "connection" in low or "timed out" in low:
        return "OFFLINE"
    return "ERROR"


def _gh(runner: Runner, path: str, *extra: str) -> "tuple[int, Any, str]":
    rc, out, err = runner(["gh", "api", path, *extra])
    if rc != 0:
        return rc, None, err or out
    try:
        return 0, json.loads(out), ""
    except ValueError:
        return 0, out, ""


def observe_pr(repo: str, number: int, runner: Runner = default_runner) -> dict:
    """One pull request as upstream says it is now."""
    rc, doc, err = _gh(runner, "repos/%s/pulls/%d" % (repo, number))
    if rc or not isinstance(doc, dict):
        return {"number": number, "ok": False, "watch_state": _watch_state(rc, err), "error": (err or "")[:160]}
    return {"number": number, "ok": True, "state": doc.get("state"), "merged": bool(doc.get("merged")),
            "base": (doc.get("base") or {}).get("ref"), "merge_commit": doc.get("merge_commit_sha") if doc.get("merged") else None,
            "head": (doc.get("head") or {}).get("sha"), "title": (doc.get("title") or "")[:160],
            "merged_at": doc.get("merged_at")}


def on_channel(rec: dict, revision: str, runner: Runner = default_runner) -> "tuple[bool | None, str]":
    """Is `revision` reachable from the channel this source watches?"""
    rc, doc, err = _gh(runner, "repos/%s/compare/%s...%s" % (rec["repo"], revision, rec["watched_channel"]))
    if rc or not isinstance(doc, dict):
        return None, (err or "compare failed")[:160]
    status = doc.get("status")
    return status in ("ahead", "identical"), str(status)


def observe(rec: dict, runner: Runner = default_runner, http_get: HttpGet = default_http_get,
            now: str | None = None) -> UC.Observation:
    """One cheap check of one source."""
    sid, stype, repo, channel = rec["source_id"], rec["source_type"], rec.get("repo"), rec["watched_channel"]
    when = now or _now()
    if stype == "git_repository":
        rc, doc, err = _gh(runner, "repos/%s/commits/%s" % (repo, channel))
        if rc:
            return UC.Observation(sid, _watch_state(rc, err), when, error=(err or "")[:200])
        return UC.Observation(sid, "OK", when, observed_revision=doc.get("sha"), fetched=False)
    if stype == "release_artifact":
        rc, doc, err = _gh(runner, "repos/%s/releases/tags/%s" % (repo, channel))
        if rc:
            return UC.Observation(sid, _watch_state(rc, err), when, error=(err or "")[:200])
        m = re.search(r"commit\s+([0-9a-f]{40})", doc.get("body") or "")
        rev = m.group(1) if m else None
        if not rev:
            return UC.Observation(sid, "ERROR", when, error="release body names no commit; a release name is a pointer, not a version")
        return UC.Observation(sid, "OK", when, observed_revision=rev, etag=doc.get("published_at"))
    if stype == "container_image":
        rc, doc, err = _gh(runner, "orgs/%s/packages/container/%s/versions" % (
            rec["owner"], rec["endpoint"].split("ghcr.io/%s/" % rec["owner"].lower())[-1].replace("/", "%2F")))
        if rc:
            return UC.Observation(sid, _watch_state(rc, err), when, error=(err or "")[:200])
        for v in doc if isinstance(doc, list) else []:
            tags = (v.get("metadata", {}).get("container", {}) or {}).get("tags", [])
            if channel in tags and DIGEST_RE.fullmatch(v.get("name", "")):
                return UC.Observation(sid, "OK", when, observed_revision=v["name"], etag=v.get("updated_at"))
        return UC.Observation(sid, "ERROR", when, error="no digest found for tag %r; a tag alone is not a pin" % channel)
    if stype == "model_repository":
        code, body = http_get("https://huggingface.co/api/models/%s" % repo)
        if code == 429:
            return UC.Observation(sid, "RATE_LIMITED", when)
        if code == 0:
            return UC.Observation(sid, "OFFLINE", when, error=body[:200])
        if code != 200:
            return UC.Observation(sid, "ERROR", when, error="HTTP %d" % code)
        try:
            doc = json.loads(body)
        except ValueError:
            return UC.Observation(sid, "ERROR", when, error="unparseable model metadata")
        return UC.Observation(sid, "OK", when, observed_revision=doc.get("sha"), etag=doc.get("lastModified"))
    return UC.Observation(sid, "NOT_CHECKED", when)


def license_at(rec: dict, revision: str, runner: Runner = default_runner, http_get: HttpGet = default_http_get) -> "str | None":
    """The licence declared at a revision, or None when it cannot be determined (never a guess)."""
    repo, stype = rec.get("repo"), rec["source_type"]
    if stype in ("git_repository", "release_artifact", "container_image"):
        rc, doc, _ = _gh(runner, "repos/%s/license?ref=%s" % (repo, revision))
        if rc == 0 and isinstance(doc, dict):
            return ((doc.get("license") or {}).get("spdx_id"))
        return None
    if stype == "model_repository":
        code, body = http_get("https://huggingface.co/api/models/%s/revision/%s" % (repo, revision))
        if code == 200:
            try:
                return ((json.loads(body).get("cardData") or {}).get("license"))
            except ValueError:
                return None
    return None


def classify_paths(paths: list, areas: list, dependency_files=(), licence_files=()) -> dict:
    """Bucket changed paths by area."""
    by_area: dict = {}
    io_paths, deps, lic = [], [], []
    unmatched = []
    for p in paths:
        base = p.split("/")[-1]
        if p in licence_files or base in ("LICENSE", "LICENSE.md", "COPYING"):
            lic.append(p)
        if p in dependency_files or base in ("pyproject.toml", "uv.lock", "requirements.txt"):
            deps.append(p)
        hit = next((a for a in areas if p.startswith(a["prefix"])), None)
        if not hit:
            unmatched.append(p)
            continue
        by_area.setdefault(hit["area"], []).append(p)
        if hit.get("io_contract"):
            io_paths.append(p)
    return {"by_area": {k: len(v) for k, v in sorted(by_area.items())},
            "io_contract_paths": sorted(io_paths), "dependency_paths": sorted(deps),
            "licence_paths": sorted(lic), "unmatched": len(unmatched)}


def semantic_diff(rec: dict, from_rev: str, to_rev: str, cfg: dict, runner: Runner = default_runner) -> dict:
    """What changed between two revisions of a git source, classified by where it lands."""
    repo = rec.get("repo")
    if rec["source_type"] != "git_repository" or not is_immutable(from_rev) or not is_immutable(to_rev):
        return {"available": False, "why": "semantic diff needs a git repository and two immutable revisions"}
    rc, doc, err = _gh(runner, "repos/%s/compare/%s...%s" % (repo, from_rev, to_rev))
    if rc or not isinstance(doc, dict):
        return {"available": False, "why": "compare API failed: %s" % ((err or "")[:160])}
    files = doc.get("files", []) or []
    truncated = len(files) >= 300
    areas_cfg = cfg.get("areas", {}).get(rec.get("areas_key") or "", [])
    cls = classify_paths([f["filename"] for f in files], areas_cfg,
                         cfg.get("areas", {}).get("dependency_files", ()) if areas_cfg else (),
                         cfg.get("areas", {}).get("licence_files", ()) if areas_cfg else ())
    secret_like = sorted(f["filename"] for f in files if re.search(r"(^|/)(\.env|id_r[s]a|.*\.pem|.*\.p12)$", f["filename"]))
    caps = sorted({a["capability"] for a in areas_cfg if a["capability"] and any(
        f["filename"].startswith(a["prefix"]) for f in files)})
    risk = "HIGH" if (cls["licence_paths"] or cls["io_contract_paths"] or cls["dependency_paths"] or secret_like) else (
        "REVIEW" if cls["by_area"] and set(cls["by_area"]) != {"documentation"} else "LOW")
    return {"available": True, "from": from_rev, "to": to_rev, "ahead_by": doc.get("ahead_by"),
            "behind_by": doc.get("behind_by"), "commits": doc.get("total_commits"),
            "files_changed": len(files), "truncated": truncated,
            "notable_commits": [(c["sha"][:9], (c["commit"]["message"] or "").split("\n")[0][:100])
                                for c in (doc.get("commits") or [])[-8:]],
            "impacted_capabilities": caps, "io_contract_touched": bool(cls["io_contract_paths"]),
            "licence_touched": bool(cls["licence_paths"]), "dependencies_touched": bool(cls["dependency_paths"]),
            "secret_like_files": secret_like, **cls, "risk": risk,
            "reading": "a triage signal about WHERE the change lands; it does not prove compatibility or incompatibility"}


def fetch_file(repo: str, path: str, revision: str, runner: Runner = default_runner) -> "str | None":
    rc, out, _ = runner(["gh", "api", "-H", "Accept: application/vnd.github.raw", "repos/%s/contents/%s?ref=%s" % (repo, path, revision)])
    return out if rc == 0 else None


def check_tokens(text: "str | None", tokens: list) -> dict:
    if text is None:
        return {t: False for t in tokens}
    return {t: (t in text) for t in tokens}


def contract_check(rec: dict, revision: str, runner: Runner = default_runner) -> dict:
    """The adapter contract: every file/token ARGUS's adapter relies on is still present at `revision`."""
    files_cfg = (rec.get("contract") or {}).get("files") or []
    if not files_cfg:
        return {"defined": False, "why": "no contract files declared for this source",
                "gates": {}, "files": []}
    out_files, all_present, sabotage_ok = [], True, True
    for f in files_cfg:
        text = fetch_file(rec["repo"], f["path"], revision, runner)
        present = check_tokens(text, f["tokens"])
        sha = hashlib.sha256((text or "").encode("utf-8")).hexdigest() if text is not None else None
        out_files.append({"path": f["path"], "fetched": text is not None, "bytes": len(text or ""),
                          "sha256": sha, "tokens": present})
        all_present = all_present and text is not None and all(present.values())
        if text is not None:
            for t in f["tokens"]:
                broken = check_tokens(text.replace(t, ""), f["tokens"]) if t in text else None
                if broken is None or broken.get(t) is not False or sum(1 for v in broken.values() if not v) < 1:
                    sabotage_ok = False
    return {"defined": True, "files": out_files,
            "gates": {"source_identity": all(x["fetched"] for x in out_files),
                      "api_compatible": all_present,
                      "schemas_parse": all(x["fetched"] for x in out_files),
                      "sabotage_still_fails": sabotage_ok}}
