#!/usr/bin/env bash
#
# FlowGate staging setup (Linux) — end to end:
#   - server: python venv + dependencies
#   - server/.env: working defaults (CONTEXT, DB, SECRET_KEY, token pepper, storage)
#   - client: build → client/dist (same-origin API base)
#   - systemd: install, enable, and start the service
#   - admin: prompt for username/password and create the first account
#   - AI provider: offer to register the first one (server/seed_ai_provider.py)
#
# Declining that last step is fine — ./setup-ai.sh runs it on its own afterwards,
# and is also how you add a second provider to the fallback chain later.
#
# Run once on the staging box as a normal user. sudo is used only for the
# systemd steps and will prompt for a password:
#   ./setup.sh
#
# DB selection (the server supports sqlite3 / mysql / postgres — see
# server/config.py). The engine is asked for interactively; the default is
# sqlite3, which needs no external server. To answer everything up front
# (non-interactive, CI-friendly) preset the values in the environment:
#
#   DB_TYPE=postgres DB_HOST=127.0.0.1 DB_PORT=5432 \
#   DB_USER=flowgate DB_PASSWORD=secret DB_DATABASE=flowgate \
#   FLOWGATE_ADMIN_USERNAME=admin FLOWGATE_ADMIN_PASSWORD=secret ./setup.sh
#
# Other settings this script honours from the environment:
#   FLOWGATE_PORT          listen port          (default 8089)
#   FLOWGATE_BIND_HOST     listen address       (default 0.0.0.0)
#   ALLOWED_ORIGIN         CORS origin          (default '*', with a warning)
#   FLOWGATE_ADMIN_EMAIL   first admin's email  (default <username>@flowgate.local)
#   FLOWGATE_AI_KIND       first AI provider    (claude|copilot|codex — setting any
#                          FLOWGATE_AI_* seeds it without the y/n prompt; the full
#                          list is in ./setup-ai.sh --help)
#
# Migrations auto-apply on first boot from the matching sql/migrations/<db>/
# set (sqlite | mysql | postgres), so no manual schema step is needed.
#
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STORAGE_DIR="$ROOT/storage"
ENV_FILE="$ROOT/server/.env"
SERVICE_FILE="$ROOT/deploy/flowgate.service"
RENDERED_SERVICE="$ROOT/deploy/flowgate.rendered.service"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

# Prompts are skipped when stdin is not a terminal (CI, piped installs) so an
# unattended run falls back to the environment and the documented defaults
# instead of blocking forever on a read.
INTERACTIVE=0
[[ -t 0 ]] && INTERACTIVE=1

# DB selection. 0273 NR0003 §5-1: every other DB_* value had a prompt, but the
# engine itself did not — it was environment-only and silently settled on
# sqlite3, so an operator running ./setup.sh was never actually offered the
# "DB selection" R0001 asked for. Ask, defaulting to sqlite3.
DB_TYPE="${DB_TYPE:-}"
if [[ -z "$DB_TYPE" && $INTERACTIVE -eq 1 ]]; then
    echo "Which database should FlowGate use?"
    echo "  sqlite3   file-backed, no external server (default)"
    echo "  mysql     MySQL / MariaDB"
    echo "  postgres  PostgreSQL"
    read -rp "DB type [sqlite3]: " DB_TYPE
fi
DB_TYPE="${DB_TYPE:-sqlite3}"
case "$DB_TYPE" in
    sqlite|sqlite3|local|mysql|postgres) ;;
    *) echo "[!] Unsupported DB_TYPE='$DB_TYPE' (use sqlite3|mysql|postgres)"; exit 1 ;;
esac

# Listen port (0273 NR0003 P1-2). setup.ps1 has had -Port since it was written;
# on Linux the port was hardcoded in server/stg.py, which the systemd unit runs,
# so a box with 8089 already taken could not be installed without editing source.
FLOWGATE_PORT="${FLOWGATE_PORT:-8089}"
if ! [[ "$FLOWGATE_PORT" =~ ^[0-9]+$ ]] || (( FLOWGATE_PORT < 1 || FLOWGATE_PORT > 65535 )); then
    echo "[!] Invalid FLOWGATE_PORT='$FLOWGATE_PORT' (expected 1-65535)"; exit 1
