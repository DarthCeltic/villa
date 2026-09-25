"""THE canonical official-identity resolver."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

CONTRACT = "argus-official-identity-v1"
MAX_DECODED_BYTES = 32 << 20

PROVEN = "PROVEN_BY_OFFICIAL_CATALOG_RESOLUTION"
UNKNOWN = "UNKNOWN"

MATCH = "MATCH"
CONFLICT = "CONFLICT"
ABSENT = "ABSENT"
NOT_APPLICABLE = "NOT_APPLICABLE"
PASSING = (MATCH, NOT_APPLICABLE)

REQUIRED_FIELDS = ("canonical_scroll", "scan_id", "segment", "canonical_source_url", "array_path",
                   "dimensions_dtype", "pitch_um", "energy_kev", "catalogue_source",
                   "metadata_hash")

REPRESENTATIONS = ("VOLUME", "SURFACE_VOLUME")

PITCH_TOL_UM = 0.001
ENERGY_TOL_KEV = 0.5

CATALOGUE_URL = ("https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/"
                 "metadata.min.json")
S3_HTTPS_ROOT = "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/"
S3_ROOT = "s3://vesuvius-challenge-open-data"
DL_ROOT = "https://dl.ash2txt.org/full-scrolls/"

METADATA_NAMES = ("metadata.min.json", ".zarray", ".zattrs", ".zgroup", "zarr.json", "meta.json")

TOKEN_RE = re.compile(r"^\d{14}$")
_VOLUME_STORE_SEGMENT = re.compile(r"^(\d{14})(?:-|\.zarr$)")
_SURFACE_VOLUME_TOKEN = re.compile(r"-volume-(\d{14})(-L\d+)?\.zarr/?$")


class OfficialIdentityRefusal(RuntimeError):
    def __init__(self, message, resolution=None):
        super().__init__(message)
        self.resolution = resolution


class MetadataOnlyRefusal(RuntimeError):
    """A request for an object that is not metadata."""


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon_sha(obj) -> str:
    return _sha(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def _key(s) -> str:
    return re.sub(r"[\s_\-.:/]+", "", str(s or "")).upper()



def is_token(s) -> bool:
    return bool(TOKEN_RE.match(str(s or "").strip()))


def parse_volume_store_token(url) -> str | None:
    """The 14-digit id from a `volumes/<id>-....zarr` path segment."""
    try:
        parts = [p for p in urllib.parse.urlsplit(str(url)).path.split("/") if p]
    except ValueError:
        return None
    found = []
    for i, seg in enumerate(parts):
        if seg.endswith(".zarr") and i > 0 and parts[i - 1] == "volumes":
            m = _VOLUME_STORE_SEGMENT.match(seg)
            if m:
                found.append(m.group(1))
    return found[0] if len(set(found)) == 1 else None


def parse_surface_volume_token(path) -> str | None:
    """The PARENT volume token embedded in a `surface-volumes/...-volume-<id>[-Ln].zarr` name."""
    s = str(path or "")
    parts = [p for p in urllib.parse.urlsplit(s).path.split("/") if p] if "://" in s else \
        [p for p in s.replace("\\", "/").split("/") if p]
    for p in parts:
        m = _SURFACE_VOLUME_TOKEN.search(p + "/") if p.endswith(".zarr") else None
        if m:
            return m.group(1)
    return None


def parse_any_volume_token(url) -> str | None:
    """The token of a `volumes/<id>-....zarr` store, else of a `surface-volumes/...-volume-<id>.zarr` render published beside a segment."""
    return parse_volume_store_token(url) or parse_surface_volume_token(url)


def https_url(path: str) -> str:
    return S3_HTTPS_ROOT + str(path).lstrip("/")


def _norm_url(u) -> str:
    s = str(u or "").strip().rstrip("/")
    for root in (S3_HTTPS_ROOT, DL_ROOT, S3_ROOT + "/"):
        if s.startswith(root.rstrip("/")):
            return s[len(root.rstrip("/")):].lstrip("/")
    return s



def _object_name(url: str) -> str:
    return urllib.parse.urlsplit(str(url)).path.rstrip("/").split("/")[-1]


def assert_metadata_url(url: str) -> str:
    name = _object_name(url)
    if name not in METADATA_NAMES:
        raise MetadataOnlyRefusal(
          "refusing %r: object %r is not a metadata object (%s). This path never requests a chunk."
          % (url, name, ", ".join(METADATA_NAMES)))
    return url


def http_fetch(url: str, timeout: float = 30.0) -> tuple:
    """(status, bytes, headers)."""
    assert_metadata_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "argus-official-identity/1",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(8 << 20)
            hdr = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, b"", {k.lower(): v for k, v in (e.headers or {}).items()}
    if hdr.get("content-encoding") == "gzip":
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = inflater.decompress(raw, MAX_DECODED_BYTES + 1)
        if len(raw) > MAX_DECODED_BYTES or inflater.unconsumed_tail:
            raise ValueError("a metadata response decodes to more than %d bytes; refused" % MAX_DECODED_BYTES)
        if not inflater.eof:
            raise ValueError("a gzip metadata response ended before its stream did; refused")
    return status, raw, hdr


class MetadataCache:
    """Append-only, content-addressed record of every metadata response."""

    def __init__(self, root, fetcher=http_fetch, offline: bool = False):
        self.root = pathlib.Path(root)
        self.fetcher = fetcher
        self.offline = offline
        self._memo = {}

    def _records(self, url: str) -> list:
        d = self.root / "records"
        if not d.is_dir():
            return []
        tag = _sha(url.encode("utf-8"))[:16]
        out = []
        for p in sorted(d.glob("*_%s.json" % tag)):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("url") == url:
                out.append(dict(rec, record_path=str(p)))
        return out

    def body(self, rec: dict) -> bytes | None:
        if not rec or not rec.get("sha256"):
            return None
        p = self.root / "bodies" / ("%s.bin" % rec["sha256"])
        try:
            b = p.read_bytes()
        except OSError:
            return None
        return b if _sha(b) == rec["sha256"] else None

    def get(self, url: str) -> dict:
        """One observation per URL per cache object."""
        assert_metadata_url(url)
        if url in self._memo:
            return self._memo[url]
        if self.offline or self.fetcher is None:
            recs = self._records(url)
            rec = recs[-1] if recs else {"url": url, "observed_utc": None, "http_status": None,
                                         "sha256": None, "bytes": None,
                                         "why": "offline and never observed"}
        else:
            try:
                status, b, hdr = self.fetcher(url)
                err = None
            except MetadataOnlyRefusal:
                raise
            except Exception as e:
                status, b, hdr, err = None, b"", {}, "%s: %s" % (type(e).__name__, e)
            observed = _utc()
            rec = {"schema": "argus-metadata-observation-v1", "contract": CONTRACT, "url": url,
                   "observed_utc": observed, "http_status": status,
                   "sha256": _sha(b) if (status == 200) else None,
                   "bytes": len(b) if status == 200 else 0, "error": err,
                   "headers": {k: hdr.get(k) for k in ("etag", "last-modified", "content-length",
                                                       "content-encoding") if hdr.get(k)}}
            (self.root / "records").mkdir(parents=True, exist_ok=True)
            (self.root / "bodies").mkdir(parents=True, exist_ok=True)
            if status == 200:
                bp = self.root / "bodies" / ("%s.bin" % rec["sha256"])
                if not bp.exists():
                    bp.write_bytes(b)
            stamp = observed.replace("-", "").replace(":", "")
            rp = self.root / "records" / ("%s_%s.json" % (stamp, _sha(url.encode())[:16]))
            n = 1
            while rp.exists():
                n += 1
                rp = rp.with_name("%s_%d_%s.json" % (stamp, n, _sha(url.encode())[:16]))
            rp.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
            rec = dict(rec, record_path=str(rp))
        self._memo[url] = rec
        return rec

    def get_json(self, url: str) -> tuple:
        rec = self.get(url)
        if rec.get("http_status") != 200:
            return rec, None
        b = self.body(rec)
        try:
            return rec, json.loads(b.decode("utf-8")) if b is not None else None
        except (ValueError, UnicodeDecodeError):
            return rec, None



class Catalogue:
    """The official catalogue at one observation."""

    def __init__(self, doc: dict, *, source_url: str, observed_utc: str | None, sha256: str,
                 basis: str):
        self.doc = doc
        self.samples = (doc or {}).get("samples") or {}
        self.source = {"url": source_url, "observed_utc": observed_utc, "sha256": sha256,
                       "basis": basis}

    @classmethod
    def from_bytes(cls, b: bytes, *, source_url: str, observed_utc: str | None, basis: str):
        return cls(json.loads(b.decode("utf-8")), source_url=source_url,
                   observed_utc=observed_utc, sha256=_sha(b), basis=basis)

    @classmethod
    def from_cache(cls, cache: MetadataCache, url: str = CATALOGUE_URL):
        rec = cache.get(url)
        b = cache.body(rec)
        if rec.get("http_status") != 200 or b is None:
            raise OfficialIdentityRefusal("catalogue %s not observed (status %s, %s)"
                                          % (url, rec.get("http_status"),
                                             rec.get("error") or rec.get("why")))
        return cls.from_bytes(b, source_url=url, observed_utc=rec["observed_utc"],
                              basis="metadata cache record %s" % rec.get("record_path"))

    @classmethod
    def from_crawl(cls):
        """The recorded crawl copy, verified against the sha256 that crawl recorded."""
        from argus.core import paths
        src = paths.find_artifact("scrollprize_crawl", "DATA_BROWSER_SOURCE.json")
        meta = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
        local = paths.find_artifact("scrollprize_crawl", "data_browser_source", "metadata.min.json")
        b = pathlib.Path(local).read_bytes()
        if _sha(b) != meta.get("sha256_decompressed"):
            raise OfficialIdentityRefusal("crawl copy %s sha256 %s != recorded %s" % (
              local, _sha(b), meta.get("sha256_decompressed")))
        return cls.from_bytes(b, source_url=meta.get("backing_source_url"),
                              observed_utc=meta.get("retrieved_utc"),
                              basis="recorded crawl copy (DATA_BROWSER_SOURCE.json)")

    def token_matches(self, token: str) -> list:
        """Every catalogue record whose id IS the token: volumes, scans, segments."""
        t = str(token)
        out = []
        for sk, s in self.samples.items():
            for kind in ("volumes", "scans", "segments"):
                if t in ((s or {}).get(kind) or {}):
                    out.append({"sample": sk, "kind": kind[:-1].upper(), "id": t})
        return out

    def volume_index(self) -> dict:
        """{sample: {volume_id: {scan_id, pixel_size_um, energy_keV, licence, store_path}}}."""
        idx = {}
        for sk, s in self.samples.items():
            for vid, v in ((s or {}).get("volumes") or {}).items():
                props = v.get("properties") or {}
                stores = [o.get("path") for d in v.get("data") or [] if d.get("type") == "ome-zarr"
                          for o in d.get("origins") or []]
                idx.setdefault(sk, {})[vid] = {
                  "scan_id": v.get("scan_id"), "pixel_size_um": props.get("pixel_size_um"),
                  "energy_keV": props.get("energy_keV"),
                  "licence": (props.get("license") or {}).get("name"), "store_paths": stores}
        return idx



def _f(state, value=None, why="", evidence=None) -> dict:
    return {"state": state, "value": value, "why": why, "evidence": evidence}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _level_factor(zattrs: dict | None, array_path: str):
    """(factor, why): level scale / level-0 scale from the official OME multiscales, if declared."""
    ms = (zattrs or {}).get("multiscales")
    if not ms:
        return None, "the official store declares no OME multiscales"
    scales = {}
    for ds in ms[0].get("datasets") or []:
        for t in ds.get("coordinateTransformations") or []:
            if t.get("type") == "scale" and t.get("scale"):
                scales[str(ds.get("path"))] = [float(x) for x in t["scale"]]
    base = scales.get("0")
    lev = scales.get(str(array_path))
    if not base or not lev:
        return None, "OME scale for level %r or level 0 not declared" % array_path
    return lev[-1] / base[-1], "OME scale level %r %s / level 0 %s" % (array_path, lev, base)


def _ome_base_scale(zattrs: dict | None):
    ms = (zattrs or {}).get("multiscales")
    for ds in ((ms or [{}])[0].get("datasets") or []):
        if str(ds.get("path")) == "0":
            for t in ds.get("coordinateTransformations") or []:
                if t.get("type") == "scale" and t.get("scale"):
                    return float(t["scale"][-1])
    return None


def resolve(claim: dict, *, catalogue: Catalogue | None, cache: MetadataCache | None) -> dict:
    """Resolve one claimed identity."""
    c = dict(claim or {})
    fields = {k: _f(ABSENT, why="not evaluated") for k in REQUIRED_FIELDS}
    token = str(c.get("token") or "").strip()
    rep = c.get("representation") or "VOLUME"
    out = {"contract": CONTRACT, "claim": c, "token": token or None, "representation": rep}

    if catalogue is None or not catalogue.samples:
        fields["catalogue_source"] = _f(ABSENT, why="no official catalogue loaded")
    else:
        s = catalogue.source
        miss = [k for k in ("url", "observed_utc", "sha256") if not s.get(k)]
        fields["catalogue_source"] = (_f(MATCH, s, "catalogue observed with hash") if not miss else
                                      _f(ABSENT, s, "catalogue provenance lacks %s" % miss))
    if rep not in REPRESENTATIONS:
        fields["canonical_source_url"] = _f(CONFLICT, rep, "unknown representation %r" % rep)

    sample = vol = scan = None
    if not is_token(token):
        why = ("no 14-digit lookup token (%r)" % token if not token else
               "%r is not a 14-digit volume token" % token)
        for k in ("canonical_scroll", "scan_id", "canonical_source_url", "pitch_um", "energy_kev"):
            fields[k] = _f(ABSENT, why=why)
    elif catalogue is not None and catalogue.samples:
        matches = catalogue.token_matches(token)
        out["token_matches"] = matches
        vols = [m for m in matches if m["kind"] == "VOLUME"]
        if len(matches) != 1 or len(vols) != 1:
            why = ("token %s matches %d catalogue records (%s); resolution needs exactly one "
                   "VOLUME record" % (token, len(matches),
                                      ", ".join("%s %s" % (m["kind"], m["sample"])
                                                for m in matches) or "none"))
            st = ABSENT if not matches else CONFLICT
            for k in ("canonical_scroll", "scan_id", "canonical_source_url", "pitch_um",
                      "energy_kev"):
                fields[k] = _f(st, why=why)
        else:
            sample = vols[0]["sample"]
            s = catalogue.samples[sample]
            vol = s["volumes"][token]
            scan = (s.get("scans") or {}).get(str(vol.get("scan_id")))

    if sample is not None:
        declared = c.get("scroll")
        if not declared:
            fields["canonical_scroll"] = _f(ABSENT, sample, "the claim declares no scroll")
        elif _key(declared) != _key(sample):
            fields["canonical_scroll"] = _f(CONFLICT, sample, "claim scroll %r but token %s belongs "
                                            "to catalogue sample %r" % (declared, token, sample))
        else:
            fields["canonical_scroll"] = _f(MATCH, sample, "token belongs to sample %s" % sample)

        vprops = vol.get("properties") or {}
        sid = str(vol.get("scan_id") or "")
        if not scan:
            fields["scan_id"] = _f(ABSENT, sid or None, "volume %s names scan %r absent from the "
                                   "catalogue" % (token, sid))
        else:
            sprops = scan.get("properties") or {}
            long_id = str(scan.get("long_id") or "")
            probs = []
            if not long_id.startswith(sid + "-"):
                probs.append("scan long_id %r does not carry scan id %s" % (long_id, sid))
            for k, tol in (("pixel_size_um", PITCH_TOL_UM), ("energy_keV", ENERGY_TOL_KEV)):
                a, b = _num(sprops.get(k)), _num(vprops.get(k))
                if a is None or b is None or abs(a - b) > tol:
                    probs.append("scan %s=%r vs volume %s=%r" % (k, sprops.get(k), k, vprops.get(k)))
            fields["scan_id"] = (_f(MATCH, {"scan_id": sid, "acquisition_id": long_id},
                                    "scan record present and consistent") if not probs else
                                 _f(CONFLICT, {"scan_id": sid, "acquisition_id": long_id},
                                    "; ".join(probs)))
        pitch = _num(vprops.get("pixel_size_um"))
        energy = _num(vprops.get("energy_keV"))
        out["licence"] = (vprops.get("license") or {}).get("name")

        origins = []
        if rep == "VOLUME":
            fields["segment"] = _f(NOT_APPLICABLE, None, "a volume store has no segment")
            origins = [o.get("path") for d in vol.get("data") or [] if d.get("type") == "ome-zarr"
                       for o in d.get("origins") or []]
            src_why = "volume %s ome-zarr origins" % token
        elif rep == "SURFACE_VOLUME":
            want = str(c.get("segment") or "").strip()
            segs = s.get("segments") or {}
            hits = []
            if want:
                for sgid, sg in segs.items():
                    names = {_key(sgid), _key(sg.get("long_id")), _key(sg.get("suffix"))}
                    short = str(sg.get("suffix") or "").split("_")[0]
                    if _key(want) in names or (short and _key(want) == _key(short)):
                        hits.append(sgid)
            if not want:
                fields["segment"] = _f(ABSENT, why="surface-volume claim declares no segment")
            elif len(hits) != 1:
                fields["segment"] = _f(ABSENT if not hits else CONFLICT, hits,
                                       "claim segment %r matches %d catalogue segments of %s"
                                       % (want, len(hits), sample))
            else:
                sg = segs[hits[0]]
                fields["segment"] = _f(MATCH, sg.get("long_id"), "one catalogue segment")
                for d in sg.get("data") or []:
                    if d.get("type") != "layers-zarr":
                        continue
                    for o in d.get("origins") or []:
                        if parse_surface_volume_token(o.get("path")) == token:
                            origins.append(o.get("path"))
            src_why = "segment layers-zarr origins naming volume-%s" % token
        if fields["segment"]["state"] in PASSING:
            if len(origins) != 1:
                fields["canonical_source_url"] = _f(ABSENT if not origins else CONFLICT, origins,
                                                    "%s: %d found, exactly one required"
                                                    % (src_why, len(origins)))
            else:
                url = https_url(origins[0]).rstrip("/")
                dec = c.get("source_url")
                if dec and _norm_url(dec).split(".zarr")[0] != _norm_url(url).split(".zarr")[0]:
                    fields["canonical_source_url"] = _f(CONFLICT, url, "declared source_url %r "
                                                        "is not the official origin %r"
                                                        % (dec, url))
                else:
                    fields["canonical_source_url"] = _f(MATCH, url, src_why)
        else:
            fields["canonical_source_url"] = _f(ABSENT, why="no segment resolved, so no origin")

        de = _num((c.get("declared") or {}).get("energy_kev"))
        if energy is None:
            fields["energy_kev"] = _f(ABSENT, why="catalogue volume declares no energy_keV")
        elif de is not None and abs(de - energy) > ENERGY_TOL_KEV:
            fields["energy_kev"] = _f(CONFLICT, energy, "declared %s keV vs official %s keV"
                                      % (de, energy))
        else:
            fields["energy_kev"] = _f(MATCH, energy, "catalogue volume energy_keV")
        out["_pitch"] = pitch

    zattrs = None
    ap = str(c.get("array_path") if c.get("array_path") is not None else "")
    if fields["canonical_source_url"]["state"] == MATCH:
        base = fields["canonical_source_url"]["value"]
        if cache is None:
            for k in ("array_path", "dimensions_dtype", "metadata_hash"):
                fields[k] = _f(ABSENT, why="official store metadata not read (no metadata cache)")
        else:
            zr, zarray = cache.get_json(base + "/" + (ap + "/" if ap else "") + ".zarray")
            ar, zattrs = cache.get_json(base + "/.zattrs")
            obs = {".zarray": {k: zr.get(k) for k in ("url", "observed_utc", "http_status",
                                                      "sha256", "record_path")},
                   ".zattrs": {k: ar.get(k) for k in ("url", "observed_utc", "http_status",
                                                      "sha256", "record_path")}}
            out["metadata_observations"] = obs
            if not isinstance(zarray, dict):
                fields["array_path"] = _f(ABSENT, ap, "official %s/.zarray not readable (HTTP %s)"
                                          % (ap or "<root>", zr.get("http_status")), obs)
                fields["dimensions_dtype"] = _f(ABSENT, why="no official .zarray")
                fields["metadata_hash"] = _f(ABSENT, why="no official .zarray bytes to hash")
            else:
                fields["array_path"] = _f(MATCH, ap, "official .zarray present at %r" % ap, obs)
                off = {"shape": zarray.get("shape"), "dtype": zarray.get("dtype"),
                       "chunks": zarray.get("chunks"),
                       "compressor": (zarray.get("compressor") or {}).get("id")
                       if isinstance(zarray.get("compressor"), dict) else zarray.get("compressor")}
                loc = c.get("local")
                if not isinstance(off["shape"], list) or not off["dtype"]:
                    fields["dimensions_dtype"] = _f(ABSENT, off, "official .zarray lacks shape/dtype")
                elif loc is None:
                    fields["dimensions_dtype"] = _f(MATCH, off, "official .zarray (no local copy "
                                                    "claimed)")
                elif list(loc.get("shape") or []) != list(off["shape"]) \
                        or str(loc.get("dtype")) != str(off["dtype"]):
                    fields["dimensions_dtype"] = _f(CONFLICT, off, "local shape %s dtype %s != "
                                                    "official shape %s dtype %s"
                                                    % (loc.get("shape"), loc.get("dtype"),
                                                       off["shape"], off["dtype"]))
                else:
                    enc = [k for k in ("chunks", "compressor") if loc.get(k) is not None
                           and loc.get(k) != off.get(k)]
                    fields["dimensions_dtype"] = _f(
                      MATCH, dict(off, local_encoding_differs=enc),
                      "local shape and dtype identical to official" +
                      ("; local storage encoding differs in %s (re-encoded copy: encoding is not "
                       "identity)" % enc if enc else ""))
                hashed = {".zarray": zr.get("sha256")}
                if ar.get("http_status") == 200:
                    hashed[".zattrs"] = ar.get("sha256")
                fields["metadata_hash"] = _f(MATCH, {"objects": hashed,
                                                     "combined_sha256": canon_sha(hashed)},
                                             "sha256 of the official metadata bytes read")
    elif fields["canonical_source_url"]["state"] != MATCH:
        for k in ("array_path", "dimensions_dtype", "metadata_hash"):
            if fields[k]["why"] == "not evaluated":
                fields[k] = _f(ABSENT, why="no canonical source URL resolved")

    if sample is not None:
        pitch = out.pop("_pitch", None)
        dp = _num((c.get("declared") or {}).get("pitch_um"))
        if pitch is None:
            fields["pitch_um"] = _f(ABSENT, why="catalogue volume declares no pixel_size_um")
        else:
            factor, fwhy = _level_factor(zattrs, ap or "0")
            level_pitch = pitch * factor if factor else (pitch if (ap or "0") == "0" else None)
            val = {"acquisition_pitch_um": pitch, "array_path": ap or "0",
                   "level_pitch_um": level_pitch, "level_basis": fwhy}
            ok = [pitch] + ([level_pitch] if level_pitch else [])
            base = _ome_base_scale(zattrs)
            if base is not None and base != 1.0 and abs(base - pitch) > PITCH_TOL_UM:
                fields["pitch_um"] = _f(CONFLICT, dict(val, ome_level0_scale=base),
                                        "official OME level-0 scale %s disagrees with the "
                                        "catalogue pixel_size_um %s" % (base, pitch))
            elif dp is not None and not any(abs(dp - x) <= PITCH_TOL_UM for x in ok):
                fields["pitch_um"] = _f(CONFLICT, val, "declared pitch %s um matches neither the "
                                        "official acquisition pitch %s nor level pitch %s"
                                        % (dp, pitch, level_pitch))
            else:
                fields["pitch_um"] = _f(MATCH, val, "catalogue pixel_size_um (scan == volume)")
    out.pop("_pitch", None)

    failing = {k: {"state": v["state"], "why": v["why"]} for k, v in fields.items()
               if v["state"] not in PASSING}
    out["fields"] = fields
    out["failing_fields"] = failing
    out["state"] = PROVEN if not failing else UNKNOWN
    out["proven"] = not failing
    out["resolved_identity"] = None if failing else {
      "canonical_scroll": fields["canonical_scroll"]["value"],
      "volume_id": token, "scan_id": fields["scan_id"]["value"]["scan_id"],
      "acquisition_id": fields["scan_id"]["value"]["acquisition_id"],
      "segment": fields["segment"]["value"],
      "canonical_source_url": fields["canonical_source_url"]["value"],
      "array_path": fields["array_path"]["value"],
      "shape": fields["dimensions_dtype"]["value"]["shape"],
      "dtype": fields["dimensions_dtype"]["value"]["dtype"],
      "pitch_um": fields["pitch_um"]["value"], "energy_kev": fields["energy_kev"]["value"],
      "catalogue_source": fields["catalogue_source"]["value"],
      "metadata_hash": fields["metadata_hash"]["value"]["combined_sha256"],
      "licence": out.get("licence")}
    out["identity_sha256"] = canon_sha(out["resolved_identity"]) if out["proven"] else None
    out["rule"] = ("a token is a lookup key; PROVEN only when every field in REQUIRED_FIELDS "
                   "resolves uniquely and consistently from the official catalogue and official "
                   "metadata; zero, multiple or conflicting -> UNKNOWN, and UNKNOWN refuses")
    return out


def require(claim: dict, *, catalogue, cache) -> dict:
    r = resolve(claim, catalogue=catalogue, cache=cache)
    if not r["proven"]:
        raise OfficialIdentityRefusal(
          "official identity UNKNOWN for %s: %s" % (
            claim.get("control_id") or claim.get("token"),
            "; ".join("%s %s (%s)" % (k, v["state"], v["why"])
                      for k, v in r["failing_fields"].items())), resolution=r)
    return r


def registry_from_resolutions(resolutions) -> dict:
    """A volume_id_gate-shaped registry holding ONLY fully PROVEN resolutions."""
    reg = {}
    for r in resolutions or []:
        if not (r or {}).get("proven"):
            continue
        ri = r["resolved_identity"]
        e = reg.setdefault(_key(ri["canonical_scroll"]), {
          "scroll": ri["canonical_scroll"], "official": set(), "superseded": set(), "sources": []})
        e["official"].add(ri["volume_id"])
        e["sources"].append("%s identity %s" % (CONTRACT, r["identity_sha256"]))
    return reg


REGISTRY_SOURCE = "%s: PROVEN_BY_OFFICIAL_CATALOG_RESOLUTION only" % CONTRACT



SURVEY_FILE = "public_official_survey.json"
SURVEY_SCHEMA = "argus-public-official-survey-v1"
IDENTIFY_CONTRACT = "argus-identify-v1"
_SEGMENT_ON_VOLUME = re.compile(r"-on-(\d{14})-(\d+(?:\.\d+)?)um(?:\.tifxyz)?$")
_STORE_SCROLL = re.compile(r"(?:^|/)([^/]+)/volumes/[^/]+\.zarr/?(?:\?.*)?$")
_NAME_PITCH = re.compile(r"(?<![\d.])(\d+\.\d+)um")
_SURVEY_MEMO: dict = {}


def survey_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1] / SURVEY_FILE


def load_survey(path=None) -> dict | None:
    """The public survey document, or None when it is absent or malformed."""
    p = pathlib.Path(path) if path is not None else survey_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key not in _SURVEY_MEMO:
            doc = json.loads(p.read_text(encoding="utf-8"))
            ok = isinstance(doc, dict) and doc.get("schema") == SURVEY_SCHEMA \
                and isinstance(doc.get("samples"), dict)
            _SURVEY_MEMO.clear()
            _SURVEY_MEMO[key] = doc if ok else None
        return _SURVEY_MEMO[key]
    except (OSError, ValueError):
        return None


def survey_volumes(survey=None) -> list:
    """Every official volume as (scroll, volume_id, row), in a stable order."""
    s = survey if survey is not None else load_survey()
    out = []
    for scroll in sorted((s or {}).get("samples") or {}):
        for vid, row in sorted(s["samples"][scroll].get("volumes", {}).items()):
            out.append((scroll, vid, row))
    return out


def parse_segment_on_volume(name):
    """(volume_token, pitch_um) from a Villa `<segment>-on-<volume id>-<pitch>um[.tifxyz]` name."""
    leaf = str(name or "").strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    m = _SEGMENT_ON_VOLUME.search(leaf)
    return (m.group(1), float(m.group(2))) if m else (None, None)


def _shape_fit(bbox, shape):
    """Does a tifxyz bbox [[x0,y0,z0],[x1,y1,z1]] sit inside an array of shape (z, y, x)?"""
    try:
        lo, hi = bbox
        ext = (shape[2], shape[1], shape[0])
        return all(float(a) >= -0.5 and float(b) <= e + 0.5 for a, b, e in zip(lo, hi, ext))
    except (TypeError, ValueError, IndexError):
        return None


def _volume_ref(scroll, vid, row):
    return {"scroll": scroll, "volume_id": vid, "scan_id": row.get("scan_id"),
            "long_id": row.get("long_id"), "store": row.get("store"),
            "source_url": row.get("source_url"), "pixel_size_um": row.get("pixel_size_um"),
            "energy_kev": row.get("energy_kev"), "array": row.get("array"),
            "prizes": row.get("prizes") or [], "checked_at": row.get("checked_at")}


def _scroll_key(s):
    """Canonical scroll id for a hint; raises KeyError for a string that is not a scroll."""
    from argus.core import scroll_ids
    return scroll_ids.resolve(s)


def identify_volume(evidence: dict, *, survey=None) -> dict:
    """Infer WHICH official volume a piece of data is, from the evidence in the data itself."""
    surv = survey if survey is not None else load_survey()
    used, contradictions, reasons = [], [], []
    res = {"contract": IDENTIFY_CONTRACT, "state": "REFUSED", "confidence": None, "identity": None,
           "evidence_used": used, "contradictions": contradictions, "reasons": reasons,
           "survey": None}
    if not surv:
        reasons.append("the public official survey is not installed, so nothing can be identified "
                       "(run scripts/refresh_official_survey.py)")
        return res
    res["survey"] = {"checked_at": surv.get("checked_at"),
                     "catalogue_sha256": ((surv.get("sources") or {}).get("catalogue") or {}).get("sha256")}
    vols = survey_volumes(surv)
    by_vid = {}
    for scroll, vid, row in vols:
        by_vid.setdefault(vid, []).append((scroll, row))
    scans = {}
    for scroll, s in (surv.get("samples") or {}).items():
        for sid in s.get("scans") or {}:
            scans.setdefault(sid, []).append(scroll)

    tokens, name_pitches = {}, []
    hints = list(evidence.get("scroll_hints") or [])
    for n in evidence.get("names") or []:
        text, kind = str(n.get("text") or ""), n.get("kind") or "name"
        tok = parse_volume_store_token(text)
        pitch = None
        if tok is None:
            tok, pitch = parse_segment_on_volume(text)
        if tok is None and kind == "url":
            tok = parse_surface_volume_token(text)
        m_scroll = _STORE_SCROLL.search(text.replace("\\", "/"))
        if m_scroll and tok:
            hints.append({"kind": "the scroll in the %s path" % kind, "text": m_scroll.group(1)})
        if tok:
            tokens.setdefault(tok, []).append(kind)
            used.append({"evidence": "volume token in %s" % kind, "value": tok, "text": text[-160:]})
        m = _NAME_PITCH.search(text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])
        if pitch is None and m:
            pitch = float(m.group(1))
        if pitch is not None:
            name_pitches.append((kind, pitch))
    if len(tokens) > 1:
        contradictions.append("the data names more than one volume: %s" % sorted(tokens))
        reasons.append("CONTRADICTORY: the names carried by the data disagree about which volume "
                       "it is (%s)" % ", ".join("%s in %s" % (t, "/".join(k))
                                                for t, k in sorted(tokens.items())))
        return res

    arr = evidence.get("array") or {}
    if not tokens:
        if arr.get("shape"):
            hits = [(s, v, r) for s, v, r in vols
                    if (r.get("array") or {}).get("shape") == list(arr["shape"])
                    and (not arr.get("chunks") or (r.get("array") or {}).get("chunks") == list(arr["chunks"]))
                    and (not arr.get("dtype") or (r.get("array") or {}).get("dtype") == arr["dtype"])]
            for key, want in (("pitch_um", "pixel_size_um"), ("energy_kev", "energy_kev")):
                if evidence.get(key) is not None:
                    tol = PITCH_TOL_UM if key == "pitch_um" else ENERGY_TOL_KEV
                    hits = [h for h in hits if h[2].get(want) is not None
                            and abs(float(h[2][want]) - float(evidence[key])) <= tol]
            if len(hits) == 1:
                s, v, r = hits[0]
                used.append({"evidence": "array shape/chunks/dtype match exactly one official volume",
                             "value": list(arr["shape"])})
                res.update(state="IDENTIFIED", confidence="MEDIUM", identity=_volume_ref(s, v, r),
                           basis="ARRAY_HEADER_ONLY")
                return res
            reasons.append("AMBIGUOUS: the array header %s fits %d official volumes%s; a shape that "
                           "does not single out one volume is not an identity"
                           % (list(arr["shape"]), len(hits),
                              " (%s)" % ", ".join("%s/%s" % (h[0], h[1]) for h in hits[:6]) if hits else ""))
            return res
        if evidence.get("bbox"):
            reasons.append("NO_VOLUME_NAMED: a segment extent alone fits many volumes and names none. "
                           "Supply volume_source.txt, or a segment name of the form "
                           "<id>-on-<volume id>-<pitch>um, or attest the volume with attested_by.")
        else:
            reasons.append("NO_EVIDENCE: no volume id, array header or extent was found in the data")
        return res

    (token, kinds), = tokens.items()
    matches = by_vid.get(token, [])
    if not matches:
        if token in scans:
            reasons.append("NOT_A_VOLUME_ID: %s is an acquisition (scan) id of %s, not a volume id; "
                           "its volumes are %s" % (token, "/".join(scans[token]),
                                                    sorted(v for s, v, r in vols if r.get("scan_id") == token)))
        else:
            reasons.append("UNKNOWN_VOLUME: %s is not an official volume id in the public survey "
                           "(checked %s); it may be new upstream (re-run scripts/refresh_official_survey.py) "
                           "or wrong" % (token, surv.get("checked_at")))
        return res
    if len(matches) > 1:
        reasons.append("AMBIGUOUS: %s is listed for several scrolls: %s"
                       % (token, ", ".join(s for s, _ in matches)))
        return res
    scroll, row = matches[0]
    corroboration = []

    def ok(what, value):
        corroboration.append(what)
        used.append({"evidence": what, "value": value})

    def bad(msg):
        contradictions.append(msg)
        reasons.append("CONTRADICTORY: " + msg)

    for kind, p in name_pitches:
        if row.get("pixel_size_um") is None:
            continue
        if abs(float(row["pixel_size_um"]) - p) <= PITCH_TOL_UM:
            ok("pitch in the %s matches the official volume" % kind, p)
        else:
            bad("the %s says %.3f um but official volume %s is %s um" % (kind, p, token, row["pixel_size_um"]))
    if evidence.get("pitch_um") is not None and row.get("pixel_size_um") is not None:
        if abs(float(row["pixel_size_um"]) - float(evidence["pitch_um"])) <= PITCH_TOL_UM:
            ok("declared voxel size matches the official volume", evidence["pitch_um"])
        else:
            bad("declared voxel size %s um but official volume %s is %s um"
                % (evidence["pitch_um"], token, row["pixel_size_um"]))
    if evidence.get("energy_kev") is not None and row.get("energy_kev") is not None:
        if abs(float(row["energy_kev"]) - float(evidence["energy_kev"])) <= ENERGY_TOL_KEV:
            ok("declared energy matches the official volume", evidence["energy_kev"])
        else:
            bad("declared energy %s keV but official volume %s is %s keV"
                % (evidence["energy_kev"], token, row["energy_kev"]))
    off = row.get("array") or {}
    if arr.get("shape") and off.get("shape"):
        if list(arr["shape"]) == list(off["shape"]):
            ok("array shape equals the official level-0 shape", list(arr["shape"]))
        else:
            bad("array shape %s differs from the official %s" % (list(arr["shape"]), off["shape"]))
    if evidence.get("bbox") and off.get("shape"):
        fit = _shape_fit(evidence["bbox"], off["shape"])
        if fit:
            ok("segment extent lies inside the official volume", evidence["bbox"])
        elif fit is False:
            bad("the segment extent %s does not fit inside official volume %s (shape z,y,x %s)"
                % (evidence["bbox"], token, off["shape"]))
    for h in hints:
        txt = str(h.get("text") or "")
        try:
            hinted = _scroll_key(txt)
        except KeyError:
            continue
        if hinted == _scroll_key(scroll):
            ok("%s names the same scroll as the volume" % h.get("kind"), txt)
        else:
            bad("%s names %s but volume %s belongs to %s" % (h.get("kind"), txt, token, scroll))
    if contradictions:
        return res
    declared_by_file = any(k in ("volume_source_file", "url") for k in kinds)
    if not corroboration and not declared_by_file:
        reasons.append("UNCORROBORATED: the name says %s but nothing else in the data agrees (no "
                       "pitch, array header or extent to compare)" % token)
        return res
    independent = [c for c in corroboration if "extent" in c or "array shape" in c or "energy" in c]
    strong = len(corroboration) >= 2 and (declared_by_file or bool(independent))
    res.update(state="IDENTIFIED", confidence="HIGH" if strong else "MEDIUM",
               identity=_volume_ref(scroll, token, row),
               basis="DECLARED_" + "_".join(sorted(set(k.upper() for k in kinds))),
               corroboration=corroboration)
    return res
