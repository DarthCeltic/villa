"""The canonical volume acquirer: URL, array path, EXACT ROI, phase, and a dry run that fetches nothing."""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import time

from argus.core import acquisition_identity as AI
from argus.core import key_list_hash as KLH

CONTRACT = "argus-volume-acquisition-v1"
ACQUISITION_ID_SCHEMA = "argus-acquisition-identity-v2"

MIRRORS = (
  ("dl", "https://dl.ash2txt.org/full-scrolls"),
  ("s3", "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com"),
)
CANONICAL_MIRROR = MIRRORS[0][0]

PHASES = {
  "A0": {"what": "metadata only", "byte_ceiling": 1 << 20, "needs_zarray": False},
  "A1": {"what": "coarse geometry for placing the ROI", "byte_ceiling": 256 << 20,
         "needs_zarray": True},
  "A2": {"what": "the level-0 ROI (<= 4 cm^2)", "byte_ceiling": 1 << 30, "needs_zarray": True},
}
MAX_PLANNED_OBJECTS = 1 << 20
MAX_PLANNED_KEY_BYTES = 64 << 20
MAX_ARRAY_PATH_BYTES = 256
FREE_SPACE_RESERVE_BYTES = 1 << 30
HTTP_READ_CHUNK = 64 << 10
METADATA_KEYS = (".zarray", ".zattrs")
CANONICAL_IDENTITY_NAME = "STORE_IDENTITY.json"
ROI_KEYS = ("z0", "z1", "y0", "y1", "x0", "x1")


_SEGMENT_OK = re.compile(r"[A-Za-z0-9._-]+")


class AcquisitionRefusal(RuntimeError):
    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record


def _norm_url(url) -> str:
    return AI.canonical_url(str(url))


def split_mirror(url) -> tuple:
    """(mirror name, path relative to the mirror base)."""
    u = _norm_url(url)
    for name, base in MIRRORS:
        b = _norm_url(base)
        if u == b or u.startswith(b + "/"):
            rel = u[len(b):].lstrip("/")
            if not rel:
                break
            if any(part in ("", ".", "..") or not _SEGMENT_OK.fullmatch(part) for part in rel.split("/")):
                raise AcquisitionRefusal("%s has a path segment that is empty, relative or not a plain name" % url)
            return name, rel
    raise AcquisitionRefusal("%s is not on a known official mirror (%s). An acquisition whose "
                             "source cannot be mapped to both mirrors has no single identity."
                             % (url, ", ".join(b for _n, b in MIRRORS)))


def volume_relative(url) -> str:
    """The relative path of the VOLUME ROOT (through the `.zarr` segment), array path removed."""
    _name, rel = split_mirror(url)
    parts = rel.split("/")
    idx = [i for i, p in enumerate(parts) if p.endswith(".zarr")]
    if len(idx) != 1:
        raise AcquisitionRefusal("%s does not name exactly one .zarr volume store" % url)
    return "/".join(parts[:idx[0] + 1])


def _validate_zarray_meta(meta: dict) -> None:
    """The .zarray is supplied by the caller, so its numbers are checked before any arithmetic or key list is built from them."""
    for field in ("shape", "chunks"):
        value = meta.get(field)
        if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value):
            raise AcquisitionRefusal(".zarray %s must be three positive integers (z, y, x), got %r" % (field, value))
    if len(meta["shape"]) != len(meta["chunks"]):
        raise AcquisitionRefusal(".zarray shape and chunks have different lengths")
    try:
        size = _itemsize(meta.get("dtype"))
    except Exception:
        size = 0
    if not isinstance(size, int) or size < 1:
        raise AcquisitionRefusal(".zarray dtype %r has no usable item size" % (meta.get("dtype"),))


def mirror_urls(url) -> dict:
    rel = volume_relative(url)
    return {name: "%s/%s" % (base, rel) for name, base in MIRRORS}


def canonical_volume_url(url) -> str:
    return mirror_urls(url)[CANONICAL_MIRROR]


