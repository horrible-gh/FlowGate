#!/bin/sh
# FlowGate container entrypoint.
#
# The host setup.sh writes a server/.env with generated secrets; in a container
# that file doesn't exist, so this script provides the same guarantees at start:
#   - sane defaults for the required Settings fields (so `docker run` works bare)
#   - SECRET_KEY + token pepper generated ONCE and persisted to the data volume
#     (never rotated on restart — JWTs and token hashes depend on them)
#   - SQLite DB path defaulted under the data volume
#   - for a networked DB (mysql/postgres): wait until it accepts connections
#   - optional, idempotent admin bootstrap (SQLite only)
# pydantic reads these from the OS environment (case-insensitive), so no .env
# file is needed inside the container.
set -eu

# ── Defaults for required config (overridable via -e / compose environment) ──
: "${FLOWGATE_STORAGE_DIR:=/data}"
: "${CONTEXT:=/flowgate}"
: "${ALLOWED_ORIGIN:=*}"
: "${DB_TYPE:=sqlite3}"
export FLOWGATE_STORAGE_DIR CONTEXT ALLOWED_ORIGIN DB_TYPE

# Compose forwards unset overrides (e.g. `DB_PORT: ${DB_PORT:-}`) as EMPTY
# strings. pydantic would then try to coerce "" into DB_PORT:int and crash on a
# plain SQLite start. Drop empty DB_* overrides so the declared defaults apply.
for _v in DB_HOST DB_PORT DB_USER DB_PASSWORD DB_DATABASE DB_SCHEMA; do
    eval "_val=\${$_v:-}"
    [ -z "$_val" ] && unset "$_v" 2>/dev/null || true
done

mkdir -p "$FLOWGATE_STORAGE_DIR"
SECRETS_FILE="$FLOWGATE_STORAGE_DIR/.flowgate-secrets.env"

# ── Load previously persisted secrets (does not override explicit env) ───────
if [ -f "$SECRETS_FILE" ]; then
    while IFS='=' read -r k v; do
        [ -z "$k" ] && continue
        case "$k" in \#*) continue ;; esac
        if [ -z "$(printenv "$k" || true)" ]; then
            export "$k=$v"
        fi
    done < "$SECRETS_FILE"
fi

gen() { python -c 'import secrets; print(secrets.token_hex(32))'; }
persist() { printf '%s=%s\n' "$1" "$2" >> "$SECRETS_FILE"; }

# SECRET_KEY — stable across restarts (JWT signing key).
if [ -z "${SECRET_KEY:-}" ]; then
    SECRET_KEY="$(gen)"; export SECRET_KEY; persist SECRET_KEY "$SECRET_KEY"
    echo "[entrypoint] generated a new SECRET_KEY (persisted to the data volume)"
fi

# Token pepper (D020) — token hashes depend on it; never rotate silently.
if [ -z "${FLOWGATE_TOKEN_PEPPER_ACTIVE_ID:-}" ]; then
    FLOWGATE_TOKEN_PEPPER_v1="$(gen)"
    FLOWGATE_TOKEN_PEPPER_ACTIVE_ID="v1"
    export FLOWGATE_TOKEN_PEPPER_v1 FLOWGATE_TOKEN_PEPPER_ACTIVE_ID
    persist FLOWGATE_TOKEN_PEPPER_v1 "$FLOWGATE_TOKEN_PEPPER_v1"
    persist FLOWGATE_TOKEN_PEPPER_ACTIVE_ID "v1"
    echo "[entrypoint] generated a new token pepper (persisted to the data volume)"
fi

# Git credential encryption key (0115 L0006 E5) — base64 32-byte AES key;
# stored git tokens become unreadable if it changes, so persist it once.
if [ -z "${FLOWGATE_GIT_ENCRYPT_KEY:-}" ]; then
    FLOWGATE_GIT_ENCRYPT_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
    export FLOWGATE_GIT_ENCRYPT_KEY
    persist FLOWGATE_GIT_ENCRYPT_KEY "$FLOWGATE_GIT_ENCRYPT_KEY"
    echo "[entrypoint] generated a new git credential key (persisted to the data volume)"
