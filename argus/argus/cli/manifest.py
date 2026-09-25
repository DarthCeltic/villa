"""The component manifest, loaded and CHECKED rather than trusted."""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from argus.core import licence_registry as LR

MANIFEST_ID = "argus-cli-component-manifest-v1"

MANIFEST_PATH = pathlib.Path(__file__).with_name("components.json")

UNKNOWN = "UNKNOWN"

VERIFIABLE_MODES = ("SHA256", "GIT_REVISION", "PER_OBJECT_MANIFEST")

ALL_MODES = VERIFIABLE_MODES + ("GIT_REVISION_ABBREVIATED", UNKNOWN)


class ManifestError(ValueError):
    """Raised on load."""


@dataclasses.dataclass(frozen=True)
class Component:
    """One external thing, with everything needed to decide whether to acquire it."""

    id: str
    name: str
    purpose: str
    kind: str
    acquire: str
    url: str
    revision: str | None
    version: str | None
    integrity_mode: str
    integrity_value: str | None
    integrity_why_unknown: str | None
    spdx: str
    licence_url: str
    licence_family: str
    licence_permits: str
    registry_name: str | None
    size_bytes: Any
    why_unknown: str | None
    required_for: tuple
    profiles: tuple
    provides_modules: tuple = ()


    @property
    def is_verifiable(self) -> bool:
        """Can this project prove the bytes it received are the bytes it meant to receive?"""
        return self.integrity_mode in VERIFIABLE_MODES

    @property
    def licence_is_declared(self) -> bool:
        """UNDECLARED is not a gap for the packager to fill in."""
        return self.licence_family != "UNDECLARED" and self.spdx != UNKNOWN

    def registry_verdict(self) -> dict | None:
        """What `argus.core.licence_registry` says this component permits."""
        for c in LR.COMPONENTS:
            if c.name == self.registry_name:
                return c.verdict()
        return None

    def licence_display(self) -> str:
        """The text a person must see before accepting."""
        lines = ["  component : %s" % self.name,
                 "  purpose   : %s" % self.purpose,
                 "  source    : %s" % self.url]
        if self.licence_is_declared:
            lines.append("  licence   : %s" % self.spdx)
            lines.append("  full text : %s" % self.licence_url)
        else:
            lines.append("  licence   : NONE DECLARED")
            lines.append("  checked at: %s" % self.licence_url)
            lines.append("  meaning   : no grant exists. Absence of a licence is absence of "
                         "permission, not a default of permission.")
        lines.append("  permits   : %s" % self.licence_permits)
        v = self.registry_verdict()
        if v:
            lines.append("  registry  : run_locally=%s bundle=%s redistribute=%s "
                         "prize_submission=%s"
                         % (v["run_locally"], v["bundle_in_installer"], v["redistribute"],
                            v["prize_submission"]))
        else:
            lines.append("  registry  : UNKNOWN -- no row in argus.core.licence_registry, so "
                         "no verdict is asserted here")
        if not self.is_verifiable:
            lines.append("  integrity : %s -- %s"
                         % (self.integrity_mode, self.integrity_why_unknown or "no reason "
                            "recorded, which is itself a manifest defect"))
        else:
            lines.append("  integrity : %s %s" % (self.integrity_mode,
                                                  (self.integrity_value or "")[:16]))
        return "\n".join(lines)

    def acceptance_fingerprint(self) -> str:
        """What the operator actually agreed to."""
        import hashlib
        payload = "|".join([self.id, self.spdx, self.licence_url, self.licence_family,
                            str(self.revision), self.url])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "url": self.url,
                "revision": self.revision, "version": self.version,
                "integrity_mode": self.integrity_mode,
                "integrity_value": self.integrity_value,
                "integrity_why_unknown": self.integrity_why_unknown,
                "verifiable": self.is_verifiable,
                "spdx": self.spdx, "licence_url": self.licence_url,
                "licence_family": self.licence_family,
                "licence_declared": self.licence_is_declared,
                "size_bytes": self.size_bytes, "why_unknown": self.why_unknown,
                "required_for": list(self.required_for), "profiles": list(self.profiles),
                "provides_modules": list(self.provides_modules),
                "acceptance_fingerprint": self.acceptance_fingerprint()}


