"""How a segment may be named in public."""
from __future__ import annotations

import re
from dataclasses import dataclass

_BARE_ALIAS = re.compile(r"(?<![A-Za-z0-9_-])w\d{2,3}(?![A-Za-z0-9_-])", re.IGNORECASE)


class AmbiguousPublicName(RuntimeError):
    """Raised when an artifact would publish a segment name that cannot be resolved."""


@dataclass(frozen=True)
class PublicSegment:
    """A segment named so a stranger can find exactly the object ARGUS used."""

    physical_scroll: str
    argus_alias: str
    upstream_segment_id: str
    volume_id: str
    pyramid_level: str | None = None
    source_url: str | None = None

    def public_name(self) -> str:
        """The only form permitted in public prose, captions and filenames."""
        lvl = ("/%s" % self.pyramid_level) if self.pyramid_level else ""
        return "%s / %s / upstream %s / volume %s%s" % (
            self.physical_scroll, self.argus_alias, self.upstream_segment_id,
            self.volume_id, lvl)

    def as_record(self) -> dict:
        return {
          "public_name": self.public_name(),
          "physical_scroll": self.physical_scroll,
          "argus_alias": self.argus_alias,
          "upstream_segment_id": self.upstream_segment_id,
          "volume_id": self.volume_id,
          "pyramid_level": self.pyramid_level,
          "source_url": self.source_url,
          "why_four_parts": "the ARGUS alias can collide with upstream tokens: one scroll's "
                            "upstream segment path can contain another scroll's alias "
                            "token (fixture1-w001's upstream path containing 'w002', while "
                            "fixture2-w002 is a different object). No single part "
                            "disambiguates.",
        }


def from_control(key: str, pub: str, vol: str, scroll: str, s3: str | None = None,
                 level: str = "2") -> PublicSegment:
    """Build a public name from an ARGUS control record."""
    upstream = pub.rsplit("/", 1)[-1]
    url = ("%s/%s/surface-volumes/%s/%s" % (s3, pub, vol, level)) if s3 else None
    return PublicSegment(physical_scroll=scroll, argus_alias=key,
                         upstream_segment_id=upstream, volume_id=vol,
                         pyramid_level=level, source_url=url)


def audit_text(text: str, *, where: str = "<text>") -> list:
    """Every bare alias occurrence in a block of public text."""
    return [{"where": where, "token": m.group(0), "offset": m.start()}
            for m in _BARE_ALIAS.finditer(text or "")]


def assert_public_safe(text: str, known: list, *, where: str = "<text>") -> dict:
    """Refuse text containing a segment alias that is not inside a fully-qualified name."""
    scrubbed = text or ""
    for seg in known or []:
        scrubbed = scrubbed.replace(seg.public_name(), " ")
        joined = "%s %s %s %s" % (seg.physical_scroll, seg.argus_alias,
                                  seg.upstream_segment_id, seg.volume_id)
        scrubbed = scrubbed.replace(joined, " ")
    hits = audit_text(scrubbed, where=where)
    if hits:
        raise AmbiguousPublicName(
            "%s contains %d bare segment alias(es) %s. A public artifact must name a segment "
            "as physical scroll + ARGUS alias + upstream segment id + volume identity: "
            "a bare alias alone can name objects on different physical scrolls."
            % (where, len(hits), sorted({h["token"] for h in hits})))
    return {"where": where, "bare_aliases": 0, "safe": True}
