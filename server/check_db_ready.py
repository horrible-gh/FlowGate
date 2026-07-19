#!/usr/bin/env python3
"""Report whether the SQLite DB has finished applying every migration.

setup.ps1 / setup.sh used to treat "flowgate.db exists on disk" as the signal
that the DB was ready. SQLite creates that file the moment sqloader first
connects — long before the migrations under sql/migrations/sqlite have been
applied. The installer could therefore stop the bootstrap server after only a
handful of migrations had committed and then run create_dev_user.py against a
DB where 004_rbac.sql had not seeded the __SYSTEM__ project or the role rows,
which surfaced as `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.

Readiness here means what the installer actually needs: every *.sql file in the
migrations directory has a row in sqloader's `migrations` tracking table
(sqloader inserts that row only after the file's statements commit).

Usage:
    python check_db_ready.py                      # one-shot check
    python check_db_ready.py --db /path/flowgate.db --wait 180

Exit codes: 0 = ready, 1 = not ready (missing DB, pending migrations, timeout).
"""

import argparse
import io
import os
import sqlite3
import sys
import time

# Force UTF-8 so output does not break in Windows cp932/cp949 consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MIGRATIONS = os.path.join(BASE_DIR, "sql", "migrations", "sqlite")


def default_db_path() -> str:
    """Resolve the DB the same way create_dev_user.py does."""
    db_path = os.environ.get("DB_PATH", "flowgate.db")
    return db_path if os.path.isabs(db_path) else os.path.join(BASE_DIR, db_path)


def expected_migrations(migrations_dir: str) -> set:
    """Filenames sqloader will apply, matching how it enumerates them."""
    if not os.path.isdir(migrations_dir):
        return set()
    return {f for f in os.listdir(migrations_dir) if f.endswith(".sql")}


def applied_migrations(db_path: str) -> set:
    """Filenames already committed, or None if the DB cannot be read yet.

    Opened read-only via URI so a missing file is reported rather than created
    as an empty DB — creating it would make the very check that guards the
    bootstrap produce the half-initialized state it exists to detect.
    """
    if not os.path.exists(db_path):
        return None
    uri = "file:{}?mode=ro".format(db_path.replace("?", "%3f").replace("#", "%23"))
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute("SELECT filename FROM migrations").fetchall()
    except sqlite3.Error:
        # Table absent (migrator has not created it yet) or DB locked mid-write.
        return None
    finally:
        conn.close()
    return {r[0] for r in rows}


def pending(db_path: str, migrations_dir: str):
    """Return the set of not-yet-applied migrations, or None if unreadable."""
    applied = applied_migrations(db_path)
    if applied is None:
        return None
    return expected_migrations(migrations_dir) - applied


def main() -> int:
    ap = argparse.ArgumentParser(description="Check whether all SQLite migrations have been applied.")
    ap.add_argument("--db", default=None, help="Path to flowgate.db (default: $DB_PATH or server/flowgate.db)")
    ap.add_argument("--migrations", default=DEFAULT_MIGRATIONS, help="Migrations directory")
    ap.add_argument("--wait", type=float, default=0, help="Poll up to this many seconds (default: one-shot)")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconds between polls")
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = ap.parse_args()

    db_path = args.db or default_db_path()
    expected = expected_migrations(args.migrations)

    def say(msg):
        if not args.quiet:
            print(msg, flush=True)

    if not expected:
        # No migrations directory means we cannot prove readiness; treat as not
        # ready rather than green-lighting a DB we know nothing about.
        say(f"[!] No .sql migrations found under {args.migrations}")
        return 1

    deadline = time.monotonic() + max(args.wait, 0)
    last_remaining = None
    while True:
        left = pending(db_path, args.migrations)
        if left is not None and not left:
            say(f"    DB ready — {len(expected)} migrations applied.")
            return 0

        remaining = len(expected) if left is None else len(left)
        if remaining != last_remaining:
            applied_n = len(expected) - remaining
            say(f"    Waiting for migrations... {applied_n}/{len(expected)} applied")
            last_remaining = remaining

        if time.monotonic() >= deadline:
            if left is None:
                say(f"[!] DB not readable yet: {db_path}")
            else:
                sample = ", ".join(sorted(left)[:3])
                say(f"[!] {remaining} migration(s) still pending (e.g. {sample})")
            return 1
        time.sleep(max(args.interval, 0.05))


if __name__ == "__main__":
    sys.exit(main())