def store_id(url) -> str:
    """ONE derivation: the canonical-mirror volume URL through acquisition_identity.store_id."""
    return AI.store_id(canonical_volume_url(url))


def exact_roi(roi, shape=None) -> dict:
    """Integers, ordered, non-empty -- and INSIDE the array."""
    if isinstance(roi, str):
        vals = roi.split(",")
        if len(vals) != 6:
            raise AcquisitionRefusal("ROI %r is not z0,z1,y0,y1,x0,x1" % roi)
        roi = dict(zip(ROI_KEYS, vals))
    try:
        r = {k: int(roi[k]) for k in ROI_KEYS}
    except (KeyError, TypeError, ValueError):
        raise AcquisitionRefusal("ROI %r must give integer %s" % (roi, ROI_KEYS)) from None
    for a in ("z", "y", "x"):
        if not 0 <= r[a + "0"] < r[a + "1"]:
            raise AcquisitionRefusal("ROI %s axis is empty or negative: %r" % (a, r))
    if shape is not None:
        for i, a in enumerate(("z", "y", "x")):
            if r[a + "1"] > int(shape[i]):
                raise AcquisitionRefusal(
                  "ROI %s1=%d exceeds the array extent %d. An exact ROI is refused, not clamped: "
                  "a clamped ROI is a different job wearing the requested one's name."
                  % (a, r[a + "1"], int(shape[i])))
    return r


