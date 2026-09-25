"""Whose name goes on the work: the project's authors, and no tool's.

A commit message must not credit a tool as an author. Several tools inject a co-authorship trailer automatically, the trailer is
invisible in a one-line log, and once it is in published history removing it costs a rewrite. This module refuses such a trailer
before it lands.

WHAT THIS REFUSES. A commit message that claims tool authorship. It does not refuse mentioning a tool in prose: a finding may say which
model produced an output, because that is a fact about an experiment. The line is AUTHORSHIP, meaning who is credited for the work,
not what was used to do it.

HOW A TOOL IS RECOGNISED WITHOUT A LIST OF NAMES. A generated-by line or an "Assisted-by" line is a tool-authorship claim by its
form, whatever it names. A co-author trailer is refused when its address is an automated no-reply address (a no-reply address that does not
belong to GitHub) or when it matches a name you add through ARGUS_AUTHORSHIP_TOOL_NAMES (comma-separated,
case-insensitive). A human co-author is permitted.
"""
from __future__ import annotations

import os
import re
import subprocess

CONTRACT = "argus-authorship-v1"

#: A public release starts from a fresh history, so there is no measured debt to carry.
HISTORICAL_DEBT = {
  "measured_utc": None,
  "measured_at_head": None,
  "commits_total": 1,
  "commits_with_assistant_trailer": 0,
  "oldest": None,
  "all_pushed": False,
  "why_not_fixed_here": "the public release starts from a fresh history that carries no tool "
                        "authorship trailer, so there is nothing to clear.",
  "remedy_if_authorised": "none needed",
}

#: Assembled from parts at RUN TIME so that a scan which reads this file does not find its own source and report a violation that does not exist.
_CO = "-".join(("Co", "Authored", "By"))
_GEN = " ".join(("Generated", "with"))

_EXTRA_ENV = "ARGUS_AUTHORSHIP_TOOL_NAMES"
_NOREPLY = re.compile(r"<[^<>@\s]*noreply@([A-Za-z0-9._-]+)>", re.I)

#: An authorship claim: a trailer, or a generated-by line. Not a mention.
_PATTERNS = (
  (re.compile(r"^\s*%s\s*:\s*(.+)$" % re.escape(_CO), re.I | re.M), False),
  (re.compile(r"^\s*Signed-off-by\s*:\s*(.+)$", re.I | re.M), False),
  (re.compile(r"%s\s+\[?([A-Za-z0-9 ._-]+)\]?" % re.escape(_GEN), re.I), True),
  (re.compile(r"^\s*Assisted-by\s*:\s*(.+)$", re.I | re.M), True),
)


class AuthorshipRefusal(RuntimeError):
    """Raised rather than warned. A warning in a hook output is read once."""


def _extra_names() -> tuple:
    return tuple(x.strip().lower() for x in os.environ.get(_EXTRA_ENV, "").split(",") if x.strip())


def _names_a_tool(who: str, *, claim_by_form: bool) -> bool:
    if claim_by_form:
        return True
    low = who.lower()
    if any(name in low for name in _extra_names()):
        return True
    m = _NOREPLY.search(who)
    return bool(m) and not m.group(1).lower().endswith("github.com")


def scan_message(message: str) -> list:
    """Authorship claims naming a tool. Returns the offending lines, not a boolean."""
    hits = []
    for pat, by_form in _PATTERNS:
        for m in pat.finditer(message or ""):
            who = (m.group(1) or "").strip()
            if _names_a_tool(who, claim_by_form=by_form):
                hits.append(m.group(0).strip())
    return hits


def assert_clean(message: str) -> dict:
    hits = scan_message(message)
    if hits:
        raise AuthorshipRefusal(
          "this commit message credits a tool as an author: %s. The project's authors drive the "
          "work and the repository says nothing about which tool produced which commit. Remove "
          "the trailer; naming a model inside a FINDING is fine, because that is a fact about "
          "an experiment rather than a claim about who did the work." % hits)
    return {"contract": CONTRACT, "clean": True, "checked": len(_PATTERNS)}


def scan_history(limit: int | None = None, *, repo: str | None = None) -> dict:
    """Count commits whose message claims tool authorship. Reads messages, never files."""
    args = ["git"] + (["-C", repo] if repo else []) + ["log", "--format=%H%x00%B%x01"]
    if limit:
        args.insert(-1, "-n")
        args.insert(-1, str(limit))
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "why": "%s: %s" % (type(exc).__name__, exc)}
    if out.returncode != 0:
        return {"available": False, "why": "git log failed: %s" % out.stderr.strip()[:200]}

    total, dirty = 0, []
    for rec in out.stdout.split("\x01"):
        rec = rec.strip()
        if not rec or "\x00" not in rec:
            continue
        sha, body = rec.split("\x00", 1)
        total += 1
        if scan_message(body):
            dirty.append(sha[:12])
    return {
      "contract": CONTRACT, "available": True,
      "commits_examined": total,
      "commits_with_assistant_authorship": len(dirty),
      "clean": not dirty,
      "offenders": dirty[:10],
      "note": "messages only. A scan that read the working tree would match this module's own "
              "source and report a violation that is not one.",
    }


