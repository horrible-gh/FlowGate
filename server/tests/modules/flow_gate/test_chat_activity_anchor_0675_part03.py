"""Independently selectable scenarios; shared fixtures remain in test_chat_activity_anchor_0675.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_chat_activity_anchor_0675 import (
    DOC,
    Path,
    _anchor,
    _change,
    _command,
    _fail_resolve,
    _legacy,
    _turn,
    anchor,
    anchors,
    build_live_sqlite_db,
    chat_command_service,
    chat_run_changes_service,
    db,
    db_changes,
    db_commands,
    install_live_sqlite_store,
    os,
    pytest,
    sys,
    turn_store,
)

class TestWindow:

    def test_window_order_is_oldest_first_and_stable(self, db):
        start = _turn("user")
        for i in range(3):
            _command("run_same", f"ccr_s{i}", start)
        first = [c["request_id"] for c in chat_command_service.list_for_doc(DOC, from_seq=start)]
        again = [c["request_id"] for c in chat_command_service.list_for_doc(DOC, from_seq=start)]
        assert first == again == ["ccr_s0", "ccr_s1", "ccr_s2"]
def test_append_turn_reanchors_before_broadcasting(db, monkeypatch):
    from modules.flow_gate.services import conversation_events
    from modules.flow_gate.services import conversation_turn_service as service

    start = _turn("user")
    _command("run_live", "ccr_live", start, status="running")
    seen: list[tuple] = []
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(service, "resolve_actor", lambda actor, hint=None: {
        "speaker": "ai", "participant_key": "provider:p", "display_name": "P", "locale": None,
        "source_run_id": "run_live",
    })
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended",
                        lambda doc, result: seen.append(_anchor(db_commands.get("ccr_live"))))
    result = service.append_turn(doc_id=DOC, actor={"kind": "session"}, body_raw="done",
                                 idempotency_key="idem-live", based_on_seq=start)
    reply = result["turn"]["seq"]
    assert seen == [(reply, "before", "reply")]
class TestReanchorRecovery:
    def test_hook_retries_a_failed_resolve(self, db, monkeypatch):
        start = _turn("user")
        _command("run_r", "ccr_r", start)
        _change("run_r", start)
        reply = _turn("ai", "run_r")
        calls = _fail_resolve(monkeypatch, times=1)
        assert anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_r"}) == 2
        assert len(calls) == 2
        assert _anchor(db_commands.get("ccr_r")) == (reply, "before", "reply")
        assert _anchor(db_changes.get("run_r")) == (reply, "after", "reply")

    def test_rows_left_at_run_start_are_found_and_moved_on_reconcile(self, db, monkeypatch):
        start = _turn("user")
        _command("run_f", "ccr_f", start)
        _change("run_f", start)
        reply = _turn("ai", "run_f")
        _fail_resolve(monkeypatch, times=anchors.HOOK_ATTEMPTS)
        assert anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_f"}) == 0
        # the failure persisted the wrong position
        assert _anchor(db_commands.get("ccr_f")) == (start, "after", "run_start")
        assert _anchor(db_changes.get("run_f")) == (start, "after", "run_start")
        assert db_commands.list_runs_to_resolve(DOC) == [{"doc_id": DOC, "ai_run_id": "run_f"}]
        assert db_changes.list_runs_to_resolve(DOC) == [{"doc_id": DOC, "ai_run_id": "run_f"}]

        assert anchors.reconcile(DOC) == 2
        assert _anchor(db_commands.get("ccr_f")) == (reply, "before", "reply")
        assert _anchor(db_changes.get("run_f")) == (reply, "after", "reply")
        # nothing left to repair; a second pass is a no-op
        assert db_commands.list_runs_to_resolve(DOC) == []
        assert db_changes.list_runs_to_resolve(DOC) == []
        assert anchors.reconcile(DOC) == 0

    def test_restart_backfill_repairs_a_decided_run_start_row(self, db, monkeypatch):
        start = _turn("user")
        _command("run_b", "ccr_b", start)
        _change("run_b", start)
        first = _turn("ai", "run_b")
        anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_b"})
        last = _turn("ai", "run_b")  # a second reply whose re-anchor fails ...
        _fail_resolve(monkeypatch, times=2 * anchors.HOOK_ATTEMPTS)
        anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_b"})
        start2 = _turn("user")
        _command("run_c", "ccr_c", start2)
        reply2 = _turn("ai", "run_c")  # ... and so does this run's first reply
        anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_c"})
        assert _anchor(db_changes.get("run_b")) == (first, "after", "reply")
        assert _anchor(db_commands.get("ccr_c")) == (start2, "after", "run_start")

        # Server restart: no later writer of these runs ever comes.
        report = anchors.backfill()
        assert report["resolved"] == 2
        assert report["before"]["commands"]["pending"] == 0  # already decided, not NULL
        assert _anchor(db_changes.get("run_b")) == (last, "after", "reply")
        assert _anchor(db_commands.get("ccr_b")) == (first, "before", "reply")
        assert _anchor(db_commands.get("ccr_c")) == (reply2, "before", "reply")
        assert anchors.backfill()["resolved"] == 0

    def test_run_without_reply_is_not_revisited(self, db):
        start = _turn("user")
        _command("run_n", "ccr_n", start)
        _turn("ai", "other_run")
        assert db_commands.list_runs_to_resolve(DOC) == []
        assert anchors.reconcile(DOC) == 0
        assert _anchor(db_commands.get("ccr_n")) == (start, "after", "run_start")

    def test_activity_read_returns_the_repaired_anchor(self, db, monkeypatch):
        from modules.flow_gate.api.v1 import chat_command_routes as routes

        start = _turn("user")
        _command("run_a", "ccr_a", start)
        _change("run_a", start)
        reply = _turn("ai", "run_a")
        _fail_resolve(monkeypatch, times=anchors.HOOK_ATTEMPTS)
        anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_a"})
        monkeypatch.setattr(routes, "_readable_chat_doc", lambda doc_id, user: {"doc_id": doc_id})
        # A reconnecting screen re-reads the window holding the reply turn.
        res = routes.chat_activity(DOC, user={"user_id": "u1"}, from_seq=reply, to_seq=None, unplaced=True)
        assert [(c["request_id"], c["anchor_seq"], c["anchor_position"], c["anchor_state"])
                for c in res["commands"]] == [("ccr_a", reply, "before", "reply")]
        assert [(c["run_id"], c["anchor_seq"], c["anchor_state"]) for c in res["changes"]] == [
            ("run_a", reply, "reply")]
