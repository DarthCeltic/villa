"""Publication is an operator-only act, never automated."""
from __future__ import annotations

import re

FORBIDDEN_PATTERNS = (
  (r"gh\s+repo\s+(edit|create).*--visibility\s+public", "flips repository visibility"),
  (r"gh\s+repo\s+create(?!.*--private)", "creates a repository without --private"),
  (r"--visibility[= ]public", "sets public visibility"),
  (r"\"private\"\s*:\s*false", "sets private=false via the API"),
  (r"'private'\s*:\s*False", "sets private=False via the API"),
  (r"gh\s+release\s+create", "publishes a release"),
  (r"gh\s+pr\s+create", "opens a pull request"),
  (r"gh\s+issue\s+create", "opens an issue"),
  (r"npm\s+publish", "publishes a package"),
  (r"twine\s+upload", "publishes to PyPI"),
)


class PublicationBlocked(RuntimeError):
    """Raised when an operation would make private work public."""


STATE = {
  "visibility": "PRIVATE",
  "may_publish": False,
  "reason": "publication is an operator-only act, never automated. Preparation is authorised; publication is not.",
  "who_may_change_it": "the operator, personally, outside this codebase, with their own "
                       "credentials. There is deliberately no override flag here.",
}


def assert_may_publish(action: str = "publish") -> None:
    raise PublicationBlocked(
        "refusing to %s. %s %s" % (action, STATE["reason"], STATE["who_may_change_it"]))


def scan_command(cmd: str) -> list:
    """Every forbidden publication action in a command string."""
    return [{"pattern": pat, "would": what}
            for pat, what in FORBIDDEN_PATTERNS
            if re.search(pat, cmd or "", re.IGNORECASE)]


def assert_command_safe(cmd: str, *, where: str = "<command>") -> dict:
    """Refuse a command that would publish."""
    hits = scan_command(cmd)
    if hits:
        raise PublicationBlocked(
            "%s would %s. Publication is blocked: %s"
            % (where, "; ".join(h["would"] for h in hits), STATE["reason"]))
    return {"where": where, "safe": True}


def assert_release_excludes_user_data(export_dir) -> dict:
    """Refuse a prepared export that contains the operator's user data."""
    from argus.core import user_data
    rep = user_data.verify_release(export_dir)
    if not rep["clean"]:
        raise PublicationBlocked(
            "%s contains %d user-data file(s), e.g. %s. User data never leaves this machine."
            % (export_dir, len(rep["findings"]),
               [(f["path"], f["rule"]) for f in rep["findings"][:5]]))
    return rep


def posture() -> dict:
    """For a receipt, so every release artifact records that nothing was published."""
    return dict(STATE, enforced_by=__name__,
                forbidden_actions=[what for _, what in FORBIDDEN_PATTERNS])
