"""The AI answer worker must own the group lease its own run holds (0389 R0001).

R0001 reported that the AI cannot answer document queries. Measured on the live server:
runs aiv_20260805_000019..000023 (items 206/207 of ansm.default.0005) each started, wrote an
answer, and then had the POST rejected — `outcome='none'`, worker message
"GROUP_AI_RUN_OWNER_MISMATCH". Every one of those runs' tokens carries `ai_run_id = NULL`
while every continuous run in the same window carries its own run id, and no AI answer has
landed since 2026-08-02, the day group 0378's durable lease started gating group mutations.

The gate (mutation_policy.assert_group_mutation_allowed) admits a worker only when its token
matches the active lease on group / token_id / run_id / action_scope. dispatch_answer_run
minted its token through an issue_builder that did not declare `ai_run_id`, so
ai_invoke_service._call_issue_builder — which inspects the signature — called it bare and the
run's own worker was a stranger to the run's own lease.

These tests drive the REAL gate and the REAL middleware; nothing here monkeypatches the
decision function, only the lease row it reads.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service as q_answer  # noqa: E402

# The live failure, verbatim.
RUN_ID = "aiv_20260805_000019"
GROUP = "ansm.default.0005"
DOC_ID = "ansm.default.0005.0009-NR"
PROJECT = "ansm"
USER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"
ITEM_ID = 206
TOKEN_ID = "tok_answer"

DOC = {"doc_id": DOC_ID, "group_id": GROUP, "project_id": PROJECT, "title": "조사 레포트"}
ITEM = {"id": ITEM_ID, "seq": 1, "title": "소넷이 나을까 오퍼스가 나을까", "body": "질문 본문",
        "options": []}
ANSWER_PATH = f"/flowgate/api/v1/q/{DOC_ID}/items/{ITEM_ID}/answers"

# What start_run activates the lease with: its own run id and the token the builder minted.
LEASE = {
    "group_id": GROUP, "project_id": PROJECT, "run_id": RUN_ID, "chain_id": RUN_ID,
    "token_id": TOKEN_ID, "action_scope": "edit", "worker_identity": USER,
    "state": "active", "generation": 1, "expires_at": "2999-01-01T00:00:00+00:00",
}


def _token_row(ai_run_id):
    """The tokens row as token_service.issue persists it and inspect_for_replay returns it."""
    return {
        "token_id": TOKEN_ID, "group_id": GROUP, "doc_ref": DOC_ID,
        "action_scope": "edit", "issued_to": USER, "ai_run_id": ai_run_id,
    }


@pytest.fixture
def issued(monkeypatch):
    """Run dispatch_answer_run for real, capturing what token_service.issue was asked for."""
    recorded: dict = {}

    def _fake_issue(**kwargs):
        recorded.update(kwargs)
        return {"raw_token": "raw-answer-token", "token_id": TOKEN_ID,
                "expires_at": "2999-01-01T00:00:00+00:00", "scratch_dir": "/scratch/tok",
                "ai_run_id": kwargs.get("ai_run_id")}

    monkeypatch.setattr(q_answer.token_service, "issue", _fake_issue)
    monkeypatch.setattr(q_answer.db_groups, "get_by_id", lambda _gid: {"title": "그룹"})
    monkeypatch.setattr(q_answer, "_source_tool_block", lambda *_a, **_k: [])
    monkeypatch.setattr(q_answer, "_document_lookup_block", lambda *_a, **_k: [])
    monkeypatch.setattr(q_answer, "_ai_answer_count", lambda _item_id: 0)

    captured: dict = {}

    def _fake_start_run(**kwargs):
        captured.update(kwargs)
        # The engine hands the run identity to the builder exactly this way; call the real
        # helper so a builder that stops declaring the keyword fails this test.
        ai_invoke_service._call_issue_builder(kwargs["issue_builder"], RUN_ID)
        return {"run_id": RUN_ID, "status": "running", "provider": "p"}

    monkeypatch.setattr(q_answer.ai_invoke_service, "start_run", _fake_start_run)
    q_answer.dispatch_answer_run(
        doc=DOC, item=ITEM, issued_to=USER, api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
    )
    recorded["_start_run_kwargs"] = captured
    return recorded


def _answer_client(monkeypatch, principal):
    """The real middleware in front of the real answer route shape."""
    app = FastAPI()
    registered: list = []

    @app.post("/flowgate/api/v1/q/{doc_id}/items/{item_id}/answers")
    def register_answer(doc_id: str, item_id: int):
        registered.append((doc_id, item_id))
        return {"ok": True}

    monkeypatch.setattr(policy, "principal_from_request", lambda _request: principal)
    monkeypatch.setattr(policy.db_leases, "get_active",
                        lambda gid: dict(LEASE) if gid == GROUP else None)
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda *_args: True)
    app.add_middleware(policy.GroupMutationPolicyMiddleware)
    return TestClient(app), registered


def test_dispatch_mints_the_answer_token_under_the_runs_own_id(issued):
    assert issued["ai_run_id"] == RUN_ID
    # The lease is keyed on the scope start_run leased with, so this must not drift either.
    assert issued["action_scope"] == "edit"
    assert issued["group_id"] == GROUP
    assert issued["_start_run_kwargs"]["action_scope"] == "edit"


def test_the_engine_hands_the_run_id_to_this_builder(issued):
    """_call_issue_builder inspects the signature — a bare def() silently gets nothing."""
    builder = issued["_start_run_kwargs"]["issue_builder"]
    import inspect

    assert "ai_run_id" in inspect.signature(builder).parameters


def test_the_answer_post_now_passes_the_real_group_lease_gate(issued, monkeypatch):
    beats: list = []
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(LEASE))
    monkeypatch.setattr(policy.db_leases, "heartbeat",
                        lambda gid, rid: beats.append((gid, rid)) or True)
    principal = policy.worker_principal(_token_row(issued["ai_run_id"]))
    lease = policy.assert_group_mutation_allowed(GROUP, principal, f"POST {ANSWER_PATH}")
    assert lease["run_id"] == RUN_ID
    assert beats == [(GROUP, RUN_ID)]


def test_a_token_without_the_run_id_is_the_403_the_answers_died_on(monkeypatch):
    """The pre-fix state, asserted so the regression cannot come back unnoticed."""
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(LEASE))
    principal = policy.worker_principal(_token_row(None))
    with pytest.raises(policy.MutationPolicyError) as exc_info:
        policy.assert_group_mutation_allowed(GROUP, principal, f"POST {ANSWER_PATH}")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"


def test_answer_route_is_a_gated_group_mutation():
    resource, _reason = policy.classify_mutation_route(
        "/api/v1/q/{doc_id}/items/{item_id}/answers", {"POST"})
    assert resource == "group"


def test_worker_answer_reaches_the_route_end_to_end(issued, monkeypatch):
    """The user-visible half: the POST the worker actually makes now succeeds."""
    principal = policy.worker_principal(_token_row(issued["ai_run_id"]))
    client, registered = _answer_client(monkeypatch, principal)
    response = client.post(ANSWER_PATH, json={"body": "답변 본문", "selected_option_ids": []})
    assert response.status_code == 200
    assert registered == [(DOC_ID, ITEM_ID)]


def test_worker_answer_was_rejected_end_to_end_before_the_fix(monkeypatch):
    principal = policy.worker_principal(_token_row(None))
    client, registered = _answer_client(monkeypatch, principal)
    response = client.post(ANSWER_PATH, json={"body": "답변 본문", "selected_option_ids": []})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"
    assert registered == []


def test_copy_mention_path_keeps_minting_a_runless_token(monkeypatch):
    """[멘트 복사] starts no run, so it must not claim one — and needs no lease to match."""
    recorded: dict = {}

    def _fake_issue(**kwargs):
        recorded.update(kwargs)
        return {"raw_token": "raw", "token_id": TOKEN_ID, "expires_at": None,
                "scratch_dir": "/scratch/tok"}

    monkeypatch.setattr(q_answer.token_service, "issue", _fake_issue)
    monkeypatch.setattr(q_answer.db_groups, "get_by_id", lambda _gid: {"title": "그룹"})
    monkeypatch.setattr(q_answer, "_source_tool_block", lambda *_a, **_k: [])
    monkeypatch.setattr(q_answer, "_document_lookup_block", lambda *_a, **_k: [])
    issue = q_answer.issue_answer_token(
        doc=DOC, item=ITEM, issued_to=USER, api_base_url="http://127.0.0.1:8089/flowgate/api/v1")
    assert recorded["ai_run_id"] is None
    assert issue["mention"]


# ── The run id must survive the tokens table, not just the function call ──────────
# worker_principal reads `ai_run_id` off the row token_service.inspect_for_replay pulls
# back out of the DB. Hand-built rows above prove the wiring; this proves the column
# actually round-trips, which is the leg a dropped INSERT column would break silently.


class _MockDB:  # same harness as test_document_query_api_0370
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture
def real_token_store(tmp_path, monkeypatch):
    """A throwaway sqlite carrying the real migrations, swapped in as the STORE object.

    The STORE object is replaced rather than get_store(), so modules that already bound
    the function keep resolving to this one for the duration of the test.
    """
    from modules.flow_gate.db import connection as conn_mod

    mock_db = _MockDB(str(tmp_path / "flowgate.db"))
    for sql_file in sorted((_SERVER_DIR / "sql" / "migrations" / "sqlite").glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — some migrations re-add existing objects
            pass
    mock_db._conn.commit()

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    monkeypatch.setattr(conn_mod, "STORE", _PatchedStore())
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "t0389")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_t0389", "pepper-for-0389-tests")
    yield
    mock_db.close()


def test_the_run_id_round_trips_through_the_tokens_table(real_token_store, monkeypatch):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.services import token_service

    # tokens.project / group_id / issued_to are real foreign keys.
    db_projects.create({"project_id": PROJECT, "project_name": PROJECT})
    db_users.create({"user_id": USER, "username": "worker", "email": "w@example.com",
                     "password": "hashed"})
    db_groups.create({"group_id": GROUP, "project_id": PROJECT, "title": "질의응답"})
    issued = token_service.issue(
        project=PROJECT, group_id=GROUP, action_scope="edit", doc_ref=DOC_ID,
        issued_to=USER, ai_run_id=RUN_ID,
    )
    replayed = token_service.inspect_for_replay(issued["raw_token"])
    assert replayed["ai_run_id"] == RUN_ID

    lease = dict(LEASE, token_id=issued["token_id"])
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(lease))
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda *_args: True)
    granted = policy.assert_group_mutation_allowed(
        GROUP, policy.worker_principal(replayed), f"POST {ANSWER_PATH}")
    assert granted["run_id"] == RUN_ID
