"""Read single voxels from an uncompressed zarr v2 store over HTTP range requests."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core.contracts import Refusal

UA = {"User-Agent": "argus/1.0 (+https://github.com/Cinder-Covenant/ARGUS)"}


class ZarrRangeReader:
    """A callable `(z, y, x) -> float` plus the chunk bookkeeping the gate needs."""

    def __init__(self, base_url: str, *, level: int = 0, timeout: int = 30,
                 opener=None):
        self.base = base_url.rstrip("/")
        self.level = int(level)
        self.timeout = timeout
        self._open = opener or (self._file if self.base.startswith("file://") else self._http)
        self.requests = 0
        self.missing_chunks: set[str] = set()
        self.present_chunks: set[str] = set()
        meta = json.loads(self._open("%s/%d/.zarray" % (self.base, self.level)).decode())
        self._check(meta)
        self.meta = meta
        self.shape = tuple(int(v) for v in meta["shape"])
        self.chunks = tuple(int(v) for v in meta["chunks"])
        self.fill = 0 if meta.get("fill_value") is None else float(meta["fill_value"])
        self.sep = meta.get("dimension_separator", ".")

    @staticmethod
    def _check(m):
        why = []
        if m.get("compressor") is not None:
            why.append("the store is compressed, so a byte offset is not a voxel")
        if m.get("filters"):
            why.append("the store has filters")
        if m.get("order", "C") != "C":
            why.append("the store is not C-ordered")
        if m.get("dtype") not in ("|u1", "|i1", "u1", "i1"):
            why.append("dtype %r is not one byte per element" % m.get("dtype"))
        if len(m.get("shape", [])) != 3:
            why.append("only 3-D stores are supported")
        if why:
            raise Refusal("REPRESENTATION",
                          "cannot address single voxels by byte offset: " + "; ".join(why)
                          + ". Guessing an offset returns a real byte from the wrong place, "
                            "which is worse than failing")

    def _http(self, url, byte_range=None):
        h = dict(UA)
        if byte_range:
            h["Range"] = "bytes=%d-%d" % byte_range
        self.requests += 1
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=self.timeout) as resp:
            return resp.read(2)

    def _file(self, url, byte_range=None):
        """Read a byte range from a local `file://` store -- no network at all."""
        self.requests += 1
        path = Path(urllib.request.url2pathname(url[len("file://"):]))
        if not path.is_file():
            raise urllib.error.HTTPError(url, 404, "no such chunk file", None, None)
        with open(path, "rb") as fh:
            if byte_range:
                fh.seek(byte_range[0])
                return fh.read(byte_range[1] - byte_range[0] + 1)
            return fh.read()

    def chunk_id(self, z, y, x) -> str:
        cz, cy, cx = self.chunks
        return self.sep.join((str(int(z) // cz), str(int(y) // cy), str(int(x) // cx)))

    def __call__(self, z, y, x) -> float:
        z, y, x = int(z), int(y), int(x)
        if not all(0 <= v < s for v, s in zip((z, y, x), self.shape)):
            return self.fill
        cz, cy, cx = self.chunks
        cid = self.chunk_id(z, y, x)
        off = ((z % cz) * cy + (y % cy)) * cx + (x % cx)
        url = "%s/%d/%s" % (self.base, self.level, cid)
        try:
            b = self._open(url, (off, off))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 416):
                self.missing_chunks.add(cid)
                return self.fill
            raise Refusal("DATA_UNAVAILABLE",
                          "chunk %s returned HTTP %d; a failed read is not an empty voxel"
                          % (cid, e.code))
        if len(b) != 1:
            raise Refusal("DATA_UNAVAILABLE",
                          "range request for chunk %s returned %d bytes, not 1; the server "
                          "ignored the Range header and the byte cannot be trusted"
                          % (cid, len(b)))
        self.present_chunks.add(cid)
        return float(b[0])

    def report(self) -> dict:
        return {"level": self.level, "shape": list(self.shape), "chunks": list(self.chunks),
                "fill_value": self.fill, "range_requests": self.requests,
                "chunks_present": len(self.present_chunks),
                "chunks_missing": sorted(self.missing_chunks)[:16],
                "chunks_missing_count": len(self.missing_chunks)}
