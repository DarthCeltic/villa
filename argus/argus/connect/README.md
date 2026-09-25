# ARGUS MCP connector

The connector lets an MCP-capable assistant inspect ARGUS and request dry-run plans. It follows
the verified live stack through the local instance registry, so release ports do not need to be
copied into client settings.

It exposes eight read-only tools: `argus_overview`, `argus_list_scrolls`, `argus_scroll_brief`,
`argus_findings`, `argus_jobs`, `argus_list_actions`, `argus_plan_action` and `argus_ui_link`.
It cannot submit, cancel or resume a job. State-changing work remains in the governed ARGUS UI.

Per-target prize intelligence is withheld by default because connector results are sent to the
assistant's model provider. Set `ARGUS_CONNECT_TARGET_DETAIL=allow` only when that disclosure is
intended.

## Runtime

Give the connector its own small Python environment; it only needs the MCP package and never loads
the science runtime.

```sh
python -m venv /opt/argus-connect
/opt/argus-connect/bin/python -m pip install "mcp==2.2.0"
```

Configure the client to run:

```text
/opt/argus-connect/bin/python -m argus.connect.launcher
```

Set `PYTHONPATH=/opt/argus-public` in that process, replacing the example path with the public
ARGUS export. An ARGUS stack must already be running.

The launcher chooses the newest healthy local stack. To pin one candidate, set
`ARGUS_CONNECT_BUILD` to its build-SHA prefix. Explicit `ARGUS_READ_URL`, `ARGUS_COMMAND_URL` and
`ARGUS_UI_URL` overrides are accepted only as a complete set and must remain on loopback.

Successful setup is proved in the client: its tool list contains `argus_overview`. The System
screen reports whether the connector code, dedicated runtime and live-stack discovery are present,
but it does not pretend to observe a client-owned stdio process.
