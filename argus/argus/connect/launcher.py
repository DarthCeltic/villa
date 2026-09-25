"""Start the ARGUS MCP connector against the live local ARGUS stack."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


class DiscoveryError(RuntimeError):
    """No healthy local stack could be selected."""


def registry_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("ARGUS_INSTANCE_REGISTRY"):
        return Path(env["ARGUS_INSTANCE_REGISTRY"])
    base = env.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "ARGUS" / "instances"


def _read_rows(directory: Path) -> list[dict]:
    rows: list[dict] = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("schema") == "argus-instance-v2":
            rows.append(row)
    return rows


def _probe(port: int, timeout_s: float = 2.0) -> dict:
    url = f"http://127.0.0.1:{int(port)}/api/runtime"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise DiscoveryError(f"observe :{port} did not answer with ARGUS runtime identity ({exc})") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "argus-runtime-v1":
        raise DiscoveryError(f"observe :{port} did not return argus-runtime-v1")
    return payload


def candidates(*, env: dict[str, str] | None = None, directory: Path | None = None,
               probe=None) -> list[dict]:
    """Return complete stacks whose live runtime identity matches their registry record."""
    env = os.environ if env is None else env
    probe = _probe if probe is None else probe
    rows = _read_rows(directory or registry_dir(env))
    observed = [row for row in rows if row.get("role") == "observe" and row.get("port")]
    out: list[dict] = []
    for row in observed:
        try:
            runtime = probe(int(row["port"]))
        except (DiscoveryError, OSError, ValueError):
            continue
        identity = runtime.get("service_identity") or {}
        ports = runtime.get("service_ports") or {}
        services = runtime.get("services") or {}
        build = str(identity.get("build_sha") or "")
        registered_build = str(row.get("build_sha") or "")
        if registered_build and build != registered_build:
            continue
        if not all(services.get(name) for name in ("observe", "command", "ui")):
            continue
        try:
            observe = int(ports.get("observe") or row["port"])
            command = int(ports["command"])
            ui = int(ports.get("ui") or row.get("ui_port"))
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "build_sha": build,
            "source_root": str(identity.get("source_root") or row.get("source_root") or ""),
            "argus_home": str(row.get("argus_home") or ""),
            "observe_port": observe,
            "command_port": command,
            "ui_port": ui,
            "started_epoch": float(row.get("started_epoch") or 0),
        })
    return sorted(out, key=lambda item: item["started_epoch"], reverse=True)


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except (OSError, TypeError):
        return False


def select_stack(stacks: list[dict], env: dict[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    build = (env.get("ARGUS_CONNECT_BUILD") or "").strip().lower()
    root = (env.get("ARGUS_CONNECT_ROOT") or "").strip()
    home = (env.get("ARGUS_CONNECT_HOME") or "").strip()
    selected = list(stacks)
    if build:
        selected = [s for s in selected if str(s.get("build_sha") or "").lower().startswith(build)]
    if root:
        selected = [s for s in selected if _same_path(str(s.get("source_root") or ""), root)]
    if home:
        selected = [s for s in selected if _same_path(str(s.get("argus_home") or ""), home)]
    if not selected:
        wanted = build or root or home
        detail = f" matching {wanted!r}" if wanted else ""
        raise DiscoveryError(f"no healthy local ARGUS stack{detail}; start ARGUS, then restart the MCP client")
    return selected[0]


def configure(env: dict[str, str] | None = None, *, directory: Path | None = None,
              probe=None) -> dict:
    """Set projection.py's URLs before it is imported and return the selected public identity."""
    env = os.environ if env is None else env
    explicit = ["ARGUS_READ_URL", "ARGUS_COMMAND_URL", "ARGUS_UI_URL"]
    present = [name for name in explicit if env.get(name)]
    if present:
        if len(present) != len(explicit):
            raise DiscoveryError("explicit connector URLs are incomplete; set READ, COMMAND and UI together")
        if not env.get("ARGUS_COMMAND_TOKEN_FILE") and not env.get("ARGUS_HOME"):
            raise DiscoveryError("explicit connector URLs also require ARGUS_HOME or ARGUS_COMMAND_TOKEN_FILE")
        return {"mode": "explicit", "build_sha": env.get("ARGUS_CONNECT_BUILD") or "unverified"}

    stack = select_stack(candidates(env=env, directory=directory, probe=probe), env)
    env["ARGUS_READ_URL"] = f"http://127.0.0.1:{stack['observe_port']}"
    env["ARGUS_COMMAND_URL"] = f"http://127.0.0.1:{stack['command_port']}"
    env["ARGUS_UI_URL"] = f"http://127.0.0.1:{stack['ui_port']}"
    env["ARGUS_HOME"] = stack["argus_home"]
    env["ARGUS_COMMAND_TOKEN_FILE"] = str(Path(stack["argus_home"]) / "state" / "command_token")
    return {"mode": "discovered", **stack}


def main() -> None:
    try:
        selected = configure()
    except DiscoveryError as exc:
        print(f"ARGUS MCP refused to start: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print("ARGUS MCP attached to build %s on observe :%s" %
          (str(selected.get("build_sha") or "unknown")[:12],
           os.environ["ARGUS_READ_URL"].rsplit(":", 1)[-1]), file=sys.stderr)
    from argus.connect.mcp_server import mcp
    mcp.run("stdio")


if __name__ == "__main__":
    main()
