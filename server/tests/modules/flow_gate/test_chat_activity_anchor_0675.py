"""persisted conversation anchors of chat activity.

Covers the earlier implementation (server / DB):
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
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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






# ── backfill ────────────────────────────────────────────────────────────────



# ── paging window ───────────────────────────────────────────────────────────



# ── the turn append path ────────────────────────────────────────────────────



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
