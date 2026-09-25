# Troubleshooting

**`argus doctor` exits 1 on a new machine.** This is expected. Each FAIL line ends with the command
that fixes it. External components show as FAIL until you accept their licences with
`argus setup --accept <id>`. The upstream pin shows as FAIL until the pinned checkout exists.

**`argus doctor` reports UNKNOWN.** The check could not run, for example the deep GPU probe, which
does not run unless you pass `--deep`. UNKNOWN never counts as a pass.

**`ModuleNotFoundError: numpy`.** Install ARGUS into a virtual environment with
`pip install -e ".[service,test]"`. numpy is the only mandatory dependency.

**`ModuleNotFoundError: fastapi` / `uvicorn` when starting the service.** Install the `service`
extra: `pip install -e ".[service]"`.

**Detector modules fail with `No module named torch`.** Detector code needs the `surface-model`
extra and a torch wheel that matches your GPU. `setup`, `doctor`, `demo` and the UI do not need it.

**The UI shows "not available, and why" almost everywhere.** A fresh install has no scan data,
receipts or components, so that is the correct state. Each empty state names what is missing.

**The UI cannot reach the service (502 or connection refused).** The dev server proxies `/api` to
`127.0.0.1:${ARGUS_API_PORT:-8787}`. Start the observatory on that port:
`python -m argus.serve observe --port 8787`. Both processes bind to 127.0.0.1.

**Port already in use.** Choose other ports: `ARGUS_UI_PORT=5180 ARGUS_API_PORT=8790 npm run dev`,
with `python -m argus.serve observe --port 8790`.

**Every POST to the observatory returns 405.** This is by design. The observatory is read-only.
Actions go through the command service, which needs its token.

**`npm ci` fails.** Use Node 20 or newer (the Docker image uses Node 20, continuous integration Node 22), and run it
from `argus/ui`, where `package-lock.json` lives.

**Where does ARGUS write?** Everything goes under `ARGUS_HOME` (default `~/.argus`) and the roots in
`config/argus.example.env`. Point `ARGUS_HOME` at a temporary directory to try ARGUS without
touching your home directory.

**`argus start` or `Start-ARGUS.cmd` says Docker is not running.** Start Docker Desktop (or the Docker
engine), wait until it reports ready, and run it again. `argus status` says what is running, whether it
is ready, and why not. The first build downloads a base image and needs about 10 GiB free.

**A container is unhealthy or restarting.** `docker compose ps` names it and `docker compose logs <service>`
shows why. The interface waits for both the command service and the read-only service, so start with
those.

**A port is already in use (8792 or 18787).** Stop whatever holds it; the interface's allowed origin is exactly
`http://127.0.0.1:8792`, so its port is not configurable.

**Receipts written by the containers are owned by root on Linux.** Keep `artifacts/` and
`.private-science-unmounted/` as they are in the repository: they exist so Docker does not create
them root-owned.

**Linux or macOS.** The install path is verified on Windows only. The commands are the same with
`.venv/bin/python`; please report what breaks (see `CONTRIBUTING.md`).
