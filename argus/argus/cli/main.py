"""The front-door commands, and nothing else."""
from __future__ import annotations

import sys
from importlib import import_module

if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

USAGE = """argus -- read a scroll, or find out honestly why you cannot yet

usage: argus <command> [options]

  setup    acquire and verify everything this machine needs.
           Shows each component's licence and asks BEFORE fetching it, downloads
           anything once, resumes safely, and refuses whatever it cannot verify.

  doctor   check the install and say precisely what is wrong and how to fix it.
           A check that cannot run reports UNKNOWN, never pass.

  demo     a synthetic demonstration. No network, no GPU, about a second.
           Its output is labelled DEMONSTRATION_ONLY by construction.

  run      run the real pipeline on a chosen target.
           Refuses a target it cannot name unambiguously, a machine that cannot
           finish, an input it has not verified, and a volume the model's declared
           provenance policy does not cover.

  start    start the Docker stack (needs Docker Desktop, no Python or Node on the
           host beyond this command) and wait, with a bound, until it is READY.
  stop     stop it; data is kept. `stop --down` removes the containers, never the volumes.
  status   running? ready? which check failed and what to do next. Exit 0 only when READY.

new here?  argus demo     then   argus doctor     then   argus start

operator:  argus identify PATH_OR_URL
           says which official scroll/volume a tifxyz segment, zarr store or volume URL is, from
           its own evidence, with a confidence level, or refuses and says why; then its route.
           argus acquire --dry-run --url ... --scroll ... --volume-id ... --phase A0
           plans an acquisition from local metadata and fetches nothing.
"""


def _dispatch(verb: str):
    """verb -> the module that owns it."""
    if verb == "setup":
        from argus.cli import cmd_setup
        return cmd_setup.run
    if verb == "doctor":
        from argus.cli import cmd_doctor
        return cmd_doctor.run
    if verb == "demo":
        from argus.cli import cmd_demo
        return cmd_demo.run
    if verb == "run":
        from argus.cli import cmd_run
        return cmd_run.run
    if verb in STACK_VERBS:
        from argus.cli import cmd_stack
        return lambda rest, out=sys.stdout: cmd_stack.run([verb] + list(rest), out=out)
    if verb == "acquire":
        from argus.cli import cmd_acquire
        return cmd_acquire.run
    if verb == "identify":
        from argus.cli import cmd_identify
        return cmd_identify.run
    if verb == "userdata":
        try:
            return import_module("argus.cli.cmd_userdata").run
        except ModuleNotFoundError as exc:
            if exc.name == "argus.cli.cmd_userdata":
                return None
            raise
    return None


STACK_VERBS = ("start", "stop", "status")
COMMANDS = ("setup", "doctor", "demo", "run") + STACK_VERBS


def main(argv=None, out=sys.stdout) -> int:
    from argus.core import process_hardening
    process_hardening.apply()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, file=out)
        return 0
    verb, rest = argv[0], argv[1:]
    fn = _dispatch(verb)
    if fn is None:
        print("unknown command %r. The commands are: %s"
              % (verb, ", ".join(COMMANDS)), file=out)
        print(file=out)
        print(USAGE, file=out)
        return 2
    return fn(rest, out=out)


if __name__ == "__main__":
    raise SystemExit(main())
