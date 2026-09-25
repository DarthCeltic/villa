"""Acquire a component ONCE, prove it, and survive being killed halfway."""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
from typing import Callable, Iterable

from argus.core import receipts

CHUNK = 1 << 20

ALREADY_PRESENT = "ALREADY_PRESENT"
ACQUIRED = "ACQUIRED"
RESUMED = "RESUMED"
REFUSED = "REFUSED"
MANUAL = "MANUAL_ACTION_REQUIRED"


class AcquireRefusal(RuntimeError):
    """Carries the machine-readable record so a caller can print the reason and the fix."""

    def __init__(self, record: dict):
        super().__init__(record.get("detail") or record.get("reason") or "refused")
        self.record = record


def _refuse(component_id: str, reason: str, detail: str, fix: str) -> dict:
    return {"component": component_id, "status": REFUSED, "reason": reason,
            "detail": detail, "fix": fix}


def sha256_file(path) -> str:
    """Streamed digest."""
    return receipts.sha_file(path)



def blob_path(root, digest: str) -> pathlib.Path:
    return pathlib.Path(root) / "blobs" / digest[:2] / digest


def component_dir(root, component_id: str) -> pathlib.Path:
    return pathlib.Path(root) / "components" / component_id


def _link_or_copy(src: pathlib.Path, dst: pathlib.Path) -> str:
    """Hard-link the blob into place, falling back to a copy across volumes."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"



def gate(component, *, accepted: dict, use: str = "run_locally") -> dict | None:
    """Return a refusal record, or None if acquisition may proceed."""
    fp = accepted.get(component.id)
    if fp is None:
        return _refuse(
            component.id, "LICENCE_NOT_ACCEPTED",
            "the licence for %s has not been accepted on this machine. Nothing is fetched "
            "before somebody has read what it permits." % component.name,
            "argus setup   (it prints the licence and asks), or "
            "argus setup --accept %s after reading %s"
            % (component.id, component.licence_url))
    if fp != component.acceptance_fingerprint():
        return _refuse(
            component.id, "LICENCE_TERMS_CHANGED",
            "an acceptance is recorded for %s but it was given for different terms -- the "
            "SPDX id, licence URL, source or pinned revision has changed since. An old yes "
            "does not cover new terms." % component.name,
            "argus setup --accept %s   (after reading %s again)"
            % (component.id, component.licence_url))
    verdict = component.registry_verdict()
    if verdict is not None and not verdict.get(use, False):
        return _refuse(
            component.id, "LICENCE_DOES_NOT_PERMIT",
            "%s is %s and argus.core.licence_registry says %s is not permitted for it: %s"
            % (component.name, component.licence_family, use, component.licence_permits),
            "there is no flag for this. Use a component whose licence grants %s, or obtain "
            "a grant from the publisher." % use)
    if not component.is_verifiable:
        return _refuse(
            component.id, "UNVERIFIABLE_NO_CONTENT_ADDRESS",
            "%s has integrity mode %s: %s" % (component.name, component.integrity_mode,
                                              component.integrity_why_unknown
                                              or "no reason recorded"),
            "record a real content address for this component in argus/cli/components.json "
            "(a 64-character sha256, or the full 40-character commit), then re-run "
            "argus setup. Do NOT fill the field in with a value you have not verified.")
    return None



def http_fetcher(timeout: float = 60.0) -> Callable:
    """The real fetcher."""
    import urllib.request

    def fetch(url: str, offset: int) -> Iterable[bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": "argus-setup/1"})
        if offset:
            req.add_header("Range", "bytes=%d-" % offset)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if offset and getattr(resp, "status", None) != 206:
                raise AcquireRefusal(_refuse(
                    url, "RANGE_IGNORED",
                    "resume asked for bytes from %d and the server sent the whole object "
                    "(HTTP %s). Appending that would corrupt the partial file."
                    % (offset, getattr(resp, "status", "?")),
                    "delete the .part file and let argus setup start this component again"))
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    return
                yield chunk

    return fetch


def acquire(component, root, *, accepted: dict, fetch: Callable,
            filename: str | None = None, use: str = "run_locally") -> dict:
    """Acquire one component into `root`, or refuse and say why."""
    refusal = gate(component, accepted=accepted, use=use)
    if refusal:
        return refusal

    if component.integrity_mode != "SHA256":
        return {"component": component.id, "status": MANUAL, "reason": "NOT_A_SINGLE_FILE",
                "detail": "%s is acquired as %s (integrity %s), which this file-level "
                          "acquirer does not perform."
                          % (component.name, component.acquire, component.integrity_mode),
                "fix": _manual_fix(component)}

    digest = (component.integrity_value or "").lower()
    blob = blob_path(root, digest)
    name = filename or (component.url.rstrip("/").rsplit("/", 1)[-1] or component.id)
    final = component_dir(root, component.id) / name

    if blob.is_file() and sha256_file(blob) == digest:
        how = _link_or_copy(blob, final)
        return {"component": component.id, "status": ALREADY_PRESENT,
                "reason": "CONTENT_ADDRESS_ALREADY_HELD",
                "detail": "a blob with digest %s is already stored; nothing was fetched"
                          % digest[:16],
                "path": str(final), "linked": how, "bytes": blob.stat().st_size,
                "sha256": digest}

    part = blob.with_suffix(".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.is_file() else 0
    resumed = offset > 0

    with part.open("ab") as fh:
        for chunk in fetch(component.url, offset):
            fh.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())

    got = sha256_file(part)
    if got != digest:
        rejected = part.with_suffix(".rejected")
        if rejected.exists():
            rejected.unlink()
        os.replace(part, rejected)
        return _refuse(
            component.id, "SHA256_MISMATCH",
            "expected %s, received %s (%d bytes). The bytes were NOT promoted; they are "
            "kept at %s so the difference can be looked at rather than retried away."
            % (digest[:16], got[:16], rejected.stat().st_size, rejected),
            "check the URL and the manifest digest. If the publisher genuinely changed the "
            "artifact, update argus/cli/components.json deliberately -- do not edit the "
            "digest to match whatever arrived.")

    os.replace(part, blob)
    how = _link_or_copy(blob, final)
    return {"component": component.id, "status": RESUMED if resumed else ACQUIRED,
            "reason": "SHA256_VERIFIED",
            "detail": "digest %s verified over the whole file%s"
                      % (digest[:16], " after resuming at %d bytes" % offset if resumed
                         else ""),
            "path": str(final), "linked": how, "bytes": blob.stat().st_size,
            "resumed_from": offset, "sha256": digest}


def _manual_fix(component) -> str:
    if component.acquire == "git":
        return ("git clone --filter=blob:none %s && git -C <dir> checkout %s"
                % (component.url, component.integrity_value or component.revision))
    if component.acquire == "pip":
        return ("install it with your own package manager, which verifies wheels: "
                "uv pip install -e .[volume]")
    if component.acquire == "s3-prefix":
        return ("fetched per region of interest by the acquisition path, which records a "
                "per-object digest in ACQUIRE_MANIFEST.json. There is nothing to download "
                "in one piece.")
    return "obtain %s from %s by hand and record its digest before using it" % (
        component.name, component.url)


def state(component, root) -> dict:
    """What is on disk for this component right now."""
    digest = (component.integrity_value or "").lower()
    out = {"component": component.id, "held": False, "verified": False,
           "partial_bytes": 0, "path": None}
    if component.integrity_mode != "SHA256":
        out["note"] = ("no single-file digest; presence is decided by the component's own "
                       "machinery (a git checkout, a sealed store)")
        return out
    blob = blob_path(root, digest)
    part = blob.with_suffix(".part")
    if part.is_file():
        out["partial_bytes"] = part.stat().st_size
        out["partial_path"] = str(part)
    if blob.is_file():
        out["path"] = str(blob)
        out["held"] = True
        out["verified"] = sha256_file(blob) == digest
    return out
