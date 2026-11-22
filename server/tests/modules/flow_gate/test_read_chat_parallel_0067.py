"""Read-chat admission/ownership with real SQLite persistence and concurrency."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from modules.flow_gate.db import chat_ai_leases as chats, group_ai_leases as groups
from modules.flow_gate.services.ai_invoke import terminal
from tests.integration.ai_run_identity.test_ai_run_identity_0138 import identity_db
from tests.regression.graph_chat.test_graph_chat_source_lock_0061 import issuing
from tests.regression.graph_chat import test_graph_chat_source_lock_0061 as legacy_lock


def test_read_and_edit_token_rules_with_valid_document_binding(issuing, monkeypatch):
    from modules.flow_gate.db import documents
    from modules.flow_gate.services import token_service
    from flowgate.graph_run import editing
    monkeypatch.setattr(documents, "get_by_id", lambda doc: dict(
        type="CH", project_id=legacy_lock.PROJECT, group_id=legacy_lock.FLOW))
    legacy_lock.test_a_read_chat_of_a_locked_flow_is_issued(issuing, monkeypatch)


@pytest.mark.parametrize("resolved", [("read_write", False), (None, True)])
def test_edit_token_still_refused_with_valid_document_binding(issuing, monkeypatch, resolved):
    from modules.flow_gate.db import documents
    monkeypatch.setattr(documents, "get_by_id", lambda doc: dict(
        type="CH", project_id=legacy_lock.PROJECT, group_id=legacy_lock.FLOW))
    legacy_lock.test_an_edit_chat_of_a_locked_flow_is_refused_before_anything_is_written(
        issuing, monkeypatch, resolved)


@pytest.mark.parametrize("resolved", [("read_write", False), (None, True), ("execute", False)])
def test_read_chat_retry_cannot_issue_a_more_powerful_token(issuing, monkeypatch, resolved):
    from modules.flow_gate.db import documents
    from modules.flow_gate.services import token_service
    monkeypatch.setattr(documents, "get_by_id", lambda doc: dict(
        type="CH", project_id=legacy_lock.PROJECT, group_id=legacy_lock.FLOW))
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", lambda *a: resolved)
    monkeypatch.setattr(chats, "get_by_run_id", lambda rid: {"run_id": rid})
    with pytest.raises(HTTPException) as refused:
        token_service.issue(legacy_lock.PROJECT, legacy_lock.FLOW, "chat",
                            legacy_lock.FLOW + ".0002-CH", "user", ai_run_id="read-run")
    assert refused.value.detail["code"] == "chat_mode_changed"
    assert issuing[1] == []


class SqlStore:
    def __init__(self, path):
        self.path = path
        self.local = threading.local()

    def connection(self):
        if not hasattr(self.local, "connection"):
            con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            con.row_factory = sqlite3.Row
            self.local.connection = con
        return self.local.connection

    @contextmanager
    def transaction(self):
        con = self.connection()
        con.execute("BEGIN IMMEDIATE")
        try:
            yield
            con.commit()
        except BaseException:
            con.rollback()
            raise

    def _execute(self, sql, args=()):
        return self.connection().execute(sql, args)

    def _fetch_one(self, sql, args=()):
        row = self._execute(sql, args).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, args=()):
        return [dict(r) for r in self._execute(sql, args).fetchall()]


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SqlStore(str(tmp_path / "chats.sqlite"))
    migration = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite/209_chat_ai_leases.sql"
    store.connection().executescript(migration.read_text())
    monkeypatch.setattr(chats, "get_store", lambda: store)
    monkeypatch.setattr(chats, "_memory_mode", lambda: False)
    groups._memory.clear()
    yield store
    groups._memory.clear()


def acquire(doc="chat-a", run="chat-run"):
    return chats.acquire(doc_id=doc, group_id="group", project_id="project", run_id=run,
                         chain_id=run, action_scope="chat", worker_identity="user")


def card(run="card-run"):
    return groups.acquire(group_id="group", project_id="project", run_id=run,
                          chain_id=run, action_scope="new", worker_identity="user")


def test_card_then_read_chat_and_reverse_do_not_share_slots(store):
    assert card()["run_id"] == "card-run"
    assert acquire()["run_id"] == "chat-run"
    assert groups.get_active("group")["run_id"] == "card-run"
    groups.release("group", "card-run")
    assert card("next-card")["run_id"] == "next-card"
    assert chats.get_active("chat-a")["run_id"] == "chat-run"


def test_same_chat_atomic_race_and_different_chats(store):
    barrier = threading.Barrier(2)
    def start(n):
        barrier.wait()
        return acquire(run="run-" + str(n))["run_id"]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(start, [1, 2]))
    assert results[0] == results[1]
    assert len(store._fetch_all("SELECT * FROM chat_ai_leases")) == 1
    assert acquire("chat-b", "run-b")["run_id"] == "run-b"


def test_reload_other_connection_retains_same_conversation_owner(store, monkeypatch):
    acquire()
    other = SqlStore(store.path)
    monkeypatch.setattr(chats, "get_store", lambda: other)
    assert acquire(run="after-reload")["run_id"] == "chat-run"


def test_lifecycle_routes_by_run_and_stop_is_independent(store, monkeypatch):
    card()
    acquire()
    assert groups.activate("group", "chat-run", "chat-token", "chat", "user", 100)
    assert groups.get_by_token_id("chat-token")["doc_id"] == "chat-a"
    assert groups.heartbeat("group", "chat-run")
    groups.update_token("group", "chat-run", "new-token")
    assert chats.get_by_run_id("chat-run")["token_id"] == "new-token"
    terminal.release_owned_lease({"run_id": "chat-run", "group_id": "group", "lease_scope": "conversation"}, "cancelled")
    assert groups.get_active("group")["run_id"] == "card-run"
    assert chats.get_active("chat-a") is None
    acquire(run="chat-next")
    groups.release("group", "card-run", "flow_cancelled")
    assert chats.get_active("chat-a")["run_id"] == "chat-next"
    assert not groups.release("group", "chat-run")


def test_active_unlimited_chat_does_not_expire_into_duplicate(store):
    acquire()
    chats.activate("group", "chat-run", "token", "chat", "user", 1)
    store._execute("UPDATE chat_ai_leases SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert acquire(run="duplicate")["run_id"] == "chat-run"


def test_graph_stop_only_cancels_its_attempt_runs(monkeypatch):
    from flowgate.graph_run import stop
    from modules.flow_gate.services import ai_invoke_service as svc
    called = []
    monkeypatch.setattr(stop.run_store, "_all", lambda sql, args: [{"ai_run_id": "card-run"}])
    monkeypatch.setattr(svc, "cancel_run", lambda rid: called.append(rid))
    stop._kill("graph-run", ["card-launcher"])
    assert called == ["card-run", "card-launcher"]


def test_chat_cancel_only_sets_its_own_cancel_event(monkeypatch):
    from modules.flow_gate.services.ai_invoke import chain
    from modules.flow_gate.services import tr_self_check_progress
    runs = {rid: dict(run_id=rid, action_scope=scope, status="running", cancel_event=threading.Event())
            for rid, scope in [("card", "new"), ("chat", "chat")]}
    monkeypatch.setattr(chain._svc(), "get_run_record", lambda rid: runs[rid])
    def chat_stop(run, announce):
        announce()
        return True
    monkeypatch.setattr(chain.chat_run_hooks, "chat_run_cancel", chat_stop)
    monkeypatch.setattr(tr_self_check_progress, "end_owned", lambda *a, **k: None)
    assert chain.cancel_run("chat")["status"] == "cancelling"
    assert runs["chat"]["cancel_event"].is_set()
    assert not runs["card"]["cancel_event"].is_set()
    assert runs["card"]["status"] == "running"


def test_restart_preserves_remote_job_owner_and_reclaims_local_orphan(store):
    acquire(run="remote")
    acquire("chat-b", "local")
    victims = chats.reclaim_orphaned("9999", keep_run_ids={"remote"})
    assert [v["run_id"] for v in victims] == ["local"]
    assert acquire(run="duplicate")["run_id"] == "remote"


def test_observation_survives_release_and_is_bound_to_conversation(store, monkeypatch):
    acquire()
    chats.observe("chat-run", "chat-a", "group", True)
    chats.observe("chat-run", "chat-a", "group", False)
    chats.release("group", "chat-run")
    assert chats.observation("chat-run", "chat-a")
    assert not chats.observation("chat-run", "chat-b")
    from modules.flow_gate.services import conversation_turn_service as turns
    monkeypatch.setattr(turns.message_audio_service, "for_turn", lambda r: None)
    row = dict(doc_id="chat-a", seq=1, speaker="ai", participant_key="ai:test", body="answer",
               source_run_id="chat-run", created_at="now")
    assert turns.turn_wire(row, images=[])["observed_during_card_run"] is True
    row["speaker"] = "user"
    assert turns.turn_wire(row, images=[])["observed_during_card_run"] is False


@pytest.fixture
def admission_setup(monkeypatch, identity_db):
    from tests.modules.flow_gate.test_ai_invoke_pre_run_start_rollback_0550 import _real_admission_stub
    real_acquire, real_release, real_active = groups.acquire, groups.release, groups.get_active
    admission, _, _, _, revokes, kwargs = _real_admission_stub(monkeypatch)
    monkeypatch.setattr(groups, "acquire", real_acquire)
    monkeypatch.setattr(groups, "release", real_release)
    monkeypatch.setattr(groups, "get_active", real_active)
    monkeypatch.setattr(admission.token_service, "_resolve_chat_source_access", lambda *a: ("read", False))
    monkeypatch.setattr(admission.carryover, "carry_into_new_run", lambda **k: (k["mention"], None))
    calls = []
    def hook(action, *args, **kw):
        calls.append(action)
        if action == "legacy_acquire":
            return args[0](**kw)
        if action == "chat_read_observation":
            return True
    monkeypatch.setattr(admission.graph_execution_hooks, "call", hook)
    def admitted(_):
        raise RuntimeError("admitted-before-provider")
    monkeypatch.setattr(admission._svc(), "request_run_scratch_sweep", admitted)
    kwargs.update(action_scope="chat", group_id="group", doc_ref="chat-a", completion_oracle=lambda: True,
                  issue_builder=lambda **k: dict(token_id="token", raw_token="raw", scratch_dir="scratch",
                                                source_access="read", command_access=None, mention="mention"))
    return admission, kwargs, calls, revokes


def test_real_admission_bypasses_graph_and_group_slots_but_serializes_chat(store, admission_setup):
    admission, kwargs, calls, _ = admission_setup
    card()
    with pytest.raises(RuntimeError, match="admitted-before-provider"):
        admission.start_run(**kwargs)
    row = chats.get_active("chat-a")
    assert row["state"] == "active"
    assert chats.observation(row["run_id"], "chat-a")
    assert "legacy_acquire" not in calls
    assert groups.get_active("group")["run_id"] == "card-run"
    with pytest.raises(HTTPException) as caught:
        admission.start_run(**kwargs)
    assert caught.value.status_code == 409
    assert caught.value.detail["lease_scope"] == "conversation"
    assert caught.value.detail["run_id"] == row["run_id"]


@pytest.mark.parametrize("mode", [("read_write", False), ("read", True), ("execute", False)])
def test_mutating_modes_keep_graph_refusal(store, admission_setup, monkeypatch, mode):
    admission, kwargs, _, _ = admission_setup
    monkeypatch.setattr(admission.token_service, "_resolve_chat_source_access", lambda *a: mode)
    def refuse(action, *a, **k):
        if action == "chat_source_refusal":
            raise HTTPException(409, detail={"code": "graph_run_locked"})
    monkeypatch.setattr(admission.graph_execution_hooks, "call", refuse)
    with pytest.raises(HTTPException) as caught:
        admission.start_run(**kwargs)
    assert caught.value.detail["code"] == "graph_run_locked"
    assert chats.get_active("chat-a") is None


@pytest.mark.parametrize("access,command", [("read_write", None), ("read", "execute")])
def test_changed_mode_token_is_revoked_without_launch(store, admission_setup, access, command):
    admission, kwargs, _, revoked = admission_setup
    kwargs["issue_builder"] = lambda **k: dict(token_id="unsafe", source_access=access,
                                                command_access=command, mention="mention")
    with pytest.raises(HTTPException) as caught:
        admission.start_run(**kwargs)
    assert caught.value.detail["code"] == "chat_mode_changed"
    assert chats.get_active("chat-a") is None
    assert any(t == "unsafe" for t, _ in revoked)
