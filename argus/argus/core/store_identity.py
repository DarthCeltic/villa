"""STORE_IDENTITY asserted only where PROVABLE."""
from __future__ import annotations

from argus.core import key_list_hash as KLH
from argus.core import pitch as PITCH

CONTRACT = "argus-store-identity-v2"
SUPERSEDES = ("the earlier STORE_IDENTITY (argus-acquire-v1): pitch_um/energy_kev written from "
              "the requested region as if measured; key-list hash convention unnamed")

PROVEN = "PROVEN"
UNKNOWN = "UNKNOWN"
CONTRADICTED = "CONTRADICTED"

FIELDS = ("pitch_um", "energy_kev", "plane_count", "key_list_hash")

PITCH_TOL_UM = 0.001
ENERGY_TOL_KEV = 0.5

ENERGY_KEYS = ("energy_kev", "energy_keV", "beam_energy_kev", "beam_energy_keV")


class StoreIdentityRefusal(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _official_basis(official, zarray, level):
    """A PROVEN official_identity resolution is a proof basis for pitch, energy and plane count."""
    from argus.core import official_identity as OI
    if not (isinstance(official, dict) and official.get("state") == OI.PROVEN
            and official.get("contract") == OI.CONTRACT):
        return None
    ri = official.get("resolved_identity") or {}
    if OI.canon_sha(ri) != official.get("identity_sha256"):
        return None
    if str(ri.get("array_path") or "0") != str(level or "0"):
        return None
    p = ri.get("pitch_um") or {}
    return {"level_pitch_um": p.get("level_pitch_um"), "energy_kev": ri.get("energy_kev"),
            "shape": ri.get("shape"), "dtype": ri.get("dtype"),
            "identity_sha256": official.get("identity_sha256")}


def assess(*, zarray: dict | None, zattrs: dict | None, manifest_keys=None,
           recorded_key_list_hash=None, declared: dict | None = None, level: str = "0",
           official: dict | None = None) -> dict:
    """Per-field verdicts, each with its basis."""
    dec = dict(declared or {})
    out = {}
    ob = _official_basis(official, zarray, level)

    ev = PITCH.from_ome(zattrs or {}, level=str(level))
    dp = _num(dec.get("pitch_um"))
    if ev.status == PITCH.VERIFIED and ev.pitch_um_yx:
        got = float(ev.pitch_um_yx[0])
        if dp is not None and abs(got - dp) > PITCH_TOL_UM:
            out["pitch_um"] = {"state": CONTRADICTED, "value": got, "declared": dp,
                               "basis": "OME_TRANSFORM %s vs declared %s" % (got, dp)}
        else:
            out["pitch_um"] = {"state": PROVEN, "value": got, "declared": dp,
                               "basis": "the store's own OME coordinateTransformations"}
    elif ob and _num(ob["level_pitch_um"]) is not None:
        got = float(ob["level_pitch_um"])
        if dp is not None and abs(got - dp) > PITCH_TOL_UM:
            out["pitch_um"] = {"state": CONTRADICTED, "value": got, "declared": dp,
                               "basis": "OFFICIAL_CATALOG_RESOLUTION %s vs declared %s" % (got, dp)}
        else:
            out["pitch_um"] = {"state": PROVEN, "value": got, "declared": dp,
                               "basis": "PROVEN_BY_OFFICIAL_CATALOG_RESOLUTION identity %s"
                                        % ob["identity_sha256"]}
    else:
        out["pitch_um"] = {"state": UNKNOWN, "value": None, "declared": dp,
                           "basis": "no OME transform declares a pitch (%s); a declaration or a "
                                    "name token is not a proof" % ev.detail.get("why")}

    de = _num(dec.get("energy_kev"))
    za = zattrs or {}
    found = [(k, _num(za.get(k))) for k in ENERGY_KEYS if _num(za.get(k)) is not None]
    if found:
        k, got = found[0]
        if de is not None and abs(got - de) > ENERGY_TOL_KEV:
            out["energy_kev"] = {"state": CONTRADICTED, "value": got, "declared": de,
                                 "basis": ".zattrs %s=%s vs declared %s" % (k, got, de)}
        else:
            out["energy_kev"] = {"state": PROVEN, "value": got, "declared": de,
                                 "basis": "the store's own .zattrs key %r" % k}
    elif ob and _num(ob["energy_kev"]) is not None:
        got = float(ob["energy_kev"])
        if de is not None and abs(got - de) > ENERGY_TOL_KEV:
            out["energy_kev"] = {"state": CONTRADICTED, "value": got, "declared": de,
                                 "basis": "OFFICIAL_CATALOG_RESOLUTION %s vs declared %s" % (got, de)}
        else:
            out["energy_kev"] = {"state": PROVEN, "value": got, "declared": de,
                                 "basis": "PROVEN_BY_OFFICIAL_CATALOG_RESOLUTION identity %s"
                                          % ob["identity_sha256"]}
    else:
        out["energy_kev"] = {"state": UNKNOWN, "value": None, "declared": de,
                             "basis": "the store declares no energy; a keV token in a URL or "
                                      "directory name is a name, not a measurement"}

    shape = (zarray or {}).get("shape")
    dn = dec.get("plane_count")
    if isinstance(shape, list) and shape and isinstance(shape[0], int):
        got = int(shape[0])
        if dn is not None and int(dn) != got:
            out["plane_count"] = {"state": CONTRADICTED, "value": got, "declared": int(dn),
                                  "basis": ".zarray shape[0]=%d vs declared %s" % (got, dn)}
        elif ob and list(ob.get("shape") or []) and list(ob["shape"]) != list(shape):
            out["plane_count"] = {"state": CONTRADICTED, "value": got, "declared": dn,
                                  "basis": ".zarray shape %s vs PROVEN official shape %s"
                                           % (shape, ob["shape"])}
        else:
            out["plane_count"] = {"state": PROVEN, "value": got, "declared": dn,
                                  "basis": "the store's own .zarray shape[0]"}
    else:
        out["plane_count"] = {"state": UNKNOWN, "value": None, "declared": dn,
                              "basis": "no .zarray shape to read"}

    if manifest_keys is None:
        out["key_list_hash"] = {"state": UNKNOWN, "value": None, "declared": recorded_key_list_hash,
                                "basis": "no manifest key list to re-derive from"}
    elif not recorded_key_list_hash:
        out["key_list_hash"] = {"state": UNKNOWN, "value": KLH.hash_record(manifest_keys),
                                "declared": None, "basis": "no recorded hash to verify"}
    else:
        v = KLH.verify(list(manifest_keys), recorded_key_list_hash)
        out["key_list_hash"] = {
          "state": PROVEN if v["verified"] else CONTRADICTED,
          "value": {"sha256": v["canonical"]["sha256"], "version": KLH.CANONICAL},
          "declared": recorded_key_list_hash,
          "matched_version": v.get("matched_version"),
          "basis": v["why"]}

    unproven = [f for f in FIELDS if out[f]["state"] != PROVEN]
    return {"contract": CONTRACT, "fields": out, "unproven": unproven,
            "all_proven": not unproven,
            "rule": "a field is asserted only when the store's own bytes prove it; otherwise it is "
                    "UNKNOWN, and UNKNOWN refuses"}


def assert_identity(**kw) -> dict:
    r = assess(**kw)
    if not r["all_proven"]:
        raise StoreIdentityRefusal(
          "STORE_IDENTITY cannot be asserted: %s"
          % "; ".join("%s %s (%s)" % (f, r["fields"][f]["state"], r["fields"][f]["basis"])
                      for f in r["unproven"]), report=r)
    return r


OPERATOR_ATTESTED = "OPERATOR_ATTESTED"
ATTESTED, SEALED = "ATTESTED", "SEALED"


def holding_state(record) -> str:
    """ATTESTED when a person vouched for the store's identity by hand (the record says `identity_basis: OPERATOR_ATTESTED`), otherwise SEALED."""
    if str((record or {}).get("identity_basis") or "").strip().upper() == OPERATOR_ATTESTED:
        return ATTESTED
    return SEALED


def identity_record(*, base: dict, assessment: dict, attestation: dict | None = None) -> dict:
    """A STORE_IDENTITY body: proven values under `asserted`, declarations under `declared`."""
    f = assessment["fields"]
    if attestation is not None:
        who, why = str(attestation.get("attested_by") or "").strip(), str(attestation.get("reason") or "").strip()
        if not who or not why:
            raise ValueError("an attested identity needs attested_by and a reason")
        base = dict(base, identity_basis=OPERATOR_ATTESTED, attested_by=who, attestation_reason=why)
    return dict(base, contract=CONTRACT, supersedes=SUPERSEDES,
                asserted={k: f[k]["value"] for k in FIELDS if f[k]["state"] == PROVEN},
                declared={k: f[k].get("declared") for k in FIELDS},
                unproven={k: {"state": f[k]["state"], "basis": f[k]["basis"]}
                          for k in FIELDS if f[k]["state"] != PROVEN},
                identity_assertable=assessment["all_proven"],
                key_list_hash_version=KLH.CANONICAL)