def _require(row: dict, key: str, where: str):
    if key not in row:
        raise ManifestError("%s is missing %r" % (where, key))
    return row[key]


def _check_unknown(value, why, where: str, field: str):
    """An UNKNOWN with no reason is a manifest defect, and the loader says so by name."""
    if value == UNKNOWN and not (why or "").strip():
        raise ManifestError(
            "%s: %s is UNKNOWN with no why_unknown. The reason is the only thing that "
            "separates 'nobody measured it' from 'no such value exists', and a reader "
            "cannot tell those apart from a blank." % (where, field))


def parse(doc: dict, *, where: str = "components.json") -> list:
    """Validate a manifest document and return its components."""
    if doc.get("schema") != MANIFEST_ID:
        raise ManifestError("%s: schema is %r, expected %r"
                            % (where, doc.get("schema"), MANIFEST_ID))
    rows = _require(doc, "components", where)
    out, seen = [], set()
    for row in rows:
        cid = _require(row, "id", where)
        if cid in seen:
            raise ManifestError("%s: duplicate component id %r. Two rows for one component "
                                "is how the same bytes get fetched twice under two names."
                                % (where, cid))
        seen.add(cid)
        w = "%s[%s]" % (where, cid)
        integ = _require(row, "integrity", w)
        mode = _require(integ, "mode", w + ".integrity")
        if mode not in ALL_MODES:
            raise ManifestError("%s: unknown integrity mode %r; declared modes are %s"
                                % (w, mode, ", ".join(ALL_MODES)))
        _check_unknown(mode, integ.get("why_unknown"), w, "integrity.mode")
        if mode == "SHA256":
            val = (integ.get("value") or "").lower()
            if len(val) != 64 or any(c not in "0123456789abcdef" for c in val):
                raise ManifestError(
                    "%s: integrity mode SHA256 needs a 64-character hex digest, got %r. A "
                    "short or malformed digest cannot fail a comparison, so it would pass "
                    "every file." % (w, integ.get("value")))
        if mode == "GIT_REVISION" and len(integ.get("value") or "") != 40:
            raise ManifestError(
                "%s: GIT_REVISION must be the full 40-character commit. Use "
                "GIT_REVISION_ABBREVIATED for a short pin, which is honestly unverifiable "
                "rather than dishonestly verified." % w)
        lic = _require(row, "licence", w)
        _check_unknown(lic.get("spdx"), lic.get("permits"), w, "licence.spdx")
        _check_unknown(row.get("size_bytes"), row.get("why_unknown"), w, "size_bytes")
        _check_unknown(row.get("revision"), integ.get("why_unknown")
                       or row.get("why_unknown"), w, "revision")
        out.append(Component(
            id=cid, name=_require(row, "name", w), purpose=_require(row, "purpose", w),
            kind=_require(row, "kind", w), acquire=_require(row, "acquire", w),
            url=_require(row, "url", w), revision=row.get("revision"),
            version=row.get("version"),
            integrity_mode=mode, integrity_value=integ.get("value"),
            integrity_why_unknown=integ.get("why_unknown"),
            spdx=_require(lic, "spdx", w + ".licence"),
            licence_url=_require(lic, "url", w + ".licence"),
            licence_family=_require(lic, "family", w + ".licence"),
            licence_permits=_require(lic, "permits", w + ".licence"),
            registry_name=lic.get("registry_name"),
            size_bytes=row.get("size_bytes"), why_unknown=row.get("why_unknown"),
            required_for=tuple(row.get("required_for") or ()),
            profiles=tuple(row.get("profiles") or ()),
            provides_modules=tuple(row.get("provides_modules") or ())))
    return out


def load(path=None) -> list:
    p = pathlib.Path(path or MANIFEST_PATH)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError("cannot read the component manifest at %s: %s" % (p, exc))
    except ValueError as exc:
        raise ManifestError("%s is not valid JSON: %s" % (p, exc))
    return parse(doc, where=str(p.name))


def by_id(components, cid: str):
    for c in components:
        if c.id == cid:
            return c
    return None
