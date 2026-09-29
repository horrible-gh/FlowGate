# FlowGate — container image (FastAPI API + built Vue clients in one process)
#
# Mirrors what setup.sh does on a host, but baked into an image:
#   1. build v0.1 (client/dist, same-origin API base = /flowgate) and v0.2
#      (client_gui/dist, served under /app — T0005) — each in its OWN stage/install,
#      see the failure-isolation note below
#   2. install the Python server + dependencies
#   3. run uvicorn, which also serves both built clients as static files
#
# Persistent state (SQLite DB, document storage, generated secrets) lives under
# /data — mount a volume there (see docker-compose.yml). The image itself is
# stateless and safe to rebuild/replace.

# ── Stage 1a: v0.1 client (client/dist) — a failure here MUST fail the image ────
# client and client_gui are independent npm packages (own package.json + lockfile +
# node_modules, no npm workspace root tying their installs together — see
# client_gui/vite.config.ts's 'vue'/'vue-i18n'/'axios' aliases for how client_gui
# resolves its own copies of client/shared's dependencies, including in this clean
# per-stage install, without one). Building this in its own stage means a
# client_gui manifest/dependency error literally cannot reach this RUN.
FROM node:20-slim AS client-build
WORKDIR /build/client
COPY client/package.json client/package-lock.json* ./
RUN npm install
COPY client/ ./
RUN printf 'VITE_API_BASE_URL=/flowgate\n' > .env.production.local && npm run build

# ── Stage 1b: v0.2 client_gui (client_gui/dist) — a failure here must NOT fail
# the image (T0005 §8). Its own stage already keeps it from touching stage 1a's
# layers; the `||` fallback additionally keeps THIS stage (and so the image build)
# from failing outright, the same non-fatal contract setup.sh/setup.ps1 apply to
# their client_gui build step. Unlike stage 1a, `npm install` here is INSIDE the
# same non-fatal `||` boundary as the build: a lockfile/manifest/registry error is
# just as much a v0.2-only failure as a build error, so it must not exit the RUN
# (and so the stage, and so the image) before the fallback below runs. `mkdir -p
# dist` on the failure path guarantees the runtime stage's COPY below always has a
# directory to copy — server/routers/main.py only treats client_gui/dist as
# present once it also finds an index.html inside it, so an empty fallback dist
# still makes /app 404 cleanly instead of erroring.
FROM node:20-slim AS client-gui-build
WORKDIR /build/client_gui
COPY client_gui/ ./
COPY client/shared/ /build/client/shared/
RUN (npm install \
     && printf 'VITE_API_BASE_URL=/flowgate\n' > .env.production.local \
     && npm run build) \
    || (echo "[!] client_gui (v0.2) install/build failed - continuing without /app; v0.1 is unaffected." && rm -rf dist && mkdir -p dist)

# ── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Keep Python lean and unbuffered (logs flush immediately to `docker logs`).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOWGATE_STORAGE_DIR=/data

WORKDIR /app

# git — required by the project git integration (0115 L0006 E1): connection
# tests, group worktrees, and merge/push finalize all shell out to git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (separate layer so source edits don't re-install deps).
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r server/requirements.txt

# Server source.
COPY server/ ./server/

# Built clients from stages 1a/1b (main.py resolves both ../../client/dist and
# ../../client_gui/dist relative to server/routers, i.e. /app/client/dist and
# /app/client_gui/dist — see server/routers/main.py's _CLIENT_DIST/_CLIENT_GUI_DIST).
# Two separate COPYs from two separate stages: client_gui-build always produces a
# dist/ (real or the empty failure fallback above), so this can never fail because
# of anything that happened — or didn't — in client_gui's stage.
COPY --from=client-build /build/client/dist ./client/dist
COPY --from=client-gui-build /build/client_gui/dist ./client_gui/dist

# Entrypoint: materialize/persist secrets, wait for a networked DB, optionally
# seed an admin, then exec uvicorn.
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Persistent state (SQLite DB + document storage + secrets) — declare as a
# volume so it isn't baked into the image layer.
VOLUME ["/data"]

# uvicorn resolves static/ and client/dist relative to server/, so run there.
WORKDIR /app/server

EXPOSE 8089

# Same-origin liveness probe: "/" serves the SPA index once dist is mounted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8089/', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# --timeout-graceful-shutdown: backstop so a stuck in-flight request (e.g. a
# long-lived SSE stream) cannot block container shutdown indefinitely (group
# 0102 R0001); the app-level shutdown_event is the primary, immediate mechanism.
CMD ["python", "-m", "uvicorn", "routers.main:app", "--host", "0.0.0.0", "--port", "8089", "--timeout-graceful-shutdown", "3"]
