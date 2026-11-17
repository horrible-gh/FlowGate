"""Chat 0043: persisted settings, author CAS, tombstones and per-run clocks."""
import importlib
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import chat_settings_service as css
from modules.flow_gate.services import chat_doc_settings_service as cds
from modules.flow_gate.services import conversation_turn_service as turns
from modules.flow_gate.services.ai_invoke import runtime, worker, provider_remote, provider_cli


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
    def _execute(self, sql, params=None):
        return self.db.execute(sql, params or [])
    def _execute_affected(self, sql, params=None):
        return self._execute(sql, params).rowcount
    def _fetch_one(self, sql, params=None):
        row = self._execute(sql, params).fetchone()
        return dict(row) if row else None
    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._execute(sql, params).fetchall()]
    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    s = Store(migrated_sqlite_db("chat0043.db"))
    for name in ["db.user_chat_settings", "db.user_conversation_settings", "db.conversation_turns",
                 "services.chat_settings_service", "services.conversation_turn_service"]:
        monkeypatch.setattr(importlib.import_module("modules.flow_gate." + name), "get_store", lambda: s)
    monkeypatch.setattr(css, "resolve_command_policy", lambda *_: "user_approval")
    monkeypatch.setattr(turns, "_validate_document_for_append", lambda doc: {"doc_id": doc})
    monkeypatch.setattr(turns.message_image_service, "for_turn", lambda *_: [])
    from modules.flow_gate.services import conversation_events
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended", lambda *_: 0)
    return s


def test_settings_preserve_legacy_counts_and_hybrid_limit(store):
    css.save_chat_settings("u1", {"context_mode": "recent", "context_turns": 30})
    css.save_chat_settings("u1", {"context_mode": "checkpoint_recent", "checkpoint_max_turns": 7})
    settings, _ = css.resolve_chat_settings("u1")
    assert settings["context_turns"] == 30
    assert settings["checkpoint_max_turns"] == 7
    assert css.domain()["context_turns_presets"] == [5, 10, 15]
    assert css.defaults()["context_turns"] == 20
    assert css.defaults()["checkpoint_max_turns"] == 10


@pytest.mark.parametrize("cp,include,expected", [(3, True, 30), (38, True, 37), (38, False, 38), (None, True, 35)])
def test_hybrid_snapshot(monkeypatch, cp, include, expected):
    monkeypatch.setattr(cds, "checkpoint_safe", lambda *_: (cp, include) if cp else None)
    w = cds.context_window("u", "d", {"context_mode": "checkpoint_recent", "context_turns": 30, "checkpoint_max_turns": 10}, head_seq_of=lambda _: 40)
    assert w["after_seq"] == expected
    assert w["through_seq"] == 40
    assert w["limit"] == (10 if cp else 5)


def test_timeout_persisted_per_chat_and_user(store):
    assert cds.get_settings("u", "a")["timeout_minutes"] is None
    cds.save_settings("u", "a", {"timeout_minutes": 31}, project_id=None)
    assert cds.get_settings("u", "a")["timeout_minutes"] == 31
    assert cds.get_settings("u", "b")["timeout_minutes"] is None
    assert cds.get_settings("other", "a")["timeout_minutes"] is None
    cds.save_settings("u", "a", {"timeout_minutes": None}, project_id=None)
    assert cds.get_settings("u", "a")["timeout_minutes"] is None


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "30", 10081])
def test_timeout_bad_values(value):
    with pytest.raises(cds.ChatDocSettingsError):
        cds.save_settings("u", "d", {"timeout_minutes": value}, project_id=None)


def seed_turn(s, seq=1, speaker="user", participant="user:u"):
    s._execute("INSERT INTO conversation_turns (doc_id,seq,speaker,participant_key,body,body_hash,based_on_seq,idempotency_key,idempotency_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ["d", seq, speaker, participant, "secret", "a"*64, 0, "key"+str(seq), str(seq)*64, "2026-01-01"])


def test_edit_delete_cas_and_image_scrub(store):
    seed_turn(store)
    seed_turn(store, 2, "ai", "provider:p")
    store._execute("INSERT INTO message_image_links VALUES (?,?,?,?,?)", ["f", "d", 1, "img", 0])
    actor = {"kind": "session", "user_id": "u"}
    result = turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=1, body_raw="new")
    assert result["turn"]["body"] == "new"
    assert result["turn"]["message_version"] == 2
    with pytest.raises(turns.ConversationTurnError) as exc:
        turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=1, body_raw="stale")
    assert exc.value.status_code == 409
    result = turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=2, delete=True)
    assert result["turn"]["body"] == "" and result["turn"]["deleted_at"]
    assert result["turn"]["images"] == []
    assert store._fetch_one("SELECT COUNT(*) n FROM message_image_links")["n"] == 0
    assert store._fetch_one("SELECT body FROM conversation_turns WHERE seq=2")["body"] == "secret"
    assert turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=2, delete=True)["turn"]["message_version"] == 3
    with pytest.raises(turns.ConversationTurnError):
        turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=3, body_raw="restore")


