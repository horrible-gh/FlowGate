# FlowGate — container image (FastAPI API + built Vue client in one process)
#
# Mirrors what setup.sh does on a host, but baked into an image:
#   1. build the client (client_gui/dist, same-origin API base = /flowgate): the /app SPA
#      plus the sign-in page (login.html) the server returns for / and /login
#   2. install the Python server + dependencies
#   3. run uvicorn, which also serves the built client as static files
#
# Persistent state (SQLite DB, document storage, generated secrets) lives under
# /data — mount a volume there (see docker-compose.yml). The image itself is
# stateless and safe to rebuild/replace.

# ── Stage 1: client (client_gui/dist) — a failure here MUST fail the image ──────
# flowgate.v02gui.0024: client_gui is the only client (v0.1's client/ is gone), so an image
# without it would have no sign-in page and no screens at all. Any install or build error
# stops the image build here instead of shipping a server with nothing to serve.
FROM node:20-slim AS client-gui-build
WORKDIR /build/client_gui
COPY client_gui/package.json client_gui/package-lock.json* ./
RUN npm install
COPY client_gui/ ./
RUN printf 'VITE_API_BASE_URL=/flowgate\n' > .env.production.local && npm run build

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

# Built client from stage 1 (main.py resolves ../../client_gui/dist relative to
# server/routers, i.e. /app/client_gui/dist — see server/routers/main.py's _CLIENT_GUI_DIST).
COPY --from=client-gui-build /build/client_gui/dist ./client_gui/dist

# Entrypoint: materialize/persist secrets, wait for a networked DB, optionally
# seed an admin, then exec uvicorn.
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Persistent state (SQLite DB + document storage + secrets) — declare as a
# volume so it isn't baked into the image layer.
VOLUME ["/data"]

# uvicorn resolves static/ and client_gui/dist relative to server/, so run there.
WORKDIR /app/server

EXPOSE 8089

# Same-origin liveness probe: "/" serves the sign-in page (login.html) once dist is mounted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8089/', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# --timeout-graceful-shutdown: backstop so a stuck in-flight request (e.g. a
# long-lived SSE stream) cannot block container shutdown indefinitely (group
# 0102 R0001); the app-level shutdown_event is the primary, immediate mechanism.
CMD ["python", "-m", "uvicorn", "routers.main:app", "--host", "0.0.0.0", "--port", "8089", "--timeout-graceful-shutdown", "3"]
