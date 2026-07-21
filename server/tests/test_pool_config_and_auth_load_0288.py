"""Pool sizing + per-request load reduction (flowgate.default.0288 NR0003) — unit suite.

TR0005 fixed the sqloader side (권고 1). This covers the two flowgate-side
recommendations that were left open:

  - 권고 2 / 발견 4: server/config.py forwarded nothing to sqloader, so the
    PostgreSQL pool stayed at the library defaults (5 parallel queries,
    pool 1..5) no matter what the environment said — 40 AnyIO worker threads
    competing over 5 slots, and pool_min=1 forcing a reconnect per query.
  - 권고 3 / 발견 5: the worker-token hash lookup ran once per authenticated
    request, and GET /document's Q&A block ran 2 + N queries for N questions.

Environment mirrors test_screen_load_query_reduction_0282.py: TESTING=1 and a
temporary SQLite built from the real sqlite migrations, patched into
connection.STORE.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
# config.py builds its Settings() at import time; these three have no default.
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    """SQLite-backed store that also counts the SELECTs it is asked for."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.reads: list[str] = []

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        self.reads.append(sql)
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        self.reads.append(sql)
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Pre-existing schema defect, unrelated to this group: sqlite migration 064
    # rebuilds `tokens` and its column list omits continuation_instruction_mode,
    # which the same-numbered 063_tokens_continuation_instruction_mode.sql added
    # just before it — so a table built by replaying the files in order loses the
    # column that db/tokens.py INSERTs. Restore it here rather than let it fail
    # this suite; reported separately in the task report.
    cols = {r[1] for r in mock_db._conn.execute("PRAGMA table_info(tokens)")}
    if "continuation_instruction_mode" not in cols:
        mock_db._conn.execute(
            "ALTER TABLE tokens ADD COLUMN continuation_instruction_mode TEXT"
        )
    mock_db._conn.commit()
    yield mock_db
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = tmp_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module", autouse=True)
def seed(patch_store):
    """Project / group / documents / user the tokens and questions rows point at."""
    from modules.flow_gate.db import connection, documents, groups, projects

    # tokens.issued_to and questions.created_by both REFERENCE users(user_id).
    connection.get_store()._execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES ('tester', 'tester', 'tester@example.invalid', 'x', ?, ?)",
        [connection.now_iso(), connection.now_iso()],
    )
    projects.create({"project_id": "poolprj", "project_name": "PoolPrj"})
    groups.create({
        "group_id": "poolprj.default.0001",
        "project_id": "poolprj",
        "module": "default",
        "title": "pool group",
    })
    for seq in (1, 9):
        documents.create({
            "doc_id": f"poolprj.default.0001.{seq:04d}-B",
            "project_id": "poolprj",
            "module": "default",
            "group_id": "poolprj.default.0001",
            "type_code": "B",
            "seq": seq,
            "title": "bug",
        })
    yield


@pytest.fixture(autouse=True)
def clean_token_cache(tmp_db):
    """Every test starts and ends with an empty token cache and read log."""
    from modules.flow_gate.auth import auth_cache

    auth_cache.invalidate_tokens()
    tmp_db.reads.clear()
    yield
    auth_cache.invalidate_tokens()


# ── 권고 2 / 발견 4: pool settings reach sqloader ────────────────────────────

class TestPoolSettingsDefaults:
    def _settings(self, **overrides):
        from config import Settings

        base = {
            "ALLOWED_ORIGIN": "*",
            "SECRET_KEY": "x" * 32,
            "CONTEXT": "/flowgate",
            "DB_TYPE": "postgres",
        }
        base.update(overrides)
        return Settings(**base)

    def test_defaults_exceed_the_sqloader_fallback(self):
        s = self._settings()
        # The point of the change: not sqloader's 5 / 1 / 5.
        assert s.DB_MAX_PARALLEL_QUERIES == 20
        assert s.DB_POOL_MIN == 5
        assert s.DB_POOL_MAX == 24
        assert s.DB_ACQUIRE_TIMEOUT == 30.0
        assert s.DB_POOL_MAX_LIFETIME is None
        assert s.DB_POOL_MAX_IDLE is None

    def test_blank_values_fall_back_to_defaults(self):
        # `.env.sample` ships DB_POOL_MAX_LIFETIME= (blank) and setup copies it
        # verbatim; a present-but-empty key must not crash the boot.
        s = self._settings(
            DB_MAX_PARALLEL_QUERIES="",
            DB_POOL_MIN="",
            DB_POOL_MAX="",
            DB_ACQUIRE_TIMEOUT="",
            DB_POOL_MAX_LIFETIME="",
            DB_POOL_MAX_IDLE="",
        )
        assert s.DB_MAX_PARALLEL_QUERIES == 20
        assert s.DB_POOL_MAX == 24
        assert s.DB_ACQUIRE_TIMEOUT == 30.0
        assert s.DB_POOL_MAX_LIFETIME is None

    def test_overrides_are_honoured(self):
        s = self._settings(DB_MAX_PARALLEL_QUERIES=8, DB_POOL_MIN=2, DB_POOL_MAX=8)
        assert (s.DB_MAX_PARALLEL_QUERIES, s.DB_POOL_MIN, s.DB_POOL_MAX) == (8, 2, 8)

    def test_pool_max_below_parallel_limit_is_rejected(self):
        # sqloader raises this too, but only at DB init and only for postgres.
        with pytest.raises(Exception) as exc:
            self._settings(DB_MAX_PARALLEL_QUERIES=20, DB_POOL_MAX=10)
        assert "DB_POOL_MAX" in str(exc.value)

    def test_pool_min_above_pool_max_is_rejected(self):
        with pytest.raises(Exception) as exc:
            self._settings(DB_POOL_MIN=30, DB_POOL_MAX=24)
        assert "DB_POOL_MIN" in str(exc.value)