def _chunk_ranges(meta: dict, roi: dict) -> tuple:
    chunks = meta["chunks"]
    sep = meta.get("dimension_separator", ".")
    if sep not in (".", "/"):
        raise AcquisitionRefusal("dimension_separator must be '.' or '/'")
    rng = []
    for i, a in enumerate(("z", "y", "x")):
        c = int(chunks[i])
        lo, hi = roi[a + "0"], roi[a + "1"]
        rng.append(range(lo // c, (hi - 1) // c + 1))
    return sep, tuple(rng)


def chunk_key_count(meta: dict, roi: dict) -> int:
    """Count chunk keys analytically, without allocating the key list."""
    _sep, ranges = _chunk_ranges(meta, roi)
    return math.prod(r.stop - r.start for r in ranges)


def _estimated_key_list_bytes(array_path: str, sep: str, count: int, ranges: tuple) -> int:
    """Conservative upper bound for the retained key strings and list pointers."""
    max_index_bytes = sum(len(str(r[-1] if r else 0)) for r in ranges)
    max_key_bytes = len(array_path.encode("utf-8")) + 1 + max_index_bytes + (2 * len(sep))
    return count * (max_key_bytes + 64)


def chunk_keys(meta: dict, roi: dict) -> list:
    """Every chunk key covering the ROI, in row-major order."""
    sep, ranges = _chunk_ranges(meta, roi)
    count = math.prod(r.stop - r.start for r in ranges)
    if count > MAX_PLANNED_OBJECTS:
        raise AcquisitionRefusal(
          "planned object count %d exceeds the in-memory planning limit %d. Shrink the ROI."
          % (count, MAX_PLANNED_OBJECTS))
    return [sep.join((str(i), str(j), str(k))) for i in ranges[0] for j in ranges[1]
            for k in ranges[2]]


def _itemsize(dtype) -> int:
    s = str(dtype or "")
    try:
        import numpy as np
        size = int(np.dtype(s).itemsize)
        if size >= 1:
            return size
    except Exception:
        pass
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else 8


def plan(*, url, array_path, roi, phase, zarray_meta=None, scroll=None, volume_id=None,
         byte_ceiling=None) -> dict:
    """Pure: the whole job, known before any request."""
    if phase not in PHASES:
        raise AcquisitionRefusal("unknown phase %r; phases are %s" % (phase, list(PHASES)))
    spec = PHASES[phase]
    raw_ap = str(array_path if array_path is not None else "").strip()
    if raw_ap.startswith(("/", "\\")) or "\\" in raw_ap or ":" in raw_ap:
        raise AcquisitionRefusal("array path must be a relative slash-separated path")
    ap = raw_ap.strip("/")
    if any(part in ("", ".", "..") or not _SEGMENT_OK.fullmatch(part) for part in ap.split("/") if ap):
        raise AcquisitionRefusal("array path contains an unsafe path segment")
    if len(ap.encode("utf-8")) > MAX_ARRAY_PATH_BYTES:
        raise AcquisitionRefusal("array path exceeds the %d-byte limit" % MAX_ARRAY_PATH_BYTES)
    if phase != "A0" and not ap:
        raise AcquisitionRefusal("phase %s needs an array path (pyramid level)" % phase)
    mirrors = mirror_urls(url)
    ceiling = int(byte_ceiling) if byte_ceiling is not None else spec["byte_ceiling"]
    if ceiling > spec["byte_ceiling"]:
        raise AcquisitionRefusal("byte ceiling %d exceeds phase %s's %d; a phase ceiling is raised "
                                 "by editing the phase, visibly" % (ceiling, phase,
                                                                    spec["byte_ceiling"]))
    if spec["needs_zarray"]:
        if not isinstance(zarray_meta, dict) or "shape" not in zarray_meta:
            raise AcquisitionRefusal(
              "phase %s cannot be planned without the array's .zarray, and fetching it to plan "
              "would make the plan a request. Supply a local .zarray (sealed from A0)." % phase)
        _validate_zarray_meta(zarray_meta)
        r = exact_roi(roi, zarray_meta["shape"])
        chunk_count = chunk_key_count(zarray_meta, r)
        separator, ranges = _chunk_ranges(zarray_meta, r)
        per_chunk = math.prod(int(c) for c in zarray_meta["chunks"]) * _itemsize(
          zarray_meta.get("dtype"))
        upper = chunk_count * per_chunk
        bound_basis = "uncompressed chunk bytes x planned objects (an upper bound)"
        if upper > ceiling:
            raise AcquisitionRefusal(
              "planned upper bound %d bytes exceeds the %s ceiling %d. Shrink the ROI." % (upper,
                                                                                              phase,
                                                                                              ceiling))
        if chunk_count > MAX_PLANNED_OBJECTS:
            raise AcquisitionRefusal(
              "planned object count %d exceeds the in-memory planning limit %d. Shrink the ROI."
              % (chunk_count, MAX_PLANNED_OBJECTS))
        estimated_keys = _estimated_key_list_bytes(ap, separator, chunk_count, ranges)
        if estimated_keys > MAX_PLANNED_KEY_BYTES:
            raise AcquisitionRefusal(
              "estimated key-list allocation %d bytes exceeds the %d-byte planning limit"
              % (estimated_keys, MAX_PLANNED_KEY_BYTES))
        keys = ["%s/%s" % (ap, k) for k in chunk_keys(zarray_meta, r)]
    else:
        r = exact_roi(roi) if roi is not None else None
        keys = [("%s/%s" % (ap, k)) if ap else k for k in METADATA_KEYS]
        upper = spec["byte_ceiling"]
        bound_basis = "metadata objects, bounded by the A0 ceiling"
    if upper > ceiling:
        raise AcquisitionRefusal(
          "planned upper bound %d bytes exceeds the %s ceiling %d. Shrink the ROI." % (upper, phase,
                                                                                  ceiling))
    klh = KLH.hash_record(keys)
    ident_payload = {
      "schema": ACQUISITION_ID_SCHEMA, "canonical_source": mirrors[CANONICAL_MIRROR],
      "array_path": ap, "roi": r, "phase": phase, "key_list_hash": klh,
      "byte_ceiling": ceiling,
    }
    acq_id = hashlib.sha256(json.dumps(ident_payload, sort_keys=True, separators=(",", ":"))
                            .encode("utf-8")).hexdigest()[:24]
    return {
      "contract": CONTRACT, "phase": phase, "phase_means": spec["what"],
      "scroll": scroll, "volume_id": volume_id,
      "requested_url": str(url), "mirrors": mirrors, "canonical_mirror": CANONICAL_MIRROR,
      "store_id": store_id(url), "store_id_derivation": "acquisition_identity.store_id(canonical "
                                                        "mirror volume URL); identical for every "
                                                        "mirror",
      "array_path": ap, "roi": r, "keys": keys, "planned_objects": len(keys),
      "key_list_hash": klh, "byte_ceiling": ceiling, "planned_upper_bound_bytes": upper,
      "upper_bound_basis": bound_basis,
      "acquisition_id": acq_id, "acquisition_id_schema": ACQUISITION_ID_SCHEMA,
      "acquisition_id_payload": ident_payload,
    }


def dry_run(**kw) -> dict:
    """The plan and nothing else."""
    p = plan(**kw)
    return dict(p, dry_run=True, requests_made=0, bytes_fetched=0,
                dry_run_means="the job was fully enumerated from local metadata; no request was "
                              "made and no byte was fetched")


def _same_host_redirect_handler():
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    class SameHostRedirect(urllib.request.HTTPRedirectHandler):
        """A redirect is followed only to the same host and never from https to http: a mirror may not send this client somewhere the plan did not vet."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            old, new = urlsplit(req.full_url), urlsplit(newurl)
            if new.hostname != old.hostname or (old.scheme == "https" and new.scheme != "https"):
                raise urllib.error.HTTPError(req.full_url, code, "redirect to %s refused (different host or downgraded scheme)" % new.hostname, headers, fp)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return SameHostRedirect


def _SameHostRedirect():
    return _same_host_redirect_handler()()


class HttpFetcher:
    """The real network client (sequential urllib), used only by a non-dry-run acquisition."""

    def __init__(self, attempts: int = 4, backoff_s: float = 2.0, max_bytes: int | None = None):
        import urllib.request
        self.opener = urllib.request.build_opener(_SameHostRedirect())
        self.opener.addheaders = [("User-Agent", "argus-acquire/2")]
        default_limit = max(spec["byte_ceiling"] for spec in PHASES.values())
        self.attempts, self.backoff_s = attempts, backoff_s
        self.max_bytes = default_limit if max_bytes is None else int(max_bytes)
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        self._configured_max_bytes = self.max_bytes
        self.requests = self.bytes = 0

    def get(self, url: str, timeout: float = 60.0):
        import urllib.error
        last = None
        for attempt in range(self.attempts):
            try:
                self.requests += 1
                with self.opener.open(url, timeout=timeout) as r:
                    remaining = self.max_bytes - self.bytes
                    if remaining < 0:
                        raise AcquisitionRefusal("HTTP byte budget is already exhausted")
                    headers = getattr(r, "headers", {})
                    try:
                        declared = int(headers.get("Content-Length"))
                    except (AttributeError, TypeError, ValueError):
                        declared = None
                    if declared is not None and declared > remaining:
                        raise AcquisitionRefusal(
                          "response for %s declares %d bytes but only %d remain in the byte budget"
                          % (url, declared, remaining))
                    data, read_bytes = bytearray(), 0
                    while True:
                        amount = min(HTTP_READ_CHUNK, remaining - read_bytes + 1)
                        chunk = r.read(amount)
                        if not chunk:
                            break
                        data.extend(chunk)
                        read_bytes += len(chunk)
                        self.bytes += len(chunk)
                        if read_bytes > remaining:
                            raise AcquisitionRefusal(
                              "response for %s exceeded the %d-byte read budget" % (url,
                                                                                       self.max_bytes))
                return data, None
            except AcquisitionRefusal:
                raise
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None, 404
                last = e
            except Exception as e:
                last = e
            time.sleep(self.backoff_s * (2 ** attempt))
        raise RuntimeError("object failed after %d attempts: %s (%s)"
                           % (self.attempts, url, type(last).__name__))


def _configure_fetcher_budget(fetcher, byte_ceiling: int) -> None:
    """Apply one acquisition budget to the fetcher and any transparent inner fetcher."""
    seen = set()
    current = fetcher
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if all(hasattr(current, name) for name in ("_configured_max_bytes", "max_bytes", "bytes")):
            current.bytes = 0
            current.max_bytes = min(int(current._configured_max_bytes), byte_ceiling)
        current = getattr(current, "inner", None)


def acquire(the_plan: dict, *, fetcher, root, volume_registry=None, target_authority=None,
            frozen_targets=None, declared: dict | None = None, official: dict | None = None) -> dict:
    """Execute a plan."""
    from argus.core import ink_socket_gate as G
    from argus.core import volume_id_gate as VG
    if the_plan.get("dry_run"):
        raise AcquisitionRefusal("a dry-run plan is not executable; plan again without dry_run")
    try:
        VG.before_acquisition(scroll=the_plan.get("scroll"),
                              declared_volume_id=the_plan.get("volume_id"),
                              source_url=the_plan["requested_url"], registry=volume_registry)
    except VG.VolumeIdRefusal as exc:
        if exc.verdict.get("state") != "UNKNOWN":
            raise
        raise AcquisitionRefusal(str(exc)) from exc
    fz = frozen_targets if frozen_targets is not None else G.frozen_targets()
    if fz.get("state") != "READ":
        raise AcquisitionRefusal("prize target set UNKNOWN (%s); UNKNOWN refuses" % fz.get("why"))
    if any(G._key(t) == G._key(the_plan.get("scroll")) for t in fz["targets"]):
        auth = G.verify_target_acquisition_authority(the_plan.get("scroll"), target_authority,
                                                     stage="acquire", plan=the_plan)
        if not auth["verified"]:
            raise AcquisitionRefusal(
                "%s is a prize target (%s) and target authority does not verify: %s. A prize-target "
                "acquisition needs a consumed target-acquisition packet whose phase matches this "
                "plan; a scroll that is not a prize target needs none, and its volume identity is "
                "checked against the public official survey instead."
                % (the_plan.get("scroll"), ", ".join((fz["targets"] if isinstance(fz["targets"], dict) else {}).get(
                    next(t for t in fz["targets"] if G._key(t) == G._key(the_plan.get("scroll"))), [])),
                   "; ".join(auth["reasons"])))
    _configure_fetcher_budget(fetcher, int(the_plan["byte_ceiling"]))
    dst = pathlib.Path(root) / the_plan["store_id"]
    dst.mkdir(parents=True, exist_ok=True)
    base = the_plan["requested_url"].rstrip("/")
    vol_base = base if base.endswith(".zarr") else base[:base.index(".zarr") + 5]
    if vol_base not in set((the_plan.get("mirrors") or {}).values()):
        raise AcquisitionRefusal("the URL to fetch is not one of this plan's validated mirrors; refusing to request an address the plan did not vet")
    import shutil as _shutil
    if _shutil.disk_usage(dst).free < int(the_plan.get("planned_upper_bound_bytes") or the_plan["byte_ceiling"]) + FREE_SPACE_RESERVE_BYTES:
        raise AcquisitionRefusal("not enough free disk space for the planned upper bound of %d bytes plus a %d-byte reserve" % (int(the_plan.get("planned_upper_bound_bytes") or the_plan["byte_ceiling"]), FREE_SPACE_RESERVE_BYTES))
    manifest, fetched_bytes, absent = {}, 0, 0
    for key in the_plan["keys"]:
        data, code = fetcher.get("%s/%s" % (vol_base, key))
        if data is None and code == 404:
            manifest[key] = {"state": "absent_404"}
            absent += 1
            continue
        if data is None:
            raise AcquisitionRefusal("object %s returned no bytes (code %r)" % (key, code))
        fetched_bytes += len(data)
        if fetched_bytes > the_plan["byte_ceiling"]:
            raise AcquisitionRefusal("byte ceiling %d exceeded at %s; stopped"
                                     % (the_plan["byte_ceiling"], key))
        local = dst / key.replace("/", os.sep)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".partial")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, local)
        manifest[key] = {"state": "present", "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()}
    from argus.core import receipts
    from argus.core import store_identity as SI

    def _local_json(name):
        ap = the_plan.get("array_path") or ""
        p = dst / ap / name if ap else dst / name
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    present_keys = [k for k in the_plan["keys"] if manifest.get(k, {}).get("state")]
    assessment = SI.assess(zarray=_local_json(".zarray"), zattrs=_local_json(".zattrs"),
                           manifest_keys=present_keys,
                           recorded_key_list_hash=the_plan["key_list_hash"],
                           declared=declared, level=str(the_plan.get("array_path") or "0"),
                           official=official)
    identity = SI.identity_record(base={
      "store_id": the_plan["store_id"], "mirrors": the_plan["mirrors"],
      "physical_scroll": the_plan.get("scroll"), "volume_id": the_plan.get("volume_id"),
      "array_path": the_plan.get("array_path"), "roi": the_plan.get("roi"),
      "phase": the_plan["phase"], "acquisition_id": the_plan["acquisition_id"],
      "key_list_hash": the_plan["key_list_hash"],
      "official_identity_sha256": (official or {}).get("identity_sha256"),
      "generator": CONTRACT}, assessment=assessment)
    receipts.write_json(identity, dst / ("STORE_IDENTITY_%s.json" % the_plan["phase"]))
    if the_plan["phase"] != "A0":
        receipts.write_json(identity, dst / CANONICAL_IDENTITY_NAME)
    body = {"contract": CONTRACT, "plan": {k: v for k, v in the_plan.items() if k != "keys"},
            "store_identity": identity,
            "objects": manifest, "fetched_bytes": fetched_bytes, "absent_404": absent,
            "complete": all(manifest.get(k, {}).get("state") in ("present", "absent_404")
                            for k in the_plan["keys"]),
            "key_list_hash": the_plan["key_list_hash"]}
    receipts.write_json(body, dst / ("ACQUIRE_MANIFEST_%s.json" % the_plan["phase"]))
    return body


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS volume acquisition (plan / dry-run)")
    ap.add_argument("--url", required=True, help="volume URL on either official mirror")
    ap.add_argument("--array-path", default="", help="pyramid level inside the volume, e.g. 0")
    ap.add_argument("--roi", default=None, help="z0,z1,y0,y1,x0,x1 (exact; never clamped)")
    ap.add_argument("--phase", required=True, choices=sorted(PHASES))
    ap.add_argument("--zarray", default=None, help="LOCAL .zarray file for A1/A2 planning")
    ap.add_argument("--scroll", default=None)
    ap.add_argument("--volume-id", default=None)
    ap.add_argument("--byte-ceiling", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="enumerate the job from local metadata and make zero requests")
    a = ap.parse_args(argv)
    meta = None
    if a.zarray:
        meta = json.loads(pathlib.Path(a.zarray).read_text(encoding="utf-8"))
    kw = dict(url=a.url, array_path=a.array_path, roi=a.roi, phase=a.phase, zarray_meta=meta,
              scroll=a.scroll, volume_id=a.volume_id, byte_ceiling=a.byte_ceiling)
    try:
        if not a.dry_run:
            raise AcquisitionRefusal(
              "this CLI performs dry runs only. A real fetch goes through acquire() under a "
              "consumed launch_authorization_v3 and, for a prize target, verified target "
              "authority -- neither of which a command-line flag can supply.")
        out = dry_run(**kw)
    except AcquisitionRefusal as e:
        print(json.dumps({"refused": True, "why": str(e)}, indent=1))
        return 2
    show = dict(out)
    show["keys"] = show["keys"][:5] + (["..."] if len(out["keys"]) > 5 else [])
    print(json.dumps(show, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
