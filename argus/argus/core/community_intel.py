"""Community-reported claims, registered as intelligence and never as findings.

The register's contents are not part of the public ARGUS release: the operator's build keeps its own
list of community claims and how it would check them. This public build ships the same contract with
an empty register, so every caller sees the same shape, nothing here can be cited as evidence, and a
lookup of any key is refused as an unknown claim.
"""
from __future__ import annotations

import dataclasses
import hashlib
import time

CONTRACT = "argus-community-intel-v1"
STATUS = "COMMUNITY_REPORTED_PENDING_REPRODUCTION"
REPRODUCED = "REPRODUCED_SEE_FINDING"
CONTRADICTED = "CONTRADICTED_SEE_FINDING"

STATUSES = (STATUS, REPRODUCED, CONTRADICTED)

NOT_PUBLIC = "the community-claims register is not part of the public release"


class IntelRefusal(RuntimeError):
    """Raised rather than storing something that could be mistaken for a measurement."""


@dataclasses.dataclass(frozen=True)
class Claim:
    key: str
    claim: str
    reported_by: str
    would_reproduce: str
    never_used_for: str
    status: str = STATUS
    finding: str | None = None

    @property
    def claim_sha256(self) -> str:
        """Binds the text."""
        return hashlib.sha256(self.claim.encode("utf-8")).hexdigest()


CLAIMS: tuple = ()

NOT_REINFORCEMENT_LEARNING = (
  "Collecting preferences does not make a system reinforcement learning. A ranking fitted from "
  "blinded comparisons may prioritise candidates; it does not license training a model against "
  "those preferences."
)


def register() -> dict:
    """The whole register, in the only shape it may be published in."""
    by_key = {c.key: c for c in CLAIMS}
    if len(by_key) != len(CLAIMS):
        raise IntelRefusal("two claims share a key; each must be separately addressable")
    return {
      "contract": CONTRACT,
      "registered_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "count": len(CLAIMS),
      "status_of_every_claim": STATUS,
      "claims": [dict(dataclasses.asdict(c), claim_sha256=c.claim_sha256) for c in CLAIMS],
      "register_note": NOT_PUBLIC,
      "these_are_not_findings": "no entry here has been reproduced. None may be cited as "
                                "evidence, enter a gate, appear in a prize package, or change "
                                "a classification.",
      "how_a_claim_leaves_this_register": "somebody reproduces it from artifacts and writes a "
                                          "receipt; the FINDING then cites the claim as its "
                                          "origin.",
      "not_reinforcement_learning": NOT_REINFORCEMENT_LEARNING,
      "why_separately": "bundling claims would let a reproduction of one read as support for its "
                        "neighbours.",
    }


def assert_not_evidence(key: str, used_as: str) -> None:
    """Refuse a community claim being used as a measurement."""
    if key not in {c.key for c in CLAIMS}:
        raise IntelRefusal("no such registered claim: %r" % key)
    c = {x.key: x for x in CLAIMS}[key]
    if c.status != REPRODUCED:
        raise IntelRefusal(
          "claim %r is %s and may not be used as %s. What would settle it: %s"
          % (key, c.status, used_as, c.would_reproduce))
