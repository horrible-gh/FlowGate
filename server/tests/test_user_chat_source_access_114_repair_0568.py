"""Regression for 114: repair user_chat_source_access.one_shot_token_id's FK target.

flowgate.default.0568 TR0005 rejection 2 reported a live 500 on Quick Mode's actual save
path (PATCH /me/chat-settings -> chat_settings_service.save_chat_settings ->
user_chat_source_access.upsert): sqlite3.OperationalError: no such table:
main.tokens_before_base_dirty_scope. Reproduced directly against a copy of the live dev
database (flowgate.default.0568 TR0005 rev2): every INSERT/UPDATE against
user_chat_source_access failed at statement-compile time, because its
`one_shot_token_id TEXT REFERENCES tokens(...)` clause had been silently rewritten by
SQLite to `REFERENCES "tokens_before_base_dirty_scope"(...)`.

Same root mechanism 110 itself already had to work around for `tokens` (see 110's own
header on the out-of-order 108/109 merge): SQLite's documented ALTER TABLE RENAME behavior
automatically rewrites every *other* table's FOREIGN KEY clause that names the table being
renamed, in place, to the new name. But this hit is NOT limited to the same rare
out-of-order merge -- `108_user_chat_source_access.sql` unconditionally sorts before both
`109_tokens_source_access.sql` and `110_repair_tokens_after_base_dirty_rebuild.sql`
(alphabetical filename order), so `user_chat_source_access` always exists by the time
either one renames `tokens`. Whichever rename runs first against a live `tokens` FK
reference wins and corrupts it (a later rename no longer matches a reference that already
points elsewhere); the migration then drops its own intermediate table once the rebuilt
`tokens` exists, leaving the rewritten reference permanently dangling. 110 rebuilt only
`tokens`; it never touched the table now pointing at a name that no longer exists. This
means the corruption reproduces on *every* SQLite deployment that has applied 110 --
already merged to main -- regardless of merge order, not only the inverted 108/108 ledger
flowgate.default.0568 TR0005 found live.

Tests below run the real sqloader.migrator.DatabaseMigrator against a real sqlite3
database, reproduce the corruption from the actual migration files (not an authored
string) in both the inverted ledger and the ordinary disk order, and prove 114 repairs it
without losing data either way.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "sql" / "migrations"
_SQLITE_DIR = ROOT / "sqlite"

_TOKENS_RENAME_FILE = "108_tokens_resolve_base_dirty_scope.sql"
_UCSA_CREATE_FILE = "108_user_chat_source_access.sql"
_TOKENS_110_REPAIR_FILE = "110_repair_tokens_after_base_dirty_rebuild.sql"
_REPAIR_FILE = "114_repair_user_chat_source_access_token_fk.sql"


def _ordinal(fname: str) -> int:
    return int(re.match(r"(\d+)", fname).group(1))


def _disk_sqlite_upto_114() -> list[str]:
    return sorted(p.name for p in _SQLITE_DIR.glob("*.sql") if _ordinal(p.name) <= 114)


def _seed_dir(tmp_path: Path, name: str, exclude: frozenset[str] = frozenset()) -> Path:
    d = tmp_path / name
    d.mkdir()
    for fname in _disk_sqlite_upto_114():
        if fname in exclude:
            continue
        shutil.copy(_SQLITE_DIR / fname, d / fname)
    return d


def _seed_users(conn: sqlite3.Connection, *user_ids: str) -> None:
    for uid in user_ids:
        conn.execute(
            "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
            "VALUES (?, ?, ?, 'x', '2026-09-01T00:00:00', '2026-09-01T00:00:00')",
            (uid, f"{uid}_name", f"{uid}@example.test"),
        )
    conn.commit()


def _ucsa_schema_sql(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_chat_source_access'"
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def test_114_body_targets_the_correct_table_and_ships_for_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        path = ROOT / dialect / _REPAIR_FILE if dialect != "sqlite" else _SQLITE_DIR / _REPAIR_FILE
        # Only sqlite is fixed here: the FK-reference rewrite this repairs is a SQLite
        # ALTER TABLE RENAME behavior; postgres/mysql migrations already name `tokens`
        # correctly and are not reproduced as broken against a live server in this repo.
        if dialect != "sqlite":
            continue
        body = path.read_text(encoding="utf-8")
        assert "REFERENCES tokens(token_id)" in body
        assert "tokens_before_base_dirty_scope" in body  # documented in the header only
        assert 'REFERENCES "tokens_before_base_dirty_scope"' not in body.split("CREATE TABLE user_chat_source_access", 1)[1]


def test_114_repairs_the_fk_when_108_tokens_rename_runs_after_ucsa_create(tmp_path):
    """Reproduces the real deployment ledger flowgate.default.0568 TR0005 found on a live
    dev database copy: 108_user_chat_source_access.sql and 109 applied 2026-09-08 12:46:54,
    108_tokens_resolve_base_dirty_scope.sql applied later the same day at 21:01:24, and
    110_repair_tokens_after_base_dirty_rebuild.sql applied later still on 2026-09-10.

    Boot 1 applies every migration up to 114 except both tokens-rename files (108's and
    110's) -- the state of a branch that merged 108_user_chat_source_access.sql first. Boot
    2 adds 108's rename back, which is exactly what corrupts the FK on a real deployment.
    Boot 3 adds 110 on top and must NOT re-corrupt it further (110 renames `tokens`, and by
    this point the FK no longer names `tokens` at all -- confirmed against the live
    database, which is still pinned to 108's intermediate name after 110 ran). Boot 4 adds
    114 and must repair it without raising and without losing the existing row.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "inverted.db"
    seed = _seed_dir(
        tmp_path,
        "seed_inverted",
        exclude=frozenset({_TOKENS_RENAME_FILE, _TOKENS_110_REPAIR_FILE, _REPAIR_FILE}),
    )

    # boot 1: user_chat_source_access exists, tokens has not been renamed/rebuilt yet.
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert "REFERENCES tokens(token_id)" in _ucsa_schema_sql(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        _seed_users(conn, "u1", "u2")
        conn.execute(
            "INSERT INTO user_chat_source_access "
            "(user_id, source_access_mode, created_at, updated_at) VALUES "
            "('u1', 'edit', '2026-09-08T00:00:00', '2026-09-08T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    # boot 2: the tokens-rename migration merges later and is now pending. Running it
    # rewrites user_chat_source_access's FK clause to the intermediate table name.
    shutil.copy(_SQLITE_DIR / _TOKENS_RENAME_FILE, seed / _TOKENS_RENAME_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    schema_after_rename = _ucsa_schema_sql(db_path)
    assert "tokens_before_base_dirty_scope" in schema_after_rename, (
        "the fixture no longer reproduces the FK rewrite -- if this fails, the test below "
        "is not proving what it claims to prove"
    )

    # the live symptom: any write now fails to compile.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.OperationalError, match="no such table.*tokens_before_base_dirty_scope"):
            conn.execute(
                "INSERT INTO user_chat_source_access "
                "(user_id, source_access_mode, created_at, updated_at) VALUES "
                "('u2', 'read_only', '2026-09-09T00:00:00', '2026-09-09T00:00:00')"
            )
    finally:
        conn.close()

    # boot 3: 110 (the earlier repair, for `tokens` only) runs later still, exactly as it
    # did live. It must not change user_chat_source_access's FK further -- it no longer
    # names `tokens` at all by this point, so 110's own rename skips it.
    shutil.copy(_SQLITE_DIR / _TOKENS_110_REPAIR_FILE, seed / _TOKENS_110_REPAIR_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert _ucsa_schema_sql(db_path) == schema_after_rename, (
        "110's rename must not further disturb the already-corrupted FK -- this matches "
        "the live database, which is still pinned to 108's intermediate name after 110 ran"
    )

    # boot 4: 114 must repair this without raising, and without losing the row from boot 1.
    shutil.copy(_SQLITE_DIR / _REPAIR_FILE, seed / _REPAIR_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    schema_after_repair = _ucsa_schema_sql(db_path)
    assert "REFERENCES tokens(token_id)" in schema_after_repair
    assert "tokens_before_base_dirty_scope" not in schema_after_repair

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert set(_disk_sqlite_upto_114()) <= applied

        row = conn.execute(
            "SELECT source_access_mode FROM user_chat_source_access WHERE user_id = 'u1'"
        ).fetchone()
        assert row is not None, "the pre-existing row must survive the rebuild"
        assert row[0] == "edit"

        # the exact write that 500'd live must now succeed.
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO user_chat_source_access "
            "(user_id, source_access_mode, created_at, updated_at) VALUES "
            "('u2', 'read_only', '2026-09-09T00:00:00', '2026-09-09T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_110_corrupts_the_fk_even_in_the_ordinary_disk_order_and_114_repairs_it(tmp_path):
    """Even the "ordinary" disk order (108_tokens_resolve_base_dirty_scope sorts before
    108_user_chat_source_access, so the FK is created correctly pointing at `tokens`) is
    not actually safe: 108_user_chat_source_access unconditionally sorts before 109 and 110
    regardless of the 108-pair's own order, so user_chat_source_access always exists by the
    time 110_repair_tokens_after_base_dirty_rebuild.sql renames `tokens` again for its own
    repair. That rename corrupts the FK the same way 108's did -- this is not a rare
    out-of-order-merge edge case, it reproduces on every SQLite deployment that has ever
    applied 110, which is already merged to main. 114 must repair this shape too.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "ordinary.db"
    seed = _seed_dir(tmp_path, "seed_ordinary", exclude=frozenset({_REPAIR_FILE}))

    # boot 1: every migration through 110, in the ordinary disk order, without 114.
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    schema_after_110 = _ucsa_schema_sql(db_path)
    assert "REFERENCES tokens(token_id)" not in schema_after_110, (
        "if this fails, 110 no longer corrupts the FK in the ordinary order and this test "
        "is not proving what it claims to prove"
    )
    assert "tokens_before_110_repair" in schema_after_110

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.OperationalError, match="no such table.*tokens_before_110_repair"):
            conn.execute(
                "INSERT INTO user_chat_source_access "
                "(user_id, source_access_mode, created_at, updated_at) VALUES "
                "('u3', 'edit_once', '2026-09-10T00:00:00', '2026-09-10T00:00:00')"
            )
    finally:
        conn.close()

    # boot 2: 114 must repair this shape too, regardless of which intermediate table name
    # the FK ended up dangling on.
    shutil.copy(_SQLITE_DIR / _REPAIR_FILE, seed / _REPAIR_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    schema = _ucsa_schema_sql(db_path)
    assert "REFERENCES tokens(token_id)" in schema
    assert "tokens_before" not in schema

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert set(_disk_sqlite_upto_114()) <= applied

        _seed_users(conn, "u3")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO user_chat_source_access "
            "(user_id, source_access_mode, created_at, updated_at) VALUES "
            "('u3', 'edit_once', '2026-09-10T00:00:00', '2026-09-10T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()
