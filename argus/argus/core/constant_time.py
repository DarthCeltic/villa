"""Constant-time comparison of two secrets that may arrive as arbitrary text."""
from __future__ import annotations

import hmac


def same_secret(presented: str | None, expected: str | None) -> bool:
    if not presented or not expected:
        return False
    try:
        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False
