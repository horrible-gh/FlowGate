"""[Request AI answer] actually dispatches a worker (group 0248 B0001 / NR0003 regression).

The defect: POST /q/{doc_id}/items/{item_id}/answers/ai-request minted an edit token,
returned it to the browser, and stopped. No worker was ever launched, so the button was a
silent no-op — 200 OK, no answer, no error, forever. Nothing caught it because no test
exercised the endpoint at all (NR0003 "테스트 공백").

What is defended here:
  1. the route calls ai_invoke_service.start_run exactly ONCE (the missing link itself),
  2. the response carries the run handle and NO secret (P0005 표기 규칙 — the old response
     leaked raw_token/scratch_dir to the browser),
  3. the token handed to the run stays edit-scoped and doc_ref-bound, so the receiving
     route (_resolve_writer) still admits it,
  4. the mention pins the worker to the ONE item it must answer, with the POST contract,
  5. admission failures (no provider / run in progress) surface instead of vanishing,
  6. the completion oracle judges by "an AI answer landed on THIS item" — the engine's
     document-reach oracle would score every such run 'none'.

Environment: TESTING=1 (temporary SQLite, no sqloader) — mirrors test_qa_route_auth.py.
"""
from __future__ import annotations

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
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
for _section, _entries in _json.loads(_QUERIES_JSON.read_text(encoding="utf-8")).items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


class _MockDB:
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

    def execute(self, sql: str, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetch_one(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]

    fetchone = fetch_one
    fetchall = fetch_all


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
    # Pre-existing sqlite migration-chain defect, NOT part of this fix (reported in
    # 0248 TR): there are two migration 063s, and 064_tokens_resolve_conflict_scope
    # rebuilds `tokens` with a column list that predates — and therefore drops —
    # continuation_instruction_mode from 063_tokens_continuation_instruction_mode.
    # Applying the chain to a fresh sqlite DB in filename order thus loses the column
    # and every token_service.issue() fails. (test_qa_route_auth.py fails this way on
    # main today.) Restore it here so these tests exercise the dispatch, not the chain.
    cols = {r[1] for r in mock_db._conn.execute("PRAGMA table_info(tokens)")}
    if "continuation_instruction_mode" not in cols:
        mock_db._conn.execute("ALTER TABLE tokens ADD COLUMN continuation_instruction_mode TEXT")
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original


PROJECT = "testprj"
GROUP = "testprj-__ALL__-0001"
DOC = "testprj-__ALL__-0001-D0001"
USER = "usr_test_001"


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": PROJECT, "project_name": "Test Project"})
    users.create({"user_id": USER, "username": "t", "email": "t@e", "password": "x"})
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    groups.create({"group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "G"})
    db_docs.create({
        "doc_id": DOC, "project_id": PROJECT, "type_code": "D", "seq": 1,
        "title": "설계 문서", "group_id": GROUP, "module": "__ALL__",
        "owner_id": USER, "status": "open",
    })
    yield


def _client():
    from starlette.testclient import TestClient
    from fastapi import FastAPI
    from modules.flow_gate.api.v1 import q_tapi_routes
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(q_tapi_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER, "is_admin": 1}
    return TestClient(app, raise_server_exceptions=True)


def _make_item(body: str = "이 스코프 맞나요?", options=None) -> int:
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(
        DOC, [{"title": "범위 확인", "body": body, "options": options or []}],
        asker_kind="human", created_by=USER, project_id=PROJECT,
    )
    return res["added_item_ids"][0]


@contextmanager
def _captured_start_run(result=None, raises=None):
    """Patch the engine so admission/dispatch is observable without launching a provider."""
    from modules.flow_gate.services import q_answer_invoke_service

    calls: list = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return result or {"ok": True, "run_id": "run-1", "status": "running",
                          "provider": {"id": "p1", "name": "prov"}}

    with patch.object(q_answer_invoke_service.ai_invoke_service, "start_run", side_effect=_fake):
        yield calls


# ── 1+2. The missing link: a run starts, and no secret comes back ─────────────────────

def test_ai_request_starts_a_run_and_returns_no_secret(seed_data):
    """The exact defect: the endpoint must LAUNCH something, not just mint a token."""
    item_id = _make_item()
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         _captured_start_run() as calls:
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    # The link that never existed.
    assert len(calls) == 1, "ai-request must start exactly one AI run"
    # The run handle replaces the token dump.
    assert body["run_id"] == "run-1"
    assert body["status"] == "running"
    assert body["item_id"] == item_id
    # P0005: the browser must never see the work token again.
    for leaked in ("raw_token", "token_id", "scratch_dir", "expires_at"):
        assert leaked not in body, f"{leaked} must not be returned to the browser"


def test_run_token_stays_edit_scoped_and_bound_to_the_document(seed_data, tmp_path):
    """The receiving route only admits an edit token whose doc_ref == the path doc_id."""
    item_id = _make_item()
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "scratch"), \
         _captured_start_run() as calls:
        _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})

    kwargs = calls[0]
    assert kwargs["action_scope"] == "edit"
    assert kwargs["doc_ref"] == DOC
    assert kwargs["group_id"] == GROUP
    assert kwargs["mode"] == "single"
    # A scoped oracle must be supplied, else the engine judges this run by documents.
    assert callable(kwargs["completion_oracle"])

    # The issue_builder is what actually mints the token — run it and check the grant.
    issued = kwargs["issue_builder"]()
    from modules.flow_gate.services import token_service
    rec = token_service.verify(issued["raw_token"])
    assert rec["action_scope"] == "edit"
    assert rec["doc_ref"] == DOC


