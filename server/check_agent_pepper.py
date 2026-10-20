#!/usr/bin/env python3
"""Decide whether an installer may generate FLOWGATE_AGENT_PEPPER (0078 T0004).

The pepper keys the HMAC that agent_registry.digest() stores for every Agent
enrollment token and Agent credential. No install path generated it, so every
installation answered 503 "temporarily_unavailable" on the first token issue
(0078 NR0003). setup.sh, setup.ps1 and deploy/docker-entrypoint.sh now generate
it once — but only when that cannot break an Agent that is already registered:
a credential stored under the ORIGINAL pepper never matches a new one, so
quietly generating a replacement after the original was lost would turn a
recoverable configuration gap into a permanent authentication failure.

This script answers that question without importing config (which would run the
migrator) and without reading or printing any secret value:

  exit 0  safe     — no DB yet, a new DB where NEITHER agent table exists yet,
                     or both tables exist without a live Agent credential /
                     pending enrollment: generating is harmless.
  exit 3  blocked  — live Agent credentials or pending enrollments exist, so a
                     pepper was in use before. Restore the original value by
                     hand; do not generate a new one.
  exit 2  unknown  — the DB could not be inspected: unreachable server or bad
                     settings, the table catalog or the registrations could not
                     be read (locked / corrupt DB, missing privilege, damaged
                     schema), or only ONE of the two agent tables exists. Do not
                     generate; fix the DB first.

"The tables are absent" is established from the engine's catalog, never inferred
from a failed SELECT: a query can fail for many reasons (lock, corruption,
privilege, a damaged column), and reading any of them as "fresh install" would
generate a replacement over registrations that may well exist (0078 TR0005 rework).

Usage:
    python check_agent_pepper.py            # DB located like check_db_ready.py
    python check_agent_pepper.py --db /path/flowgate.db
"""

import argparse
import io
import os
import sys
from datetime import datetime, timezone

import db_bootstrap as dbb
from db_bootstrap import BootstrapDBError

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Same rule as check_db_ready.py: the installers record DB_* only in .env, and
# an explicit OS environment (the container entrypoint) still wins.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:  # pragma: no cover - python-dotenv is pinned
    pass

SAFE, UNKNOWN, BLOCKED = 0, 2, 3


def _parse_ts(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# The two tables 121_agent_registry.sql creates together. Both absent = the
# migration has not run yet (new DB); exactly one present is a damaged schema.
AGENT_TABLES = ("agent_credentials", "agent_enrollments")


def _why(exc) -> str:
    """Error class plus its first line — enough to act on, never a secret value."""
    text = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def existing_agent_tables(conn, db_type: str) -> set:
    """Names of AGENT_TABLES present in the schema the server uses, from the catalog.

    Raises whatever the driver raises when the catalog itself cannot be read; the
    caller treats that as "unknown", not as "absent".
    """
    marks = ", ".join("?" for _ in AGENT_TABLES)
    if db_type == dbb.SQLITE:
        sql = f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({marks})"
        params = AGENT_TABLES
    elif db_type == dbb.MYSQL:
        sql = ("SELECT table_name AS name FROM information_schema.tables "
               f"WHERE table_schema = DATABASE() AND table_name IN ({marks})")
        params = AGENT_TABLES
    else:
        # Same schema db_bootstrap pins as search_path (and the migrator writes to).
        schema = (os.environ.get("DB_SCHEMA") or "public").strip()
        sql = ("SELECT table_name AS name FROM information_schema.tables "
               f"WHERE table_schema = ? AND table_name IN ({marks})")
        params = (schema,) + AGENT_TABLES
    return {str(r["name"]).lower() for r in dbb.rows(dbb.execute(conn, db_type, sql, params))}


def registration_counts(conn, db_type: str):
    """(live credentials, pending enrollments). Both tables must exist.

    Revoked credentials and consumed / revoked / expired enrollments are ignored:
    nothing still authenticates with them, so a new pepper cannot break them.
    Query errors propagate — the caller refuses to generate on them.
    """
    live = dbb.one(dbb.execute(
        conn, db_type,
        "SELECT COUNT(*) AS n FROM agent_credentials WHERE revoked_at IS NULL"))
    pending = dbb.rows(dbb.execute(
        conn, db_type,
        "SELECT expires_at FROM agent_enrollments "
        "WHERE consumed_at IS NULL AND revoked_at IS NULL"))
    now = datetime.now(timezone.utc)
    unexpired = 0
    for row in pending:
        t = _parse_ts(row["expires_at"])
        # An unreadable expiry is counted: the safe side is to keep the pepper.
        if t is None or t > now:
            unexpired += 1
    return int((live or {}).get("n") or 0), unexpired


_REFUSE = "FLOWGATE_AGENT_PEPPER is not generated; fix the database (or restore the original value) first."


def judge(conn, db_type: str):
    """Return (exit code, message) for an open connection."""
    try:
        present = existing_agent_tables(conn, db_type)
    except Exception as exc:
        return UNKNOWN, f"the table catalog could not be read ({_why(exc)}). {_REFUSE}"
    if not present:
        return SAFE, "agent registry tables not created yet (new database) — a new Agent pepper can be generated"
    missing = [t for t in AGENT_TABLES if t not in present]
    if missing:
        return UNKNOWN, (
            f"inconsistent Agent registry schema: {', '.join(missing)} is missing while "
            f"{', '.join(sorted(present))} exists. Registrations cannot be ruled out. {_REFUSE}")
    try:
        live, pending = registration_counts(conn, db_type)
    except Exception as exc:
        return UNKNOWN, f"Agent registrations could not be read ({_why(exc)}). {_REFUSE}"
    if live or pending:
        return BLOCKED, (
            f"{live} registered Agent credential(s) and {pending} pending enrollment token(s) exist, "
            "but FLOWGATE_AGENT_PEPPER is not set. They were issued under the ORIGINAL pepper: "
            "restore that value by hand (server/.env, or the container's .flowgate-secrets.env / "
            "environment) and restart. A newly generated value would never authenticate them, "
            "so none is generated.")
    return SAFE, "no registered Agent credential — a new Agent pepper can be generated"


def check(db_type: str):
    """Return (exit code, message)."""
    if db_type == dbb.SQLITE and not os.path.exists(dbb.sqlite_db_path(BASE_DIR)):
        return SAFE, "no database yet — a new Agent pepper can be generated"
    try:
        conn = dbb.connect(db_type, BASE_DIR, readonly=True)
    except Exception as exc:
        return UNKNOWN, f"database not inspectable ({dbb.describe_target(db_type, BASE_DIR)}): {type(exc).__name__}"
    try:
        return judge(conn, db_type)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Check whether FLOWGATE_AGENT_PEPPER may be generated.")
    ap.add_argument("--db", default=None, help="SQLite path override (default: $DB_PATH or server/flowgate.db).")
    ap.add_argument("--db-type", default=None, help="Engine override (default: $DB_TYPE, else sqlite3)")
    ap.add_argument("--quiet", action="store_true", help="Suppress the explanation")
    args = ap.parse_args()

    db_type_arg = args.db_type
    if db_type_arg is None and args.db:
        db_type_arg = "sqlite3"
    try:
        db_type = dbb.resolve_db_type(db_type_arg)
    except BootstrapDBError as exc:
        print(f"[!] {exc}")
        return UNKNOWN
    if args.db and db_type == dbb.SQLITE:
        os.environ["DB_PATH"] = str(args.db)
    code, message = check(db_type)
    if not args.quiet or code != SAFE:
        print(("    " if code == SAFE else "[!] ") + message, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