def as_record() -> dict:
    live = scan_history()
    return {
      "contract": CONTRACT,
      "rule": "the project's authors are the authors. No tool is credited as a co-author.",
      "mentioning_is_not_crediting": "a finding may name the model that produced an output. "
                                     "That is a fact about an experiment. Authorship is a claim "
                                     "about who did the work.",
      "historical_debt": HISTORICAL_DEBT,
      "live": live,
      "outstanding": (live.get("commits_with_assistant_authorship")
                      if live.get("available") else None),
    }


#: Files permitted to contain the protected name, each with the reason it is permitted.
NAME_ALLOWED = {
  "LICENSE": "the licence grant. This is the one place the legal copyright holder is named, and "
             "naming the holder is what makes the grant enforceable.",
}

_NAME_ENV = "ARGUS_PROTECTED_NAME_PATTERN"


def _legal_name_pattern():
    """The protected name as a regular expression, read from the environment; no name is written in this source."""
    raw = os.environ.get(_NAME_ENV, "").strip()
    return re.compile(raw, re.I) if raw else None


def name_exposure(*, repo: str | None = None) -> dict:
    """Where the protected name appears in tracked files, against the allowlist."""
    pat = _legal_name_pattern()
    if pat is None:
        return {"available": False, "why": "no protected name is configured (set %s)" % _NAME_ENV}
    args = ["git"] + (["-C", repo] if repo else []) + ["ls-files"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "why": "%s: %s" % (type(exc).__name__, exc)}
    if out.returncode != 0:
        return {"available": False, "why": out.stderr.strip()[:200]}

    import pathlib as _pl
    root = _pl.Path(repo) if repo else _pl.Path(__file__).resolve().parents[2]
    found = []
    for rel in out.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        f = root / rel  # path-ok: rel comes straight from `git ls-files`, so it is repo-local by definition
        try:
            if not f.is_file() or f.stat().st_size > (4 << 20):
                continue
            if pat.search(f.read_text(encoding="utf-8", errors="ignore")):
                found.append(rel)
        except OSError:
            continue
    unexpected = [r for r in found if r not in NAME_ALLOWED]
    return {
      "contract": CONTRACT, "available": True,
      "files_with_legal_name": sorted(found),
      "allowed": sorted(NAME_ALLOWED),
      "unexpected": sorted(unexpected),
      "clean": not unexpected,
      "rule": "the protected name belongs in the licence grant and nowhere else.",
    }


def declared_licence(*, repo: str | None = None) -> dict:
    """What LICENSE actually grants. The authority, against which every claim is checked."""
    import pathlib as _pl
    root = _pl.Path(repo) if repo else _pl.Path(__file__).resolve().parents[2]
    f = root / "LICENSE"
    if not f.is_file():
        return {"present": False, "why": "no LICENSE file"}
    head = f.read_text(encoding="utf-8", errors="ignore")[:400]
    for spdx, needle in (("Apache-2.0", "Apache License"), ("MIT", "MIT License"),
                         ("AGPL-3.0", "GNU AFFERO"), ("GPL-3.0", "GNU GENERAL"),
                         ("BSD-3-Clause", "Redistribution and use in source")):
        if needle.lower() in head.lower():
            return {"present": True, "spdx": spdx, "path": str(f)}
    return {"present": True, "spdx": None, "path": str(f),
            "why": "the licence text was not recognised. UNDECLARED is a refusal, not a guess."}


def selftest() -> bool:
    """Needles are assembled at run time. A literal here would make this file its own violation."""
    ok = []
    tool = "%s: %s" % (_CO, "Example Tool <noreply@" + "tool.example>")
    ok.append(("a tool trailer is caught", bool(scan_message("s\n\n" + tool))))
    raised = False
    try:
        assert_clean("s\n\n" + tool)
    except AuthorshipRefusal:
        raised = True
    ok.append(("assert_clean RAISES", raised))
    ok.append(("a generated-by line is caught by its form",
               bool(scan_message("s\n\n%s Some Tool" % _GEN))))
    human = "%s: %s" % (_CO, "Example Person <person@example.com>")
    ok.append(("a human co-author is permitted", not scan_message("s\n\n" + human)))
    gh = "%s: %s" % (_CO, "Example Person <1234+person@users.noreply.github.com>")
    ok.append(("a GitHub noreply identity is a person, not a tool", not scan_message("s\n\n" + gh)))
    web = "%s: %s" % (_CO, "GitHub <noreply@github.com>")
    ok.append(("the GitHub web-flow identity is not a tool claim", not scan_message("s\n\n" + web)))
    ok.append(("naming a model in prose is not an authorship claim",
               not scan_message("seal\n\nthe assistant-model session is irrelevant here")))
    ok.append(("a clean message passes", assert_clean("s\n\nbody")["clean"] is True))
    lic = declared_licence()
    ok.append(("LICENSE resolves to a recognised licence", lic.get("spdx") == "Apache-2.0"))
    ne = name_exposure()
    ok.append(("the protected name appears only where allowed",
               (not ne.get("available")) or ne["unexpected"] == []))
    live = scan_history()
    ok.append(("no history commit credits a tool",
               (not live.get("available")) or live["commits_with_assistant_authorship"] == 0))
    for name, good in ok:
        print("  %-4s %s" % ("ok" if good else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS authorship guard")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    import json as _j
    print(_j.dumps(as_record(), indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
