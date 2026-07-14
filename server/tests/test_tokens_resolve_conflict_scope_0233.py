"""flowgate.default.0233 B0001 — resolve_conflict token INSERT must pass the CHECK.

The [멘트복사] (/token/issue) and [AI호출] (/ai-invoke/start) buttons for an unresolved
git conflict both issue a token with action_scope='resolve_conflict'. Migration 063 added
tokens.merge_id for those tokens but forgot to widen the tokens_action_scope_check CHECK
(introduced by 062), so the INSERT in db.tokens.create() violated the constraint and leaked
a bodyless HTTP 500 (NR0010). Migration 064 re-defines the CHECK to include
'resolve_conflict' across all three dialects.

These tests exercise the REAL migration DDL — no stubbing of the issue layer (the gap that
let 1200 local pytests pass while prod 500'd). The house `test_db` fixture applies every
sqlite migration, including 064, to a real connection, and we run the exact INSERT that
db.tokens.create() emits. Before 064 the resolve_conflict INSERT raises IntegrityError;
after 064 it is accepted. A static scan additionally pins all three dialect files.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_ROOT = _SERVER_DIR / "sql" / "migrations"

# The full action_scope vocabulary the code issues today (token_routes / ai_invoke_service).
_ALL_SCOPES = [
    "new", "edit", "workflow_decide", "review",
    "test_run", "workflow_sequence_edit", "resolve_conflict",
]

# Exact column list db.tokens.create() inserts (connection.py binds these positionally).
_INSERT_SQL = (
    "INSERT INTO tokens "
    "(token_id, hash, pepper_id, project, group_id, doc_ref, "
    "action_scope, issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir, "
    "continuation_target_seq, continuation_review_mode, continuation_locale, merge_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_token(conn, token_id: str, action_scope: str, *, merge_id=None) -> None:
    """Insert one token exactly as db.tokens.create() would (seeded __SYSTEM__/usr_admin)."""
    conn.execute(
        _INSERT_SQL,
        [
            token_id, f"hash_{token_id}", "pep_1",
            "__SYSTEM__", None, None,
            action_scope, "usr_admin",
            "2026-07-14T12:00:00+09:00", "2026-07-15T12:00:00+09:00",
            None, None, None,
            None, 0, None, merge_id,
        ],
    )


# ── the regression: the resolve_conflict INSERT must be accepted ─────────────

def test_resolve_conflict_token_insert_passes_check(test_db):
    """A resolve_conflict token (with merge_id, as 063+064 intend) inserts cleanly."""
    _insert_token(test_db, "tok_rc_1", "resolve_conflict", merge_id=28)

    row = test_db.execute(
        "SELECT action_scope, merge_id FROM tokens WHERE token_id = ?", ["tok_rc_1"]
    ).fetchone()
    assert row is not None
    assert row["action_scope"] == "resolve_conflict"
    assert row["merge_id"] == 28


@pytest.mark.parametrize("scope", _ALL_SCOPES)
def test_every_issued_scope_is_accepted(test_db, scope):
    """None of the seven scopes the code issues may be rejected by the CHECK."""
    tid = f"tok_scope_{scope}"
    _insert_token(test_db, tid, scope)
    row = test_db.execute(
        "SELECT action_scope FROM tokens WHERE token_id = ?", [tid]
    ).fetchone()
    assert row["action_scope"] == scope


def test_unknown_scope_still_rejected(test_db):
    """The CHECK is widened, not dropped: an unknown scope still raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_token(test_db, "tok_bogus_1", "definitely_not_a_scope")


# ── static guard so the sqlite-only pytest also pins postgres + mysql ────────

def _read_064(dialect: str) -> str:
    path = _MIGRATIONS_ROOT / dialect / "064_tokens_resolve_conflict_scope.sql"
    assert path.exists(), f"missing 064 migration for {dialect}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("dialect", ["postgres", "sqlite", "mysql"])
def test_064_migration_declares_all_scopes(dialect):
    """Every dialect's 064 must (re)declare the CHECK with all seven scopes present."""
    sql = _read_064(dialect)
    assert "action_scope IN (" in sql, f"{dialect} 064 does not redefine the CHECK"
    for scope in _ALL_SCOPES:
        assert f"'{scope}'" in sql, f"{dialect} 064 CHECK is missing '{scope}'"
