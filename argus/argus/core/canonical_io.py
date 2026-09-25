"""The canonical upstream model's INPUT CONTRACT, in one place, derived from declarations."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import numpy as np

MODEL_PITCH_UM = 2.403
FRAG_PITCH_UM = 3.24
MODEL_PATCH_PX = 256
MODEL_DEPTH = 62
CLIP_MAX = 200
OUT_SCALE = 4
OUT_PX = MODEL_PATCH_PX // OUT_SCALE

TILE_PX = int(round(MODEL_PATCH_PX * MODEL_PITCH_UM / FRAG_PITCH_UM))
SRC_DEPTH = int(round(MODEL_DEPTH * MODEL_PITCH_UM / FRAG_PITCH_UM))

CKPT = _argus_public_path('legacy', 'models/canonical/model.ckpt')
CKPT_SHA = ""


def to_uint8(a: np.ndarray) -> np.ndarray:
    """16-bit CT plane to the 8 bits upstream's reader produces."""
    a = np.asarray(a)
    return (a >> 8).astype(np.uint8) if a.dtype == np.uint16 else a.astype(np.uint8)


def to_model_tile(tile_u8: np.ndarray, spine) -> np.ndarray:
    """(D, TILE_PX, TILE_PX) uint8 -> (MODEL_DEPTH, 256, 256) float32 in the model's contract."""
    a = np.clip(np.asarray(tile_u8, dtype=np.float32), 0, CLIP_MAX) / float(CLIP_MAX)
    d, h, w = a.shape
    z = a.transpose(1, 2, 0).reshape(h * w, d, 1)
    z = spine.resample_image(z, FRAG_PITCH_UM, MODEL_PITCH_UM)
    a = z[:, :, 0].reshape(h, w, -1).transpose(2, 0, 1)
    a = spine.resample_image(a, FRAG_PITCH_UM, MODEL_PITCH_UM)
    return fit_exact(np.ascontiguousarray(a, dtype=np.float32), MODEL_DEPTH, MODEL_PATCH_PX)


def fit_exact(a: np.ndarray, depth: int, side: int) -> np.ndarray:
    """Rounding can land a resample one sample off the declared patch."""
    if a.shape == (depth, side, side):
        return a
    d, h, w = a.shape
    out = np.zeros((depth, side, side), dtype=np.float32)
    dd, hh, ww = min(d, depth), min(h, side), min(w, side)
    out[:dd, :hh, :ww] = a[:dd, :hh, :ww]
    if dd < depth:
        out[dd:] = out[dd - 1]
    if hh < side:
        out[:, hh:] = out[:, hh - 1:hh]
    if ww < side:
        out[:, :, ww:] = out[:, :, ww - 1:ww]
    return out


def needs_fit(a: np.ndarray) -> bool:
    return a.shape != (MODEL_DEPTH, MODEL_PATCH_PX, MODEL_PATCH_PX)


def load_window(surface_dir, window, reverse: bool = False) -> np.ndarray:
    """The located depth window as uint8, in the order the model will see it."""
    import tifffile
    files = sorted(surface_dir.glob("*.tif"))
    lo, hi = window
    sel = files[lo:hi]
    if reverse:
        sel = sel[::-1]
    first = to_uint8(tifffile.imread(sel[0]))
    out = np.empty((len(sel),) + first.shape, dtype=np.uint8)
    out[0] = first
    for i, f in enumerate(sel[1:], start=1):
        out[i] = to_uint8(tifffile.imread(f))
    return out


def load_released_model(torch, *, expect_sha: str = CKPT_SHA, with_norm: bool | None = None):
    """The released checkpoint, strict-loaded, with the safe unpickler."""
    import hashlib
    from pathlib import Path
    from model_resnet3d_3d_decoder import RegressionModel
    p = Path(CKPT)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
    if not expect_sha or h.hexdigest() != expect_sha:
        raise RuntimeError("checkpoint %s does not match the pinned sha256; set CKPT_SHA to "
                           "the checkpoint you verified" % h.hexdigest()[:16])
    ck = torch.load(str(p), map_location="cpu", weights_only=True)
    sd = {}
    for k, v in ck["state_dict"].items():
        while k.startswith("model.") or k.startswith("module."):
            k = k.split(".", 1)[1]
        sd[k] = v
    norm = any(k.startswith("normalization.") for k in sd) if with_norm is None else with_norm
    model = RegressionModel(with_norm=norm)
    model.load_state_dict(sd, strict=True)
    return model, {"sha256": h.hexdigest(), "epoch": ck.get("epoch"),
                   "global_step": ck.get("global_step"), "with_norm": norm,
                   "strict": True}