class TestPoolSettingsForwarding:
    def test_postgres_branch_carries_every_pool_key(self, monkeypatch):
        import config

        monkeypatch.setattr(config.settings, "DB_TYPE", config.DBType.POSTGRES)
        monkeypatch.setattr(config.settings, "DB_MAX_PARALLEL_QUERIES", 12)
        monkeypatch.setattr(config.settings, "DB_POOL_MIN", 3)
        monkeypatch.setattr(config.settings, "DB_POOL_MAX", 15)
        monkeypatch.setattr(config.settings, "DB_ACQUIRE_TIMEOUT", 7.5)
        monkeypatch.setattr(config.settings, "DB_POOL_MAX_LIFETIME", 600.0)
        monkeypatch.setattr(config.settings, "DB_POOL_MAX_IDLE", 120.0)
        # Build the config dict only — no server, no connection.
        monkeypatch.setattr(config.DatabaseSetting, "instance_init", lambda self: None)

        setting = object.__new__(config.DatabaseSetting)
        setting._init_db()

        pg = setting.config["postgres"]
        assert pg["max_parallel_queries"] == 12
        assert pg["pool_min"] == 3
        assert pg["pool_max"] == 15
        assert pg["acquire_timeout"] == 7.5
        assert pg["max_lifetime"] == 600.0
        assert pg["max_idle"] == 120.0

    def test_mysql_branch_carries_only_the_keys_that_backend_reads(self, monkeypatch):
        import config

        monkeypatch.setattr(config.settings, "DB_TYPE", config.DBType.MYSQL)
        monkeypatch.setattr(config.settings, "DB_MAX_PARALLEL_QUERIES", 12)
        monkeypatch.setattr(config.DatabaseSetting, "instance_init", lambda self: None)

        setting = object.__new__(config.DatabaseSetting)
        setting._init_db()

        my = setting.config["mysql"]
        assert my["max_parallel_queries"] == 12
        assert "acquire_timeout" in my
        # MySqlWrapper has no pool; sending pool keys there would be dead config.
        assert "pool_min" not in my and "pool_max" not in my


# ── 권고 3 / 발견 5a: the worker-token lookup is cached ──────────────────────

def _issue_token(token_id: str, token_hash: str) -> None:
    from modules.flow_gate.db import tokens as db_tokens

    now = datetime.now(timezone.utc)
    db_tokens.create({
        "token_id": token_id,
        "hash": token_hash,
        "pepper_id": "v1",
        "project": "poolprj",
        "group_id": None,
        "doc_ref": None,
        "action_scope": "new",
        "issued_to": "tester",
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(hours=24)).isoformat(timespec="seconds"),
        "scratch_dir": None,
    })


def _hash_reads(mock_db) -> int:
    return sum(1 for sql in mock_db.reads if "FROM tokens WHERE hash" in sql)


class TestTokenCachePolicy:
    def test_disabled_by_default_under_testing(self):
        from modules.flow_gate.auth import auth_cache

        assert os.environ.get("TESTING") == "1"
        assert os.environ.get("FLOWGATE_TOKEN_CACHE_TTL") is None
        # Single-use tokens + a suite that rewrites them with raw SQL: off.
        assert auth_cache._token_ttl() == 0.0

    def test_env_override_enables(self, monkeypatch):
        from modules.flow_gate.auth import auth_cache

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        assert auth_cache._token_ttl() == 300.0
        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "0")
        assert auth_cache._token_ttl() == 0.0


