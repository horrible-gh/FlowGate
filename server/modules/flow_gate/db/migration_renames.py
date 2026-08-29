"""Carry sqloader's applied-migration bookkeeping across a file rename.

flowgate.default.0394 T0004 (NR0003 §4.4, recommendation 1).

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

flowgate.default.0408 TR0018 §0 adds a second failure mode: this file only ever
described *one* rename per collision, but 078/079 turned out to have **two
independent already-applied histories** for the same content. Group 0408's own
checkout carried 078_continuation_auto_approve.sql / 078_seed_work_plan_doctype.sql
/ 079_ai_invoke_step_timeout.sql / 079_workflow_sequence_note_source.sql under
their original bare numbers, while a sibling group's checkout (surfaced through
the shared dev preview, `FlowGate-dev/server/storage/flowgate.db`) had already
renamed the same four files to 078a_continuation_auto_approve.sql /
078b_seed_work_plan_doctype.sql / 079a_ai_invoke_step_timeout.sql /
079a_workflow_sequence_note_source.sql — a *different* letter assignment, chosen
independently before this file's RENAMES entry existed for that ordinal. Any
single database only ever has ONE of the two histories (never both at once), so
RENAMES may now list more than one `old` name converging on the same final
`new` name — one entry per distinct already-observed history. `old` values must
still be pairwise distinct (that is what makes each entry unambiguous); only the
one-`new`-per-`old` constraint is dropped. See `apply_migration_renames`'s "both
rows present" branch: it already deletes the disqualified duplicate instead of
raising, which is exactly what makes convergence safe.

flowgate.default.0414 T0017: 080 collided the same way (0406's
ai_invoke_prompt_audit.sql against 0408's workflow_sequence_provider.sql), split
into 080a/080b in sort order. The shared dev preview's database (M0016's boot
failure) turned out to hold a THIRD name for the provider file —
081_workflow_sequence_provider.sql, from before 081/082 were claimed elsewhere and
it was renumbered down to 080 — so that ordinal has three convergent `old` names
for the one `new` name, the same convergence shape as 078b/079b above.
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
    ("074_test_run_cancel_status.sql", "074a_test_run_cancel_status.sql"),
    ("074_ai_invoke_chain_progress.sql", "074b_ai_invoke_chain_progress.sql"),
    ("074a_ai_invoke_chain_progress.sql", "074b_ai_invoke_chain_progress.sql"),
    ("074_document_type_descriptions.sql", "074c_document_type_descriptions.sql"),
    ("074b_document_type_descriptions.sql", "074c_document_type_descriptions.sql"),
    ("075_tokens_chat_scope.sql", "075a_tokens_chat_scope.sql"),
    ("076_ai_invoke_paused_provider.sql", "076a_ai_invoke_paused_provider.sql"),
    ("076_ai_invoke_runs.sql", "076b_ai_invoke_runs.sql"),
    # 078/079: two independent already-applied histories converge on each final
    # name (module docstring above). continuation_auto_approve and
    # ai_invoke_step_timeout happen to already match the sibling history's
    # letter, so only this branch's own bare-number history needs an entry;
    # seed_work_plan_doctype and workflow_sequence_note_source were assigned
    # different letters on each side and need one entry per side.
    ("078_continuation_auto_approve.sql", "078a_continuation_auto_approve.sql"),
    ("078_seed_work_plan_doctype.sql", "078b_seed_work_plan_doctype.sql"),
    ("078a_seed_work_plan_doctype.sql", "078b_seed_work_plan_doctype.sql"),
    ("079_ai_invoke_step_timeout.sql", "079a_ai_invoke_step_timeout.sql"),
    ("079_workflow_sequence_note_source.sql", "079b_workflow_sequence_note_source.sql"),
    ("079a_workflow_sequence_note_source.sql", "079b_workflow_sequence_note_source.sql"),
    # flowgate.default.0413 T0007: 080 ordinal collided between
    # 080_ai_invoke_prompt_audit.sql (0406) and 080_workflow_sequence_provider.sql
    # (0408). 0408 is the later arrival, so it moves to the next free ordinal
    # instead of taking a letter suffix.
    #
    # flowgate.default.0452: the ordinal it landed on, 081, turned out to already be taken
    # too — flowgate.default.0410 independently added 081_document_origin_snapshot.sql in a
    # parallel branch that could not see this file's 081 (the same class of collision T0007
    # itself was fixing, one ordinal later). Unlike 080, this one was not caught by
    # `test_no_two_migrations_share_an_ordinal` before it shipped, and a real
    # `DatabaseMigrator` reboot hit it as `duplicate column name: provider_id`. THREE already-
    # applied histories exist for this file, and no database holds more than one, so (078/079's
    # situation, module docstring above) every entry converges directly on the final name
    # rather than chaining through the now-taken 081:
    #
    #   080_…   databases migrated before T0007 moved the file off 080.
    #   080b_…  what the dev deployment (FlowGate-dev/server/storage/flowgate.db) actually
    #           holds — a letter suffix assigned in yet another branch, for the very same
    #           080 collision, before T0007 chose to move the file instead. Read straight
    #           off that database's `migrations` table while diagnosing this rejection.
    #   081_…   databases migrated after T0007 but before this fix. This is what the stg
    #           deployment's PostgreSQL ledger holds.
    #
    # The 0406 side of the same collision is a pure letter-suffix rename on an unchanged
    # ordinal, so `reconcile_renamed_migrations` carries it dynamically; the entry is kept
    # anyway because that dynamic pass runs after this one and nothing else pins the pair.
    ("080_ai_invoke_prompt_audit.sql", "080a_ai_invoke_prompt_audit.sql"),
    ("080_workflow_sequence_provider.sql", "081a_workflow_sequence_provider.sql"),
    ("080b_workflow_sequence_provider.sql", "081a_workflow_sequence_provider.sql"),
    ("081_workflow_sequence_provider.sql", "081a_workflow_sequence_provider.sql"),
    # flowgate.default.0332: this group authored its ledger migration as 085 against a base whose
    # newest file on disk was 083. origin/main has since merged 084_ai_invoke_provider_pin.sql and
    # 085_conversation_backward_page_audit.sql, so 085 is taken and both of this group's files move
    # up one — the later arrival takes the next free ordinal, the same rule 0413 T0007 applied to
    # 080_workflow_sequence_provider.sql. Any database that already ran this branch under the old
    # names (a developer checkout, the preview slot) is carried across by these lines; without
    # them the MySQL dialect of the ledger file would re-run its `group_git_state` ADD COLUMNs,
    # which carry no IF NOT EXISTS, and the boot migration would fail.
    #
    # T0021: a parallel group reached 086 first, so the ledger file moves once more, to
    # 086a_tr_commit_ledger.sql — a letter suffix rather than the next free number, so it keeps its
    # position between the sibling's 086 and this group's own 087/088. That makes **two** already-
    # applied histories for one file (078/079's situation, module docstring above): databases that
    # ran this branch before the first move hold `085_tr_commit_ledger.sql`, databases that ran it
    # after hold `086_tr_commit_ledger.sql`, and no database holds both. Each history therefore
    # gets its own entry converging directly on the final name — never chained through the
    # intermediate 086, which would leave whichever side is not visited first pointing at a name
    # no longer on disk. The reapply file keeps its existing 086 -> 087 history untouched: that is
    # a different file, and 086_tr_commit_reapply.sql never collided with anything.
    ("086_ai_invoke_paused_provider_fk.sql", "086a_ai_invoke_paused_provider_fk.sql"),
    ("086_ai_invoke_restart_max_attempts.sql", "086b_ai_invoke_restart_max_attempts.sql"),
    ("086_ai_invoke_run_diagnostics.sql", "086c_ai_invoke_run_diagnostics.sql"),
    ("085_tr_commit_ledger.sql", "086d_tr_commit_ledger.sql"),
    ("086_tr_commit_ledger.sql", "086d_tr_commit_ledger.sql"),
    ("086a_tr_commit_ledger.sql", "086d_tr_commit_ledger.sql"),
    ("086_tr_commit_reapply.sql", "087_tr_commit_reapply.sql"),
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
                # previous boot was interrupted between the two statements), OR —
                # 0408 TR0018 — an earlier entry in THIS pass already carried a
                # convergent old name over to `new` (module docstring: two
                # histories, one final name). Either way, drop the orphan so the
                # table keeps one row per file.
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
            # `applied` must reflect this write immediately, not just the
            # snapshot taken before the loop started: a convergent second entry
            # (different `old`, same `new`) checks `new in applied` right above,
            # and must see the row the first entry just created — otherwise it
            # would try to UPDATE into a filename that already exists and hit a
            # UNIQUE-constraint error instead of taking the "both present" branch.
            applied.discard(old)
            applied.add(new)
            changed += 1
        if changed:
            conn.commit()
        return changed
    finally:
        try:
            conn.close()
        except Exception:
            pass