fi

# TOTP secret encryption key (0273 NR0003 P1-3) — base64 32-byte AES key read by
# modules/flow_gate/auth/totp_service.py. It was missing from every install path,
# including this one. The server boots fine without it; the failure appears later
# as a RuntimeError the first time a user enrols in 2FA. Persist it once —
# changing it makes already-enrolled secrets unreadable.
if [ -z "${FLOWGATE_TOTP_ENCRYPT_KEY:-}" ]; then
    FLOWGATE_TOTP_ENCRYPT_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
    export FLOWGATE_TOTP_ENCRYPT_KEY
    persist FLOWGATE_TOTP_ENCRYPT_KEY "$FLOWGATE_TOTP_ENCRYPT_KEY"
    echo "[entrypoint] generated a new TOTP encryption key (persisted to the data volume)"
fi
[ -f "$SECRETS_FILE" ] && chmod 600 "$SECRETS_FILE" 2>/dev/null || true

# ── DB selection ─────────────────────────────────────────────────────────────
case "$DB_TYPE" in
    sqlite|sqlite3|local)
        : "${DB_PATH:=$FLOWGATE_STORAGE_DIR/flowgate.db}"
        export DB_PATH
        echo "[entrypoint] DB_TYPE=$DB_TYPE  (file: $DB_PATH)"
        ;;
    mysql|postgres)
        : "${DB_HOST:=db}"
        if [ "$DB_TYPE" = "postgres" ]; then
            : "${DB_PORT:=5432}"
            : "${DB_SCHEMA:=public}"   # postgres default schema (matches setup.sh)
            export DB_SCHEMA
        else
            : "${DB_PORT:=3306}"
        fi
        export DB_HOST DB_PORT
        echo "[entrypoint] DB_TYPE=$DB_TYPE  waiting for $DB_HOST:$DB_PORT ..."
        i=0
        while [ "$i" -lt 60 ]; do
            if python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('$DB_HOST', int('$DB_PORT'))); s.close()" 2>/dev/null; then
                echo "[entrypoint] $DB_TYPE is reachable."
                break
            fi
            i=$((i + 1)); sleep 2
        done
        [ "$i" -ge 60 ] && echo "[entrypoint] WARNING: $DB_HOST:$DB_PORT not reachable after 120s — starting anyway."
        ;;
    *)
        echo "[entrypoint] WARNING: unrecognized DB_TYPE='$DB_TYPE' — letting the server validate it."
        ;;
esac

# ── Optional admin bootstrap (idempotent; every engine) ──────────────────────
# 0273 NR0003 P1-1: this was SQLite-only because create_dev_user.py opened the DB
# with `import sqlite3`, so a mysql/postgres container came up with no account
# that could log in. The script is engine-neutral now (server/db_bootstrap.py).
# Set FLOWGATE_ADMIN_USERNAME + FLOWGATE_ADMIN_PASSWORD to auto-seed the first
# account; re-runs skip if it already exists.
if [ -n "${FLOWGATE_ADMIN_USERNAME:-}" ] && [ -n "${FLOWGATE_ADMIN_PASSWORD:-}" ]; then
    echo "[entrypoint] ensuring admin account '$FLOWGATE_ADMIN_USERNAME' ..."
    # Importing config runs auto-migration → creates the schema before seeding.
    ( cd /app/server && python -c "import config" ) \
        || echo "[entrypoint] schema init warning (continuing)"
    ( cd /app/server && python create_dev_user.py \
        --username "$FLOWGATE_ADMIN_USERNAME" \
        --email "${FLOWGATE_ADMIN_EMAIL:-${FLOWGATE_ADMIN_USERNAME}@flowgate.local}" \
        --password "$FLOWGATE_ADMIN_PASSWORD" --admin ) \
        || echo "[entrypoint] admin seed skipped/failed (continuing)"
fi

echo "[entrypoint] starting: $*"
exec "$@"
