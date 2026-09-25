#!/usr/bin/env bash
# ARGUS bootstrap for Linux/macOS. Creates .venv, installs the exact pinned runtime from
# runtime/requirements.lock.txt and then ARGUS itself (no dependency resolution), verifies the
# environment against the lock, runs the demo and doctor, and optionally builds the UI.
# Stops at the first failure.
#
#   bash scripts/bootstrap.sh [--with-ui]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3.11}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r runtime/requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m argus.core.runtime_identity --require-match
.venv/bin/python -m argus demo --no-render
echo "==> doctor (a non-zero exit on a new machine is expected; read its fixes)"
.venv/bin/python -m argus doctor || true
if [ "${1:-}" = "--with-ui" ]; then
  (cd argus/ui && npm ci --no-audit --no-fund && npm run build)
fi
echo "bootstrap complete. Next: .venv/bin/python -m argus.serve observe --port 8787"
