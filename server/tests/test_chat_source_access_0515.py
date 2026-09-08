"""Group 0515 T0009 — chat source-access DB, capability, handoff gate (D0006, L0007, DB0008).

Runs the CAS/state-machine contracts against a REAL SQLite database built from this
repo's migrations (the same LiveSqliteDB pattern test_review_atomicity_0535.py uses):
nothing that decides claim/commit/rollback/B-type-repair outcomes is mocked, because a
mock cannot show a CAS losing a race or a CHECK constraint rejecting a bad row.

Sections:
  1. migration files (no DB)
  2. schema constraints (FK/CHECK) on a real connection
  3. db.user_chat_source_access CRUD (claim/commit/rollback/upsert/B-type repair)
  4. chat_settings_service.recover_stale_claim's four token states + B-type
  5. token_service.issue() capability resolution + the edit_once claim transaction
  6. token_service.revoke() -> rollback wiring
  7. tool_registry.kind_for_token's chat branch
  8. runtime.HandoffGate's CAS state machine
  9. worker._await_handoff_gate's winner-only cleanup semantics
  10. token_routes._finish_chat_handoff's commit/ALREADY_CLEARED/exception paths
  11. save_chat_settings against the real two-store split (§3.3)
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.db import user_chat_settings as legacy_settings_store  # noqa: E402
from modules.flow_gate.db import user_chat_source_access as db_source_access  # noqa: E402
from modules.flow_gate.db.connection import now_iso  # noqa: E402
from modules.flow_gate.services import chat_settings_service as css  # noqa: E402
from modules.flow_gate.services import token_service  # noqa: E402
from modules.flow_gate.services import tool_registry  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402
from modules.flow_gate.services.ai_invoke import worker as worker_module  # noqa: E402

_MIGRATIONS_ROOT = _SERVER_DIR / "sql" / "migrations"
_MIGRATIONS_DIR = _MIGRATIONS_ROOT / "sqlite"
DIALECTS = ("sqlite", "mysql", "postgres")

PROJECT = "flowgate"
USER = "usr_0515"
OLD_TS = "2020-01-01T00:00:00+09:00"


# ── 1. migration files (no DB) ────────────────────────────────────────────────

class TestMigrationFiles:
    def test_108_and_109_ship_for_every_dialect(self):
        for dialect in DIALECTS:
            assert (_MIGRATIONS_ROOT / dialect / "108_user_chat_source_access.sql").is_file(), dialect
            assert (_MIGRATIONS_ROOT / dialect / "109_tokens_source_access.sql").is_file(), dialect

    def test_numbers_are_not_shared_with_another_file(self):
        for dialect in DIALECTS:
            names108 = sorted(p.name for p in (_MIGRATIONS_ROOT / dialect).glob("108_*.sql"))
            names109 = sorted(p.name for p in (_MIGRATIONS_ROOT / dialect).glob("109_*.sql"))
            assert names108 == ["108_user_chat_source_access.sql"], dialect
            assert names109 == ["109_tokens_source_access.sql"], dialect

    def test_mysql_uses_named_foreign_key_constraints_not_inline_references(self):
        body = (_MIGRATIONS_ROOT / "mysql" / "108_user_chat_source_access.sql").read_text(encoding="utf-8")
        assert "CONSTRAINT fk_ucsa_user" in body
        assert "CONSTRAINT fk_ucsa_token" in body
        assert "ON DELETE RESTRICT" in body
        assert "ON DELETE CASCADE" in body

    def test_108_only_adds(self):
        for dialect in DIALECTS:
            body = (_MIGRATIONS_ROOT / dialect / "108_user_chat_source_access.sql").read_text(encoding="utf-8")
            assert "DROP TABLE" not in body
            assert "ALTER TABLE" not in body

    def test_109_adds_a_nullable_capability_column(self):
        for dialect in DIALECTS:
            body = (_MIGRATIONS_ROOT / dialect / "109_tokens_source_access.sql").read_text(encoding="utf-8")
            assert "ADD COLUMN source_access" in body
            assert "DROP TABLE" not in body
            assert "UPDATE tokens" not in body  # no backfill (DB0008 §3.5)


# ── real SQLite backend (test_review_atomicity_0535.py's pattern) ──────────────

class _Txn:
    def __init__(self, db: "LiveSqliteDB"):
        self._db = db
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._db.conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class LiveSqliteDB:
    db_type = 1  # dialect.SQLITE

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self)
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def commit(self):
        self.conn.commit()

    def fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def rows(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _build_db(path: str) -> LiveSqliteDB:
    db = LiveSqliteDB(path)
    for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            db.conn.executescript(migration.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    now = "2026-09-08T00:00:00+09:00"
    db.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (PROJECT, "FlowGate", now, now),
    )
    db.conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (USER, "u0515", "u0515@test", "hashed", now, now),
    )
    db.conn.commit()
    return db


def _insert_token(db: LiveSqliteDB, token_id: str, *, source_access=None,
                   revoked_at=None, consumed_at=None, action_scope="chat") -> None:
    now = "2026-09-08T00:00:00+09:00"
    db.conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, action_scope, issued_to, "
        "created_at, expires_at, revoked_at, consumed_at, source_access) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (token_id, f"hash-{token_id}", "v1", PROJECT, action_scope, USER, now,
         "2036-01-01T00:00:00+09:00", revoked_at, consumed_at, source_access),
    )
    db.conn.commit()


@pytest.fixture
def db_env(monkeypatch, tmp_path):
    db = _build_db(str(tmp_path / "flowgate.db"))
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "v1")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_v1", "test-pepper-0515")
    return db


# ── 2. schema constraints ───────────────────────────────────────────────────────

class TestSchema:
    def test_mode_domain_is_enforced(self, db_env):
        now = "T"
        with pytest.raises(sqlite3.IntegrityError):
            db_env.conn.execute(
                "INSERT INTO user_chat_source_access (user_id, source_access_mode, created_at, updated_at) "
                "VALUES (?, 'bogus', ?, ?)", (USER, now, now),
            )
        db_env.conn.rollback()

    def test_marker_pair_check_rejects_a_half_pair(self, db_env):
        now = "T"
        _insert_token(db_env, "tok-half")
        with pytest.raises(sqlite3.IntegrityError):
            db_env.conn.execute(
                "INSERT INTO user_chat_source_access "
                "(user_id, source_access_mode, one_shot_token_id, one_shot_claimed_at, created_at, updated_at) "
                "VALUES (?, 'read_only', 'tok-half', NULL, ?, ?)", (USER, now, now),
            )
        db_env.conn.rollback()

    def test_one_shot_token_id_fk_requires_a_real_token(self, db_env):
        now = "T"
        with pytest.raises(sqlite3.IntegrityError):
            db_env.conn.execute(
                "INSERT INTO user_chat_source_access "
                "(user_id, source_access_mode, one_shot_token_id, one_shot_claimed_at, created_at, updated_at) "
                "VALUES (?, 'read_only', 'tok-missing-fk', ?, ?, ?)", (USER, now, now, now),
            )
        db_env.conn.rollback()

    def test_deleting_a_claimed_tokens_parent_is_restricted(self, db_env):
        now = "T"
        _insert_token(db_env, "tok-restrict")
        db_env.conn.execute(
            "INSERT INTO user_chat_source_access "
            "(user_id, source_access_mode, one_shot_token_id, one_shot_claimed_at, created_at, updated_at) "
            "VALUES (?, 'read_only', 'tok-restrict', ?, ?, ?)", (USER, now, now, now),
        )
        db_env.conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db_env.conn.execute("DELETE FROM tokens WHERE token_id = 'tok-restrict'")
        db_env.conn.rollback()

    def test_deleting_the_user_cascades(self, db_env):
        now = "T"
        db_env.conn.execute(
            "INSERT INTO user_chat_source_access (user_id, source_access_mode, created_at, updated_at) "
            "VALUES (?, 'edit', ?, ?)", (USER, now, now),
        )
        db_env.conn.commit()
        db_env.conn.execute("DELETE FROM users WHERE user_id = ?", (USER,))
        db_env.conn.commit()
        assert db_env.rows("SELECT * FROM user_chat_source_access WHERE user_id=?", (USER,)) == []

    def test_tokens_source_access_domain(self, db_env):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_token(db_env, "tok-bad-domain", source_access="bogus")
        db_env.conn.rollback()

    def test_legacy_tokens_keep_source_access_null(self, db_env):
        _insert_token(db_env, "tok-legacy", source_access=None, action_scope="new")
        row = db_env.rows("SELECT source_access FROM tokens WHERE token_id='tok-legacy'")[0]
        assert row["source_access"] is None


# ── 3. db.user_chat_source_access CRUD ──────────────────────────────────────────

class TestSourceAccessStoreCRUD:
    def test_resolve_default_is_read_only_with_no_row(self, db_env):
        assert db_source_access.resolve_source_access_mode(USER) == "read_only"

    def test_invalid_persisted_mode_resolves_fail_closed(self, monkeypatch):
        # A corrupted row can bypass the database CHECK (for example after an
        # out-of-band restore). It must never leak through settings or token issuance.
        monkeypatch.setattr(
            db_source_access,
            "get",
            lambda _user_id: {"source_access_mode": "corrupted"},
        )
        assert db_source_access.resolve_source_access_mode(USER) == "read_only"

    def test_upsert_then_resolve(self, db_env):
        db_source_access.upsert(USER, "edit", "T1")
        assert db_source_access.resolve_source_access_mode(USER) == "edit"
        row = db_source_access.get(USER)
        assert row["created_at"] == "T1"
        assert row["updated_at"] == "T1"

    def test_upsert_again_changes_mode_and_keeps_created_at(self, db_env):
        db_source_access.upsert(USER, "edit", "T1")
        db_source_access.upsert(USER, "edit_once", "T2")
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        assert row["created_at"] == "T1"
        assert row["updated_at"] == "T2"

    def test_upsert_clears_a_pending_claim(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-claim1")
        result = db_source_access.claim(USER, "tok-claim1", "T2", "T2")
        assert result == {"source_access": "read_write", "one_shot_claimed": True}
        db_source_access.upsert(USER, "read_only", "T3")
        row = db_source_access.get(USER)
        assert row["one_shot_token_id"] is None
        assert row["one_shot_claimed_at"] is None
        assert db_source_access.commit(USER, "tok-claim1", "T4") == "ALREADY_CLEARED"

    def test_claim_wins_exactly_once(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-a")
        _insert_token(db_env, "tok-b")
        first = db_source_access.claim(USER, "tok-a", "T2", "T2")
        second = db_source_access.claim(USER, "tok-b", "T3", "T3")
        assert first == {"source_access": "read_write", "one_shot_claimed": True}
        assert second == {"source_access": "read", "one_shot_claimed": False}
        row = db_source_access.get(USER)
        assert row["one_shot_token_id"] == "tok-a"
        assert row["source_access_mode"] == "read_only"

    def test_commit_clears_marker_without_touching_mode(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-c")
        db_source_access.claim(USER, "tok-c", "T2", "T2")
        assert db_source_access.commit(USER, "tok-c", "T3") == "COMMITTED"
        row = db_source_access.get(USER)
        assert row["one_shot_token_id"] is None
        assert row["one_shot_claimed_at"] is None
        assert row["source_access_mode"] == "read_only"

    def test_commit_twice_is_already_cleared(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-d")
        db_source_access.claim(USER, "tok-d", "T2", "T2")
        assert db_source_access.commit(USER, "tok-d", "T3") == "COMMITTED"
        assert db_source_access.commit(USER, "tok-d", "T4") == "ALREADY_CLEARED"

    def test_rollback_restores_edit_once_and_is_idempotent(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-e")
        db_source_access.claim(USER, "tok-e", "T2", "T2")
        assert db_source_access.rollback("tok-e", "T3") is True
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        assert row["one_shot_token_id"] is None
        assert db_source_access.rollback("tok-e", "T4") is False

    def test_rollback_after_commit_is_a_no_op(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-f")
        db_source_access.claim(USER, "tok-f", "T2", "T2")
        db_source_access.commit(USER, "tok-f", "T3")
        assert db_source_access.rollback("tok-f", "T4") is False
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "read_only"

    def test_clear_b_type_marker_is_fail_closed_and_idempotent(self, db_env):
        db_source_access.upsert(USER, "read_only", "T1")
        # B-type cannot be produced by normal DML (the pair CHECK forbids it) --
        # simulate the damaged shape directly, bypassing the CHECK.
        db_env.conn.execute("PRAGMA ignore_check_constraints = 1")
        db_env.conn.execute(
            "UPDATE user_chat_source_access SET one_shot_claimed_at = ? WHERE user_id = ?",
            ("T-corrupt", USER),
        )
        db_env.conn.commit()
        db_env.conn.execute("PRAGMA ignore_check_constraints = 0")
        assert db_source_access.clear_b_type_marker(USER, "T2") is True
        row = db_source_access.get(USER)
        assert row["one_shot_claimed_at"] is None
        assert row["source_access_mode"] == "read_only"
        assert db_source_access.clear_b_type_marker(USER, "T3") is False


# ── 4. stale-claim recovery ─────────────────────────────────────────────────────

class TestRecoverStaleClaim:
    def test_no_row_is_nothing_to_do(self, db_env):
        assert css.recover_stale_claim(USER) == "NOTHING_TO_DO"

    def test_available_row_is_nothing_to_do(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        assert css.recover_stale_claim(USER) == "NOTHING_TO_DO"

    def test_fresh_claim_is_left_alone(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-fresh")
        db_source_access.claim(USER, "tok-fresh", now_iso(), now_iso())
        assert css.recover_stale_claim(USER) == "NOTHING_TO_DO"

    def test_missing_token_is_rolled_back(self, db_env, monkeypatch):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-missing")
        db_source_access.claim(USER, "tok-missing", OLD_TS, OLD_TS)
        monkeypatch.setattr(db_tokens, "get_by_id", lambda _tid: None)
        assert css.recover_stale_claim(USER) == "MISSING_RECOVERED"
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        assert row["one_shot_token_id"] is None

    def test_revoked_token_is_rolled_back(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-revoked", revoked_at=OLD_TS)
        db_source_access.claim(USER, "tok-revoked", OLD_TS, OLD_TS)
        assert css.recover_stale_claim(USER) == "REVOKED_RECOVERED"
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        assert row["one_shot_token_id"] is None

    def test_consumed_token_marker_cleared_without_rearming(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-consumed", consumed_at=OLD_TS)
        db_source_access.claim(USER, "tok-consumed", OLD_TS, OLD_TS)
        assert css.recover_stale_claim(USER) == "CONSUMED_MARKER_CLEANED"
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "read_only"
        assert row["one_shot_token_id"] is None

    def test_live_stale_token_gets_revoked_and_rolled_back(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        _insert_token(db_env, "tok-live")
        db_source_access.claim(USER, "tok-live", OLD_TS, OLD_TS)
        assert css.recover_stale_claim(USER) == "LIVE_REVOKED"
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        tok = db_env.rows("SELECT revoked_at FROM tokens WHERE token_id='tok-live'")[0]
        assert tok["revoked_at"] is not None

    def test_b_type_marker_is_cleaned_via_recover(self, db_env):
        db_source_access.upsert(USER, "read_only", "T1")
        db_env.conn.execute("PRAGMA ignore_check_constraints = 1")
        db_env.conn.execute(
            "UPDATE user_chat_source_access SET one_shot_claimed_at = ? WHERE user_id = ?",
            (OLD_TS, USER),
        )
        db_env.conn.commit()
        db_env.conn.execute("PRAGMA ignore_check_constraints = 0")
        assert css.recover_stale_claim(USER) == "B_TYPE_CLEANED"


# ── 5. token_service.issue() capability + claim transaction ────────────────────

class TestChatTokenIssueCapability:
    def test_read_only_mode_issues_read(self, db_env):
        db_source_access.upsert(USER, "read_only", "T1")
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        assert result["source_access"] == "read"
        assert result["one_shot_claimed"] is False
        assert db_tokens.get_by_id(result["token_id"])["source_access"] == "read"

    def test_edit_mode_issues_read_write_without_claiming(self, db_env):
        db_source_access.upsert(USER, "edit", "T1")
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        assert result["source_access"] == "read_write"
        assert result["one_shot_claimed"] is False
        assert db_source_access.get(USER)["one_shot_token_id"] is None

    def test_edit_once_first_caller_wins_the_claim(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        assert result["source_access"] == "read_write"
        assert result["one_shot_claimed"] is True
        row = db_source_access.get(USER)
        assert row["one_shot_token_id"] == result["token_id"]
        assert row["source_access_mode"] == "read_only"

    def test_edit_once_second_caller_gets_read(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        first = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        second = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        assert first["one_shot_claimed"] is True
        assert second["source_access"] == "read"
        assert second["one_shot_claimed"] is False

    def test_non_chat_token_carries_no_source_access(self, db_env):
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="new", doc_ref=None, issued_to=USER,
        )
        assert result["source_access"] is None
        assert result["one_shot_claimed"] is False
        assert db_tokens.get_by_id(result["token_id"])["source_access"] is None

    def test_legacy_source_access_falls_back_to_read_via_kind_for_token(self, db_env):
        # T0009 §7 / §12.6: None/legacy is a `kind_for_step` fallback, never a widen.
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="new", doc_ref=None, issued_to=USER,
        )
        token_rec = db_tokens.get_by_id(result["token_id"])
        kind, _reason = tool_registry.kind_for_token(token_rec)
        assert kind in ("read", "read_write", "none")  # judged by kind_for_step, not by source_access


# ── 6. revoke() -> rollback wiring ──────────────────────────────────────────────

class TestRevokeRollbackWiring:
    def test_revoking_a_claimed_token_restores_edit_once(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T1")
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="chat", doc_ref=None, issued_to=USER,
        )
        assert result["one_shot_claimed"] is True
        token_service.revoke(result["token_id"], reason="test")
        row = db_source_access.get(USER)
        assert row["source_access_mode"] == "edit_once"
        assert row["one_shot_token_id"] is None

    def test_revoking_a_non_claimed_token_is_a_harmless_no_op(self, db_env):
        result = token_service.issue(
            project=PROJECT, group_id=None, action_scope="new", doc_ref=None, issued_to=USER,
        )
        token_service.revoke(result["token_id"], reason="test")  # must not raise


# ── 7. tool_registry.kind_for_token's chat branch ───────────────────────────────

class TestKindForTokenChatCapability:
    def test_chat_read_write_source_access_wins(self):
        assert tool_registry.kind_for_token(
            {"action_scope": "chat", "source_access": "read_write"}
        ) == ("read_write", None)

    def test_chat_read_source_access_wins(self):
        assert tool_registry.kind_for_token(
            {"action_scope": "chat", "source_access": "read"}
        ) == ("read", None)

    def test_chat_without_source_access_falls_back_to_read(self):
        assert tool_registry.kind_for_token(
            {"action_scope": "chat", "source_access": None}
        ) == ("read", None)

    def test_non_chat_token_ignores_a_stray_source_access(self):
        assert tool_registry.kind_for_token(
            {"action_scope": "review", "source_access": "read_write"}
        ) == ("read", None)

    def test_resolve_base_dirty_never_reaches_a_chat_token(self):
        names = tool_registry.tool_names("read_write", "chat")
        assert "resolve_base_dirty" not in names


# ── 8. HandoffGate CAS state machine ────────────────────────────────────────────

class TestHandoffGate:
    def test_claim_less_path_opens_directly(self):
        gate = ai_runtime.HandoffGate()
        assert gate.open_direct() is True
        assert gate.state == ai_runtime.OPEN

    def test_one_shot_path_seals_then_opens(self):
        gate = ai_runtime.HandoffGate()
        assert gate.seal() is True
        assert gate.state == ai_runtime.SEALED
        assert gate.open_direct() is False  # wrong predecessor state
        assert gate.open_after_seal() is True
        assert gate.state == ai_runtime.OPEN

    def test_only_one_caller_wins_abort(self):
        gate = ai_runtime.HandoffGate()
        results = [gate.abort() for _ in range(5)]
        assert results.count(True) == 1
        assert gate.state == ai_runtime.ABORT

    def test_open_after_abort_fails(self):
        gate = ai_runtime.HandoffGate()
        gate.abort()
        assert gate.open_direct() is False
        assert gate.seal() is False

    def test_wait_pending_returns_immediately_once_state_changes(self):
        gate = ai_runtime.HandoffGate()
        gate.open_direct()
        assert gate.wait_pending(timeout=0.05) == ai_runtime.OPEN

    def test_wait_pending_times_out_while_still_pending(self):
        gate = ai_runtime.HandoffGate()
        assert gate.wait_pending(timeout=0.05) == ai_runtime.PENDING


# ── 9. worker._await_handoff_gate's winner-only cleanup ─────────────────────────

class _FakeGate:
    def __init__(self, sequence):
        self._sequence = list(sequence)

    def wait_pending(self, timeout=None):
        return self._sequence.pop(0)

    def wait_sealed(self, timeout=None):
        return self._sequence.pop(0)

    def abort(self):
        return True


class TestAwaitHandoffGate:
    def test_no_gate_is_treated_as_already_open(self):
        assert worker_module._await_handoff_gate({}) is True

    def test_open_from_pending_returns_true(self):
        gate = _FakeGate([ai_runtime.OPEN])
        assert worker_module._await_handoff_gate({"handoff_gate": gate}) is True

    def test_pending_timeout_wins_abort_and_cleans_up(self, monkeypatch):
        gate = _FakeGate([ai_runtime.PENDING])
        calls = []
        monkeypatch.setattr(admission, "_abort_handoff", lambda run, reason: calls.append(reason))
        assert worker_module._await_handoff_gate({"handoff_gate": gate, "run_id": "r1"}) is False
        assert calls == ["pending_timeout"]

    def test_pending_abort_lost_race_does_not_recleanup(self, monkeypatch):
        gate = _FakeGate([ai_runtime.ABORT])
        calls = []
        monkeypatch.setattr(admission, "_abort_handoff", lambda run, reason: calls.append(reason))
        assert worker_module._await_handoff_gate({"handoff_gate": gate}) is False
        assert calls == []

    def test_sealed_then_open_returns_true(self):
        gate = _FakeGate([ai_runtime.SEALED, ai_runtime.OPEN])
        assert worker_module._await_handoff_gate({"handoff_gate": gate}) is True

    def test_sealed_timeout_wins_abort_and_cleans_up(self, monkeypatch):
        gate = _FakeGate([ai_runtime.SEALED, ai_runtime.SEALED])
        calls = []
        monkeypatch.setattr(admission, "_abort_handoff", lambda run, reason: calls.append(reason))
        assert worker_module._await_handoff_gate({"handoff_gate": gate}) is False
        assert calls == ["sealed_timeout"]

    def test_sealed_abort_lost_race_does_not_recleanup(self, monkeypatch):
        gate = _FakeGate([ai_runtime.SEALED, ai_runtime.ABORT])
        calls = []
        monkeypatch.setattr(admission, "_abort_handoff", lambda run, reason: calls.append(reason))
        assert worker_module._await_handoff_gate({"handoff_gate": gate}) is False
        assert calls == []


# ── 10. token_routes._finish_chat_handoff ───────────────────────────────────────

class TestFinishChatHandoff:
    def test_empty_mention_revokes_and_raises_gate_lost_code(self, monkeypatch):
        revoked = []
        monkeypatch.setattr(
            token_routes.token_service, "revoke",
            lambda tid, reason=None: revoked.append((tid, reason)),
        )
        with pytest.raises(HTTPException) as exc:
            token_routes._finish_chat_handoff({"token_id": "tok-x"}, "", USER)
        assert exc.value.status_code == 500
        assert exc.value.detail["code"] == "chat_token_revoked_before_handoff"
        assert revoked == [("tok-x", "chat_mention_build_failed")]

    def test_committed_claim_returns_the_mention(self, monkeypatch):
        monkeypatch.setattr(db_source_access, "commit", lambda user_id, token_id, updated_at: "COMMITTED")
        result = {"token_id": "tok-y", "one_shot_claimed": True}
        assert token_routes._finish_chat_handoff(result, "## mention", USER) == "## mention"

    def test_already_cleared_revokes_and_returns_409(self, monkeypatch):
        monkeypatch.setattr(db_source_access, "commit", lambda user_id, token_id, updated_at: "ALREADY_CLEARED")
        revoked = []
        monkeypatch.setattr(
            token_routes.token_service, "revoke",
            lambda tid, reason=None: revoked.append((tid, reason)),
        )
        result = {"token_id": "tok-z", "one_shot_claimed": True}
        with pytest.raises(HTTPException) as exc:
            token_routes._finish_chat_handoff(result, "## mention", USER)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "chat_one_shot_claim_superseded"
        assert revoked == [("tok-z", "chat_one_shot_claim_superseded")]

    def test_commit_exception_revokes_rolls_back_and_returns_500(self, monkeypatch):
        def _boom(user_id, token_id, updated_at):
            raise RuntimeError("db down")

        monkeypatch.setattr(db_source_access, "commit", _boom)
        rolled_back = []
        monkeypatch.setattr(
            db_source_access, "rollback",
            lambda token_id, updated_at: rolled_back.append(token_id),
        )
        revoked = []
        monkeypatch.setattr(
            token_routes.token_service, "revoke",
            lambda tid, reason=None: revoked.append((tid, reason)),
        )
        result = {"token_id": "tok-w", "one_shot_claimed": True}
        with pytest.raises(HTTPException) as exc:
            token_routes._finish_chat_handoff(result, "## mention", USER)
        assert exc.value.status_code == 500
        assert exc.value.detail["code"] == "chat_one_shot_commit_failed"
        assert revoked == [("tok-w", "chat_one_shot_commit_failed")]
        assert rolled_back == ["tok-w"]

    def test_read_only_result_with_a_mention_passes_through_untouched(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            db_source_access, "commit",
            lambda *a, **k: called.append(1) or "COMMITTED",
        )
        result = {"token_id": "tok-v", "one_shot_claimed": False}
        assert token_routes._finish_chat_handoff(result, "## mention", USER) == "## mention"
        assert called == []


# ── 11. save_chat_settings against the real two-store split ────────────────────

class TestSaveChatSettingsRealDb:
    def test_source_only_patch_does_not_create_a_legacy_row(self, db_env):
        css.save_chat_settings(USER, {"source_access_mode": "edit"})
        assert legacy_settings_store.get(USER) is None
        assert db_source_access.resolve_source_access_mode(USER) == "edit"

    def test_mixed_patch_writes_both_stores_in_one_transaction(self, db_env):
        css.save_chat_settings(USER, {"context_turns": 12, "source_access_mode": "edit_once"})
        assert legacy_settings_store.get(USER)["context_turns"] == 12
        assert db_source_access.resolve_source_access_mode(USER) == "edit_once"

    def test_saving_clears_a_pending_one_shot_claim(self, db_env):
        db_source_access.upsert(USER, "edit_once", "T0")
        _insert_token(db_env, "tok-clear")
        db_source_access.claim(USER, "tok-clear", now_iso(), now_iso())
        css.save_chat_settings(USER, {"source_access_mode": "read_only"})
        row = db_source_access.get(USER)
        assert row["one_shot_token_id"] is None
        assert db_source_access.commit(USER, "tok-clear", now_iso()) == "ALREADY_CLEARED"
