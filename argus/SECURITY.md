# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use the repository host's private
vulnerability reporting (the "Report a vulnerability" button on the Security tab) and include steps
to reproduce. We will acknowledge the report and agree on a disclosure timeline with you.

## Design boundaries you can rely on

- **Loopback only.** The services bind to 127.0.0.1 by default. The UI transport refuses any other
  address. In the Docker stack the two published ports (the interface and the read-only service) are
  published on 127.0.0.1 only, and the command service is not published at all.
- **Contained.** The containers run as a non-root user with `no-new-privileges` and every Linux
  capability dropped. The third-party packages the image installs are pinned by version and sha256.
- **Read-only observatory.** The observatory service answers every write method with 405.
- **Authenticated commands.** The command service requires a bearer token on every route except
  `/health`. The token is generated on first start, stored under `ARGUS_HOME/state` outside the
  repository, and never returned by any endpoint or written to a log.
- **No arbitrary execution.** Commands resolve to registered callables. No route accepts a shell
  command, script path, module name or URL to execute.
- **No source maps in production builds.**
- **Receipts are sanitised** before the service serves them: local paths and credential-shaped
  strings are withheld.

## Out of scope

Exposing the services beyond loopback, or running the UI transport on a shared machine, is outside
the supported configuration.