class TestTokenLookupCache:
    def test_repeat_lookups_hit_the_db_once(self, monkeypatch, tmp_db):
        from modules.flow_gate.db import tokens as db_tokens

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        _issue_token("tok_cache_0001", "h-cache-0001")
        tmp_db.reads.clear()

        for _ in range(5):
            assert db_tokens.get_by_hash("h-cache-0001")["token_id"] == "tok_cache_0001"
        assert _hash_reads(tmp_db) == 1

    def test_disabled_ttl_reads_through_every_time(self, tmp_db):
        from modules.flow_gate.db import tokens as db_tokens

        _issue_token("tok_cache_0002", "h-cache-0002")
        tmp_db.reads.clear()

        for _ in range(3):
            db_tokens.get_by_hash("h-cache-0002")
        assert _hash_reads(tmp_db) == 3

    def test_consume_invalidates_immediately(self, monkeypatch, tmp_db):
        """The single-use guarantee must not be weakened by the cache."""
        from modules.flow_gate.db import tokens as db_tokens

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        _issue_token("tok_cache_0003", "h-cache-0003")

        assert db_tokens.get_by_hash("h-cache-0003")["consumed_at"] is None
        db_tokens.consume("tok_cache_0003")
        # Not TTL seconds later — now.
        assert db_tokens.get_by_hash("h-cache-0003")["consumed_at"] is not None

    def test_revoke_invalidates_immediately(self, monkeypatch):
        from modules.flow_gate.db import tokens as db_tokens

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        _issue_token("tok_cache_0004", "h-cache-0004")

        assert db_tokens.get_by_hash("h-cache-0004")["revoked_at"] is None
        db_tokens.revoke("tok_cache_0004")
        assert db_tokens.get_by_hash("h-cache-0004")["revoked_at"] is not None

    def test_issue_invalidates_a_cached_miss(self, monkeypatch):
        from modules.flow_gate.db import tokens as db_tokens

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        assert db_tokens.get_by_hash("h-cache-0005") is None
        _issue_token("tok_cache_0005", "h-cache-0005")
        # A cached None must not outlive the INSERT that fills it in.
        assert db_tokens.get_by_hash("h-cache-0005")["token_id"] == "tok_cache_0005"

    def test_copy_out_prevents_cache_poisoning(self, monkeypatch):
        """token_service.verify() rewrites scratch_dir on the record it returns."""
        from modules.flow_gate.db import tokens as db_tokens

        monkeypatch.setenv("FLOWGATE_TOKEN_CACHE_TTL", "300")
        _issue_token("tok_cache_0006", "h-cache-0006")

        row = db_tokens.get_by_hash("h-cache-0006")
        row["scratch_dir"] = "/resolved/by/caller"
        row["issued_to"] = "someone-else"
        again = db_tokens.get_by_hash("h-cache-0006")
        assert again["scratch_dir"] is None
        assert again["issued_to"] == "tester"


# ── 권고 3 / 발견 5b: GET /document's Q&A block is one query ─────────────────

class TestAnswersForDocumentBatching:
    def _seed_doc(self, doc_id: str, questions: list[tuple[str, list[str]]]) -> None:
        from modules.flow_gate.db import (
            answers as db_answers,
            question_items as db_question_items,
            questions as db_questions,
        )

        db_questions.insert_container_for_doc(
            doc_id=doc_id, project_id="poolprj", title=doc_id, created_by="tester"
        )
        container = db_questions.get_container_by_doc(doc_id)
        for seq, (body, answer_bodies) in enumerate(questions, start=1):
            db_question_items.insert(
                question_pk=container["id"], seq=seq, title=body, body=body,
                asker_kind="ai", options="[]",
            )
            item = db_question_items.list_by_question(container["id"])[seq - 1]
            for a_body in answer_bodies:
                db_answers.insert(question_item_id=item["id"], body=a_body)

    def test_matches_the_per_item_loop_and_runs_one_query(self, tmp_db):
        from modules.flow_gate.services.q_service import get_answers_for_document

        doc_id = "poolprj.default.0001.0001-B"
        self._seed_doc(doc_id, [
            ("Q one", ["first answer", "later answer"]),  # latest wins
            ("Q two", []),                                # unanswered → A is None
            ("Q three", ["only answer"]),
        ])
        tmp_db.reads.clear()

        assert get_answers_for_document(doc_id) == [
            {"Q": "Q one", "A": "later answer"},
            {"Q": "Q two", "A": None},
            {"Q": "Q three", "A": "only answer"},
        ]
        # Was 2 + N (container, items, then answers per item).
        assert len(tmp_db.reads) == 1

    def test_document_without_questions_is_empty(self, tmp_db):
        from modules.flow_gate.services.q_service import get_answers_for_document

        assert get_answers_for_document("poolprj.default.0001.0009-B") == []
        assert len(tmp_db.reads) == 1
