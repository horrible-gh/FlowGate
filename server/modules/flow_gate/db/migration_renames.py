"""Carry sqloader's applied-migration bookkeeping across a file rename.

flowgate.default.0394 T0004 (NR0003 §4.4, 권고 1).

`sql/migrations/<dialect>/` had ten numbers carrying more than one file — two
parallel groups each picked the same next number, and the loser was only ever
distinguished by the rest of its name. T0004 gives each file a unique ordinal by
appending a letter to the later arrivals (`076_ai_invoke_runs.sql` ->
`076b_ai_invoke_runs.sql`), which keeps every file in the exact position it
already occupied in sort order.

The catch is how sqloader decides what still needs applying. `DatabaseMigrator`
records **the file name** in the `migrations` table and skips a file when that
exact string is already there (sqloader/migrator.py: `get_applied_migrations`).
A rename therefore reads as ten brand-new migrations on every database that has
already applied them — and re-running e.g. `ALTER TABLE tokens ADD COLUMN
merge_id` raises, so the server would fail to boot rather than fail quietly.

So the bookkeeping has to move with the file. `apply_migration_renames()` does
exactly that, and nothing else: it rewrites rows in the `migrations` table and
never touches a schema object. It has to run *before* `database_init()` builds
the migrator, which is why it opens its own short-lived connection instead of
borrowing the pooled instance (config.py `instance_init`).

Three cases, all of which have to be safe because this runs on every boot:

  fresh database   the `migrations` table does not exist yet -> 0 rows, and the
                   renamed files apply normally under their new names.
  already migrated the old name is present -> renamed in place; the file is
                   correctly skipped.
  already renamed  neither name matches, or both do (an operator applied the new
                   file by hand) -> 0 rows, or the stale old row is dropped.

Adding to RENAMES is safe for the same reason; entries never expire, and an old
name that no database has is simply not found.
"""

from __future__ import annotations

import os

import db_bootstrap as dbb

# (old file name, new file name). The three dialect directories use identical
# file names, so one table covers sqlite, mysql and postgres alike.
RENAMES: tuple[tuple[str, str], ...] = (
    ("031_remove_unused_log_doctype.sql", "031a_remove_unused_log_doctype.sql"),
    ("042_tokens_review_scope.sql", "042a_tokens_review_scope.sql"),
    ("043_tokens_dry_run_count.sql", "043a_tokens_dry_run_count.sql"),
    (
        "062_tokens_workflow_sequence_edit_scope.sql",
        "062a_tokens_workflow_sequence_edit_scope.sql",
    ),
    ("063_tokens_conflict_merge_id.sql", "063a_tokens_conflict_merge_id.sql"),
    ("067_auth_sessions.sql", "067a_auth_sessions.sql"),
    ("074_ai_invoke_chain_progress.sql", "074a_ai_invoke_chain_progress.sql"),
    ("074_document_type_descriptions.sql", "074b_document_type_descriptions.sql"),
    ("076_ai_invoke_paused_provider.sql", "076a_ai_invoke_paused_provider.sql"),
    ("076_ai_invoke_runs.sql", "076b_ai_invoke_runs.sql"),
    ("078_continuation_auto_approve.sql", "078a_continuation_auto_approve.sql"),
    ("078_seed_work_plan_doctype.sql", "078b_seed_work_plan_doctype.sql"),
    ("079_ai_invoke_step_timeout.sql", "079a_ai_invoke_step_timeout.sql"),
    ("079_workflow_sequence_note_source.sql", "079b_workflow_sequence_note_source.sql"),
)


def _connect(db_type: str, *, sqlite_path, host, port, user, password, database, schema):
    """Open a throwaway DB-API connection, or return None when there is nothing to read.

    Deliberately does NOT go through `db_bootstrap.connect()`: that reads
    DB_HOST/DB_USER/... from os.environ, while the running server takes them from
    `settings` (pydantic, .env). Those two agree on a normal install and diverge
    on any install that does not export .env into the process environment — and
    connecting to the wrong database here would rename nothing while reporting
    success. The caller passes the values the server itself is about to use.
    """
    if db_type == dbb.SQLITE:
        import sqlite3

        path = (sqlite_path or "").strip()
        if not path or not os.path.exists(path):
            # No file means no prior migration run. `sqlite3.connect` would
            # CREATE the file, so check first (same reason as db_bootstrap's
            # readonly branch).
            return None
        return sqlite3.connect(path, timeout=15)

    if db_type == dbb.MYSQL:
        import pymysql

        return pymysql.connect(
            host=host or "127.0.0.1",
            port=int(port or 3306),
            user=user or "",
            password=password or "",
            database=database or "",
            charset="utf8mb4",
            autocommit=False,
        )

    import psycopg2

    return psycopg2.connect(
        host=host or "127.0.0.1",
        port=int(port or 5432),
        user=user or "",
        password=password or "",
        dbname=database or "",
        options=f"-c search_path={(schema or 'public').strip()}",
    )


def _applied_names(conn, db_type: str) -> set[str] | None:
    """Names in the `migrations` table, or None when the table is not there yet."""
    try:
        cursor = dbb.execute(conn, db_type, "SELECT filename FROM migrations")
        return {row["filename"] for row in dbb.rows(cursor)}
    except Exception:
        # PostgreSQL aborts the whole transaction on a failed statement, so the
        # rollback is what lets the caller close cleanly.
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def apply_migration_renames(
    db_type: str,
    *,
    sqlite_path: str | None = None,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    schema: str | None = None,
    renames: tuple[tuple[str, str], ...] = RENAMES,
) -> int:
    """Point the `migrations` bookkeeping at the renamed files. Returns rows changed."""
    resolved = dbb.resolve_db_type(db_type)
    conn = _connect(
        resolved,
        sqlite_path=sqlite_path,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        schema=schema,
    )
    if conn is None:
        return 0

    try:
        applied = _applied_names(conn, resolved)
        if not applied:
            return 0

        changed = 0
        for old, new in renames:
            if old not in applied:
                continue
            if new in applied:
                # Both rows present: the new file was applied by hand (or a
                # previous boot was interrupted between the two statements).
                # Drop the orphan so the table keeps one row per file.
                dbb.execute(
                    conn, resolved, "DELETE FROM migrations WHERE filename = ?", (old,)
                )
            else:
                dbb.execute(
                    conn,
                    resolved,
                    "UPDATE migrations SET filename = ? WHERE filename = ?",
                    (new, old),
                )
            changed += 1
        if changed:
            conn.commit()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass
