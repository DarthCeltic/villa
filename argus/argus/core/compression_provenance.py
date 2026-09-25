"""Compression provenance as a detector preflight gate."""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

COMPRESSION_TOKENS = ("volcomp", "q8", "q4", "quant", "jpegxl", "jxl", "lossy")

RESOLVED_ORIGINAL = "ORIGINAL_UNCOMPRESSED"
RESOLVED_COMPRESSED = "COMPRESSED_VARIANT"
UNRESOLVED = "UNRESOLVED"


class CompressionPreflightRefusal(RuntimeError):
    """Raised instead of returning a score computed under undeclared conditions."""


@dataclass(frozen=True)
class ModelCompressionPolicy:
    """What one checkpoint declares about the volumes it can legitimately consume."""

    key: str
    accepts: tuple
    requires_resolution: bool
    adapted_checkpoint: str | None = None
    evidence: str = ""


POLICIES = {
  "hengck23_9th/fold1-Resnet34MeanPool": ModelCompressionPolicy(
      key="hengck23_9th/fold1-Resnet34MeanPool",
      accepts=(RESOLVED_ORIGINAL,),
      requires_resolution=True,
      adapted_checkpoint=None,
      evidence="trained on the detached Kaggle fragment surface volumes as published for that "
               "competition. No compression-adapted variant of this checkpoint exists in the "
               "ARGUS registry, so there is nothing to route to: compressed or unresolved "
               "input is a refusal, and a run made anyway must be labelled "
               "COMPRESSED_VOLUME_TRANSFER rather than detector transfer."),
}


def classify_source(url: str | None, zarray: dict | None) -> dict:
    """Two axes, never collapsed: what the container is, and what the source was."""
    zarray = zarray or {}
    comp = zarray.get("compressor")
    filt = zarray.get("filters")
    container = ("UNCOMPRESSED_AS_STORED" if comp is None and not filt
                 else "CONTAINER_COMPRESSED")
    hits = sorted(t for t in COMPRESSION_TOKENS if t in (url or "").lower())
    source = RESOLVED_COMPRESSED if hits else UNRESOLVED
    return {
      "container": container,
      "dtype": zarray.get("dtype"),
      "compressor": comp,
      "codec_parameters": comp if isinstance(comp, dict) else None,
      "filters": filt,
      "source": source,
      "tokens_found": hits,
      "note": ("an uncompressed container does NOT establish an uncompressed source. "
               "Resolving the source axis needs the publisher's own metadata."),
    }


def from_store(store_dir) -> dict:
    """Read provenance from a sealed local store."""
    d = pathlib.Path(store_dir)
    zarray, url = None, None
    zp = d / ".zarray"
    if zp.is_file():
        try:
            zarray = json.loads(zp.read_text(encoding="utf-8"))
        except ValueError:
            zarray = None
    mp = d / "ACQUIRE_MANIFEST.json"
    if mp.is_file():
        try:
            url = json.loads(mp.read_text(encoding="utf-8")).get("url")
        except ValueError:
            url = None
    out = classify_source(url, zarray)
    out["store"] = str(d)
    out["url"] = url
    return out


def preflight(model_key: str, provenance: dict, *, raise_on_refusal: bool = True) -> dict:
    """ALLOW, ROUTE or REFUSE, with the reason stated in the returned record."""
    pol = POLICIES.get(model_key)
    if pol is None:
        verdict = {
          "verdict": "REFUSE", "reason": "NO_DECLARED_POLICY",
          "detail": ("%r has no declared compression policy. A default would be a scientific "
                     "decision made by a lookup table, so it is refused instead. Declare the "
                     "model's policy with its evidence." % model_key),
          "model": model_key, "provenance": provenance}
    else:
        src = provenance.get("source")
        if src in pol.accepts:
            verdict = {"verdict": "ALLOW", "reason": "PROVENANCE_COMPATIBLE",
                       "detail": "source %s is accepted by %s" % (src, model_key),
                       "model": model_key, "provenance": provenance}
        elif src == UNRESOLVED and pol.requires_resolution:
            verdict = {
              "verdict": "REFUSE", "reason": "PROVENANCE_UNRESOLVED",
              "detail": ("%s requires resolved provenance and the source axis is UNRESOLVED. "
                         "Unresolved is NOT clean: an uncompressed container is exactly what a "
                         "lossy quantisation looks like once written. Proceeding would turn "
                         "'we could not tell' into 'it was fine'." % model_key),
              "model": model_key, "provenance": provenance,
              "if_run_anyway": "the result must be labelled COMPRESSED_VOLUME_TRANSFER"
                               "_UNRESOLVED and may not be reported as detector transfer."}
        elif pol.adapted_checkpoint:
            verdict = {"verdict": "ROUTE", "reason": "COMPRESSION_ADAPTED_CHECKPOINT",
                       "detail": "route to %s for source %s" % (pol.adapted_checkpoint, src),
                       "route_to": pol.adapted_checkpoint,
                       "model": model_key, "provenance": provenance}
        else:
            verdict = {
              "verdict": "REFUSE", "reason": "INCOMPATIBLE_NO_ADAPTED_CHECKPOINT",
              "detail": ("%s does not accept source %s and no compression-adapted checkpoint "
                         "is registered, so there is nothing to route to." % (model_key, src)),
              "model": model_key, "provenance": provenance}

    verdict["policy_evidence"] = pol.evidence if pol else None
    if verdict["verdict"] == "REFUSE" and raise_on_refusal:
        raise CompressionPreflightRefusal(json.dumps(verdict, indent=1))
    return verdict
