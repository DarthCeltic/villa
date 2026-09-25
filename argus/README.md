# ARGUS

> **Villa monorepo copy.** The canonical source is <https://github.com/Cinder-Covenant/ARGUS>. This
> directory, `argus/`, is a mirror of a tagged release (`SOURCE_PROVENANCE.json` names its revision and
> `RELEASE_MANIFEST.json` lists every file with its sha256); file issues and pull requests for ARGUS in the
> canonical repository. It is licensed Apache-2.0 (see `LICENSE` and `NOTICE`), which differs from the MIT
> licence of the rest of this repository. Run every command below from this `argus/` directory;
> [docs/VILLA_SUBPROJECT.md](docs/VILLA_SUBPROJECT.md) covers the monorepo layout, `uv`, Docker and CI.

ARGUS is a local instrument for working with Herculaneum scroll CT data. It walks the chain from a
CT volume to a surface to a detector output, and at each step it records where the result came from
and why a step could not run. It works offline and has no cloud component.

Built by **DarthCeltic** and **clexious**, under the
[Cinder Covenant](https://github.com/Cinder-Covenant) organisation. Source:
<https://github.com/Cinder-Covenant/ARGUS>.

> **Claim boundary.** This release ships no detector and makes no scientific claim.
>
> - It does not read unread scrolls, and it claims no ink, readability or prize result.
> - **A rendered candidate is not a reading.**
> - Every export packet is `never_published`: ARGUS prepares evidence for a human to review and never
>   publishes anything itself.
>
> This release is a working instrument and a demonstration on synthetic data.
> `PUBLIC_CLAIMS_AND_LIMITS.md` says exactly what is and is not claimed.

## Start ARGUS

**Recommended: Docker.** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(or Docker Engine with the Compose plugin), then:

- **Windows:** double-click `Start-ARGUS.cmd`. It starts Docker Desktop if needed, builds and starts
  the stack, waits until it is healthy and opens <http://127.0.0.1:8792>. `Stop-ARGUS.cmd` stops it
  and keeps its data.
- **Any platform, from a terminal** (needs Python 3.11+ for the command, see the next section):

  ```bash
  argus start        # build, start, and wait until ARGUS is ready
  argus status       # is it running, is it ready, and if not, why
  argus stop         # stop the stack, keeping its data (add --down to remove the containers)
  ```

  Without the `argus` command, `docker compose up -d --build` starts the same stack.

The stack is three containers (a read-only service, a governed command service and the interface)
published on `127.0.0.1` only. The containers have no GPU passthrough: stages that need a GPU need a
native install and say so. A fresh install has no scan data, so most rooms show an empty state that
explains what is missing. `QUICKSTART.md` has the details.

## Five-minute native start (no Docker)

You need Python 3.11+, Git, and, for the interface, Node 20+ (the Docker image builds it with Node
20; continuous integration builds and tests it with Node 22).

**Windows (PowerShell)**

```powershell
git clone https://github.com/Cinder-Covenant/ARGUS.git
cd ARGUS
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[service,test]"
.venv\Scripts\python -m argus demo        # about a second, offline, labelled DEMONSTRATION_ONLY
.venv\Scripts\python -m argus doctor      # what this machine can and cannot do, and how to fix it
```

**Linux / macOS (bash)** (the commands are the same; only the Windows path is verified, see
`docs/LIMITATIONS.md`)

```bash
git clone https://github.com/Cinder-Covenant/ARGUS.git && cd ARGUS
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[service,test]"
.venv/bin/python -m argus demo
.venv/bin/python -m argus doctor
```

Or run the bootstrap script, which does the same steps and stops at the first failure:
`scripts/bootstrap.ps1` (Windows) or `scripts/bootstrap.sh`.

`argus doctor` exits non-zero on a new machine. That is expected: it lists each missing component,
its licence, and the command that fetches it. Nothing is downloaded until you accept its licence.

## The interface, natively

```powershell
# terminal 1: the read-only service (it refuses every write)
.venv\Scripts\python -m argus.serve observe --port 8787

# terminal 2: the UI
cd argus\ui
npm ci
npm run dev                               # http://127.0.0.1:5173
```

See `docs/WORKFLOW.md` for the full walk from data to evidence.

## Test your own data and providers

`argus/tests/` is a public test kit: hermetic tests, on synthetic inputs, of the controls that decide
whether a result can be trusted. It checks scroll, volume and pitch identity, input representation,
zero-input, label-shuffle, phase and depth sabotage, label composition, boundary and geometry
confounds, lineage and independence, renderer parity, orientation sensitivity, memory limits,
deterministic replay and evidence-packet integrity. It needs no private data and publishes no
outcomes.

```bash
python scripts/run_portable_ci.py            # the whole kit
python scripts/run_portable_ci.py --list     # the groups and the files in each
python -m pytest argus/tests tests           # the same tests through pytest
```

`docs/TEST_KIT.md` explains each group, how to point a check at your own data or provider, and what a
pass does and does not show. `MODEL_PACKAGE_BOUNDARY.md` says what a model or data package must
declare before ARGUS will accept it.

## What is in this repository

| Path | What it is |
|---|---|
| `argus/cli/` | the `argus` command: `setup`, `doctor`, `demo`, `run`, `start`, `stop`, `status` |
| `argus/core/` | gates, metrics, controls, result classes, provenance, licence registry, acquisition identity |
| `argus/service/` | the read-only observatory service, plus the separate authenticated command service |
| `argus/ui/` | the React/Vite interface |
| `argus/tests/`, `tests/` | the public test kit and the release tests |
| `src/argus_vesuvius/` | volume, geometry and baseline helpers the services and tests use |
| `evidence_gate/`, `evidence_gate_fixtures/` | the standalone evidence gate and its synthetic fixtures |
| `Dockerfile`, `docker-compose.yml`, `docker/` | the container stack |
| `Start-ARGUS.cmd`, `Stop-ARGUS.cmd`, `scripts/` | launchers, bootstrap and the test-kit runner |
| `config/argus.example.env` | every configurable root, with neutral defaults |
| `docs/` | architecture, workflow, capabilities, test kit, reproduction, troubleshooting, limitations |
| `SBOM.md`, `THIRD_PARTY_ACKNOWLEDGEMENTS.md` | components, their licences and what ARGUS may do with each |
| `RELEASE_MANIFEST.json` | sha256 of every file in this release (`python tools/verify_manifest.py`) |

## Documentation

- `QUICKSTART.md`: the fastest route to a running stack
- `PUBLIC_CLAIMS_AND_LIMITS.md`: what ARGUS claims, what it does not, and what its tests can show
- `MODEL_PACKAGE_BOUNDARY.md`: what a model or data package must declare, and what ARGUS never ships
- `docs/ARCHITECTURE.md`: components and how data moves between them
- `docs/WORKFLOW.md`: the end-to-end workflow
- `docs/CAPABILITIES.md`: what works now, what is exploratory, what is not built
- `docs/TEST_KIT.md`: the public test kit
- `docs/REPRODUCTION.md`: provenance records and how to reproduce a result
- `docs/TROUBLESHOOTING.md`: common problems
- `docs/LIMITATIONS.md`: what this release does not do
- `docs/ACKNOWLEDGEMENTS.md`: the projects, data and people ARGUS depends on, and their licences
- `CONTRIBUTING.md`, `SECURITY.md`

## Licence

ARGUS source code is licensed under Apache-2.0 (`LICENSE`, `NOTICE`). The grant covers ARGUS code
only. CT data, ink labels, upstream tools and model weights keep their own terms, and none of them
is included here. See `docs/ACKNOWLEDGEMENTS.md`.
