#!/usr/bin/env python
"""One-off data migration: copy every row from the SQLite ``flowgate.db`` into a
target PostgreSQL or MariaDB/MySQL database.

The target *schema* is built by the normal sqloader auto-migration
(``sql/migrations/<dialect>``), so this tool only moves DATA. Point it at an
EMPTY target database (it will run the migrations to create the tables, then
load the rows).

Run from the ``server/`` directory so ``sql/migrations`` resolves on the
relative path::

    # Postgres
    python tools/migrate_data.py --target postgres \
        --host 192.168.0.250 --port 5432 --user flowgate --password flowgate \
        --database flowgate --schema public

    # MariaDB / MySQL
    python tools/migrate_data.py --target mysql \
        --host 192.168.0.250 --port 3306 --user flowgate --password flowgate \
        --database flowgate

Source defaults to ``./flowgate.db`` (override with ``--sqlite-path``).
Use ``--no-migrate`` to skip schema creation (target already has the schema).
Use ``--dry-run`` to print the plan and per-table row counts without writing.
"""

import argparse
import os
import sys

# Make sure we can import the project's sqloader + config helpers regardless of
# the invoking CWD (we still default relative paths to the server/ dir).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from sqloader.init import database_init  # noqa: E402
from sqloader import SQLiteWrapper  # noqa: E402

# Tables that must never be copied: the migrator owns ``migrations`` on the
# target, and ``sqlite_sequence`` is a SQLite-internal bookkeeping table.
EXCLUDED_TABLES = {"migrations", "sqlite_sequence"}

MIGRATION_PATHS = "sql/migrations"
SERVICE_SQLOADER = "sql/queries"


# --------------------------------------------------------------------------- #
# Source (SQLite) introspection
# --------------------------------------------------------------------------- #
def list_source_tables(src):
    rows = src.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r["name"] for r in rows if r["name"] not in EXCLUDED_TABLES]


def foreign_key_parents(src, table):
    """Return the set of parent tables referenced by ``table`` (excluding self)."""
    rows = src.fetch_all(f'PRAGMA foreign_key_list("{table}")')
    return {r["table"] for r in rows if r["table"] != table}


def topo_sort(src, tables):
    """Order tables so a parent is loaded before its children.

    Self-references are ignored (handled by the row-wise multi-pass loader).
    On a dependency cycle the remaining tables are appended in name order and a
    warning is printed; the FK-disabled / multi-pass loaders still cope.
    """
    table_set = set(tables)
    deps = {t: foreign_key_parents(src, t) & table_set for t in tables}
    ordered, placed = [], set()
    while len(ordered) < len(tables):
        progressed = False
        for t in tables:
            if t in placed:
                continue
            if deps[t] <= placed:
                ordered.append(t)
                placed.add(t)
                progressed = True
        if not progressed:
            remaining = sorted(t for t in tables if t not in placed)
            print(f"  [warn] FK cycle among {remaining}; appending as-is")
            ordered.extend(remaining)
            placed.update(remaining)
    return ordered


# --------------------------------------------------------------------------- #
# Target schema introspection (for type coercion)
# --------------------------------------------------------------------------- #
def target_tables(tgt, db_type, schema, database):
    """Tables that actually exist on the target.

    Some source tables (e.g. the 2FA ``totp_auth`` / ``backup_codes``) are
    created at app runtime by the auth2fa library, not by sql/migrations, so a
    freshly-migrated target may not have them.
    """
    where = "table_schema = %s"
    arg = database if db_type == "mysql" else (schema or "public")
    rows = tgt.fetch_all(
        f"SELECT table_name FROM information_schema.tables WHERE {where}",
        (arg,),
    )
    return {r["table_name"] for r in rows}


def target_boolean_columns(tgt, db_type, schema, database):
    """Map ``table -> {boolean column names}`` on the target.

    Only Postgres has a native BOOLEAN type that rejects integer 0/1; MySQL
    stores booleans as TINYINT(1) and accepts 0/1 directly, so this is a no-op
    there.
    """
    if db_type != "postgres":
        return {}
    rows = tgt.fetch_all(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND data_type = 'boolean'",
        (schema or "public",),
    )
    out = {}
    for r in rows:
        out.setdefault(r["table_name"], set()).add(r["column_name"])
    return out


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def quote_ident(name, db_type):
    if db_type == "mysql":
        return f"`{name}`"
    return f'"{name}"'


def coerce_row(row, columns, bool_cols):
    vals = []
    for c in columns:
        v = row[c]
        if c in bool_cols and v is not None:
            v = bool(v)
        vals.append(v)
    return tuple(vals)