# ── 4. The mention pins the worker to one item ────────────────────────────────────────

def test_mention_names_the_target_item_and_the_post_contract(seed_data, tmp_path):
    """A generic Q&A dump would not tell the worker WHICH item to answer (NR0003)."""
    item_id = _make_item(body="A안과 B안 중 무엇인가요?", options=["A안", "B안"])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "scratch"), \
         _captured_start_run() as calls:
        _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})

    issued = calls[0]["issue_builder"]()
    mention = issued["mention"]
    assert "A안과 B안 중 무엇인가요?" in mention           # the item body, verbatim
    assert f"/q/{DOC}/items/{item_id}/answers" in mention  # the exact POST target
    assert issued["raw_token"] in mention                  # usable credential
    # Server-assigned option ids must be echoable back in selected_option_ids (L0008 §2.3).
    from modules.flow_gate.services import q_answer_invoke_service
    item = q_answer_invoke_service.resolve_item(DOC, item_id)
    for opt in item["options"]:
        assert opt["id"] in mention


# ── 5. Admission failures surface ─────────────────────────────────────────────────────

def test_no_enabled_provider_surfaces_as_409(seed_data):
    """Silence was the bug. A run that cannot start must say so."""
    from fastapi import HTTPException
    item_id = _make_item()
    exc = HTTPException(status_code=409, detail={
        "code": "no_enabled_provider", "message": "No enabled AI provider for this project."})
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         _captured_start_run(raises=exc):
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})

    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "no_enabled_provider"       # UI branches on this
    assert body["error_message"]                        # ...and shows this


def test_run_in_progress_surfaces_as_409(seed_data):
    from fastapi import HTTPException
    item_id = _make_item()
    exc = HTTPException(status_code=409, detail={
        "code": "run_in_progress", "message": "An AI run is already in progress for this group."})
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         _captured_start_run(raises=exc):
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "run_in_progress"


def test_item_of_another_document_is_rejected(seed_data):
    """Item/doc binding must hold before any token is minted."""
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         _captured_start_run() as calls:
        resp = _client().post(f"/api/v1/q/{DOC}/items/999999/answers/ai-request", json={})
    assert resp.status_code == 404
    assert calls == [], "no run may start for an item that is not on this document"


def test_disposed_group_does_not_dispatch(seed_data):
    """TR0079.0003 rework: a discarded group must not get an AI worker writing into it."""
    item_id = _make_item()
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.process_service.is_group_disposed", return_value=True), \
         _captured_start_run() as calls:
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})
    assert resp.status_code == 409
    assert calls == []


# ── 7. [멘트 복사] — the hand-off that works without a provider ────────────────────────
# Rework: the panel offered NO hand-off at all, so whoever registered a query was left
# writing its answer themselves. This route is the copy half of the pair the legacy Q flow
# has always had, and the only one that survives a project with no AI provider configured.

