# ARGUS container image -- multi-stage, three runtime targets sharing one dependency base.
#
# Base image digests are pinned (immutable), not floating tags -- ":3.11-slim" moves under you;
# "@sha256:..." does not. Pulled and verified locally on 2026-09-18 via `docker pull` +
# `docker inspect --format='{{index .RepoDigests 0}}'`.
#
# Targets:
#   service  -- runs either the observe (read-only) or command (governed-write) API. Which one
#               is chosen at container-start time by CMD/entrypoint args in docker-compose.yml,
#               not by building two separate images -- they share one dependency set
#               (argus/service/app.py and argus/service/command.py both live in the same
#               package, per pyproject.toml's [service] extra).
#   ui       -- nginx serving the built production UI bundle, plus the UI command-transport
#               (bff) process on loopback inside the SAME container. This is not a convenience
#               shortcut: argus/serve.py's ui-transport subcommand hard-refuses (`return 2`) to
#               bind anything but 127.0.0.1, by design, because it is the one process that holds
#               the command-service bearer token. nginx reverse-proxies /ui/* to it over
#               loopback inside this container and serves the static bundle for everything else.
#
# No CUDA layer anywhere: neither the observe nor the command service imports torch or runs
# inference -- both are declared explicitly under `service`, which the ordinary [service] extra
# never pulls in the surface-model extra (torch, nnunetv2) is a wholly separate optional-deps
# group in pyproject.toml and is never installed here.

# ---------------------------------------------------------------------------- node-build stage
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS node-build
WORKDIR /ui
COPY argus/ui/package.json argus/ui/package-lock.json* ./
RUN npm ci
COPY argus/ui/ ./
RUN npm run build

# -------------------------------------------------------------------------------- python base
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS py-base
RUN groupadd --gid 1000 argus && useradd --uid 1000 --gid argus --create-home --shell /usr/sbin/nologin argus
WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY docker/requirements.lock.txt ./docker/requirements.lock.txt
COPY src/ ./src/
COPY argus/ ./argus/
COPY evidence_gate/ ./evidence_gate/
# Runtime policy and provider-source declarations are application inputs, not
# mutable state.  Keep them in the image so observe/command/UI-transport behave
# the same in Docker as they do from a checkout (notably GET /api/updates).
COPY config/ ./config/
# [volume-local] adds zarr + fsspec so the Raw CT plane and 3D brick routes can read a local OME-Zarr store.
# Every third-party package comes from docker/requirements.lock.txt, pinned by version AND sha256 (`uv pip compile --generate-hashes`, Linux, Python 3.11,
# from pyproject.toml's [service] and [volume-local] extras plus docker/build-requirements.in), so a compromised or newer release on the index cannot
# change what the image contains. ARGUS itself is then installed without resolving anything.
RUN pip install --no-cache-dir --require-hashes -r docker/requirements.lock.txt \
    && pip install --no-cache-dir --no-deps --no-build-isolation -e ".[service,volume-local]"

# ------------------------------------------------------------------------ service (observe/command)
FROM py-base AS service
ENV ARGUS_REPO=/app \
    PYTHONUNBUFFERED=1
RUN mkdir -p /argus-home/state /argus-home/science /private-science && chown -R argus:argus /argus-home /app
USER argus
EXPOSE 8787 8788
ENTRYPOINT ["python", "-m", "argus.serve"]
CMD ["observe", "--host", "0.0.0.0"]

# ------------------------------------------------------------------------------------- ui
FROM py-base AS ui
RUN apt-get update && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/*
ENV ARGUS_REPO=/app \
    PYTHONUNBUFFERED=1
COPY --from=node-build /ui/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/ui-entrypoint.sh /entrypoint.sh
RUN mkdir -p /argus-home/state /argus-home/bff /var/lib/nginx/body /var/lib/nginx/proxy /var/log/nginx /var/run/nginx \
    && chown -R argus:argus /argus-home /app /usr/share/nginx/html /etc/nginx/nginx.conf \
       /var/lib/nginx /var/log/nginx /var/run/nginx \
    && chmod +x /entrypoint.sh
USER argus
EXPOSE 8792
ENTRYPOINT ["/entrypoint.sh"]
