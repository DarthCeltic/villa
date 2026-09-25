#!/bin/sh
# Starts the UI command-transport (bff) on loopback, then runs nginx in the foreground as the
# container's main process. bff must be loopback-only (argus/serve.py enforces this itself,
# returning exit code 2 on any other bind) and must therefore share nginx's network namespace,
# which is exactly what "same container" gives it -- this is not a workaround, it is what the
# ui-transport module's own security design requires of whatever puts it behind a public origin.
set -e

python -m argus.serve ui-transport --host 127.0.0.1 &
BFF_PID=$!

trap 'kill -TERM "$BFF_PID" 2>/dev/null' TERM INT

# Give the bff a moment to either bind or fail fast (e.g. missing command token file) before
# nginx starts proxying to it; nginx itself will still retry on a slow start regardless.
for i in $(seq 1 20); do
    if ! kill -0 "$BFF_PID" 2>/dev/null; then
        echo "ui-transport exited before starting nginx -- see logs above" >&2
        exit 1
    fi
    python -c "import socket,sys; s=socket.create_connection(('127.0.0.1',8789),1)" 2>/dev/null && break
    sleep 0.5
done

# exec, not `nginx & wait -n ...`: this image's /bin/sh is dash, whose `wait` builtin has no
# `-n` (that's a bash-ism -- the first version of this script used it and crash-looped every
# few seconds, confirmed via `docker logs` showing repeated "Illegal option -n"). nginx becomes
# PID 1 here; a container stop sends it SIGTERM directly, and container teardown reclaims the
# backgrounded bff process regardless, so nothing is orphaned in practice.
exec nginx -g "daemon off;"
