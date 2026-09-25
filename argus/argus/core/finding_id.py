"""One way to recognise a finding id."""
from __future__ import annotations

import pathlib
import re

ID = re.compile(r"N-(\d{3,5})(?!\d)")

HEADING = re.compile(r"^#{1,6}[ \t]*N-(\d{3,5})(?!\d)", re.MULTILINE)

EXACT = re.compile(r"^N-(\d{3,5})$")

BASENAME = re.compile(r"^n(\d{3,5})_")


class AmbiguousId(ValueError):
    """A string that looks like an id but is not one, whole."""


def parse(text: str) -> int:
    """The integer behind a bare id string."""
    m = EXACT.match(text.strip())
    if not m:
        raise AmbiguousId(
            "%r is not a finding id. An id is N- followed by 3 to 5 digits and nothing else; "
            "a value that merely CONTAINS one is a reference, not an identity." % text)
    return int(m.group(1))


def format_id(n: int) -> str:
    """The canonical written form."""
    return "N-%03d" % int(n)


def numeric(legacy_id: str) -> int:
    """The numeric part of an identifier, ignoring a letter suffix."""
    m = re.match(r"^N-(\d{1,5})([a-z]?)$", str(legacy_id).strip())
    if not m:
        raise ValueError("not a finding identifier: %r" % (legacy_id,))
    return int(m.group(1))


def sort_key(legacy_id: str) -> tuple:
    """Order by number, then by letter suffix, so N-250 precedes N-250a precedes N-251."""
    m = re.match(r"^N-(\d{1,5})([a-z]?)$", str(legacy_id).strip())
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, str(legacy_id))


def references(text: str) -> list:
    """Every id MENTIONED anywhere."""
    return sorted({int(x) for x in ID.findall(text or "")})


def records(text: str) -> list:
    """Every id that OPENS AN ENTRY, by heading."""
    return sorted({int(x) for x in HEADING.findall(text or "")})


def newest_recorded(text: str) -> int:
    ids = records(text)
    return max(ids) if ids else 0


def dir_id(name: str):
    """The id of a receipt directory or script basename, or None."""
    m = BASENAME.match(pathlib.PurePath(name).name)
    return int(m.group(1)) if m else None


def find_receipt_dirs(root, wanted: int) -> list:
    """Every directory under `root` belonging to EXACTLY this finding."""
    root = pathlib.Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and dir_id(p.name) == int(wanted))


def has_receipt(root, wanted: int) -> bool:
    """Does a receipt for exactly this finding exist, with at least one json in it?"""
    return any(any(d.glob("*.json")) for d in find_receipt_dirs(root, wanted))


def code_only(text: str) -> str:
    """Source with comments removed, so a scanner cannot match the prose describing the bug."""
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string if tok.type == tokenize.STRING else tok.string)
            out.append(" ")
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    return "".join(out)


def audit_source(text: str) -> list:
    """Find id-matching patterns in SOURCE that carry one of the four known defects."""
    text = code_only(text)
    out = []
    for m in re.finditer(r"""N-\\?\(?\\d\{(\d)(?:,(\d))?\}""", text):
        lo, hi = m.group(1), m.group(2)
        if hi is None and lo == "3":
            out.append({"at": m.start(), "pattern": m.group(0), "defect": "TRUNCATION",
                        "why": "a fixed 3-digit match captures 123 from N-1234 and reports it "
                               "as a different real finding"})
    for m in re.finditer(r"""\\b\s*N-\\?\(?\\d\{3\}""", text):
        out.append({"at": m.start(), "pattern": m.group(0), "defect": "BLINDNESS",
                    "why": "a trailing \\b after 3 digits matches nothing from N-1000 onward"})
    for m in re.finditer(r"""glob\s*\(\s*["']n\d{1,4}\*""", text):
        out.append({"at": m.start(), "pattern": m.group(0), "defect": "PREFIX",
                    "why": "an id prefix glob collects neighbouring findings; n123* matches "
                           "n1234 and n1235"})
    return out
