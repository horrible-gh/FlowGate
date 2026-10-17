"""flowgate.v02.0064 T-B5 — who answered a Q&A question: the recorded run evidence and the read.

Covers (L0005 2.18 / 6, P0006 §5.1·§5.3 / V-P-15, DB0007 §2.6):
  V-B5-1  no in-memory run record + a persisted ai_invoke_runs row -> the provider id/name are
          stored, requested_provider_id / fallback_used stay null.
  V-B5-2  neither record has a provider -> only ai_run_id is stored (and a failing DB lookup
          degrades the same way, never guessed).
  V-B5-3  the token carries no run -> every field null; neither record is consulted.
  P §5.1  every answer of GET /q/{doc_id} carries author_name: users.username for a person
          (one batched lookup for the whole list), null for an AI, a removed user or a failed
          lookup.
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

_QUERIES: dict = {}
_raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
for _section, _entries in _raw.items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")

_RUNTIME_GET = "modules.flow_gate.services.ai_invoke.runtime.get_run_record"
_DB_GET = "modules.flow_gate.db.ai_invoke_runs.get"

PERSISTED_ROW = {
    "run_id": "run_20261016_0040",
    "provider_id": "codex",
    "provider_name": "Codex",
    "selected_provider_source": "project_chain",
    "attempt_no": 1,
}


def _routes():
    from modules.flow_gate.api.v1 import q_tapi_routes
    return q_tapi_routes


# ── Part A: the persisted-run fallback, pure logic ─────────────────────────────────

def test_persisted_row_gives_actual_provider_and_null_requested_half():
    from modules.flow_gate.services.ai_invoke import provenance as prov

    with patch(_DB_GET, return_value=dict(PERSISTED_ROW)):
        snap = prov.resolve_persisted_run_provenance("run_20261016_0040")
    assert snap == {
        "ai_run_id": "run_20261016_0040",
        "requested_provider_id": None,
        "actual_provider_id": "codex",
        "actual_provider_name": "Codex",
        "provider_source": "project_chain",
        "attempt_no": 1,
        "fallback_used": None,
    }


@pytest.mark.parametrize("row", [None, {**PERSISTED_ROW, "provider_id": None}, {**PERSISTED_ROW, "provider_id": ""}])
def test_persisted_row_without_provider_is_no_evidence(row):
    from modules.flow_gate.services.ai_invoke import provenance as prov

    with patch(_DB_GET, return_value=row):
        assert prov.resolve_persisted_run_provenance("run_x") == {}


def test_persisted_lookup_failure_is_no_evidence():
    from modules.flow_gate.services.ai_invoke import provenance as prov

    with patch(_DB_GET, side_effect=RuntimeError("db down")):
        assert prov.resolve_persisted_run_provenance("run_x") == {}
    assert prov.resolve_persisted_run_provenance(None) == {}


def test_persisted_row_empty_name_and_attempt_stay_null():
    from modules.flow_gate.services.ai_invoke import provenance as prov

    row = {**PERSISTED_ROW, "provider_name": "", "selected_provider_source": None, "attempt_no": 0}
    with patch(_DB_GET, return_value=row):
        snap = prov.resolve_persisted_run_provenance("run_x")
    assert snap["actual_provider_id"] == "codex"
    assert snap["actual_provider_name"] is None
    assert snap["provider_source"] is None
    assert snap["attempt_no"] is None


# ── Part B: q_tapi_routes._resolve_ai_provenance order (memory -> DB -> run id only) ──

def test_v_b5_1_memory_missing_persisted_row_fills_the_provider():
    with patch(_RUNTIME_GET, return_value=None), patch(_DB_GET, return_value=dict(PERSISTED_ROW)) as db_get:
        snap = _routes()._resolve_ai_provenance("run_20261016_0040")
    db_get.assert_called_once_with("run_20261016_0040")
    assert snap["ai_run_id"] == "run_20261016_0040"
    assert snap["actual_provider_id"] == "codex"
    assert snap["actual_provider_name"] == "Codex"
    assert snap["requested_provider_id"] is None
    assert snap["fallback_used"] is None


def test_memory_record_wins_and_the_db_is_not_read():
    run = {
        "action_scope": "edit", "requested_provider_id": "p_req", "provider_id": "p_act",
        "provider": {"name": "Live Name"}, "selected_provider_source": "request", "attempt_no": 2,
    }
    with patch(_RUNTIME_GET, return_value=run), patch(_DB_GET) as db_get:
        snap = _routes()._resolve_ai_provenance("run_live")
    db_get.assert_not_called()
    assert snap["actual_provider_name"] == "Live Name"
    assert snap["requested_provider_id"] == "p_req"
    assert snap["fallback_used"] is True


def test_memory_record_without_provider_ids_still_reads_the_db():
    """L0005 2.18: a memory record that yields {} (provider id missing) does not stop the lookup."""
    run = {"action_scope": "edit", "requested_provider_id": None, "provider_id": None}
    with patch(_RUNTIME_GET, return_value=run), patch(_DB_GET, return_value=dict(PERSISTED_ROW)):
        snap = _routes()._resolve_ai_provenance("run_20261016_0040")
    assert snap["actual_provider_id"] == "codex"


@pytest.mark.parametrize("db_side", [
    {"return_value": None},
    {"return_value": {**PERSISTED_ROW, "provider_id": None}},
    {"side_effect": RuntimeError("db down")},
])
def test_v_b5_2_neither_record_has_a_provider_keeps_only_the_run_id(db_side):
    with patch(_RUNTIME_GET, return_value=None), patch(_DB_GET, **db_side):
        assert _routes()._resolve_ai_provenance("run_20261016_0041") == {"ai_run_id": "run_20261016_0041"}


@pytest.mark.parametrize("run_id", [None, ""])
def test_v_b5_3_no_run_on_the_token_is_all_null(run_id):
    with patch(_RUNTIME_GET) as mem_get, patch(_DB_GET) as db_get:
        assert _routes()._resolve_ai_provenance(run_id) == {}
    mem_get.assert_not_called()
    db_get.assert_not_called()


# ── Part C: on a real SQLite DB (same harness as test_0582_ai_run_provenance.py) ─────

class _MockDB:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]


DOC = "p064.none.0001.0001-D"


@pytest.fixture()
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mock_db = _MockDB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()

    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()

    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    projects.create({"project_id": "p064", "project_name": "P064"})
    users.create({"user_id": "u1", "username": "owner", "email": "u1@e", "password": "x"})
    users.create({"user_id": "u_hong", "username": "홍길동", "email": "hong@e", "password": "x"})
    groups.create({"group_id": "p064.none.0001", "project_id": "p064", "module": "none", "title": "G"})
    md = Path(path).with_suffix(".doc.md")
    md.write_text("# d", encoding="utf-8")
    now = "2026-10-16T00:00:00Z"
    conn_mod.STORE._execute(
        "INSERT OR IGNORE INTO document_types (project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": DOC, "project_id": "p064", "type_code": "D", "seq": 1,
        "title": "Doc", "group_id": "p064.none.0001", "module": "none",
        "owner_id": "u1", "file_path": str(md), "status": "open",
    })
    yield mock_db
    conn_mod.STORE = original
    mock_db.close()
    os.unlink(path)
    try:
        md.unlink()
    except OSError:
        pass


def _question(text: str = "로그인 방식?") -> int:
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "로그인 방식", "body": text}], asker_kind="human", created_by="u1")
    return res["added_item_ids"][0]


def _answers(item_id: int) -> list[dict]:
    from modules.flow_gate.services import q_service
    detail = q_service.get_qa_detail(DOC)
    return next(it for it in detail["items"] if it["id"] == item_id)["answers"]


def test_v_p_15_persisted_run_answer_stores_and_reads_the_provider_name(store):
    """P0006 §5.3: a new AI answer whose run is only in ai_invoke_runs is stored with its provider."""
    from modules.flow_gate.services import q_service

    item_id = _question()
    with patch(_RUNTIME_GET, return_value=None), patch(_DB_GET, return_value=dict(PERSISTED_ROW)):
        provenance = _routes()._resolve_ai_provenance("run_20261016_0040")
    q_service.register_answer(DOC, item_id, "문서에 따르면 이메일 링크 방식입니다.", author_kind="ai", author_provenance=provenance)

    answer = _answers(item_id)[0]
    assert answer["author_provider"] == {
        "ai_run_id": "run_20261016_0040", "ai_provider_id": "codex", "ai_provider_name": "Codex",
    }
    assert answer["author_provenance"] == {
        "requested_provider_id": None, "provider_source": "project_chain", "fallback_used": None,
    }
    assert answer["author_name"] is None


def test_v_b5_2_run_only_answer_reads_run_id_without_provider(store):
    from modules.flow_gate.services import q_service

    item_id = _question()
    with patch(_RUNTIME_GET, return_value=None), patch(_DB_GET, return_value=None):
        provenance = _routes()._resolve_ai_provenance("run_20261016_0041")
    q_service.register_answer(DOC, item_id, "근거를 확인하지 못했습니다.", author_kind="ai", author_provenance=provenance)

    answer = _answers(item_id)[0]
    assert answer["author_provider"] == {"ai_run_id": "run_20261016_0041", "ai_provider_id": None, "ai_provider_name": None}
    assert answer["author_provenance"] is None


def test_author_name_is_the_username_for_a_person_and_null_otherwise(store):
    """P0006 §5.1: the four answer kinds of the example, each with its author_name."""
    from modules.flow_gate.services import q_service

    item_id = _question()
    q_service.register_answer(DOC, item_id, "이메일 링크 방식입니다.", author_kind="human", author_id="u_hong")
    q_service.register_answer(DOC, item_id, "AI 답변", author_kind="ai", author_provenance={
        "ai_run_id": "run_a", "actual_provider_id": "codex", "actual_provider_name": "Codex",
    })
    q_service.register_answer(DOC, item_id, "실행만", author_kind="ai", author_provenance={"ai_run_id": "run_b"})
    q_service.register_answer(DOC, item_id, "외부", author_kind="ai", author_provenance={})

    names = [(a["author_kind"], a["author_name"]) for a in _answers(item_id)]
    assert names == [("human", "홍길동"), ("ai", None), ("ai", None), ("ai", None)]


def test_author_name_is_null_for_a_removed_user(store):
    from modules.flow_gate.services import q_service

    item_id = _question()
    q_service.register_answer(DOC, item_id, "답", author_kind="human", author_id="u_hong")
    store._conn.execute("PRAGMA foreign_keys = OFF")
    store._conn.execute("DELETE FROM users WHERE user_id = ?", ["u_hong"])
    store._conn.commit()
    store._conn.execute("PRAGMA foreign_keys = ON")

    answer = _answers(item_id)[0]
    assert answer["author_id"] == "u_hong"
    assert answer["author_name"] is None


def test_author_names_are_looked_up_once_for_the_whole_list(store):
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.services import q_service

    first = _question("첫 질문")
    second = _question("둘째 질문")
    q_service.register_answer(DOC, first, "a1", author_kind="human", author_id="u_hong")
    q_service.register_answer(DOC, second, "a2", author_kind="human", author_id="u1")

    with patch.object(db_users, "usernames_by_ids", wraps=db_users.usernames_by_ids) as lookup:
        detail = q_service.get_qa_detail(DOC)
    assert lookup.call_count == 1
    assert sorted(lookup.call_args.args[0]) == ["u1", "u_hong"]
    by_body = {a["body"]: a["author_name"] for it in detail["items"] for a in it["answers"]}
    assert by_body == {"a1": "홍길동", "a2": "owner"}


def test_no_person_answer_skips_the_lookup(store):
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.services import q_service

    item_id = _question()
    q_service.register_answer(DOC, item_id, "외부", author_kind="ai", author_provenance={})
    with patch.object(db_users, "usernames_by_ids") as lookup:
        assert _answers(item_id)[0]["author_name"] is None
    lookup.assert_not_called()


def test_a_failed_name_lookup_does_not_fail_the_read(store):
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.services import q_service

    item_id = _question()
    q_service.register_answer(DOC, item_id, "답", author_kind="human", author_id="u_hong")
    with patch.object(db_users, "usernames_by_ids", side_effect=RuntimeError("db down")):
        answer = _answers(item_id)[0]
    assert answer["author_kind"] == "human"
    assert answer["author_name"] is None


def test_usernames_by_ids_ignores_blanks_and_unknown_ids(store):
    from modules.flow_gate.db import users as db_users

    assert db_users.usernames_by_ids([]) == {}
    assert db_users.usernames_by_ids(["", None, "nobody"]) == {}  # type: ignore[list-item]
    assert db_users.usernames_by_ids(["u_hong", "u_hong", "u1"]) == {"u_hong": "홍길동", "u1": "owner"}
