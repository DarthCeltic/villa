"""One atomic, numpy-aware receipt writer."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any

import numpy as np


def plain(o: Any):
    """JSON `default` that unwraps numpy scalars and arrays, and refuses anything else."""
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (pathlib.Path, os.PathLike)):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError("refusing to guess a JSON encoding for %r" % type(o))


def write_json(obj: Any, path, *, indent: int = 1) -> pathlib.Path:
    """Write `obj` atomically."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(obj, indent=indent, default=plain) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    return p


def sha_file(path) -> str:
    """sha256 of a file, streamed."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
