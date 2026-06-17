# FlowGate — container image (FastAPI API + built Vue client in one process)
#
# Mirrors what setup.sh does on a host, but baked into an image:
#   1. build the client into client/dist (same-origin API base = /flowgate)
#   2. install the Python server + dependencies
#   3. run uvicorn, which also serves the built client as static files
#
# Persistent state (SQLite DB, document storage, generated secrets) lives under
# /data — mount a volume there (see docker-compose.yml). The image itself is
# stateless and safe to rebuild/replace.

# ── Stage 1: build the Vue client ───────────────────────────────────────────
FROM node:20-slim AS client-build
WORKDIR /build/client

# Install deps first (cache layer keyed on the lockfile / manifest only).
COPY client/package.json client/package-lock.json* ./
RUN npm install

# Build with a same-origin API base so the served SPA talks to /flowgate on the
# same host:port as the API (equivalent to client/build.sh's default).
COPY client/ ./
RUN printf 'VITE_API_BASE_URL=/flowgate\n' > .env.production.local \
    && npm run build

# ── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Keep Python lean and unbuffered (logs flush immediately to `docker logs`).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOWGATE_STORAGE_DIR=/data

WORKDIR /app

# Python dependencies (separate layer so source edits don't re-install deps).
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r server/requirements.txt

# Server source.
COPY server/ ./server/

# Built client from stage 1 (main.py serves ../../client/dist relative to
# server/routers, i.e. /app/client/dist).
COPY --from=client-build /build/client/dist ./client/dist

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
CMD ["python", "-m", "uvicorn", "routers.main:app", "--host", "0.0.0.0", "--port", "8089"]
