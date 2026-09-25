"""Start an ARGUS service."""
from __future__ import annotations

import argparse
import os


_MARKER = None


def _admit_and_register(role: str, port: int) -> bool:
    """Join the machine-wide stack registry (evicting idle stacks if memory needs it), or refuse."""
    global _MARKER
    from argus.core import instance_guard as IG
    try:
        adm = IG.admit(role, port)
    except IG.StackRefused as exc:
        print(str(exc), flush=True)
        return False
    for key in adm.get("evicted") or []:
        print("  retired an idle ARGUS stack to make room: %s" % key, flush=True)
    _MARKER = IG.register(role, port)["activity_marker"]
    return True


def _serve(app_path: str, host: str, port: int) -> None:
    """Run the app with real requests (not health probes) refreshing this stack's activity marker."""
    import importlib
    import uvicorn
    from argus.core import instance_guard as IG
    mod, _, name = app_path.partition(":")
    app = getattr(importlib.import_module(mod), name)
    uvicorn.run(IG.track(app, _MARKER) if _MARKER else app, host=host, port=port, log_level="info")


def main(argv=None) -> int:
    import sys
    from argus.core import process_hardening
    process_hardening.apply()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("instances", "find", "stop-stack", "admit"):
        from argus.core import instance_guard as IG
        return IG.cli(args)
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    ap.add_argument("service", choices=["observe", "command", "ui-transport"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    ns = ap.parse_args(args)

    if ns.service == "ui-transport":
        from argus.service.bff import ALLOWED_ORIGINS, OPERATIONS
        port = ns.port or int(os.environ.get("ARGUS_UI_TRANSPORT_PORT", 8789))
        if ns.host not in ("127.0.0.1", "localhost"):
            print("REFUSED: the UI transport binds loopback only. It holds a credential, so "
                  "a non-loopback bind would publish the ability to act.")
            return 2
        if not _admit_and_register("ui-transport", port):
            return 3
        print("ARGUS UI command transport on %s:%d" % (ns.host, port))
        print("  carries: %s" % ", ".join(sorted(OPERATIONS)))
        print("  origins: %s" % ", ".join(sorted(ALLOWED_ORIGINS)))
        print("  the command token is read here and never returned to a browser")
        _serve("argus.service.bff:app", ns.host, port)
    elif ns.service == "command":
        from argus.service.command import (UI_ACCESS_KEY_PATH, TOKEN_PATH, _token,
                                           _ui_access_key)
        from argus.core import update_scheduler
        port = ns.port or int(os.environ.get("ARGUS_COMMAND_PORT", 8787))
        if not _admit_and_register("command", port):
            return 3
        _token()
        _ui_access_key()
        scheduler_started = update_scheduler.start()
        print("ARGUS command service on %s:%d" % (ns.host, port))
        print("  token file: %s  (the service never prints or returns its value)"
              % TOKEN_PATH)
        print("  operator access key file: %s  (the service never prints or returns its value)"
              % UI_ACCESS_KEY_PATH)
        print("  automatic upstream metadata checks: %s" %
              ("enabled" if scheduler_started else "disabled by ARGUS_UPDATE_SCHEDULER_ENABLED"))
        if ns.host not in ("127.0.0.1", "localhost"):
            print("  NOTE: binding beyond loopback. Everything here is authenticated, but "
                  "this port should still reach the internet only through an authenticated private network.")
        _serve("argus.service.command:app", ns.host, port)
    else:
        port = ns.port or int(os.environ.get("ARGUS_PORT", 8000))
        os.environ["ARGUS_SERVICE_PORT"] = str(port)
        if not _admit_and_register("observe", port):
            return 3
        print("ARGUS observatory (read-only) on %s:%d" % (ns.host, port))
        _serve("argus.service.app:app", ns.host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