def test_ai_mention_returns_a_usable_token_and_mention(seed_data, tmp_path):
    """The raw token IS the deliverable here — it has to reach the browser to be pasted."""
    item_id = _make_item(body="A안과 B안 중 무엇인가요?", options=["A안", "B안"])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "scratch"):
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-mention", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["item_id"] == item_id
    mention = body["mention"]
    assert "A안과 B안 중 무엇인가요?" in mention
    assert f"/q/{DOC}/items/{item_id}/answers" in mention
    # Pasting the mention must be enough on its own — the credential rides inside it.
    assert body["raw_token"] and body["raw_token"] in mention

    # And the grant is the one the receiving route admits.
    from modules.flow_gate.services import token_service
    rec = token_service.verify(body["raw_token"])
    assert rec["action_scope"] == "edit"
    assert rec["doc_ref"] == DOC


def test_ai_mention_needs_no_provider(seed_data, tmp_path):
    """The whole point of the copy path: no provider chain is consulted, so a project with
    none configured can still get an AI answer. [AI에게 답변 요청] 409s in that same state."""
    item_id = _make_item()
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "scratch"), \
         _captured_start_run() as calls:
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-mention", json={})
    assert resp.status_code == 200
    assert calls == [], "copying a mention must not start a run"


def test_ai_mention_and_ai_request_render_the_same_prompt(seed_data, tmp_path):
    """One builder feeds both paths. If they drift, a user debugging a copied mention is
    reading a different prompt than the one the in-app run actually sent."""
    item_id = _make_item(body="스코프 확인 부탁드립니다", options=["넓힘", "유지"])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "scratch"), \
         _captured_start_run() as calls:
        resp = _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-mention", json={})
        _client().post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})

    copied = resp.json()["mention"]
    run_issued = calls[0]["issue_builder"]()
    # The tokens differ (each issue mints one); everything else must be identical.
    normalize = lambda text, tok, scratch: text.replace(tok, "<TOKEN>").replace(scratch, "<SCRATCH>")
    assert normalize(copied, resp.json()["raw_token"], resp.json()["scratch_dir"]) == \
        normalize(run_issued["mention"], run_issued["raw_token"], run_issued["scratch_dir"])


def test_ai_mention_enforces_the_same_guards_as_the_run(seed_data):
    """A mention carries a live write credential, so its guards cannot be laxer than the
    run's: wrong document, disposed group, and missing permission all block BEFORE issue."""
    item_id = _make_item()
    # (a) item that is not on this document
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        assert _client().post(
            f"/api/v1/q/{DOC}/items/999999/answers/ai-mention", json={}).status_code == 404
    # (b) disposed group
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.process_service.is_group_disposed", return_value=True):
        assert _client().post(
            f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-mention", json={}).status_code == 409
    # (c) no permission
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=False):
        assert _client().post(
            f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-mention", json={}).status_code == 403


# ── 6. The completion oracle ──────────────────────────────────────────────────────────

def test_oracle_is_satisfied_only_by_a_new_ai_answer_on_this_item(seed_data):
    """docs-reached would be 0 for a perfect run; this is what judges it instead."""
    from modules.flow_gate.services import q_answer_invoke_service, q_service

    item_id = _make_item()
    oracle = q_answer_invoke_service._make_oracle(item_id, q_answer_invoke_service._ai_answer_count(item_id))
    assert oracle() is False, "nothing answered yet"

    # A human answering mid-run is not the AI doing its job.
    q_service.register_answer(DOC, item_id, body="사람이 먼저 답함",
                              author_kind="human", author_id=USER)
    assert oracle() is False

    q_service.register_answer(DOC, item_id, body="AI 답변", author_kind="ai")
    assert oracle() is True


# ── 7. End to end: button → run → worker POST → answer on the item ───────────────────

def test_end_to_end_dispatch_lands_an_ai_answer_on_the_item(seed_data, tmp_path):
    """The whole chain the defect broke, with a fake provider standing in for the worker.

    Nothing connected these halves before: the request minted a token nobody used, so an
    answer could never arrive. Here the 'worker' does what the mention tells it to — POST
    the answer with the handed token — and the run must settle 'complete' off the scoped
    oracle (docs_target is 0; the document oracle would have said 'none').
    """
    import re
    import time as _time
    from modules.flow_gate.services import ai_invoke_service

    item_id = _make_item(body="이 방식으로 갈까요?")
    client = _client()
    posted: dict = {}

    def _fake_worker(provider, prompt, run):
        """Stand-in for the AI: read the mention, then answer exactly as instructed."""
        token = re.search(r"Authorization: Bearer (\S+)", prompt).group(1)
        url = re.search(r"POST \S+(/api/v1/q/\S+/answers)", prompt).group(1)
        posted["resp"] = client.post(url, json={"body": "AI가 작성한 답변"},
                                     headers={"Authorization": f"Bearer {token}"})
        return "started_ok", None

    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "tok"), \
         patch.object(ai_invoke_service, "ORACLE_SETTLE_SEC", 0), \
         patch.object(ai_invoke_service, "_create_scratch", return_value=tmp_path / "run"), \
         patch.object(ai_invoke_service, "_cleanup_retained_scratches"), \
         patch.object(ai_invoke_service.storage_paths, "resolve_project_src_root",
                      return_value=None), \
         patch.object(ai_invoke_service.ai_settings_service, "resolve_effective",
                      return_value={"source": "test", "providers": [
                          {"id": "p1", "name": "fake", "exec_type": "cli"}]}), \
         patch.object(ai_invoke_service, "_cli_execute", side_effect=_fake_worker):
        resp = client.post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        for _ in range(200):  # the run is a background thread
            record = ai_invoke_service.get_run_record(run_id)
            if record and record["status"] == "finished":
                break
            _time.sleep(0.02)

    # The worker's POST was accepted by the receiving route with the dispatched token.
    assert posted["resp"].status_code == 200, posted["resp"].text

    record = ai_invoke_service.get_run_record(run_id)
    assert record["status"] == "finished"
    assert record["docs_target"] == 0, "an answer run creates no document"
    assert record["outcome"] == "complete", "the scoped oracle must see the answer"
    assert record["oracle_mismatch"] is False

    # The answer landed on the right item, attributed to the AI (D0005 §3.2).
    from modules.flow_gate.services import q_service
    item = next(i for i in q_service.get_qa_detail(DOC)["items"] if i["id"] == item_id)
    assert item["answer_count"] == 1
    ai_answers = [a for a in item["answers"] if a["author_kind"] == "ai"]
    assert len(ai_answers) == 1
    assert ai_answers[0]["body"] == "AI가 작성한 답변"
    assert ai_answers[0]["author_id"] is None


