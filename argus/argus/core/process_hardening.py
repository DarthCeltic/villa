"""Process-wide settings that must hold before anything launches a subprocess."""
from __future__ import annotations

import os


def apply() -> None:
    """On Windows a bare program name (`git`, `docker`, `nvidia-smi`) is searched for in the current directory before PATH, so running ARGUS from a folder that holds someone else's `git.exe` would run it."""
    if os.name == "nt":
        os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"


def which_on_path(name: str):
    """`shutil.which`, except that on Windows a program that is only in the current directory is not found: running ARGUS from an untrusted folder must not execute a `docker.exe` or `nvidia-smi.exe` that..."""
    import shutil
    found = shutil.which(name)
    if found and os.name == "nt":
        here = os.path.normcase(os.path.dirname(os.path.abspath(found)))
        on_path = any(os.path.normcase(os.path.abspath(d)) == here for d in os.environ.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d))
        if here == os.path.normcase(os.getcwd()) and not on_path:
            return None
    return found
