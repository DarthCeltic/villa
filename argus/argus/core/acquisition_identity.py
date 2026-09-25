"""Two different identities, kept apart on purpose: WHERE bytes come from, and WHAT job ran."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import pathlib
import re
import urllib.parse

SCHEMA = "argus-acquisition-identity-v1"


def canonical_url(url: str) -> str:
    """One spelling per source."""
    u = urllib.parse.urlsplit(url.strip())
    scheme = (u.scheme or "https").lower()
    host = (u.hostname or "").lower()
    port = u.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = "%s:%d" % (host, port)
    path = re.sub(r"/{2,}", "/", u.path).rstrip("/")
    return urllib.parse.urlunsplit((scheme, host, path, "", ""))


def key_list_hash(keys) -> str:
    """The planned set, IN ORDER."""
    h = hashlib.sha256()
    for k in keys:
        h.update(k.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def store_id(url: str) -> str:
    """SOURCE identity."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def acquisition_id(url: str, array_path: str, roi: dict, keys, config_hash: str) -> str:
    """JOB identity."""
    payload = {
      "schema": SCHEMA,
      "canonical_source": canonical_url(url),
      "array_path": str(array_path),
      "roi": {k: roi.get(k) for k in ("z0", "z1", "y0", "y1", "x0", "x1")},
      "ordered_key_list_sha256": key_list_hash(keys),
      "object_count": len(list(keys)) if not hasattr(keys, "__len__") else len(keys),
      "config_sha256": config_hash,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def binding(url: str, array_path: str, roi: dict, keys, config_hash: str) -> dict:
    """The full, auditable identity record."""
    keys = list(keys)
    return {
      "schema": SCHEMA,
      "store_id": store_id(url),
      "store_id_proves": "SOURCE identity only -- which remote array this directory mirrors. "
                         "It is sha256(canonical_source_url)[:16] and is therefore verifiable "
                         "by construction, but two different regions of one array share it.",
      "acquisition_id": acquisition_id(url, array_path, roi, keys, config_hash),
      "acquisition_id_proves": "JOB identity -- source, array path, ROI, the ordered planned "
                               "key list, and the config that produced it. A resumed run "
                               "either matches this and is finishing the same job, or it is a "
                               "different job and may not inherit this one's progress.",
      "canonical_source_url": canonical_url(url),
      "raw_source_url": url,
      "array_path": str(array_path),
      "roi": roi,
      "planned_objects": len(keys),
      "ordered_key_list_sha256": key_list_hash(keys),
      "config_sha256": config_hash,
    }


def matches(record: dict, url: str, array_path: str, roi: dict, keys, config_hash: str) -> dict:
    """Does a stored binding describe the job about to run?"""
    want = binding(url, array_path, roi, keys, config_hash)
    diffs = [k for k in ("canonical_source_url", "array_path", "ordered_key_list_sha256",
                         "config_sha256", "planned_objects")
             if record.get(k) != want.get(k)]
    if record.get("roi") != want.get("roi"):
        diffs.append("roi")
    return {"same_job": not diffs and record.get("acquisition_id") == want["acquisition_id"],
            "differing_fields": diffs,
            "stored_acquisition_id": record.get("acquisition_id"),
            "expected_acquisition_id": want["acquisition_id"]}



STORE_ROOTS = (
  pathlib.Path(_argus_public_path('home', 'cache/stores')),
  pathlib.Path(_argus_public_path('home', 'cache/stores')),
)


def store_roots() -> tuple:
    """Every root a sealed store may live under, in search order."""
    return STORE_ROOTS


def find_store(url: str):
    """The directory holding this url's store, or None."""
    sid = store_id(url)
    for root in STORE_ROOTS:
        d = root / sid
        if (d / ".zarray").is_file():
            return d
    return None


def store_search_report(url: str) -> dict:
    """Where a store was looked for and what was found -- for a receipt, not a log line."""
    sid = store_id(url)
    return {
      "store_id": sid,
      "searched": [str(r / sid) for r in STORE_ROOTS],
      "found": str(find_store(url)) if find_store(url) else None,
      "why_more_than_one_root": "a fetch may be redirected off the disk the scoring run uses; "
                                "a store that exists but cannot be found reads as absence.",
    }
