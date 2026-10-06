"""Chat activity and conversation anchors (flowgate.v02.0032 T#1).

D0006 §3.9 (anchor states, reply re-anchor, reconcile on read, created_at never used),
§5.3 activity errors and completion conditions 9, 15 and 16.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    CHAT_DOC, GROUP, PROJECT, RUN_ID, USER, FakeExecutor, add_turn, bind_worktree,  # noqa: F401
    clean_commands_state, clean_runs, fg_store, init_worktree, make_run, seed, set_policy, use_executor,  # noqa: F401
)

from flowgate.conversation import activity  # noqa: E402
from flowgate.conversation import commands  # noqa: E402
from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import repository as repo  # noqa: E402


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    use_executor(monkeypatch, FakeExecutor())
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: True)
    return {"root": root}


def _command(run):
    return commands.create_request(run, {"program": "git", "args": ["status"]})


def _change(run_id=RUN_ID, start_seq=None):
    """Store a run change the way run_changes.finalize_run does (row, then its anchor)."""
    repo.upsert_run_change(
        run_id=run_id, doc_id=CHAT_DOC, project_id=PROJECT, group_id=GROUP, start_tree="a" * 40,
        end_tree="b" * 40, run_started_at=None, run_finished_at=None,
        files=[{"path": "x.txt", "status": "M", "insertions": 1, "deletions": 0}],
        insertions=1, deletions=0, run_start_seq=start_seq,
    )
    activity.resolve_change(CHAT_DOC, run_id)
    return repo.get_run_change(run_id)


def _anchor(row):
    return (row.get("anchor_seq"), row.get("anchor_position"), row.get("anchor_state"))


# ── the rule ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,replies,start,expected", [
    (activity.KIND_COMMAND, [], 3, (3, "after", "run_start")),
    (activity.KIND_CHANGE, [], 0, (0, "after", "run_start")),
    (activity.KIND_COMMAND, [5, 7], 3, (5, "before", "reply")),
    (activity.KIND_CHANGE, [5, 7], 3, (7, "after", "reply")),
    (activity.KIND_COMMAND, [], None, (None, None, "unresolved")),
    (activity.KIND_CHANGE, [4], None, (4, "after", "reply")),
    (activity.KIND_COMMAND, [4, 6], None, (None, None, "ambiguous")),
])
def test_anchor_rule(kind, replies, start, expected):
    assert activity.compute(kind, replies, start) == expected


def test_a_reply_anchor_never_falls_back_and_only_moves_forward():
    reply = {"anchor_seq": 5, "anchor_position": "before", "anchor_state": "reply"}
    assert not activity.should_replace(activity.KIND_COMMAND, reply, (3, "after", "run_start"))
    assert not activity.should_replace(activity.KIND_COMMAND, reply, (6, "before", "reply"))
    assert activity.should_replace(activity.KIND_COMMAND, reply, (4, "before", "reply"))
    change = {"anchor_seq": 5, "anchor_position": "after", "anchor_state": "reply"}
    assert activity.should_replace(activity.KIND_CHANGE, change, (8, "after", "reply"))
    assert not activity.should_replace(activity.KIND_CHANGE, change, (4, "after", "reply"))
    assert activity.should_replace(activity.KIND_COMMAND, {}, (1, "after", "run_start"))


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_rows_are_anchored_at_run_start_then_move_to_the_reply(env):
    add_turn(CHAT_DOC, "user")
    head = add_turn(CHAT_DOC, "user")
    run = make_run(source_root=env["root"], chat_start_seq=head)
    row = _command(run)
    assert _anchor(row) == (head, "after", "run_start")
    _change(start_seq=head)
    assert _anchor(repo.get_run_change(RUN_ID)) == (head, "after", "run_start")

    first = add_turn(CHAT_DOC, "ai", RUN_ID)
    assert activity.on_turn_appended(CHAT_DOC, {"speaker": "ai", "source_run_id": RUN_ID}) == 2
    assert _anchor(repo.get_command(row["request_id"])) == (first, "before", "reply")
    assert _anchor(repo.get_run_change(RUN_ID)) == (first, "after", "reply")

    second = add_turn(CHAT_DOC, "ai", RUN_ID)
    activity.on_turn_appended(CHAT_DOC, {"speaker": "ai", "source_run_id": RUN_ID})
    assert _anchor(repo.get_command(row["request_id"])) == (first, "before", "reply")
    assert _anchor(repo.get_run_change(RUN_ID)) == (second, "after", "reply")


def test_human_and_foreign_turns_move_nothing(env):
    run = make_run(source_root=env["root"], chat_start_seq=0)
    row = _command(run)
    add_turn(CHAT_DOC, "user")
    add_turn(CHAT_DOC, "ai", "aiv_someone_else")
    assert activity.on_turn_appended(CHAT_DOC, {"speaker": "user", "source_run_id": None}) == 0
    assert activity.on_turn_appended(CHAT_DOC, {"speaker": "ai", "source_run_id": RUN_ID}) == 0
    assert _anchor(repo.get_command(row["request_id"])) == (0, "after", "run_start")


def test_unknown_start_is_unresolved_never_guessed_from_created_at(env):
    run = make_run(source_root=env["root"], chat_start_seq=None)
    add_turn(CHAT_DOC, "user")
    row = _command(run)
    assert _anchor(row) == (None, None, "unresolved")


def test_reconcile_on_read_repairs_a_missed_reply_hook(env):
    run = make_run(source_root=env["root"], chat_start_seq=0)
    row = _command(run)
    seq = add_turn(CHAT_DOC, "ai", RUN_ID)  # the reply hook never ran for this turn
    assert _anchor(repo.get_command(row["request_id"])) == (0, "after", "run_start")
    listed = activity.list_for_doc(CHAT_DOC)
    assert [(item["request_id"], item["anchor_seq"], item["anchor_state"]) for item in listed["commands"]] == [
        (row["request_id"], seq, "reply")]
    assert activity.reconcile(CHAT_DOC) == 0  # idempotent


def test_reply_hook_failure_never_raises(env, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(activity, "resolve_run", boom)
    assert activity.on_turn_appended(CHAT_DOC, {"speaker": "ai", "source_run_id": RUN_ID}) == 0


# ── activity listing ─────────────────────────────────────────────────────────

def test_listing_returns_commands_and_changes_with_anchors(env):
    run = make_run(source_root=env["root"], chat_start_seq=0)
    pending = _command(run)
    _change(start_seq=0)
    body = activity.list_for_doc(CHAT_DOC)
    assert body["ok"] is True and body["doc_id"] == CHAT_DOC
    assert body["commands"][0]["status"] == c.PENDING_APPROVAL
    assert body["commands"][0]["request_id"] == pending["request_id"]
    change = body["changes"][0]
    assert change["run_id"] == RUN_ID and change["files_changed"] == 1
    assert change["files"] == [{"path": "x.txt", "status": "M", "insertions": 1, "deletions": 0}]
    for item in (body["commands"][0], change):
        assert {"anchor_seq", "anchor_position", "anchor_state"} <= set(item)


def test_reopening_recovers_pending_and_running_rows(env, monkeypatch):
    """Completion 9: same process, the screen re-reads activity and finds open rows."""
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    run = make_run(source_root=env["root"], chat_start_seq=0)
    pending = _command(run)
    running = _command(run)
    commands.decide(running["request_id"], {"user_id": USER}, "approve")
    assert slow.spawned_event.wait(5)
    statuses = {item["request_id"]: item["status"] for item in activity.list_for_doc(CHAT_DOC)["commands"]}
    assert statuses[pending["request_id"]] == c.PENDING_APPROVAL
    assert statuses[running["request_id"]] in (c.APPROVED, c.RUNNING)
    commands.cancel_for_run(RUN_ID)


def test_listing_window_by_turn_range(env):
    add_turn(CHAT_DOC, "user")
    add_turn(CHAT_DOC, "user")
    early = make_run(run_id="aiv_early", source_root=env["root"], chat_start_seq=1)
    late = make_run(run_id="aiv_late", source_root=env["root"], chat_start_seq=2)
    unplaced = make_run(run_id="aiv_unplaced", source_root=env["root"], chat_start_seq=None)
    a, b, u = _command(early), _command(late), _command(unplaced)
    ids = lambda body: {item["request_id"] for item in body["commands"]}  # noqa: E731
    assert ids(activity.list_for_doc(CHAT_DOC, from_seq=2, to_seq=2)) == {b["request_id"], u["request_id"]}
    assert ids(activity.list_for_doc(CHAT_DOC, from_seq=2, include_unplaced=False)) == {b["request_id"]}
    assert ids(activity.list_for_doc(CHAT_DOC)) == {a["request_id"], b["request_id"], u["request_id"]}


def test_activity_document_errors(env, fg_store, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    db_documents.create({"doc_id": f"{GROUP}.0002-M", "project_id": PROJECT, "module": "v02",
                         "group_id": GROUP, "type_code": "M", "seq": 2, "title": "memo"})
    for doc_id in ("missing.doc", f"{GROUP}.0002-M"):
        with pytest.raises(c.ConversationError) as exc:
            activity.readable_chat_document(doc_id, {"user_id": USER})
        assert (exc.value.status, exc.value.code) == (404, "chat_document_not_found")
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: False)
    with pytest.raises(c.ConversationError) as denied:
        activity.readable_chat_document(CHAT_DOC, {"user_id": USER})
    assert (denied.value.status, denied.value.code) == (403, "forbidden")
