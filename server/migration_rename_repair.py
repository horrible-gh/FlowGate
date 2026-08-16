#!/usr/bin/env python3
"""Stop a renamed migration file from being applied a second time.

0358 R0001, 4th rejection. sqloader's DatabaseMigrator tracks applied migrations
by the *filename string* alone (`migrations.filename`): on every boot it re-globs
the migration directory and applies every file whose name is not in that table.
It compares nothing else — not the number, not the contents, not a checksum. So
the moment a migration file is renamed, every database that already applied it
under the old name sees the new name as pending and runs the file again.

That is what aborts the dev deployment's boot:

    Starting Database Migrator
    Database Migration Failed.Failed to apply migration 042_tokens_review_scope.sql:
    CHECK constraint failed: action_scope IN ('new', 'edit', 'workflow_decide', 'review')

Its `migrations` table holds `042a_tokens_review_scope.sql` — the name the file
carried when that database was migrated — while the directory now ships
`042_tokens_review_scope.sql`. Six migrations have that history; they are the
ones whose number collided with a parallel branch and were disambiguated with a
letter suffix at some point:

    031a_remove_unused_log_doctype   042a_tokens_review_scope
    043a_tokens_dry_run_count        062a_tokens_workflow_sequence_edit_scope
    063a_tokens_conflict_merge_id    067a_auth_sessions

Re-applying them is not merely redundant, it is destructive. 042 and 062 rebuild
`tokens` with the column set of their own era, so a second run would drop every
column later migrations added (dry_run_count, continuation_*, merge_id, …) and
narrow action_scope back. The CHECK failure that rolls the transaction back is
the only reason the database survived; a database whose token rows happened to
use only the older scopes would have been silently downgraded instead.

The repair is bookkeeping, not schema. Before the migrator applies anything,
record the on-disk name for each migration that was already applied under a
same-number, same-slug variant of that name. Nothing is executed and nothing is
skipped that has not demonstrably already run — a name is only ever accepted as
"already applied" when the table proves it ran under the sibling spelling.
"""

from __future__ import annotations

import os
import re

try:  # the value DatabaseMigrator itself branches on for the placeholder
    from sqloader._prototype import SQLITE
except ImportError:  # pragma: no cover - vendored layout guard
    SQLITE = 1

# "042a_tokens_review_scope.sql" and "042_tokens_review_scope.sql" are the same
# migration; "042_project_messages.sql" is a different one that merely shares the
# number. The key keeps the number AND the slug and drops only the disambiguating
# letters between them, so a match means "same number, same name".
_LETTER_SUFFIX = re.compile(r"^(\d+)[A-Za-z]*_")


def migration_key(filename: str) -> str:
    """Collapse a migration filename to number + slug, ignoring the letter suffix."""
    return _LETTER_SUFFIX.sub(r"\1_", filename.strip()).lower()


def find_renamed_migrations(applied, on_disk) -> list[tuple[str, str]]:
    """Pair each pending file with the older name it was already applied under.

    Returns [(applied_as, on_disk_name), …] sorted by the on-disk name. A file
    that is already recorded, or that has no already-applied sibling, is absent —
    genuinely new migrations are left pending so the migrator still applies them.
    """
    applied = set(applied)
    by_key: dict[str, list[str]] = {}
    for name in applied:
        by_key.setdefault(migration_key(name), []).append(name)

    pairs = []
    for name in sorted(on_disk):
        if name in applied:
            continue
        siblings = sorted(by_key.get(migration_key(name), []))
        if siblings:
            pairs.append((siblings[0], name))
    return pairs


def reconcile_renamed_migrations(db, migrations_path) -> list[tuple[str, str]]:
    """Record on-disk names for migrations already applied under an older name.

    `db` is the sqloader wrapper (the same object DatabaseMigrator writes with),
    so this works for all three engines. Returns the pairs it recorded. Safe on
    every boot: once the names line up it is a no-op, and it never writes when a
    pending file has no already-applied counterpart.
    """
    directory = os.path.abspath(migrations_path)
    if not os.path.isdir(directory):
        return []

    on_disk = [name for name in os.listdir(directory) if name.endswith(".sql")]
    applied = {row["filename"] for row in db.fetch_all("SELECT filename FROM migrations")}
    pairs = find_renamed_migrations(applied, on_disk)

    placeholder = "?" if getattr(db, "db_type", None) == SQLITE else "%s"
    for _applied_as, name in pairs:
        db.execute(
            f"INSERT INTO migrations (filename) VALUES ({placeholder})",
            (name,),
            commit=True,
        )
    return pairs