fi
FLOWGATE_BIND_HOST="${FLOWGATE_BIND_HOST:-0.0.0.0}"

# Set or replace KEY=VALUE in server/.env (| used as sed delimiter for paths).
set_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
}

# True when the key is missing or set to an empty value.
env_unset() { ! grep -q "^${1}=.\+" "$ENV_FILE"; }

# base64-encoded 32-byte key, the form the AES-GCM helpers expect.
gen_b64_key() { python3 -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())'; }

echo "==> Server: venv + dependencies"
# venv lives outside server/ so uvicorn's reload watcher (which watches the
# server/ tree) doesn't crawl thousands of venv files. Remove any stale inner
# venv left by an earlier setup.
rm -rf "$ROOT/server/.venv"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install -r "$ROOT/server/requirements.txt"

echo "==> Server: .env (DB_TYPE=$DB_TYPE)"
[[ -f "$ENV_FILE" ]] || cp "$ROOT/server/.env.sample" "$ENV_FILE"
mkdir -p "$STORAGE_DIR"
set_env CONTEXT /flowgate
set_env DB_TYPE "$DB_TYPE"
case "$DB_TYPE" in
    sqlite|sqlite3|local)
        # File-backed DB — no external server needed.
        set_env DB_PATH "$STORAGE_DIR/flowgate.db"
        ;;
    mysql|postgres)
        # Networked DB — pull connection settings from the environment, prompting
        # for anything left unset so a bare `DB_TYPE=postgres ./setup.sh` still works.
        prompt_default() { local var="$1" msg="$2" def="$3" cur
            cur="$(eval "printf '%s' \"\${$var:-}\"")"
            if [[ -z "$cur" ]]; then read -rp "$msg [$def]: " cur; cur="${cur:-$def}"; fi
            printf '%s' "$cur"
        }
        DB_HOST="$(prompt_default DB_HOST 'DB host' 127.0.0.1)"
        DB_PORT="$(prompt_default DB_PORT 'DB port' "$([[ $DB_TYPE == postgres ]] && echo 5432 || echo 3306)")"
        DB_USER="$(prompt_default DB_USER 'DB user' flowgate)"
        DB_DATABASE="$(prompt_default DB_DATABASE 'DB name' flowgate)"
        DB_SCHEMA="${DB_SCHEMA:-$([[ $DB_TYPE == postgres ]] && echo public || echo '')}"
        if [[ -z "${DB_PASSWORD:-}" ]]; then read -rsp 'DB password: ' DB_PASSWORD; echo; fi
        set_env DB_HOST "$DB_HOST"
        set_env DB_PORT "$DB_PORT"
        set_env DB_USER "$DB_USER"
        set_env DB_PASSWORD "$DB_PASSWORD"
        set_env DB_DATABASE "$DB_DATABASE"
        set_env DB_SCHEMA "$DB_SCHEMA"
        ;;
esac
# FLOWGATE_STORAGE_DIR is NOT a Settings field — having it in .env triggers
# pydantic's extra_forbidden. Strip it; it's passed via the systemd unit instead.
sed -i '/^FLOWGATE_STORAGE_DIR=/d' "$ENV_FILE"
# Generate a SECRET_KEY only when empty — don't rotate it on re-runs.
if ! grep -q '^SECRET_KEY=.\+' "$ENV_FILE"; then
    set_env SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi
# Token pepper (D020): generate v1 and point ACTIVE_ID at it, only when unset.
# Don't rotate on re-runs — existing token hashes depend on this value.
if ! grep -q '^FLOWGATE_TOKEN_PEPPER_v1=.\+' "$ENV_FILE"; then
    set_env FLOWGATE_TOKEN_PEPPER_v1 "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    set_env FLOWGATE_TOKEN_PEPPER_ACTIVE_ID v1
fi
# TOTP secret encryption key (0273 NR0003 P1-3). This existed in NO install path
# — not .env.sample, not either setup script, not the container entrypoint. The
# server still boots without it, so installs looked fine; the failure surfaced
# later, as a RuntimeError the first time a user enrolled in 2FA. Like the
# pepper, rotating it would orphan already-stored secrets, so generate once.
if env_unset FLOWGATE_TOTP_ENCRYPT_KEY; then
    set_env FLOWGATE_TOTP_ENCRYPT_KEY "$(gen_b64_key)"