def _row_multipass(tgt, table, insert_sql, data, db_type):
    """Row-wise multi-pass loader, used when the bulk insert fails.

    Inserts every row it can, retrying across passes so self-referential / out-
    of-order rows still land. Rows that never succeed (e.g. orphaned FK
    references — SQLite does not enforce FKs, the target does) are returned as
    ``skipped`` rather than aborting the whole migration.

    Returns ``(inserted, skipped, last_err)``.
    """
    tx = tgt.begin_transaction()
    try:
        if db_type == "mysql":
            tx.cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        pending, inserted, last_err = data, 0, None
        while pending:
            failed = []
            for params in pending:
                if db_type == "postgres":
                    tx.cursor.execute("SAVEPOINT sp_row")
                try:
                    tx.cursor.execute(insert_sql, params)
                    if db_type == "postgres":
                        tx.cursor.execute("RELEASE SAVEPOINT sp_row")
                    inserted += 1
                except Exception as e:
                    if db_type == "postgres":
                        tx.cursor.execute("ROLLBACK TO SAVEPOINT sp_row")
                    last_err = e
                    failed.append(params)
            if len(failed) == len(pending):
                # No progress this pass: the rest are genuinely unloadable
                # (orphan FKs). Keep what we have, report the rest.
                tx.commit()
                return inserted, failed, last_err
            pending = failed
        tx.commit()
        return inserted, [], None
    except Exception:
        tx.rollback()
        raise
    finally:
        tx.close()


