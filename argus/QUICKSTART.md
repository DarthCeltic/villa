# ARGUS quickstart

The fastest route to a running ARGUS is the Docker stack. It needs Docker Desktop (or Docker Engine
with the Compose plugin) and nothing else on the host: no Python or Node setup.

## 1. Start it

**Windows.** Double-click `Start-ARGUS.cmd` in the root of the checkout. It starts Docker Desktop if
it is not running, builds and starts the stack, waits until the interface and the read-only service
are both healthy, and opens <http://127.0.0.1:8792>. It stops with a specific message instead of
opening a half-ready page. Double-click `Stop-ARGUS.cmd` when you are finished; command state and the
audit log are kept.

**Any platform.** From a terminal, with the `argus` command installed (`pip install -e .`, Python
3.11+):

```bash
argus start                     # build, start, and wait until ARGUS is ready
argus status                    # is it running, is it ready, and if not, why  (--json for scripts)
argus stop                      # stop the stack, keeping its data
argus stop --down               # also remove the containers (named volumes are never removed)
argus start --profile cpu_only  # the resource profile the service reports (see argus start --help)
```

`argus start` also accepts `--no-build` (reuse built images), `--no-wait` and `--timeout SECONDS`.
Without the command, the same stack starts with `docker compose up -d --build` and stops with
`docker compose down`.

## 2. What is running

| service | what it is | host port |
|---|---|---|
| `observe` | the read-only service (`GET /api/health` answers `"read_only": true`) | 18787 (container port 8787) |
| `ui` | nginx serving the built interface, plus the loopback-only UI transport that holds the command credential | 8792 |
| `command` | the token-authenticated command service that runs governed actions | none, on purpose: reachable only from `ui` over the compose network |

Everything is published on `127.0.0.1` only. The containers run as a non-root user with every Linux
capability dropped.

- State (the generated command token, the operator UI access key and the audit log) lives in the
  named volume `argus_state`. `docker compose down` keeps it; only `docker compose down -v` deletes it.
- `artifacts/` is bind-mounted read-write from your checkout, so receipts the containers write land in
  your working tree. `corpus/` is mounted read-only.
- There is no GPU, no CT data and no model weights in the containers. Rooms that need them show an
  empty state that says what is missing. Governed actions that would fetch or prepare data stay
  refused until an operator sets the matching `ARGUS_*_ENABLED` flag; the refusal names the flag.

## 3. The operator key

The command container generates an operator access key on first start. You need it only when you
open a governed session in the interface:

```bash
docker compose exec command cat /argus-home/state/ui_access_key
```

Paste it into the prompt the interface shows for its first governed action. The browser sends it only
to `POST /ui/session`; the interface does not store it. Treat terminal output and clipboard history
that contain it as sensitive. If the command service has not started, the session endpoint fails closed
and no session is issued.

## 4. Or run it natively

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[service,test]"
.venv\Scripts\python -m argus demo       # offline, about a second, labelled DEMONSTRATION_ONLY
.venv\Scripts\python -m argus doctor     # what this machine can and cannot do, and how to fix it
```

`argus doctor` exits 1 on a fresh machine and lists what is missing, its licence and the command that
fetches it. That is an honest finding, not a broken install. The interface needs Node 20 or newer
(`cd argus/ui && npm ci && npm run dev`).

## 5. Check your own data and providers

```bash
python scripts/run_portable_ci.py --list    # the test kit, by the question each group answers
python scripts/run_portable_ci.py           # run all of it (offline, no GPU, no private data)
```

See `docs/TEST_KIT.md`, and `MODEL_PACKAGE_BOUNDARY.md` for what a model or data package must declare.

## Claim boundary

This release ships no detector and makes no scientific claim. It does not read unread scrolls.
**A rendered candidate is not a reading.** Every export packet is `never_published`. What ARGUS
claims and does not claim is in `PUBLIC_CLAIMS_AND_LIMITS.md`.