fi
# Git credential encryption key (0273 NR0003 P2-2). The container entrypoint
# already generates this; host installs left it blank and fell back to a
# plaintext key file under the storage root. Same one-shot rule.
if env_unset FLOWGATE_GIT_ENCRYPT_KEY; then
    set_env FLOWGATE_GIT_ENCRYPT_KEY "$(gen_b64_key)"
fi

# Listen address — read by server/stg.py, which the systemd unit runs.
set_env FLOWGATE_PORT "$FLOWGATE_PORT"
set_env FLOWGATE_BIND_HOST "$FLOWGATE_BIND_HOST"

# CORS (0273 NR0003 P2-1). .env.sample ships ALLOWED_ORIGIN=* and neither setup
# script overwrote it, so every host install finished permanently allowing every
# origin. Ask for the real service URL; keep '*' only as a deliberate, warned choice.
if [[ -z "${ALLOWED_ORIGIN:-}" && $INTERACTIVE -eq 1 ]]; then
    echo
    echo "Service URL browsers will load FlowGate from (used as the CORS origin)."
    echo "Example: https://flowgate.example.com   — leave blank to allow any origin."
    read -rp "Service URL []: " ALLOWED_ORIGIN
fi
if [[ -n "${ALLOWED_ORIGIN:-}" ]]; then
    set_env ALLOWED_ORIGIN "$ALLOWED_ORIGIN"
else
    set_env ALLOWED_ORIGIN '*'
    echo "[!] ALLOWED_ORIGIN=* — every origin may call this API."
    echo "    Set ALLOWED_ORIGIN in $ENV_FILE to your service URL before exposing it."
fi

echo "==> Client: build → dist"
bash "$ROOT/client/build.sh"

echo "==> systemd: install & start (sudo)"
sed -e "s|__ROOT__|$ROOT|g" \
    -e "s|__USER__|$RUN_USER|g" \
    -e "s|__GROUP__|$RUN_GROUP|g" \
    -e "s|__STORAGE__|$STORAGE_DIR|g" \
    "$SERVICE_FILE" > "$RENDERED_SERVICE"
sudo cp "$RENDERED_SERVICE" /etc/systemd/system/flowgate.service
sudo systemctl daemon-reload
sudo systemctl enable --now flowgate || true

echo "==> Admin account"
# 0273 NR0003 P1-1: this bootstrap now runs for EVERY engine. It used to be
# gated on sqlite because create_dev_user.py opened the DB with `import sqlite3`,
# which meant a completed DB_TYPE=postgres install had no account that could log
# in — the install finished, and the product was unusable. Both that script and
# check_db_ready.py are engine-neutral now (server/db_bootstrap.py).
#
# Wait for the migrations to finish, not merely for the DB to answer: SQLite
# creates flowgate.db as soon as sqloader connects, and the networked engines
# accept connections long before 004_rbac.sql has seeded the __SYSTEM__ project
# and role rows that create_dev_user.py needs (missing them => FK violation).
READY_ARGS=(--wait 300)
if [[ "$DB_TYPE" == sqlite || "$DB_TYPE" == sqlite3 || "$DB_TYPE" == local ]]; then
    READY_ARGS+=(--db "$STORAGE_DIR/flowgate.db")
