#!/usr/bin/env bash
#
# FlowGate staging setup (Linux) — end to end:
#   - server: python venv + dependencies
#   - server/.env: working defaults (CONTEXT, DB, SECRET_KEY, token pepper, storage)
#   - client: build → client/dist (same-origin API base)
#   - systemd: install, enable, and start the service
#   - admin: prompt for username/password and create the first account
#
# Run once on the staging box as a normal user. sudo is used only for the
# systemd steps and will prompt for a password:
#   ./setup.sh
#
# DB selection (the server supports sqlite3 / mysql / postgres — see
# server/config.py). The default is sqlite3, which needs no external server.
# To target MySQL/MariaDB or PostgreSQL instead, preset the connection via
# environment variables before running (non-interactive, CI-friendly):
#
#   DB_TYPE=postgres DB_HOST=127.0.0.1 DB_PORT=5432 \
#   DB_USER=flowgate DB_PASSWORD=secret DB_DATABASE=flowgate ./setup.sh
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

# DB selection — default sqlite3; override with DB_TYPE=mysql|postgres (+ DB_*).
DB_TYPE="${DB_TYPE:-sqlite3}"
case "$DB_TYPE" in
    sqlite|sqlite3|local|mysql|postgres) ;;
    *) echo "[!] Unsupported DB_TYPE='$DB_TYPE' (use sqlite3|mysql|postgres)"; exit 1 ;;
esac

# Set or replace KEY=VALUE in server/.env (| used as sed delimiter for paths).
set_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
}

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
# create_dev_user.py talks to SQLite directly, so the interactive bootstrap below
# only runs for the file-backed DBs. For mysql/postgres the server still creates
# the schema on first boot; seed the admin with your own tooling against that DB.
case "$DB_TYPE" in
    sqlite|sqlite3|local)
        # Wait for the migrations to finish, not merely for the file to appear:
        # SQLite creates flowgate.db as soon as sqloader connects, well before
        # 004_rbac.sql seeds the __SYSTEM__ project and role rows that
        # create_dev_user.py needs (missing them => FOREIGN KEY constraint failed).
        DB_FILE="$STORAGE_DIR/flowgate.db"
        if "$ROOT/.venv/bin/python" "$ROOT/server/check_db_ready.py" --db "$DB_FILE" --wait 300; then
            read -rp "Admin username [admin]: " ADMIN_USER
            ADMIN_USER="${ADMIN_USER:-admin}"
            ADMIN_PW=""
            while [[ -z "$ADMIN_PW" ]]; do
                read -rsp "Admin password: " ADMIN_PW; echo
            done
            # Skips automatically if the account already exists (re-run safe).
            "$ROOT/.venv/bin/python" "$ROOT/server/create_dev_user.py" \
                --username "$ADMIN_USER" \
                --email "${ADMIN_USER}@flowgate.local" \
                --password "$ADMIN_PW" \
                --admin || true
        else
            echo "[!] DB migrations did not finish — create the admin account manually later:"
            echo "    $ROOT/.venv/bin/python server/create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
        fi
        ;;
    *)
        echo "[i] DB_TYPE=$DB_TYPE: skipping the SQLite admin bootstrap."
        echo "    create_dev_user.py is SQLite-only; seed the first admin directly against your $DB_TYPE database."
        ;;
esac

echo
echo "──────────────────────────────────────────────────────────────"
sudo systemctl status flowgate --no-pager || true
cat <<EOF

Done. FlowGate staging should be running on port 8089.

  Open:     http://<this-host>:8089
  Logs:     journalctl -u flowgate -f
  Restart:  sudo systemctl restart flowgate
  Rebuild client after FE changes:
            ./client/build.sh        (then hard-refresh the browser; no restart needed)

Notes:
  - DB: $DB_TYPE (schema auto-migrates on first boot from sql/migrations/).
  - Server runs as user '$RUN_USER'.
  - For outbound/external token links, rebuild with an absolute URL:
      ./client/build.sh https://<public-host>/flowgate
  - If 'status' above is not active, check:  journalctl -u flowgate -n 50 --no-pager
──────────────────────────────────────────────────────────────
EOF
