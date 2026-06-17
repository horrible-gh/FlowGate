"""One-time, dialect-agnostic data backfills run at startup after migrations.

Some legacy SQLite data migrations cannot be mechanically translated to
MariaDB/PostgreSQL because they depend on SQLite-only JSON DML (json_each,
json_group_array, ...). Those are re-expressed here as portable Python so the
identical transform runs on every backend. Each backfill MUST be idempotent.
"""
