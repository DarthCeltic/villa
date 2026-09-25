"""Neutral, configurable roots for the public build of ARGUS."""
from __future__ import annotations

import os
import pathlib


def home() -> pathlib.Path:
    env = os.environ.get("ARGUS_HOME")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".argus"


def repo() -> pathlib.Path:
    env = os.environ.get("ARGUS_REPO")
    return pathlib.Path(env) if env else pathlib.Path(__file__).resolve().parents[1]


def public_path(kind: str, rest: str = "") -> str:
    """kind: home | repo | legacy | cache | anchor. Returns a string, as the literal it replaced."""
    base = {"home": home(), "repo": repo(), "legacy": home() / "legacy",
            "cache": home() / "cache", "anchor": pathlib.Path(home().anchor or "/")}[kind]
    p = base / rest if rest else base
    s = p.as_posix()
    return s if s.endswith("/") or kind != "anchor" or rest else s + "/"
