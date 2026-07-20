#!/usr/bin/env python3
"""Report whether the configured DB has finished applying every migration.

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

0273 NR0003 P1-1: this check is engine-neutral. It used to hardcode sqlite3, so
the mysql/postgres install paths had no readiness gate at all and their admin
bootstrap was skipped outright. The migrations set is chosen from DB_TYPE
(sql/migrations/{sqlite,mysql,postgres}), matching what config.py hands the
migrator.

Usage:
    python check_db_ready.py                      # one-shot check
    python check_db_ready.py --db /path/flowgate.db --wait 180

Exit codes: 0 = ready, 1 = not ready (missing DB, pending migrations, timeout).
"""

import argparse
import io
import os
import sys
import time

import db_bootstrap as dbb
from db_bootstrap import BootstrapDBError

# Force UTF-8 so output does not break in Windows cp932/cp949 consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env is the only place the installers record DB_TYPE / DB_*, and this script
# runs as a bare `python check_db_ready.py` with none of that exported. config.py
# is deliberately NOT imported: constructing it runs the migrator, which would
# advance the very state this script exists to observe.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:  # pragma: no cover - python-dotenv is pinned
    pass


DEFAULT_MIGRATIONS = os.path.join(BASE_DIR, "sql", "migrations", "sqlite")


def migrations_dir_for(db_type: str) -> str:
    return os.path.join(BASE_DIR, "sql", "migrations", dbb.migrations_dirname(db_type))


def default_db_path() -> str:
    """Resolve the SQLite DB the same way create_dev_user.py does."""
    return dbb.sqlite_db_path(BASE_DIR)


def expected_migrations(migrations_dir: str) -> set:
    """Filenames sqloader will apply, matching how it enumerates them."""
    if not os.path.isdir(migrations_dir):
        return set()
    return {f for f in os.listdir(migrations_dir) if f.endswith(".sql")}


def applied_migrations(db_path: str, db_type: str = dbb.SQLITE) -> set:
    """Filenames already committed, or None if the DB cannot be read yet.

    `db_path` stays the first parameter, and `db_type` defaults to sqlite, so the
    0272 contract — applied_migrations(path) / pending(path, dir) — keeps working
    unchanged. It is ignored for the networked engines, which are located by the
    DB_* settings instead.

    SQLite is opened read-only so a missing file is reported rather than created
    as an empty DB — creating it would make the very check that guards the
    bootstrap produce the half-initialized state it exists to detect. The
    networked engines cannot be created by connecting, so they need no such
    guard; an unreachable server or an absent `migrations` table both mean
    "not ready yet", which is what None conveys.
    """
    if db_type == dbb.SQLITE and db_path:
        os.environ["DB_PATH"] = str(db_path)
    try:
        conn = dbb.connect(db_type, BASE_DIR, readonly=True)
    except Exception:
        # BootstrapDBError (missing file / bad settings) or any driver-specific
        # connection error — both mean "cannot tell yet", i.e. not ready.
        return None
    try:
        rows = dbb.rows(dbb.execute(conn, db_type, "SELECT filename FROM migrations"))
    except Exception:
        # Table absent (migrator has not created it yet), DB locked mid-write, or
        # the connection dropped. Each driver raises its own class, so catch broadly.
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {r["filename"] for r in rows}


def pending(db_path: str, migrations_dir: str, db_type: str = dbb.SQLITE):
    """Return the set of not-yet-applied migrations, or None if unreadable."""
    applied = applied_migrations(db_path, db_type)
    if applied is None:
        return None
    return expected_migrations(migrations_dir) - applied


def main() -> int:
    ap = argparse.ArgumentParser(description="Check whether all DB migrations have been applied.")
    ap.add_argument("--db", default=None, help="SQLite path override (default: $DB_PATH or server/flowgate.db). Ignored for mysql/postgres, which use the DB_* settings.")
    ap.add_argument("--db-type", default=None, help="Engine override (default: $DB_TYPE, else sqlite3)")
    ap.add_argument("--migrations", default=None, help="Migrations directory (default: chosen from the engine)")
    ap.add_argument("--wait", type=float, default=0, help="Poll up to this many seconds (default: one-shot)")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconds between polls")
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = ap.parse_args()

    def say(msg):
        if not args.quiet:
            print(msg, flush=True)

    # An explicit --db names a SQLite file, so it settles the engine on its own.
    # Without this the ambient $DB_TYPE would win and `--db some.sqlite` could go
    # and probe a completely different (networked) database instead.
    db_type_arg = args.db_type
    if db_type_arg is None and args.db:
        db_type_arg = "sqlite3"
    try:
        db_type = dbb.resolve_db_type(db_type_arg)
    except BootstrapDBError as exc:
        say(f"[!] {exc}")
        return 1

    # --db is a SQLite-only convenience the installers already pass; for the
    # networked engines the connection comes from DB_HOST/DB_PORT/... instead.
    db_path = (args.db or default_db_path()) if db_type == dbb.SQLITE else None
    migrations_dir = args.migrations or migrations_dir_for(db_type)
    expected = expected_migrations(migrations_dir)
    args.migrations = migrations_dir

    if not expected:
        # No migrations directory means we cannot prove readiness; treat as not
        # ready rather than green-lighting a DB we know nothing about.
        say(f"[!] No .sql migrations found under {args.migrations}")
        return 1

    deadline = time.monotonic() + max(args.wait, 0)
    last_remaining = None
    while True:
        left = pending(db_path, args.migrations, db_type)
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
                say(f"[!] DB not readable yet: {dbb.describe_target(db_type, BASE_DIR)}")
            else:
                sample = ", ".join(sorted(left)[:3])
                say(f"[!] {remaining} migration(s) still pending (e.g. {sample})")
            return 1
        time.sleep(max(args.interval, 0.05))


if __name__ == "__main__":
    sys.exit(main())
