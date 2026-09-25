"""One definition of \"a name that is safe to join onto a directory\"."""
from __future__ import annotations

import re

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_LEVEL = re.compile(r"[A-Za-z0-9_-]{1,32}")
_DEVICE = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", re.IGNORECASE)


def is_plain_file_name(value) -> bool:
    """No trailing dot (Windows treats `x.` as `x`) and not a device name (`CON`, `nul.txt`): both make a name that is not the file it appears to be."""
    return isinstance(value, str) and not value.endswith(".") and not _DEVICE.fullmatch(value)


def is_safe_name(value) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value)) and ".." not in value and is_plain_file_name(value)


def safe_name(value, what: str = "name") -> str:
    if not is_safe_name(value):
        raise ValueError("%s must be letters, digits, dot, dash or underscore (1 to 64 characters), not a path" % what)
    return value


def is_safe_level(value) -> bool:
    """A pyramid level key: `0`, `1`, `s0`."""
    return isinstance(value, str) and bool(_LEVEL.fullmatch(value)) and is_plain_file_name(value)


def safe_level(value) -> str:
    if not is_safe_level(value):
        raise ValueError("a pyramid level is a short key such as 0 or s1, not a path")
    return value
