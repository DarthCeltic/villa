"""One gate at the ink-stage socket."""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

from argus.core import paths

CONTRACT = "argus-ink-socket-gate-v1"

MECHANICS_ONLY = "MECHANICS_ONLY"

REFUSED = "REFUSED"
PERMITTED = "PERMITTED"

TARGET_SOURCE_PARTS = ("first_letters_acquisition", "FIRST_LETTERS_ACQUISITION.json")

FIRST_LETTERS = "FIRST_LETTERS"
GRAND_PRIZE_PREFIX = "GRAND_PRIZE"


VERIFIABLE_TARGET_AUTHORITY_TYPES: tuple = ()


def verify_target_authority(scroll, authority) -> dict:
    """The only path by which a prize target could be admitted."""
    reasons = []
    if authority is None:
        reasons.append("NO_TARGET_AUTHORITY: none was presented")
    elif not isinstance(authority, dict):
        reasons.append("MALFORMED_TARGET_AUTHORITY: %s is not a packet" % type(authority).__name__)
    else:
        t = authority.get("packet_type")
        if t not in VERIFIABLE_TARGET_AUTHORITY_TYPES:
            reasons.append(
              "UNVERIFIABLE_TARGET_AUTHORITY_TYPE: %r. Verifiable types: %s. No target-authority "
              "packet type is registered in this build."
              % (t, list(VERIFIABLE_TARGET_AUTHORITY_TYPES) or "NONE"))
        if _key(authority.get("scroll")) != _key(scroll):
            reasons.append("TARGET_AUTHORITY_SCROLL_MISMATCH: packet names %r, socket has %r"
                           % (authority.get("scroll"), scroll))
    if not VERIFIABLE_TARGET_AUTHORITY_TYPES:
        reasons.append("TARGET_AUTHORITY_REGISTRY_EMPTY: this verifier admits nothing")
    return {"verified": False if reasons else True, "reasons": reasons,
            "verifiable_types": list(VERIFIABLE_TARGET_AUTHORITY_TYPES)}


def verify_target_acquisition_authority(scroll, authority, *, stage, plan=None) -> dict:
    """Acquisition-side verifier."""
    from argus.core import target_acquisition_packet as TAP
    st = _norm(stage).lower()
    if st in TAP.FORBIDDEN_STAGES or st.startswith("ink"):
        return {"verified": False, "reasons": ["INK_OR_FORBIDDEN_STAGE_ON_TARGET: %r is never "
                                               "authorised by a target-acquisition packet" % stage],
                "verifiable_types": [TAP.PACKET_TYPE]}
    v = TAP.verify_for_stage(authority, scroll=scroll, stage=st, plan=plan)
    return dict(v, verifiable_types=[TAP.PACKET_TYPE])


class InkSocketRefusal(RuntimeError):
    """Raised after the REFUSED receipt is on disk."""

    def __init__(self, message: str, receipt_path=None, record=None):
        super().__init__(message)
        self.receipt_path = receipt_path
        self.record = record


def _norm(s) -> str:
    return str(s or "").strip()


def _key(s) -> str:
    """Physical identity comparison: case- and separator-insensitive, never alias-merging."""
    return _norm(s).upper().replace("_", "").replace("-", "").replace(" ", "")


def _is_target_prize(p) -> bool:
    p = _norm(p).upper()
    return p == FIRST_LETTERS or p.startswith(GRAND_PRIZE_PREFIX)


def _public_registry_targets() -> dict:
    """{title: sorted target prizes} from the packaged public registry, or {} if unreadable."""
    p = pathlib.Path(__file__).resolve().parent.parent / "public_target_registry.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("targets") or []
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for r in rows:
        prizes = [x for x in ((r or {}).get("prizes") or []) if _is_target_prize(x)]
        if _norm((r or {}).get("scroll")) and prizes:
            out[_norm(r["scroll"])] = sorted(prizes)
    return out