def load_table(tgt, table, rows, db_type, bool_cols):
    """Insert ``rows`` (list of dicts) into ``table`` in its own transaction.

    Fast path is a single executemany; because tables are truncated first and
    loaded parents-before-children, FK constraints are satisfied without needing
    to disable enforcement. A per-table transaction also bounds lock usage
    (avoids ``max_locks_per_transaction`` exhaustion on big tables). If the bulk
    insert fails (e.g. an intra-table self-reference ordering issue) we fall
    back to a row-wise multi-pass loader.
    """
    columns = list(rows[0].keys())
    col_sql = ", ".join(quote_ident(c, db_type) for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = (
        f"INSERT INTO {quote_ident(table, db_type)} ({col_sql}) "
        f"VALUES ({placeholders})"
    )
    data = [coerce_row(r, columns, bool_cols) for r in rows]

    # Fast path: chunked multi-row INSERT — one network round-trip per chunk
    # (``INSERT INTO t (...) VALUES (...),(...),...``). Driver executemany helpers
    # round-trip per row over the network, which is the slow part here.
    one_row = "(" + ", ".join(["%s"] * len(columns)) + ")"
    base_sql = f"INSERT INTO {quote_ident(table, db_type)} ({col_sql}) VALUES "
    # Keep bound params under the protocol limit (~65535 for psycopg2).
    chunk_rows = max(1, 60000 // max(1, len(columns)))

    tx = tgt.begin_transaction()
    try:
        if db_type == "mysql":
            tx.cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        for i in range(0, len(data), chunk_rows):
            chunk = data[i:i + chunk_rows]
            sql = base_sql + ", ".join([one_row] * len(chunk))
            flat = [v for row in chunk for v in row]
            tx.cursor.execute(sql, flat)
        tx.commit()
        return len(data), [], None
    except Exception:
        tx.rollback()
        tx.close()
        # Fall back to the resilient row-wise path (isolates bad rows).
        return _row_multipass(tgt, table, insert_sql, data, db_type)
    finally:
        try:
            tx.close()
        except Exception:
            pass


def truncate_target(tgt, tables, db_type):
    """Empty all target tables before loading.

    The schema auto-migration seeds reference data (permissions, roles, …), so
    a fresh-migrated target is NOT empty and a plain INSERT collides on PK. The
    same seed rows also live in the SQLite source, so wiping and reloading from
    SQLite yields a faithful, complete copy.
    """
    if not tables:
        return
    if db_type == "postgres":
        ids = ", ".join(quote_ident(t, db_type) for t in tables)
        tgt.execute(f"TRUNCATE {ids} RESTART IDENTITY CASCADE")
    else:  # mysql — TRUNCATE is non-transactional; disable FK on the session
        tx = tgt.begin_transaction()
        try:
            tx.cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in tables:
                tx.cursor.execute(f"TRUNCATE TABLE {quote_ident(t, db_type)}")
            tx.cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            tx.commit()
        finally:
            tx.close()


def reset_postgres_sequences(tgt, tables, schema):
    """Advance SERIAL/identity sequences past the max id we just inserted."""
    for t in tables:
        cols = tgt.fetch_all(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "AND column_default LIKE 'nextval%%'",
            (schema or "public", t),
        )
        for c in cols:
            col = c["column_name"]
            tgt.execute(
                f"SELECT setval(pg_get_serial_sequence(%s, %s), "
                f'(SELECT COALESCE(MAX("{col}"), 1) FROM "{t}"), true)',
                (f'{schema or "public"}.{t}', col),
            )


# --------------------------------------------------------------------------- #
# Target wiring
# --------------------------------------------------------------------------- #
def build_target(args, run_migration):
    if args.target == "mysql":
        conn = {
            "host": args.host, "port": args.port, "user": args.user,
            "password": args.password, "database": args.database,
            "schema": args.schema, "log": False,
        }
    else:  # postgres
        conn = {
            "host": args.host, "port": args.port, "user": args.user,
            "password": args.password, "database": args.database,
            "schema": args.schema, "log": False,
        }
    cfg = {
        "type": args.target,
        "placeholder": "?",
        args.target: conn,
        "service": {"log": False, "sqloder": SERVICE_SQLOADER},
    }
    if run_migration:
        cfg["migration"] = {
            "auto_migration": True,
            "migration_path": f"{MIGRATION_PATHS}/{args.target}",
        }
    db_instance, _sqloader, _migrator = database_init(cfg)
    return db_instance


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, choices=["postgres", "mysql"])
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", default="public")
    ap.add_argument("--sqlite-path", default="flowgate.db")
    ap.add_argument("--no-migrate", action="store_true",
                    help="target already has the schema; skip auto-migration")
    ap.add_argument("--keep-existing", action="store_true",
                    help="do NOT truncate target tables before load "
                         "(default: wipe seeded/old rows for a faithful copy)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report tables and row counts, write nothing")
    args = ap.parse_args()

    # Windows consoles may default to a legacy codepage (cp932/cp949); force
    # UTF-8 so summary output never crashes on a non-ASCII character.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not os.path.exists(args.sqlite_path):
        sys.exit(f"source not found: {args.sqlite_path} "
                 f"(run from server/ or pass --sqlite-path)")

    print(f"Source : sqlite  {os.path.abspath(args.sqlite_path)}")
    print(f"Target : {args.target}  {args.user}@{args.host}:{args.port}/{args.database}")
    print("-" * 60)

    src = SQLiteWrapper(db_name=args.sqlite_path)
    tables = topo_sort(src, list_source_tables(src))
    counts = {t: src.fetch_all(f'SELECT COUNT(*) AS n FROM "{t}"')[0]["n"]
              for t in tables}
    total = sum(counts.values())
    print(f"Tables : {len(tables)}   Rows : {total}")

    if args.dry_run:
        for t in tables:
            print(f"  {t:<34} {counts[t]:>8}")
        print("-" * 60)
        print("[dry-run] nothing written")
        return

    tgt = build_target(args, run_migration=not args.no_migrate)

    # Only operate on tables that exist on BOTH sides.
    tgt_set = target_tables(tgt, args.target, args.schema, args.database)
    absent = [t for t in tables if t not in tgt_set]
    if absent:
        print("Tables in source but NOT on target (skipped):")
        for t in absent:
            flag = "  <-- HAS DATA" if counts[t] else ""
            print(f"  {t:<34} {counts[t]:>8}{flag}")
        tables = [t for t in tables if t in tgt_set]
        print("-" * 60)

    bool_cols = target_boolean_columns(tgt, args.target, args.schema, args.database)

    if not args.keep_existing:
        print("Truncating target tables (RESTART IDENTITY / FK-safe) ...")
        truncate_target(tgt, tables, args.target)

    loaded, skipped = {}, {}
    for t in tables:
        if counts[t] == 0:
            loaded[t] = 0
            continue
        rows = src.fetch_all(f'SELECT * FROM "{t}"')
        n, dropped, err = load_table(tgt, t, rows, args.target,
                                     bool_cols.get(t, set()))
        loaded[t] = n
        if dropped:
            skipped[t] = (len(dropped), err)
            print(f"  {t:<34} {n:>8}   (! {len(dropped)} orphan row(s) skipped)")
        else:
            print(f"  {t:<34} {n:>8}")

    if args.target == "postgres":
        print("Resetting sequences ...")
        reset_postgres_sequences(tgt, tables, args.schema)

    print("-" * 60)
    if skipped:
        print("Orphan rows skipped (FK target missing in SQLite source - "
              "SQLite does not enforce FKs):")
        for t, (n, err) in skipped.items():
            print(f"  {t}: {n} row(s)  e.g. {str(err).splitlines()[0]}")
        print("-" * 60)

    # A mismatch is only unexpected when it is NOT explained by skipped orphans.
    bad = [t for t in tables
           if loaded[t] + (skipped.get(t, (0,))[0]) != counts[t]]
    if bad:
        print("[FAIL] unexplained row count mismatch:")
        for t in bad:
            print(f"  {t}: source={counts[t]} loaded={loaded[t]}")
        sys.exit(1)

    total_skipped = sum(n for n, _ in skipped.values())
    print(f"[OK] migrated {sum(loaded.values())} rows across "
          f"{sum(1 for t in tables if loaded[t])} non-empty tables"
          + (f"; {total_skipped} orphan row(s) skipped" if total_skipped else ""))


if __name__ == "__main__":
    main()
