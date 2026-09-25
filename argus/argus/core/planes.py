"""Fixed-contrast crops of staged surface volumes, for looking at what is actually there."""
from __future__ import annotations

import io
from pathlib import Path

from argus.core import paths, storage_catalog

DEFAULT_FRAGS = paths.science_data("fragments")
FRAGS = DEFAULT_FRAGS

CLIP_MAX = 200

MAX_SIDE = 1024
MAX_PIXELS = 1024 * 1024

MAX_ZOOM = 16
MAX_READ_PIXELS = 32 * 1024 * 1024


class PlaneRefusal(Exception):
    pass


def staged() -> list:
    if Path(FRAGS) != Path(DEFAULT_FRAGS):
        root = Path(FRAGS)
        return sorted(p.name for p in root.iterdir()
                      if p.is_dir() and (p / "surface_volume").is_dir()) \
            if root.is_dir() else []
    roots = storage_catalog.local_fragment_paths(fallback_root=FRAGS)
    return sorted(name for name, root in roots.items()
                  if (root / "surface_volume").is_dir())


def fragment_path(fragment: str) -> Path:
    """The conveyor-owned location for one fragment, whether present or restorable."""
    if Path(FRAGS) != Path(DEFAULT_FRAGS):
        return Path(FRAGS) / fragment
    records = {row["member"]: row for row in storage_catalog.fragments(
        fallback_root=FRAGS)}
    record = records.get(fragment)
    if record and record.get("path"):
        return Path(record["path"])
    return Path(FRAGS) / fragment


def plane_count(fragment: str) -> int:
    d = fragment_path(fragment) / "surface_volume"
    return len(sorted(d.glob("*.tif"))) if d.is_dir() else 0


def crop(fragment: str, plane: int, y: int, x: int, h: int, w: int, *,
         auto: bool = False, reverse: bool = False, zoom: int = 1) -> dict:
    """One fixed-contrast crop, as PNG bytes plus what it is."""
    import numpy as np
    import tifffile
    from PIL import Image

    if fragment not in staged():
        raise PlaneRefusal(
            "%r is not a staged fragment. Staged: %s. Nothing outside this list is "
            "reachable from here" % (fragment, staged()))
    files = sorted((fragment_path(fragment) / "surface_volume").glob("*.tif"))
    if not files:
        raise PlaneRefusal("%s has no surface volume" % fragment)
    if reverse:
        files = files[::-1]
    if not (0 <= plane < len(files)):
        raise PlaneRefusal("plane %d is outside 0..%d" % (plane, len(files) - 1))
    h = max(1, min(int(h), MAX_SIDE))
    w = max(1, min(int(w), MAX_SIDE))
    if h * w > MAX_PIXELS:
        raise PlaneRefusal("a crop of %dx%d exceeds the %d-pixel cap" % (h, w, MAX_PIXELS))
    zoom = max(1, min(int(zoom), MAX_ZOOM))
    rh, rw = h * zoom, w * zoom
    if rh * rw > MAX_READ_PIXELS:
        raise PlaneRefusal(
            "zoom %d over %dx%d would read %d pixels, above the %d cap"
            % (zoom, h, w, rh * rw, MAX_READ_PIXELS))

    arr = tifffile.memmap(str(files[plane]), mode="r")
    H, W = arr.shape[-2:]
    y = max(0, min(int(y), max(0, H - rh)))
    x = max(0, min(int(x), max(0, W - rw)))
    field_fits = rh <= H and rw <= W
    sub = np.array(arr[y:y + rh, x:x + rw])
    del arr
    if zoom > 1:
        bh = (sub.shape[0] // zoom) * zoom
        bw = (sub.shape[1] // zoom) * zoom
        if bh and bw:
            sub = (sub[:bh, :bw].astype(np.float32)
                   .reshape(bh // zoom, zoom, bw // zoom, zoom).mean(axis=(1, 3)))

    if sub.dtype == np.float32:
        u8 = (sub / 256.0).astype(np.uint8)
    elif sub.dtype.kind == "u" and sub.dtype.itemsize == 2:
        u8 = (sub >> 8).astype(np.uint8)
    else:
        u8 = sub.astype(np.uint8)
    if auto:
        lo, hi = int(np.percentile(u8, 1)), int(np.percentile(u8, 99))
        hi = max(hi, lo + 1)
        shown = np.clip((u8.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
        contrast = {"mode": "auto", "low": lo, "high": hi,
                    "comparable": False,
                    "why": ("an auto stretch is chosen from THIS crop, so two auto crops are "
                            "not comparable and a shape that appears only here may be a "
                            "property of the stretch")}
    else:
        shown = np.clip(u8.astype(np.float32), 0, CLIP_MAX) * (255.0 / CLIP_MAX)
        contrast = {"mode": "fixed", "clip_max": CLIP_MAX, "comparable": True,
                    "why": ("the detector's own window, so the picture and the model agree "
                            "about what they are looking at")}

    buf = io.BytesIO()
    Image.fromarray(shown.astype(np.uint8), mode="L").save(buf, format="PNG")
    return {
        "png": buf.getvalue(),
        "fragment": fragment, "plane": plane, "n_planes": len(files),
        "depth_order": "reversed" if reverse else "forward",
        "y": y, "x": x, "h": sub.shape[0], "w": sub.shape[1],
        "zoom": zoom,
        "field_of_view": "%dx%d source pixels averaged to %dx%d"
                         % (rh, rw, sub.shape[0], sub.shape[1]),
        "source_file": files[plane].name,
        "source_dtype": str(sub.dtype),
        "eight_bit_rule": "a >> 8, the same shift upstream's cv2.imread(path, 0) performs",
        "contrast": contrast,
        "plane_shape": {"h": H, "w": W},
        "field_available": field_fits,
        "field_available_why": (
            "the full requested field fits inside this plane" if field_fits else
            "the requested field is %dx%d source pixels; this plane is only %dx%d, so the "
            "field was NOT read in full -- the image below is narrower or shorter than "
            "requested, not a defect in what you are looking at" % (rh, rw, H, W)),
        "reproduce": ("argus.core.planes.crop(%r, %d, %d, %d, %d, %d, auto=%s, reverse=%s)"
                      % (fragment, plane, y, x, sub.shape[0], sub.shape[1], auto, reverse)),
        "not_reachable_from_here": ["any held-out fragment", "any unread target", "any sealed run root"],
    }
