"""Canonical scroll identifiers, and a hard refusal to let confusable ids pass silently."""
from __future__ import annotations

import re

CANONICAL = (
    "PHerc0009B", "PHerc0125", "PHerc0139", "PHerc0172", "PHerc0175A", "PHerc0175B", "PHerc0191",
    "PHerc0211", "PHerc0257", "PHerc0268", "PHerc0322", "PHerc0332",
    "PHerc0306B", "PHerc0343", "PHerc0343P", "PHerc0358", "PHerc0483A", "PHerc0483B",
    "PHerc0490A", "PHerc0490B", "PHerc0500P2", "PHerc0800",
    "PHerc0813", "PHerc0814", "PHerc0826", "PHerc0841", "PHerc0846A",
    "PHerc0846B", "PHerc1203", "PHerc1218", "PHerc1299", "PHerc1447",
    "PHerc1451", "PHerc1545", "PHerc1667", "PHercMAN5", "PHercMANB",
    "PHercMANBp", "PHercParis4",
)

ALIASES = {
    "PHerc172": "PHerc0172",
    "Paris4": "PHercParis4",
}

PLACEHOLDERS = frozenset({
    "PHercNNNN", "PHercZZZ9999", "PHercQ", "PHercX", "PHercY", "PHercZ", "PHercE",
})

_SEP = re.compile(r"[\s_\-.:/]+")


def normalize(s: str) -> str:
    """Case- and separator-insensitive key."""
    return _SEP.sub("", str(s)).lower()


_BY_KEY = {normalize(c): c for c in CANONICAL}
_BY_KEY.update({normalize(a): c for a, c in ALIASES.items()})
_PLACEHOLDER_KEYS = {normalize(p) for p in PLACEHOLDERS}


def resolve(s: str) -> str:
    """Canonical id, or refuse."""
    k = normalize(s)
    if k in _PLACEHOLDER_KEYS:
        raise KeyError("%r is a PLACEHOLDER, not a scroll. It appears in fixtures and "
                       "templates and must never be resolved to real data." % s)
    if k not in _BY_KEY:
        near = sorted(c for c in CANONICAL if _digits(c) == _digits(s) and normalize(c) != k)
        hint = (" -- did you mean one of %s? They share the same digits in a different "
                "order, which is exactly the confusion this refuses to resolve for you."
                % near) if near else ""
        raise KeyError("unknown scroll id %r%s" % (s, hint))
    return _BY_KEY[k]


def _digits(s: str) -> str:
    """Multiset of digits, order-independent -- the signature a transposition preserves."""
    return "".join(sorted(ch for ch in str(s) if ch.isdigit()))


def _letters(s: str) -> str:
    return "".join(sorted(ch for ch in normalize(s) if ch.isalpha()))


def is_confusable(a: str, b: str) -> bool:
    """True when two DIFFERENT ids share letters and digit multiset -- e.g."""
    if normalize(a) == normalize(b):
        return False
    if not _digits(a) or _digits(a) != _digits(b):
        return False
    return _letters(a) == _letters(b)


def confusable_pairs(ids=CANONICAL) -> list:
    """Every confusable pair in a set of ids."""
    out = []
    ids = list(ids)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if is_confusable(a, b):
                out.append(tuple(sorted((a, b))))
    return sorted(out)


def assert_no_confusable_collision(selected, forbidden, *, context: str = "") -> None:
    """Refuse if a SELECTED id is confusable with a FORBIDDEN one."""
    hits = [(s, f) for s in selected for f in forbidden if is_confusable(s, f)]
    if hits:
        raise ValueError(
            "confusable scroll ids across a selection boundary%s: %s. These are DIFFERENT "
            "scrolls that differ only by digit order. Confirm explicitly which was meant "
            "and record it, rather than letting the resemblance pass."
            % ((" (%s)" % context) if context else "",
               "; ".join("%s selected vs %s forbidden" % h for h in hits)))
