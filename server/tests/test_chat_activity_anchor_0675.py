"""flowgate.default.0675 T0004 -- persisted conversation anchors of chat activity.

Covers T0004 §4 (server / DB):
  1. a new run's command/change rows store an anchor and the activity API returns it;
  2. with an AI reply: command = before the reply, change = after the reply;
  3. without a reply: both fixed after the turn the run started from;
  4. re-processing the same event (re-resolve, retried finalize upsert, stale resolver)
     never moves the anchor or duplicates a row;
  5. backfill separates unique match / no-reply (unresolved) / ambiguous, with counts;
  6. the anchor window of the activity read neither drops nor repeats a row at a page
     boundary;
plus the turn-append hook re-anchoring before the turn is broadcast, and the repair of
rows a failed post-commit re-anchor left behind its reply (hook retry, per-doc reconcile
on the activity read, startup backfill of already-decided run_start/reply rows).
A real migrated SQLite store is used: the subject is what is stored.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.db import ai_run_source_changes as db_changes  # noqa: E402
from modules.flow_gate.db import chat_activity_anchor as anchor  # noqa: E402
from modules.flow_gate.db import chat_command_requests as db_commands  # noqa: E402
from modules.flow_gate.db import conversation_turns as turn_store  # noqa: E402
from modules.flow_gate.services import chat_activity_anchor_service as anchors  # noqa: E402
from modules.flow_gate.services import chat_command_service  # noqa: E402
from modules.flow_gate.services import chat_run_changes_service  # noqa: E402

from tests.store_transaction_support import build_live_sqlite_db, install_live_sqlite_store  # noqa: E402

DOC = "flowgate.default.0675.0009-CH"


@pytest.fixture
def db(tmp_path, monkeypatch):
    live = install_live_sqlite_store(monkeypatch, build_live_sqlite_db(tmp_path / "anchor_0675.db"))
    # conversation_turns.doc_id references documents; the document's own parents are not
    # this suite's subject, so only the CH row itself is seeded (FKs off for that insert).
    live.conn.execute("PRAGMA foreign_keys = OFF")
    live.conn.execute(
        "INSERT INTO documents (doc_id, project_id, type_code, seq, title, created_at, updated_at) "
        "VALUES (?, 'flowgate', 'CH', 9, 'chat', 't', 't')", [DOC],
    )
    live.conn.commit()
    return live


def _turn(speaker: str, run_id: str | None = None, doc: str = DOC) -> int:
    n = turn_store.current_head_seq(doc) + 1
    row = turn_store.insert_turn_with_next_seq(
        doc_id=doc, speaker=speaker, participant_key=f"{speaker}:p", display_name=None,
        locale=None, body=f"turn {n}", body_hash="0" * 64, based_on_seq=n - 1,
        source_run_id=run_id, idempotency_key=f"k{n}-{doc}", idempotency_hash=f"{n:064d}",
        created_at=f"2026-10-06T10:{n:02d}:00+09:00",
    )
    return int(row["seq"])


def _command(run_id: str, request_id: str, start: int | None, status: str = "succeeded") -> dict:
    return db_commands.create(
        request_id=request_id, ai_run_id=run_id, doc_id=DOC, project_id="flowgate",
        group_id="flowgate.default.0675", token_id=None, issued_to="u1", provider_name="m",
        program="git", args=["status"], cwd_relative=".", timeout_seconds=30,
        category="read_only", policy="always_approve", status=status,
        decision_source="policy", run_start_seq=start,
    )


def _change(run_id: str, start: int | None) -> dict:
    return db_changes.upsert(
        run_id=run_id, doc_id=DOC, project_id="flowgate", group_id="flowgate.default.0675",
        start_tree="a" * 40, end_tree="b" * 40, run_started_at="s", run_finished_at="f",
        files=[{"path": "x.py", "status": "M", "insertions": 1, "deletions": 0}],
        insertions=1, deletions=0, run_start_seq=start,
    )


def _anchor(row: dict) -> tuple:
    return (row.get("anchor_seq"), row.get("anchor_position"), row.get("anchor_state"))


def _legacy(table: str, key: str, value: str) -> None:
    """A row as written before migration 137: no start seq, no anchor."""
    from modules.flow_gate.db.connection import get_store

    get_store()._execute(
        f"UPDATE {table} SET run_start_seq = NULL, anchor_seq = NULL, anchor_position = NULL, "
        f"anchor_state = NULL WHERE {key} = ?", [value],
    )


# ── the rule (pure) ─────────────────────────────────────────────────────────

class TestRule:
    def test_reply_places_commands_before_first_and_change_after_last_reply(self):
        assert anchor.compute("command", [7, 5], 4) == (5, "before", "reply")
        assert anchor.compute("change", [7, 5], 4) == (7, "after", "reply")

    def test_no_reply_is_fixed_after_the_start_turn(self):
        assert anchor.compute("command", [], 4) == (4, "after", "run_start")
        assert anchor.compute("change", [], 0) == (0, "after", "run_start")

    def test_unknown_start_trusts_only_a_single_reply(self):
        assert anchor.compute("command", [9], None) == (9, "before", "reply")
        assert anchor.compute("command", [], None) == (None, None, "unresolved")
        assert anchor.compute("change", [3, 9], None) == (None, None, "ambiguous")

    def test_a_stale_answer_never_downgrades_a_reply_anchor(self):
        stored = {"anchor_seq": 5, "anchor_position": "before", "anchor_state": "reply"}
        assert anchor.should_replace("command", stored, (4, "after", "run_start")) is False
        assert anchor.should_replace("command", stored, (5, "before", "reply")) is False
        assert anchor.should_replace("command", stored, (7, "before", "reply")) is False
        change = {"anchor_seq": 5, "anchor_position": "after", "anchor_state": "reply"}
        assert anchor.should_replace("change", change, (7, "after", "reply")) is True
        assert anchor.should_replace("change", change, (3, "after", "reply")) is False


# ── live writes ─────────────────────────────────────────────────────────────

class TestLiveAnchors:
    def test_new_run_rows_store_an_anchor_returned_by_the_activity_read(self, db):
        start = _turn("user")
        _command("run_a", "ccr_a1", start)
        _change("run_a", start)
        commands = chat_command_service.list_for_doc(DOC)
        changes = chat_run_changes_service.list_for_doc(DOC)
        assert [_anchor(c) for c in commands] == [(start, "after", "run_start")]
        assert [_anchor(c) for c in changes] == [(start, "after", "run_start")]
        assert changes[0]["created_at"]

    def test_reply_moves_command_before_and_change_after_the_reply(self, db):
        start = _turn("user")
        _command("run_b", "ccr_b1", start, status="pending_approval")
        _turn("user")  # the user keeps talking while the run works
        reply = _turn("ai", "run_b")
        assert anchors.on_turn_appended(DOC, {"speaker": "ai", "source_run_id": "run_b"}) == 1
        _change("run_b", start)  # finalize runs after the reply
        assert _anchor(db_commands.get("ccr_b1")) == (reply, "before", "reply")
        assert _anchor(db_changes.get("run_b")) == (reply, "after", "reply")

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


# ── backfill ────────────────────────────────────────────────────────────────

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


# ── paging window ───────────────────────────────────────────────────────────

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

    def test_window_order_is_oldest_first_and_stable(self, db):
        start = _turn("user")
        for i in range(3):
            _command("run_same", f"ccr_s{i}", start)
        first = [c["request_id"] for c in chat_command_service.list_for_doc(DOC, from_seq=start)]
        again = [c["request_id"] for c in chat_command_service.list_for_doc(DOC, from_seq=start)]
        assert first == again == ["ccr_s0", "ccr_s1", "ccr_s2"]


# ── the turn append path ────────────────────────────────────────────────────

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


# ── a failed reply-time re-anchor is repaired (retry / reconcile / restart) ──

def _fail_resolve(monkeypatch, times: int) -> list[int]:
    """Make the hook's resolve raise ``times`` times (the turn is committed already)."""
    calls: list[int] = []
    real = anchors.resolve_run

    def flaky(doc_id, run_id):
        calls.append(1)
        if len(calls) <= times:
            raise RuntimeError("db hiccup")
        return real(doc_id, run_id)

    monkeypatch.setattr(anchors, "resolve_run", flaky)
    return calls


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
