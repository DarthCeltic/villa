"""Upstream sources: what exists out there, and what we actually hold."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from argus.core import paths

UA = {"User-Agent": "argus/1.0 (+https://github.com/Cinder-Covenant/ARGUS)"}
CACHE = paths.artifact_write_root() / "_argus_cache" / "sources.json"
CACHE_TTL_S = 900.0


@dataclass
class Source:
    id: str
    name: str
    kind: str
    url: str
    authoritative_for: str
    probe: str | None = None
    local_paths: list[str] = field(default_factory=list)
    notes: str = ""
    caution: str | None = None
    assets: list[dict] = field(default_factory=list)
    acquisition_policy: str | None = None


SOURCES: list[Source] = [
    Source(
        id="open-data-s3",
        name="Vesuvius Challenge open data (S3)",
        kind="object-store",
        url="https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/",
        probe=("https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/"
               "?list-type=2&delimiter=/&max-keys=1"),
        authoritative_for=("volumes, published segments and per-segment surface volumes; "
                           "the frame every ARGUS geometry run is checked against"),
        local_paths=[],
        notes=("Anonymous REST. The masked volumes are stored uncompressed, which is what "
               "lets the chunk-identity gate read single voxels by range request instead "
               "of downloading gigabytes."),
    ),
    Source(
        id="villa-dl",
        name="Villa download host (dl.ash2txt.org)",
        kind="http",
        url="https://dl.ash2txt.org/",
        probe="https://dl.ash2txt.org/",
        authoritative_for=("the full scroll archive as the Villa publishes it, including "
                           "material not mirrored into the S3 bucket"),
        local_paths=[_argus_public_path('cache', '')],
        caution=("Large. Plan the bytes before any pull; this registry never starts a bulk "
                 "download."),
    ),
    Source(
        id="huggingface",
        name="Hugging Face",
        kind="dataset-hub",
        url="https://huggingface.co/",
        probe="https://huggingface.co/api/whoami-v2",
        authoritative_for="published checkpoints and community datasets",
        local_paths=["~/.cache/huggingface"],
        notes=("Probe answers without a token and reports whether one is configured. A "
               "token is never read or printed by this module."),
    ),
    Source(
        id="upstream-tracker",
        name="Upstream issue tracker",
        kind="tracker",
        url="https://github.com/ScrollPrize/villa",
        probe="https://api.github.com/repos/ScrollPrize/villa",
        authoritative_for=("known defects in the reference tooling; the source of the "
                           "silent-failure modes ARGUS's gates exist to catch"),
        local_paths=[],
        notes=("Everything archived from here is marked UNVERIFIED_UPSTREAM_CLAIM until we "
               "reproduce it locally."),
    ),
    Source(
        id="challenge-site",
        name="Vesuvius Challenge site",
        kind="site",
        url="https://scrollprize.org/",
        probe="https://scrollprize.org/",
        authoritative_for="prize rules, submission requirements and data policy",
        local_paths=[],
    ),
]


def _expand(p: str) -> Path:
    """Resolve a declared local path across every root, not just the one beside this file."""
    q = Path(os.path.expanduser(p))
    if q.is_absolute():
        return q
    return paths.resolve_repo_relative(q.as_posix())


def local_holdings(src: Source) -> dict:
    """What is on this disk from this source."""
    out = []
    for p in src.local_paths:
        q = _expand(p)
        if not q.exists():
            out.append({"path": str(q), "present": False})
            continue
        try:
            entries = sum(1 for _ in q.iterdir()) if q.is_dir() else 1
        except OSError as e:
            out.append({"path": str(q), "present": True, "error": str(e)[:120]})
            continue
        out.append({
            "path": str(q), "present": True, "entries": entries,
            "is_dir": q.is_dir(),
            "modified": q.stat().st_mtime,
        })
    return {"paths": out, "any_present": any(x.get("present") for x in out)}


def probe(src: Source, timeout: float = 6.0) -> dict:
    """One cheap read."""
    if not src.probe:
        return {"checked": False, "why": "no probe declared for this source"}
    t0 = time.time()
    req = urllib.request.Request(src.probe, headers=UA, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2048)
            return {"checked": True, "reachable": True, "status": r.status,
                    "ms": round((time.time() - t0) * 1000),
                    "bytes_sampled": len(body)}
    except urllib.error.HTTPError as e:
        return {"checked": True, "reachable": True, "status": e.code,
                "ms": round((time.time() - t0) * 1000),
                "note": "responded with an HTTP error, which still proves it is up"}
    except Exception as e:
        return {"checked": True, "reachable": False,
                "error": "%s: %s" % (type(e).__name__, str(e)[:140]),
                "ms": round((time.time() - t0) * 1000)}


def inventory(*, live: bool = True, timeout: float = 6.0, use_cache: bool = True) -> dict:
    """The whole registry, with holdings and (optionally) reachability."""
    if use_cache and live and CACHE.is_file():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - cached.get("generated_at", 0) < CACHE_TTL_S:
                return cached
        except (ValueError, OSError):
            pass

    rows = []
    for s in SOURCES:
        rows.append({
            **asdict(s),
            "local": local_holdings(s),
            "probe_result": probe(s, timeout) if live else
                            {"checked": False, "why": "not probed"},
        })
    out = {"schema": "argus-sources-v1", "generated_at": time.time(), "sources": rows,
           "read_only": True,
           "note": ("this registry reads; it never downloads. Reachable and held are "
                    "separate facts and are never merged into one indicator.")}
    if live:
        try:
            paths.assert_writable(CACHE)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
        except OSError:
            pass
    return out