@pytest.mark.parametrize("speaker,key,actor", [("user", "user:other", {"kind":"session","user_id":"u"}), ("ai", "user:u", {"kind":"session","user_id":"u"}), ("user","user:u", {"kind":"worker"})])
def test_non_author_rejected(store, speaker, key, actor):
    seed_turn(store, speaker=speaker, participant=key)
    with pytest.raises(turns.ConversationTurnError) as exc:
        turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=1, delete=True)
    assert exc.value.status_code == 403
    assert store._fetch_one("SELECT body FROM conversation_turns")["body"] == "secret"


def test_run_deadline_snapshot_and_unlimited(monkeypatch):
    monkeypatch.setattr(runtime, "_svc", lambda: SimpleNamespace(_now_mono=lambda: 90000, RUN_TIMEOUT_CAP_SEC=14400))
    r = {"started_mono": 0, "chat_timeout_policy": True, "chat_timeout_sec": None}
    assert runtime._absolute_remaining_sec(r) == float("inf")
    r["chat_timeout_sec"] = 60
    assert runtime._absolute_remaining_sec(r) < 0
    assert runtime._absolute_remaining_sec({"started_mono": 89999}) == 14399
    assert not provider_remote._RemoteProgress(r).stalled("STARTED")


def test_api_stop_interrupts_wait(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    r = {"chat_timeout_policy": True, "cancel_event": cancelled}
    monkeypatch.setattr(worker, "_absolute_remaining_sec", lambda _: float("inf"))
    def model():
        entered.set()
        release.wait(3)
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            worker._chat_model_call(r, model)
        assert entered.is_set()
    finally:
        release.set()
        timer.join()


def test_worker_page_hides_deleted_body_and_pins_upper_seq(store, monkeypatch):
    from modules.flow_gate.services import conversation_query_service as query
    seed_turn(store)
    seed_turn(store, 2)
    seed_turn(store, 3)
    actor = {"kind": "session", "user_id": "u"}
    turns.mutate_user_turn(doc_id="d", seq=1, actor=actor, expected_version=1, delete=True)
    monkeypatch.setattr(query, "_readable_document", lambda _: {"doc_id": "d"})
    monkeypatch.setattr(query, "_ensure_readable_rows", lambda _: None)
    monkeypatch.setattr(query, "record_read", lambda **_: None)
    monkeypatch.setattr(query.message_image_service, "for_range", lambda *_: {})
    result = query.list_turns(doc_id="d", actor={"kind":"worker","token":{"token_id":"t"}},
                              after_seq=0, through_seq=2, limit=10)
    assert [t["seq"] for t in result["turns"]] == [2]
    assert result["next_after_seq"] is None
    session = query.list_turns(doc_id="d", actor=actor, after_seq=0, limit=10)
    assert len(session["turns"]) == 3
    assert session["turns"][0]["body"] == ""
    from modules.flow_gate.services import conversation_markdown_service as markdown
    assert markdown._row_to_legacy_turn(store._fetch_one("SELECT * FROM conversation_turns WHERE seq=1"))["body"] == "삭제된 메세지"


def test_chat_watchdog_ignores_stall_but_enforces_finite_deadline(monkeypatch):
    from modules.flow_gate.services.ai_invoke import provider_cli as cli
    monkeypatch.setattr(cli, "_svc", lambda: SimpleNamespace(_now_mono=lambda: 90000))
    monkeypatch.setattr(cli, "_absolute_remaining_sec", lambda r: float("inf") if r["chat_timeout_sec"] is None else -1)
    def no_observation(*_):
        raise AssertionError("chat must not use the no-progress observer")
    monkeypatch.setattr(cli.oracle, "_observe_group_max_seq", no_observation)
    class Stop:
        calls = 0
        def wait(self, _):
            self.calls += 1
            return self.calls > 1
    r = {"started_mono": 0, "chat_timeout_policy": True, "chat_timeout_sec": None, "cancel_event": threading.Event()}
    proc = SimpleNamespace(poll=lambda: None)
    assert cli._progress_watchdog_loop(r, proc, Stop()) is None
    killed = []
    monkeypatch.setattr(cli, "_claim_watchdog_kill", lambda *args: killed.append(args[3]) or True)
    r["chat_timeout_sec"] = 60
    assert cli._progress_watchdog_loop(r, proc, Stop()) == "absolute_cap"
    assert killed == ["absolute_cap"]


def test_api_timeout_discards_late_result(monkeypatch):
    release = threading.Event()
    r = {"chat_timeout_policy": True, "cancel_event": threading.Event()}
    monkeypatch.setattr(worker, "_absolute_remaining_sec", lambda _: -1)
    try:
        with pytest.raises(InterruptedError):
            worker._chat_model_call(r, lambda: release.wait(3))
        assert r["timed_out"] is True
    finally:
        release.set()