fi
if "$ROOT/.venv/bin/python" "$ROOT/server/check_db_ready.py" "${READY_ARGS[@]}"; then
    # FLOWGATE_ADMIN_* let an unattended install seed the account without a TTY;
    # they mirror the names docker-compose.yml already uses for the same purpose.
    ADMIN_USER="${FLOWGATE_ADMIN_USERNAME:-}"
    ADMIN_PW="${FLOWGATE_ADMIN_PASSWORD:-}"
    if [[ -z "$ADMIN_USER" && $INTERACTIVE -eq 1 ]]; then
        read -rp "Admin username [admin]: " ADMIN_USER
    fi
    ADMIN_USER="${ADMIN_USER:-admin}"
    if [[ -z "$ADMIN_PW" && $INTERACTIVE -eq 1 ]]; then
        while [[ -z "$ADMIN_PW" ]]; do
            read -rsp "Admin password: " ADMIN_PW; echo
        done
    fi
    # The email was hardcoded to <username>@flowgate.local; make it overridable.
    ADMIN_EMAIL="${FLOWGATE_ADMIN_EMAIL:-${ADMIN_USER}@flowgate.local}"
    if [[ -z "$ADMIN_PW" ]]; then
        echo "[!] No admin password given (set FLOWGATE_ADMIN_PASSWORD for unattended installs)."
        echo "    Create the account later with:"
        echo "    $ROOT/.venv/bin/python server/create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
    else
        # Skips automatically if the account already exists (re-run safe).
        "$ROOT/.venv/bin/python" "$ROOT/server/create_dev_user.py" \
            --username "$ADMIN_USER" \
            --email "$ADMIN_EMAIL" \
            --password "$ADMIN_PW" \
            --admin || true
    fi

    echo "==> AI provider"
    # ── AI provider (0292 T0003) ─────────────────────────────────────────────
    # An install used to finish with an EMPTY ai_providers table, so nothing
    # AI-driven worked until someone found the settings screen — and the omission
    # only showed up later as a run dying with "all_providers_failed".
    #
    # Deliberately just y/n here. Which provider, which command and which key are
    # all asked by seed_ai_provider.py, so the prompts exist once instead of once
    # per shell, and adding a provider kind never touches this file (CH0002).
    SEED_AI=0
    if [[ -n "${FLOWGATE_AI_KIND:-}${FLOWGATE_AI_EXEC_TYPE:-}${FLOWGATE_AI_CLI_COMMAND:-}" ]]; then
        # Preset for an unattended install — seed without asking, mirroring how
        # FLOWGATE_ADMIN_* skips the admin prompts above.
        SEED_AI=1
    elif [[ $INTERACTIVE -eq 1 ]]; then
        read -rp "Register an AI provider now? [y/N]: " ANSWER
        # `if` rather than `[[ ]] && SEED_AI=1`: under `set -e` the short-circuit
        # form exits the whole installer the moment the answer is not y.
        if [[ "$ANSWER" =~ ^[Yy] ]]; then SEED_AI=1; fi
    fi
    if [[ $SEED_AI -eq 1 ]]; then
        # Skips a provider that is already registered (re-run safe), and a failed
        # probe must not fail the install — the provider is stored either way.
        "$ROOT/.venv/bin/python" "$ROOT/server/seed_ai_provider.py" || true
    else
        echo "    Skipped. Register one whenever you like — this runs exactly the"
        echo "    step that was just declined:"
        echo "    $ROOT/setup-ai.sh"
    fi
else
    echo "[!] DB migrations did not finish — create the admin account manually later:"
    echo "    $ROOT/.venv/bin/python server/create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
    echo "    ...and register an AI provider with:"
    echo "    $ROOT/setup-ai.sh"
fi

echo
echo "──────────────────────────────────────────────────────────────"
sudo systemctl status flowgate --no-pager || true
cat <<EOF

Done. FlowGate staging should be running on port $FLOWGATE_PORT.

  Open:     http://<this-host>:$FLOWGATE_PORT
  Logs:     journalctl -u flowgate -f
  Restart:  sudo systemctl restart flowgate
  Rebuild client after FE changes:
            ./client/build.sh        (then hard-refresh the browser; no restart needed)

Notes:
  - DB: $DB_TYPE (schema auto-migrates on first boot from sql/migrations/).
  - Server runs as user '$RUN_USER', listening on $FLOWGATE_BIND_HOST:$FLOWGATE_PORT.
  - CORS origin: $(grep '^ALLOWED_ORIGIN=' "$ENV_FILE" | cut -d= -f2-)
  - For outbound/external token links, rebuild with an absolute URL:
      ./client/build.sh https://<public-host>/flowgate
  - If 'status' above is not active, check:  journalctl -u flowgate -n 50 --no-pager
──────────────────────────────────────────────────────────────
EOF
