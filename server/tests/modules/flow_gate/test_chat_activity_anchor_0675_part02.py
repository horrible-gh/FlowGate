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

class TestLiveAnchors:


    def test_run_without_reply_stays_after_its_start_turn(self, db):
        start = _turn("user")
        _command("run_c", "ccr_c1", start, status="failed")
        _change("run_c", start)
        later = [_turn("user"), _turn("ai", "other_run"), _turn("user")]
        anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "other_run"})
        assert later[-1] > start
        assert _anchor(db_commands.get("ccr_c1")) == (start, "after", "run_start")
        assert _anchor(db_changes.get("run_c")) == (start, "after", "run_start")

    def test_reprocessing_keeps_the_same_anchor_and_rows(self, db):
        start = _turn("user")
        _command("run_d", "ccr_d1", start)
        reply = _turn("ai", "run_d")
        assert anchors.resolve_run(DOC, "run_d") == 1
        # duplicate reply event / retry: nothing moves
        assert anchors.resolve_run(DOC, "run_d") == 0
        assert anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_d"}) == 0
        # retried finalize upsert keeps one row and the same anchor
        _change("run_d", start)
        _change("run_d", start + 99)
        assert len(db_changes.list_for_doc(DOC)) == 1
        assert db_changes.get("run_d")["run_start_seq"] == start
        assert _anchor(db_changes.get("run_d")) == (reply, "after", "reply")
        assert _anchor(db_commands.get("ccr_d1")) == (reply, "before", "reply")

    def test_cas_refuses_an_update_based_on_a_stale_read(self, db):
        from modules.flow_gate.db.connection import get_store

        start = _turn("user")
        _command("run_e", "ccr_e1", start)
        stale = dict(db_commands.get("ccr_e1"))  # read while still run_start
        reply = _turn("ai", "run_e")
        anchors.resolve_run(DOC, "run_e")
        guard, params = anchor.cas_guard(stale)
        moved = get_store()._execute_affected(
            "UPDATE chat_command_requests SET anchor_seq = ?, anchor_position = ?, anchor_state = ? "
            f"WHERE request_id = ? AND {guard}",
            [start, "after", "run_start", "ccr_e1", *params],
        )
        assert not moved
        assert _anchor(db_commands.get("ccr_e1")) == (reply, "before", "reply")

    def test_user_turns_and_unrelated_runs_do_not_touch_rows(self, db):
        assert anchors.on_turn_appended(DOC, {"speaker": "user", "source_run_id": None}) == 0
        assert anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": None}) == 0
class TestBackfill:
    def test_unique_no_reply_and_ambiguous_are_told_apart_with_counts(self, db):
        _turn("user")
        _command("old_unique", "ccr_u", None)
        _change("old_unique", None)
        unique_reply = _turn("ai", "old_unique")
        _command("old_none", "ccr_n", None)
        _change("old_none", None)
        _command("old_many", "ccr_m", None)
        _turn("ai", "old_many")
        _turn("ai", "old_many")
        for request_id in ("ccr_u", "ccr_n", "ccr_m"):
            _legacy("chat_command_requests", "request_id", request_id)
        for run_id in ("old_unique", "old_none"):
            _legacy("ai_run_source_changes", "run_id", run_id)

        report = anchors.backfill()
        assert report["before"]["commands"] == {
            "total": 3, "reply": 0, "run_start": 0, "ambiguous": 0, "unresolved": 0, "pending": 3}
        assert report["after"]["commands"] == {
            "total": 3, "reply": 1, "run_start": 0, "ambiguous": 1, "unresolved": 1, "pending": 0}
        assert report["after"]["changes"] == {
            "total": 2, "reply": 1, "run_start": 0, "ambiguous": 0, "unresolved": 1, "pending": 0}
        assert _anchor(db_commands.get("ccr_u")) == (unique_reply, "before", "reply")
        assert _anchor(db_changes.get("old_unique")) == (unique_reply, "after", "reply")
        assert _anchor(db_commands.get("ccr_n")) == (None, None, "unresolved")
        assert _anchor(db_commands.get("ccr_m")) == (None, None, "ambiguous")
        # idempotent: a second boot changes nothing
        again = anchors.backfill()
        assert again["resolved"] == 0 and again["after"] == report["after"]

    def test_backfill_never_guesses_from_created_at(self, db):
        _command("old_x", "ccr_x", None)
        _legacy("chat_command_requests", "request_id", "ccr_x")
        _turn("user")
        _turn("ai", None)  # an AI turn right after, but not written by that run
        anchors.backfill()
        assert _anchor(db_commands.get("ccr_x")) == (None, None, "unresolved")
class TestWindow:
    def test_page_boundaries_neither_drop_nor_repeat_rows(self, db):
        seqs = []
        for i in range(6):
            start = _turn("user")
            _command(f"run_w{i}", f"ccr_w{i}", start)
            seqs.append(start)
        _command("legacy", "ccr_legacy", None)
        _legacy("chat_command_requests", "request_id", "ccr_legacy")
        anchors.backfill()
        # Screen holds turns 4..6 first, then pages in 1..3 (to = previous oldest - 1).
        newest = chat_command_service.list_for_doc(DOC, from_seq=4)
        older = chat_command_service.list_for_doc(DOC, from_seq=1, to_seq=3, include_unplaced=False)
        ids_new = [c["request_id"] for c in newest]
        ids_old = [c["request_id"] for c in older]
        assert sorted(ids_new) == ["ccr_legacy", "ccr_w3", "ccr_w4", "ccr_w5"]
        assert sorted(ids_old) == ["ccr_w0", "ccr_w1", "ccr_w2"]
        assert not set(ids_new) & set(ids_old)
        assert len(ids_new) + len(ids_old) == len(db_commands.list_for_doc(DOC))
