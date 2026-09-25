"""A publication / receipt packet for the interpretation tail -- exportable, reopenable, hash-verifiable."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from argus.core import control_evidence_kinds as CEK
from argus.core import evidence_package as EP
from argus.core import paths

SCHEMA = "argus-publication-packet-v1"
VERIFY_SCHEMA = "argus-packet-verification-v1"
MANIFEST_NAME = "PACKET_MANIFEST.json"
LIMITS_NAME = "CLAIM_LIMITS.json"
LIMITATIONS_VERSION = 1

FILE_NAMES = {
    "board": "READING_BOARD.json",
    "reviews": "HUMAN_REVIEW_RECORDS.json",
    "transcription": "TRANSCRIPTION_WORKSHEET.json",
    "translation": "TRANSLATION_CANDIDATES.json",
    "chain": "RECEIPT_CHAIN.json",
    "limits": LIMITS_NAME,
}

FILE_ROLES = {
    FILE_NAMES["board"]: [CEK.HUMAN_JUDGMENT, CEK.DERIVED_VISUALIZATION],
    FILE_NAMES["reviews"]: [CEK.HUMAN_JUDGMENT, CEK.PREDICTION_DISCOVERY_EVIDENCE],
    FILE_NAMES["transcription"]: [CEK.HUMAN_JUDGMENT],
    FILE_NAMES["translation"]: [CEK.HUMAN_JUDGMENT],
    FILE_NAMES["chain"]: [],
    FILE_NAMES["limits"]: [],
}

LIMITATIONS = {
    1: [
        {"id": "NO_UNREAD_SCROLL_READING",
         "text": "This packet contains no reading of an unread scroll. Whatever it records about a "
                 "region is what named people judged about that region, and it says nothing "
                 "beyond the regions it cites."},
        {"id": "NO_QUALIFIED_DETECTOR",
         "text": "No detector has been qualified across scrolls, and no OCR/HTR model is installed. "
                 "Ink-model agreement is not independent confirmation, and a model's proposal is "
                 "never counted as a person agreeing."},
        {"id": "HUMAN_JUDGMENT_IS_SELF_DECLARED",
         "text": "Reviewer names and classes are self-declared. Independence is attested by "
                 "distinct names and distinct sessions, not authenticated. Every human answer is "
                 "HUMAN_JUDGMENT; every model proposal is PREDICTION_DISCOVERY_EVIDENCE and is "
                 "listed apart."},
        {"id": "TRANSLATIONS_ARE_PROPOSALS",
         "text": "Every translation candidate is a PROPOSAL drafted by a named person from "
                 "human-agreed letters. There is no machine translation of Ancient Greek or Latin "
                 "here, and a proposal is not a reading."},
        {"id": "CANDIDATES_ARE_PLACES_TO_LOOK",
         "text": "Candidate regions were ranked under a frozen rule to give a person somewhere to "
                 "look. A candidate is not a finding, and reading order across regions is not "
                 "established: gaps are gaps."},
        {"id": "NOT_PRIZE_EVIDENCE",
         "text": "This packet makes no prize-eligibility claim and does not satisfy any prize "
                 "package kind."},
        {"id": "NOT_PUBLISHED",
         "text": "Nothing here was published, uploaded or submitted by this tool."},
        {"id": "VALID_MEANS_UNCHANGED_ONLY",
         "text": "A valid verification means the files are byte-identical to what was exported and "
                 "the cited receipts still hash as cited. It does not mean anything in the packet "
                 "is true."},
    ],
}

CLAIM_LIMIT_KEYS = ("unread_scroll_reading_claimed", "prize_eligibility_claimed",
                    "qualified_detector_claimed", "independent_physical_ground_truth_claimed",
                    "published")


class PacketError(RuntimeError):
    pass


def _canon(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n"
            ).encode("utf-8")


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def limitations_block(version: int = LIMITATIONS_VERSION) -> dict:
    items = LIMITATIONS[version]
    return {"version": version, "limitations": [dict(i) for i in items],
            "limitations_sha256": _sha_bytes(_canon(items))}


def claim_limits() -> dict:
    return {k: False for k in CLAIM_LIMIT_KEYS}


def packets_root(target: str) -> Path:
    safe = re.sub(r"[^\w.\-]", "_", str(target))[:80] or "target"
    return paths.user_data_root("interpretation_packets", safe)



def _chain(receipt_paths: list) -> dict:
    roots, citations, unreadable = [], [], []
    for rp in receipt_paths:
        p = Path(rp)
        if not p.is_file():
            roots.append({"path": str(p), "sha256": None, "status": "MISSING"})
            continue
        roots.append({"path": str(p), "sha256": _sha_file(p), "status": "OK"})
        try:
            walked = EP.walk_chain(p)
        except EP.EvidencePackageError as exc:
            unreadable.append({"path": str(p), "why": str(exc)})
            continue
        for c in walked["citations"]:
            citations.append({k: c.get(k) for k in (
                "declared_path", "cited_by", "resolved_path", "sha256_declared", "status")})
    return {"roots": roots, "citations": citations, "unreadable_roots": unreadable,
            "note": "no receipt was cited for this packet" if not roots else
                    "every cited {path, sha256} was re-hashed at export; verify re-walks them"}



def _board_file(board_state: dict) -> dict:
    return {"schema": "argus-packet-board-v1", "state": board_state.get("board_state"),
            "board": board_state.get("board"), "why": board_state.get("why"),
            "refused_regions": board_state.get("refused_regions") or [],
            "claim": board_state.get("claim"),
            "this_is_not": ["a transcription unless the claim block says ACTIVE",
                            "a reading of an unread scroll"]}


def _reviews_file(review_payload: dict | None) -> dict:
    records = list((review_payload or {}).get("tasks") or [])
    roles: dict = {}
    reviewers: dict = {}
    for r in records:
        for a in r.get("answers") or []:
            role = a.get("evidence_role") or (
                CEK.PREDICTION_DISCOVERY_EVIDENCE if a.get("reviewer_class") == "AI_AGENT"
                else CEK.HUMAN_JUDGMENT)
            roles[role] = roles.get(role, 0) + 1
            key = (a.get("reviewer_id"), a.get("reviewer_class"))
            row = reviewers.setdefault(key, {
                "reviewer_id": key[0], "reviewer_class": key[1], "evidence_role": role,
                "identity_attestation": a.get("identity_attestation"), "answers": 0,
                "source": a.get("source")})
            row["answers"] += 1
        for v in r.get("validations") or []:
            roles[CEK.HUMAN_JUDGMENT] = roles.get(CEK.HUMAN_JUDGMENT, 0) + 1
    return {"schema": "argus-packet-review-records-v1", "records": records,
            "role_counts": roles,
            "reviewers": sorted(reviewers.values(), key=lambda x: str(x["reviewer_id"])),
            "note": "HUMAN_JUDGMENT and PREDICTION_DISCOVERY_EVIDENCE are counted apart and never "
                    "merged. Reviewer identity is self-declared."}


def _transcription_file(board_state: dict) -> dict:
    return {"schema": "argus-packet-transcription-v1",
            "proposed_reading": board_state.get("proposed_reading"),
            "claim": board_state.get("claim"),
            "label": "PROPOSED_READING worksheet",
            "what_this_is_not": "a reading of an unread scroll, a detector result or a measurement"}


def _translation_file(candidates: list) -> dict:
    return {"schema": "argus-packet-translation-v1", "label": "PROPOSAL",
            "candidates": list(candidates),
            "note": "every entry is a PROPOSAL drafted by a named person; none is a reading"}


def build(scroll: str, *, target: str, board_state: dict, review_payload: dict | None,
          translation_candidates: list, receipt_paths: list | None = None,
          out_root: Path | None = None, dry_run: bool = True) -> dict:
    """Bundle one target's interpretation state into a packet, or plan exactly that."""
    if not str(scroll or "").strip():
        raise PacketError("a packet names the scroll it is about")
    from argus.core import sealed_review_queue as SQ
    try:
        SQ.assert_promoted([board_state, review_payload, translation_candidates])
    except SQ.UnreviewedTaskRefused as exc:
        raise PacketError(str(exc)) from exc
    chain = _chain([str(p) for p in (receipt_paths or [])])
    limits = dict(limitations_block(), claim_limits=claim_limits(),
                  prize_posture=_posture(), evidence_roles={
                      k: v for k, v in CEK.EVIDENCE_ROLES.items()
                      if k in (CEK.HUMAN_JUDGMENT, CEK.PREDICTION_DISCOVERY_EVIDENCE,
                               CEK.DERIVED_VISUALIZATION, CEK.GROUND_TRUTH)})
    bodies = {
        FILE_NAMES["board"]: _board_file(board_state),
        FILE_NAMES["reviews"]: _reviews_file(review_payload),
        FILE_NAMES["transcription"]: _transcription_file(board_state),
        FILE_NAMES["translation"]: _translation_file(translation_candidates),
        FILE_NAMES["chain"]: chain,
        FILE_NAMES["limits"]: limits,
    }
    blobs = {name: _canon(body) for name, body in bodies.items()}
    files = [{"path": name, "sha256": _sha_bytes(blob), "bytes": len(blob),
              "evidence_roles": FILE_ROLES[name]} for name, blob in sorted(blobs.items())]
    content_sha256 = _sha_bytes(json.dumps([[f["path"], f["sha256"]] for f in files]).encode())
    packet_id = "PKT-" + content_sha256[:16]
    out_root = Path(out_root) if out_root is not None else packets_root(target)
    manifest = {
        "schema": SCHEMA, "packet_id": packet_id, "scroll": scroll, "target": target,
        "content_sha256": content_sha256, "files": files,
        "limitations_version": LIMITATIONS_VERSION,
        "limitations_sha256": limits["limitations_sha256"],
        "claim_limits": claim_limits(), "published": False,
        "publication": _posture()["publication"],
        "citations": {"roots": len(chain["roots"]), "cited": len(chain["citations"])},
        "verify_with": "argus.core.publication_packet.verify (also the governed packet.verify "
                       "action and GET /api/interpretation/packet/verify)",
    }
    result = {"schema": SCHEMA, "packet_id": packet_id, "dry_run": bool(dry_run), "written": False,
              "files": files, "content_sha256": content_sha256,
              "out_dir": str(out_root / packet_id), "limitations_version": LIMITATIONS_VERSION,
              "never_published": True}
    if dry_run:
        return result
    dest = out_root / packet_id
    if dest.exists():
        raise PacketError("packet %s is already exported at %s; the same state exports to the "
                          "same packet" % (packet_id, dest))
    dest.mkdir(parents=True)
    for name, blob in blobs.items():
        (dest / name).write_bytes(blob)
    manifest["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    (dest / MANIFEST_NAME).write_bytes(_canon(manifest))
    result.update(written=True, manifest_sha256=manifest["manifest_sha256"],
                  generated_utc=manifest["generated_utc"])
    return result


def _posture() -> dict:
    from argus.core import prize_package as PP
    return PP.posture()


def _manifest_hash(manifest: dict) -> str:
    return _sha_bytes(_canon({k: v for k, v in manifest.items() if k != "manifest_sha256"}))



def list_packets(target: str, *, root: Path | None = None) -> list:
    base = Path(root) if root is not None else packets_root(target)
    out = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        m = d / MANIFEST_NAME
        if not m.is_file():
            continue
        try:
            doc = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append({"packet_id": d.name, "path": str(d), "readable": False})
            continue
        out.append({"packet_id": doc.get("packet_id"), "path": str(d), "readable": True,
                    "generated_utc": doc.get("generated_utc"), "scroll": doc.get("scroll"),
                    "target": doc.get("target"), "files": len(doc.get("files") or []),
                    "citations": doc.get("citations"), "published": doc.get("published")})
    return out


def _resolve_packet(packet_path) -> tuple:
    p = Path(packet_path)
    if p.is_file() and p.name == MANIFEST_NAME:
        return p.parent, p
    return p, p / MANIFEST_NAME


def verify(packet_path) -> dict:
    """Reopen a packet and re-walk every hash."""
    root, manifest_path = _resolve_packet(packet_path)
    out = {"schema": VERIFY_SCHEMA, "packet_path": str(root), "packet_id": None,
           "verdict": "REFUSED", "valid": False, "reasons": [], "files": [],
           "unlisted_files": [], "citations": [], "roots": [],
           "what_valid_means": "the files are byte-identical to what was exported, the cited "
                               "receipts still hash as cited, and the limitations travelled "
                               "unaltered. It does not mean anything in the packet is true."}
    if not manifest_path.is_file():
        out["reasons"].append("no %s at %s: this is not a packet, or its manifest was removed"
                              % (MANIFEST_NAME, root))
        return out
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["reasons"].append("the manifest is not readable JSON: %s" % exc)
        return out
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        out["reasons"].append("the manifest is not a %s document" % SCHEMA)
        return out
    out["packet_id"] = manifest.get("packet_id")
    reasons = out["reasons"]

    self_ok = manifest.get("manifest_sha256") == _manifest_hash(manifest)
    out["manifest"] = {"self_hash": "OK" if self_ok else "HASH_MISMATCH"}
    if not self_ok:
        reasons.append("the manifest does not hash to its own manifest_sha256: it was edited "
                       "after export")

    listed = manifest.get("files") or []
    if not listed:
        reasons.append("the manifest lists no files")
    for f in listed:
        name = str(f.get("path") or "")
        fp = root / name
        safe = name and not Path(name).is_absolute() and ".." not in Path(name).parts
        if not safe or not fp.is_file():
            row = {"path": name, "sha256_declared": f.get("sha256"), "sha256_actual": None,
                   "status": "MISSING"}
            reasons.append("%s is missing from the packet" % (name or "<unnamed file>"))
        else:
            actual = _sha_file(fp)
            ok = actual == f.get("sha256")
            row = {"path": name, "sha256_declared": f.get("sha256"), "sha256_actual": actual,
                   "status": "OK" if ok else "HASH_MISMATCH"}
            if not ok:
                reasons.append("%s does not hash as exported (HASH_MISMATCH)" % name)
        out["files"].append(row)
    declared = {str(f.get("path")) for f in listed} | {MANIFEST_NAME}
    out["unlisted_files"] = sorted(
        str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
        if p.is_file() and str(p.relative_to(root)).replace("\\", "/") not in declared)
    if out["unlisted_files"]:
        reasons.append("files the manifest does not list are present: %s" % out["unlisted_files"])

    out["limitations"] = _check_limits(root, manifest, reasons)
    _check_claims_and_roles(root, manifest, reasons)
    _check_citations(root, out, reasons)

    out["valid"] = not reasons
    out["verdict"] = "VALID" if out["valid"] else "INVALID"
    return out


def _load_listed(root: Path, name: str):
    try:
        return json.loads((root / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _check_limits(root: Path, manifest: dict, reasons: list) -> dict:
    version = manifest.get("limitations_version")
    if version not in LIMITATIONS:
        reasons.append("the manifest declares limitations version %r, which this verifier does not "
                       "know: a packet whose caveats cannot be checked is not valid" % (version,))
        return {"status": "UNKNOWN_VERSION", "claim_limits": "UNCHECKED"}
    canon = limitations_block(version)
    doc = _load_listed(root, LIMITS_NAME)
    if not isinstance(doc, dict) or "limitations" not in doc:
        reasons.append("the limitations block is missing from %s" % LIMITS_NAME)
        return {"status": "MISSING", "claim_limits": "MISSING"}
    lim_ok = doc.get("limitations") == canon["limitations"]
    if not lim_ok:
        reasons.append("the limitations block was altered: it is not the canonical text for "
                       "version %s" % version)
    limits_ok = doc.get("claim_limits") == claim_limits()
    if not limits_ok:
        reasons.append("the claim limits are missing or altered: every claim in the packet must "
                       "be False (%s)" % ", ".join(CLAIM_LIMIT_KEYS))
    if manifest.get("limitations_sha256") != canon["limitations_sha256"]:
        reasons.append("the manifest's limitations_sha256 is not the canonical one")
    return {"status": "OK" if lim_ok else "ALTERED",
            "claim_limits": "OK" if limits_ok else "ALTERED",
            "version": version}


def _check_claims_and_roles(root: Path, manifest: dict, reasons: list) -> None:
    if manifest.get("claim_limits") != claim_limits() or manifest.get("published") is not False:
        reasons.append("the manifest makes a claim the packet may not make (a claim limit is not "
                       "False, or it says it was published)")
    for f in manifest.get("files") or []:
        bad = [r for r in f.get("evidence_roles") or [] if r not in CEK.EVIDENCE_ROLES]
        if bad:
            reasons.append("%s carries evidence roles outside the vocabulary: %s"
                           % (f.get("path"), bad))
    reviews = _load_listed(root, FILE_NAMES["reviews"])
    for rec in (reviews or {}).get("records") or []:
        for a in rec.get("answers") or []:
            if a.get("reviewer_class") == "AI_AGENT" and a.get("evidence_role") not in (
                    None, CEK.PREDICTION_DISCOVERY_EVIDENCE):
                reasons.append("a model answer on %s is labelled %s: a model proposal was "
                               "relabelled as something else" % (rec.get("task_id"),
                                                                 a.get("evidence_role")))
            if a.get("reviewer_class") != "AI_AGENT" and a.get("evidence_role") not in (
                    None, CEK.HUMAN_JUDGMENT):
                reasons.append("a human answer on %s is labelled %s"
                               % (rec.get("task_id"), a.get("evidence_role")))


def _check_citations(root: Path, out: dict, reasons: list) -> None:
    chain = _load_listed(root, FILE_NAMES["chain"]) or {}
    for r in chain.get("roots") or []:
        p = Path(str(r.get("path")))
        if not p.is_file():
            row = {"path": str(p), "status": "MISSING", "sha256_declared": r.get("sha256"),
                   "sha256_actual": None}
        else:
            actual = _sha_file(p)
            row = {"path": str(p), "sha256_declared": r.get("sha256"), "sha256_actual": actual,
                   "status": "OK" if actual == r.get("sha256") else "HASH_MISMATCH"}
        out["roots"].append(row)
        if row["status"] != "OK":
            reasons.append("cited receipt %s is %s" % (row["path"], row["status"]))
    recorded = {(c.get("declared_path"), c.get("sha256_declared"))
                for c in chain.get("citations") or []}
    seen = set()
    for r in out["roots"]:
        if r["status"] != "OK":
            continue
        try:
            walked = EP.walk_chain(Path(r["path"]))
        except EP.EvidencePackageError:
            continue
        for c in walked["citations"]:
            seen.add((c.get("declared_path"), c.get("sha256_declared")))
            status = {"OK": "OK", "HASH_MISMATCH": "HASH_MISMATCH",
                      "MISSING_ON_DISK": "MISSING"}[c["status"]]
            out["citations"].append({"declared_path": c.get("declared_path"),
                                     "cited_by": c.get("cited_by"), "status": status,
                                     "sha256_declared": c.get("sha256_declared"),
                                     "sha256_actual": c.get("sha256_actual")})
            if status != "OK":
                reasons.append("cited %s is %s" % (c.get("declared_path"), status))
    if recorded and seen and recorded != seen:
        reasons.append("the receipt chain no longer cites what it cited at export")