def frozen_targets(source=None) -> dict:
    """Prize targets from the frozen acquisition survey."""
    p = pathlib.Path(source) if source is not None else paths.find_artifact(*TARGET_SOURCE_PARTS)
    out = {"source": str(p), "sha256": None, "state": "UNKNOWN", "targets": {}, "why": None}
    try:
        raw = p.read_bytes()
    except OSError as e:
        if source is None:
            pub = _public_registry_targets()
            if pub:
                out.update(state="READ", targets=pub, source="argus/public_target_registry.json",
                           source_kind="PUBLIC_TARGET_REGISTRY",
                           why="frozen survey unreadable (%s); using the packaged public registry" % e)
                return out
        out["why"] = "frozen target source unreadable: %s" % e
        return out
    out["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode("utf-8"))
        rows = doc["rows"]
    except (ValueError, KeyError, TypeError) as e:
        out["why"] = "frozen target source does not parse as the acquisition survey: %s" % e
        return out
    targets = {}
    for r in rows:
        title = _norm((r or {}).get("physical_title"))
        prizes = [x for x in ((r or {}).get("prizes") or []) if _is_target_prize(x)]
        if title and prizes:
            targets[title] = sorted(prizes)
    declared = doc.get("targets")
    if not targets or (isinstance(declared, int) and declared != len(rows)):
        out["why"] = ("the survey declares %r targets and yielded %d named prize rows; a target "
                      "list that disagrees with itself is not a list" % (declared, len(targets)))
        return out
    out.update(state="READ", targets=targets)
    return out


def live_targets() -> dict:
    """Second net: the control matrix, which is EMPTY when the service is down."""
    try:
        from argus.core import control_matrix as CM
        rec = CM.as_record()
        rows = rec.get("rows") or []
    except Exception as e:
        return {"state": "UNKNOWN", "targets": {}, "rows": 0,
                "why": "%s: %s" % (type(e).__name__, e)}
    t = {}
    for r in rows:
        prizes = []
        if r.get("eligible_first_letters"):
            prizes.append(FIRST_LETTERS)
        if r.get("eligible_grand_prize"):
            prizes.append(GRAND_PRIZE_PREFIX)
        if prizes:
            t[_norm(r.get("scroll"))] = prizes
    return {"state": "READ" if rows else "EMPTY", "targets": t, "rows": len(rows),
            "why": None if rows else "the matrix returned zero rows (service down?); it is a "
                                     "second net only and its emptiness permits nothing"}


def _write(record: dict, receipt_dir) -> pathlib.Path:
    from argus.core import receipts
    d = pathlib.Path(receipt_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, default=str)
                            .encode("utf-8")).hexdigest()[:12]
    name = "INK_SOCKET_%s_%s_%s_%s.json" % (record["verdict"], _key(record["scroll"]) or "NONE",
                                           stamp, digest)
    p = d / name
    if p.exists():
        raise InkSocketRefusal("receipt %s already exists; receipts are append-only" % p)
    record = dict(record, receipt_sha256=hashlib.sha256(
      json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest())
    return receipts.write_json(record, p)


def decide(scroll, *, purpose, stage: str = "ink", frozen=None, live=None,
           target_authority=None, declared_volume_id=None, store_identity=None,
           volume_registry=None, volume_registry_source=None) -> dict:
    """The decision alone, with every reason at once."""
    s, pur = _norm(scroll), _norm(purpose).upper()
    fz = frozen if frozen is not None else frozen_targets()
    lv = live if live is not None else live_targets()
    reasons = []
    if not s:
        reasons.append("UNDECLARED_SCROLL: an ink stage that cannot name its material cannot be "
                       "shown not to be a prize target")
    if not pur:
        reasons.append("UNDECLARED_PURPOSE: an undeclared purpose is the one read as whatever "
                       "the reader hoped for")
    elif pur != MECHANICS_ONLY:
        reasons.append("PURPOSE_ABOVE_CEILING: %r; the ink socket admits %s only" % (pur,
                                                                                    MECHANICS_ONLY))
    if fz.get("state") != "READ":
        reasons.append("TARGET_SET_UNKNOWN: %s. UNKNOWN refuses: an unreadable list of targets is "
                       "not an empty one" % fz.get("why"))
    hits = {}
    for src_name, src in (("frozen_survey", fz), ("live_control_matrix", lv)):
        for t, prizes in (src.get("targets") or {}).items():
            if s and _key(t) == _key(s):
                hits[src_name] = {"listed_as": t, "prizes": prizes}
    from argus.core import volume_id_gate as VG
    volume = VG.verify("INFERENCE", scroll=s or None, declared_volume_id=declared_volume_id,
                       observed_volume_id=(store_identity or {}).get("volume_id")
                       or VG.parse_volume_id_from_url((store_identity or {}).get("source_url")),
                       registry=volume_registry, registry_source=volume_registry_source)
    if not volume["verified"]:
        reasons.append("VOLUME_ID_%s: %s" % (volume["state"], "; ".join(volume["reasons"])))
    authority = None
    if hits:
        authority = verify_target_authority(s, target_authority)
        if not authority["verified"]:
            reasons.append(
              "PRIZE_TARGET_AT_INK_SOCKET: %s is a prize target (%s) and no verified target "
              "authority is present (%s)."
              % (s, "; ".join("%s lists %s" % (k, v["prizes"]) for k, v in sorted(hits.items())),
                 "; ".join(authority["reasons"])))
    return {
      "contract": CONTRACT,
      "stage": stage,
      "scroll": s or None,
      "purpose": pur or None,
      "verdict": REFUSED if reasons else PERMITTED,
      "reasons": reasons,
      "target_hits": hits,
      "target_authority": authority,
      "volume_identity": volume,
      "target_sources": {
        "frozen_survey": {k: fz.get(k) for k in ("source", "sha256", "state", "why")}
                        | {"count": len(fz.get("targets") or {})},
        "live_control_matrix": {k: lv.get(k) for k in ("state", "rows", "why")}
                               | {"count": len(lv.get("targets") or {})},
      },
      "decided_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "permitted_means": "only that this socket does not refuse a mechanics control on non-target "
                         "material. It authorises nothing.",
    }


def enter(scroll, *, purpose, receipt_dir, stage: str = "ink", frozen=None, live=None,
          target_authority=None, declared_volume_id=None, store_identity=None,
          volume_registry=None, volume_registry_source=None) -> dict:
    """THE FIRST LINE OF AN INK STAGE."""
    if receipt_dir is None or not _norm(receipt_dir):
        raise InkSocketRefusal("enter() needs an explicit receipt_dir; a refusal with no receipt "
                               "is indistinguishable from the stage never being called")
    rec = decide(scroll, purpose=purpose, stage=stage, frozen=frozen, live=live,
                 target_authority=target_authority, declared_volume_id=declared_volume_id,
                 store_identity=store_identity, volume_registry=volume_registry,
                 volume_registry_source=volume_registry_source)
    p = _write(rec, receipt_dir)
    rec = dict(rec, receipt_path=str(p))
    if rec["verdict"] == REFUSED:
        raise InkSocketRefusal("ink socket REFUSED %r for purpose %r:\n  - %s\nreceipt: %s"
                               % (rec["scroll"], rec["purpose"], "\n  - ".join(rec["reasons"]), p),
                               receipt_path=p, record=rec)
    return rec