def test_end_to_end_worker_that_never_answers_settles_as_none(seed_data, tmp_path):
    """A run that produces nothing must not be reported as success."""
    import time as _time
    from modules.flow_gate.services import ai_invoke_service

    item_id = _make_item()
    client = _client()

    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True), \
         patch("modules.flow_gate.services.token_service._scratch_dir",
               return_value=tmp_path / "tok2"), \
         patch.object(ai_invoke_service, "ORACLE_SETTLE_SEC", 0), \
         patch.object(ai_invoke_service, "_create_scratch", return_value=tmp_path / "run2"), \
         patch.object(ai_invoke_service, "_cleanup_retained_scratches"), \
         patch.object(ai_invoke_service.storage_paths, "resolve_project_src_root",
                      return_value=None), \
         patch.object(ai_invoke_service.ai_settings_service, "resolve_effective",
                      return_value={"source": "test", "providers": [
                          {"id": "p1", "name": "fake", "exec_type": "cli"}]}), \
         patch.object(ai_invoke_service, "_cli_execute",
                      side_effect=lambda p, pr, r: ("started_ok", None)):
        resp = client.post(f"/api/v1/q/{DOC}/items/{item_id}/answers/ai-request", json={})
        run_id = resp.json()["run_id"]
        for _ in range(200):
            record = ai_invoke_service.get_run_record(run_id)
            if record and record["status"] == "finished":
                break
            _time.sleep(0.02)

    record = ai_invoke_service.get_run_record(run_id)
    assert record["outcome"] == "none"
    # Exited cleanly yet answered nothing — the signal operators triage on.
    assert record["oracle_mismatch"] is True


def test_oracle_requires_a_fresh_answer_when_the_item_already_has_one(seed_data):
    """Re-requesting on an already-AI-answered item must not auto-complete."""
    from modules.flow_gate.services import q_answer_invoke_service, q_service

    item_id = _make_item()
    q_service.register_answer(DOC, item_id, body="이전 AI 답변", author_kind="ai")

    baseline = q_answer_invoke_service._ai_answer_count(item_id)
    oracle = q_answer_invoke_service._make_oracle(item_id, baseline)
    assert oracle() is False, "the pre-existing answer must not satisfy the new run"

    q_service.register_answer(DOC, item_id, body="새 AI 답변", author_kind="ai")
    assert oracle() is True
