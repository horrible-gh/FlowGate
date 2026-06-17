"""Group 0088 — multi-DB runtime SQL portability.

Unit tests for db.dialect.translate(): feed the real SQLite-dialect query strings
that the db submodules emit and assert the rewrites for MariaDB(MySQL)/PostgreSQL.
The translator must be a strict no-op for SQLite so existing behaviour is preserved.
"""
import os

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.db import dialect  # noqa: E402
from modules.flow_gate.db.dialect import SQLITE, MYSQL, POSTGRESQL, translate  # noqa: E402


# ── SQLite: strict no-op ────────────────────────────────────────────────────────

def test_sqlite_is_noop():
    sqls = [
        "SELECT * FROM tokens WHERE token_id = ?",
        "INSERT INTO notification_seen (user_id, project_id, last_seen_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, project_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        "SELECT doc_id FROM documents WHERE note LIKE '%rejected%'",
    ]
    for s in sqls:
        assert translate(s, SQLITE) == s
        assert translate(s, None) == s


# ── Placeholders ────────────────────────────────────────────────────────────────

def test_placeholder_qmark_to_pyformat():
    sql = "SELECT * FROM tokens WHERE token_id = ? AND action_scope = ?"
    assert "?" not in translate(sql, MYSQL)
    assert translate(sql, MYSQL).count("%s") == 2
    assert "?" not in translate(sql, POSTGRESQL)
    assert translate(sql, POSTGRESQL).count("%s") == 2


def test_qmark_inside_string_literal_preserved():
    sql = "SELECT * FROM t WHERE col = '? literal' AND id = ?"
    out = translate(sql, MYSQL)
    assert "'? literal'" in out
    assert out.count("%s") == 1


def test_literal_percent_is_doubled_for_pyformat():
    sql = "SELECT doc_id FROM documents WHERE LOWER(note) LIKE '%rejected%' AND id = ?"
    for d in (MYSQL, POSTGRESQL):
        out = translate(sql, d)
        assert "%%rejected%%" in out
        assert out.endswith("= %s")


# ── ON CONFLICT DO UPDATE ───────────────────────────────────────────────────────

NOTIF = (
    "INSERT INTO notification_seen (user_id, project_id, last_seen_at) VALUES (?, ?, ?) "
    "ON CONFLICT(user_id, project_id) DO UPDATE SET last_seen_at = excluded.last_seen_at"
)


def test_on_conflict_do_update_mysql():
    out = translate(NOTIF, MYSQL)
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "ON CONFLICT" not in out
    assert "excluded." not in out
    assert "last_seen_at = VALUES(last_seen_at)" in out
    assert out.count("%s") == 3


def test_on_conflict_do_update_postgres_keeps_native():
    out = translate(NOTIF, POSTGRESQL)
    assert "ON CONFLICT(user_id, project_id) DO UPDATE" in out
    assert "excluded.last_seen_at" in out  # PostgreSQL-native
    assert out.count("%s") == 3


def test_complex_upsert_table_qualifier_stripped_mysql():
    # The real upsert_content() query: CASE expression referencing both the
    # excluded row and the existing table row.
    sql = (
        "INSERT INTO document_type_template_contents "
        "(template_id, locale, content, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(template_id, locale) DO UPDATE SET "
        "content = excluded.content, "
        "updated_by = CASE WHEN document_type_template_contents.content = excluded.content "
        "THEN document_type_template_contents.updated_by ELSE excluded.updated_by END"
    )
    out = translate(sql, MYSQL)
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "excluded." not in out
    assert "document_type_template_contents." not in out
    assert "content = VALUES(content)" in out
    # existing-row reference becomes the bare column
    assert "WHEN content = VALUES(content) THEN updated_by" in out


def test_conditional_where_dropped_for_mysql():
    # projects.add_allowed_project: ON CONFLICT ... DO UPDATE ... WHERE projects.is_active = 0
    sql = (
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET is_active = 1, updated_at = excluded.updated_at "
        "WHERE projects.is_active = 0"
    )
    out = translate(sql, MYSQL)
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "WHERE" not in out.upper().split("ON DUPLICATE KEY UPDATE")[1]
    assert "is_active = 1" in out
    assert "updated_at = VALUES(updated_at)" in out
    # PostgreSQL keeps the conditional WHERE
    pg = translate(sql, POSTGRESQL)
    assert "WHERE projects.is_active = 0" in pg


# ── ON CONFLICT DO NOTHING ──────────────────────────────────────────────────────

DO_NOTHING = (
    "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?) ON CONFLICT DO NOTHING"
)


def test_do_nothing_mysql_becomes_insert_ignore():
    out = translate(DO_NOTHING, MYSQL)
    assert out.startswith("INSERT IGNORE INTO role_permissions")
    assert "ON CONFLICT" not in out
    assert "DO NOTHING" not in out
    assert out.rstrip().endswith("(%s, %s)")


def test_do_nothing_postgres_keeps_native():
    out = translate(DO_NOTHING, POSTGRESQL)
    assert "ON CONFLICT DO NOTHING" in out
    assert out.count("%s") == 2


# ── INSERT OR REPLACE / IGNORE (defensive) ──────────────────────────────────────

def test_insert_or_replace_mysql():
    out = translate("INSERT OR REPLACE INTO t (a, b) VALUES (?, ?)", MYSQL)
    assert out.startswith("REPLACE INTO t")
    assert "OR REPLACE" not in out


def test_insert_or_ignore_mysql_and_pg():
    src = "INSERT OR IGNORE INTO t (a, b) VALUES (?, ?)"
    assert translate(src, MYSQL).startswith("INSERT IGNORE INTO t")
    pg = translate(src, POSTGRESQL)
    assert pg.startswith("INSERT INTO t")
    assert pg.rstrip().endswith("ON CONFLICT DO NOTHING")
