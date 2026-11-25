"""Run changes: the run's own writes, diff, file detail.

A run change lists only what ONE chat run wrote itself -- through a file tool call made
with its token, or through a command it executed -- never what card runs, other chat
runs or people changed in the shared group worktree meanwhile. Also: recording
unavailable, finalize, file detail from stored trees. Uses a real throwaway Git worktree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    CHAT_DOC, RUN_ID, bind_worktree, clean_commands_state, clean_runs, fg_store, init_worktree,  # noqa: F401
    make_run, seed,
)

from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import repository as repo  # noqa: E402
from flowgate.conversation import run_changes  # noqa: E402
from modules.flow_gate.services import run_source_ledger as ledger  # noqa: E402

CARD_TOKEN = {"ai_run_id": "aiv_card_run", "action_scope": "card"}


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    events = []
    monkeypatch.setattr(legacy_adapter, "emit_event",
                        lambda event, project, group, payload: events.append((event, payload)))
    ledger.reset()
    yield {"root": root, "events": events}
    ledger.reset()


def _start(root, run_id=RUN_ID, **kw):
    run = make_run(run_id=run_id, source_root=root, **kw)
    run_changes.start_snapshot(run)
    return run


def _tool(root, path, content, run_id=RUN_ID, token=None):
    """One file tool call (write / remove) made with a run's token."""
    with ledger.recording(token or {"ai_run_id": run_id}, root, [path]):
        target = root / path
        if content is None:
            target.unlink()
        elif isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")    # platform newlines, like init_worktree


def test_recording_never_touches_index_head_or_worktree(env):
    root = env["root"]
    (root / "staged.txt").write_text("s\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    run = _start(root)
    assert run["source_change_capture"] == c.CAPTURE_READY and ledger.is_open(RUN_ID)
    _tool(root, "keep.txt", "one\nchanged\n")
    before = (_git(root, "status", "--porcelain"), _git(root, "rev-parse", "HEAD"),
              _git(root, "diff", "--cached", "--name-only"))
    assert run_changes.finalize_run(run) is not None
    after = (_git(root, "status", "--porcelain"), _git(root, "rev-parse", "HEAD"),
             _git(root, "diff", "--cached", "--name-only"))
    assert before == after


def test_finalize_records_one_summary_with_per_file_stats(env):
    root = env["root"]
    run = _start(root)
    _tool(root, "keep.txt", "one\ntwo\nthree\n")
    _tool(root, "gone.txt", None)
    _tool(root, "new.txt", "a\nb\n")
    _tool(root, "blob.bin", b"\x00\x01\x02binary\x00")
    _tool(root, "ignored.log", "noise\n")
    run["outcome"] = "succeeded"
    row = run_changes.finalize_run(run)
    assert row is not None and row["run_id"] == RUN_ID and row["doc_id"] == CHAT_DOC
    files = {f["path"]: f for f in row["files"]}
    assert set(files) == {"keep.txt", "gone.txt", "new.txt", "blob.bin"}
    assert (files["keep.txt"]["status"], files["keep.txt"]["insertions"], files["keep.txt"]["deletions"]) == ("M", 1, 0)
    assert (files["gone.txt"]["status"], files["gone.txt"]["deletions"]) == ("D", 1)
    assert (files["new.txt"]["status"], files["new.txt"]["insertions"]) == ("A", 2)
    assert files["blob.bin"]["insertions"] is None and files["blob.bin"]["deletions"] is None
    assert (row["files_changed"], row["insertions"], row["deletions"]) == (4, 3, 1)
    assert [e for e, _ in env["events"]] == [c.EVENT_RUN_CHANGES]
    # One row per run: a retried finalize adds nothing.
    run_changes.finalize_run(run)
    assert len(repo.list_run_changes_for_doc(CHAT_DOC)) == 1


def test_no_change_and_reverted_change_store_nothing(env):
    root = env["root"]
    run = _start(root)
    assert run_changes.finalize_run(run) is None
    run2 = _start(root, run_id="aiv_revert")
    _tool(root, "keep.txt", "tmp\n", run_id="aiv_revert")
    _tool(root, "keep.txt", "one\ntwo\n", run_id="aiv_revert")
    assert run_changes.finalize_run(run2) is None
    assert repo.list_run_changes_for_doc(CHAT_DOC) == []
    assert env["events"] == []


# ── only the run's own writes ────────────────────────────────────────────────

def test_changes_of_other_runs_during_the_run_are_not_its_change(env):
    """The reported case: a card run edits files while a chat run only answers."""
    root = env["root"]
    run = _start(root)
    _tool(root, "card_tool.txt", "by a card token\n", token=CARD_TOKEN)
    _tool(root, "keep.txt", "one\ntwo\ncard\n", token=CARD_TOKEN)
    (root / "card_disk.txt").write_text("written straight to disk\n", encoding="utf-8")
    (root / "gone.txt").unlink()
    assert run_changes.finalize_run(run) is None
    assert repo.list_run_changes_for_doc(CHAT_DOC) == []
    assert env["events"] == []


def test_only_the_file_the_run_wrote_is_listed(env):
    root = env["root"]
    run = _start(root)
    (root / "card_a.txt").write_text("a\n", encoding="utf-8")
    _tool(root, "chat.txt", "mine\n")
    _tool(root, "card_b.txt", "b\n", token=CARD_TOKEN)
    row = run_changes.finalize_run(run)
    assert [(f["path"], f["status"], f["insertions"]) for f in row["files"]] == [("chat.txt", "A", 1)]
    assert (row["files_changed"], row["insertions"], row["deletions"]) == (1, 1, 0)


def test_a_file_both_edited_counts_only_the_runs_lines(env):
    root = env["root"]
    run = _start(root)
    _tool(root, "keep.txt", "card\none\ntwo\n", token=CARD_TOKEN)
    _tool(root, "keep.txt", "card\none\ntwo\nchat\n")
    _tool(root, "keep.txt", "card\ncard2\none\ntwo\nchat\n", token=CARD_TOKEN)
    _tool(root, "keep.txt", "card\ncard2\none\ntwo\nchat\nchat2\n")
    row = run_changes.finalize_run(run)
    assert [(f["path"], f["status"], f["insertions"], f["deletions"]) for f in row["files"]] == [
        ("keep.txt", "M", 2, 0)]
    data = run_changes.file_diff(CHAT_DOC, RUN_ID, "keep.txt")["data"]
    assert data["old"]["content"].replace("\r\n", "\n") == "card\none\ntwo\n"
    assert data["new"]["content"].replace("\r\n", "\n") == "card\none\ntwo\nchat\nchat2\n"


def test_two_chat_runs_at_once_each_get_their_own_change(env):
    root = env["root"]
    first = _start(root)
    second = _start(root, run_id="aiv_second")
    _tool(root, "a.txt", "a\n")
    _tool(root, "b.txt", "b\n", run_id="aiv_second")
    assert [f["path"] for f in run_changes.finalize_run(first)["files"]] == ["a.txt"]
    assert [f["path"] for f in run_changes.finalize_run(second)["files"]] == ["b.txt"]


def test_a_command_of_the_run_counts_and_foreign_tool_writes_meanwhile_do_not(env):
    root = env["root"]
    run = _start(root)
    window = ledger.begin_command(RUN_ID)
    assert window is not None
    (root / "generated.txt").write_text("by the command\n", encoding="utf-8")
    (root / "keep.txt").write_text("one\ntwo\nformatted\n", encoding="utf-8")
    _tool(root, "card.txt", "card\n", token=CARD_TOKEN)
    ledger.end_command(window)
    ledger.end_command(window)          # idempotent
    (root / "after_command.txt").write_text("nobody's command\n", encoding="utf-8")
    row = run_changes.finalize_run(run)
    assert {f["path"]: (f["status"], f["insertions"], f["deletions"]) for f in row["files"]} == {
        "generated.txt": ("A", 1, 0), "keep.txt": ("M", 1, 0)}


def test_a_command_run_through_the_request_flow_is_recorded(env, monkeypatch):
    """commands._execute_owned compares the worktree around a command that may change
    files (``git add``), and does not for a reading one (``git status``)."""
    from _fg_conversation_support import FakeExecutor, set_policy, use_executor, wait_until
    from flowgate.conversation import commands

    root = env["root"]

    class WritingExecutor(FakeExecutor):
        def spawn(self, argv, cwd, env_, timeout_seconds, run_id):
            (root / f"out{len(self.spawned)}.txt").write_text("generated\n", encoding="utf-8")
            return super().spawn(argv, cwd, env_, timeout_seconds, run_id)

    use_executor(monkeypatch, WritingExecutor())
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    run = _start(root)
    for args in (["status"], ["add", "-A"]):
        row = commands.create_request(run, {"program": "git", "args": args})
        assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.SUCCEEDED)
    change = run_changes.finalize_run(run)
    assert [(f["path"], f["status"]) for f in change["files"]] == [("out1.txt", "A")]


def test_a_command_of_an_unrecorded_run_opens_no_window(env):
    assert ledger.begin_command("aiv_unknown") is None
    ledger.end_command(None)


def test_file_tool_calls_are_recorded_for_the_tokens_run(env):
    """remote_tool_service wraps every mutating file tool call with the ledger."""
    from modules.flow_gate.services import remote_tool_service as tools

    root = env["root"]
    run = _start(root)
    seen = []

    def fake_execute(op, body, exec_root, grant=None):
        seen.append(op)
        if op == "move":
            (exec_root / body["source_path"]).rename(exec_root / body["target_path"])
        elif op == "remove":
            (exec_root / body["path"]).unlink()
        elif op in ("write", "patch"):
            (exec_root / body["path"]).write_text("tool\n", encoding="utf-8")
        return {}, None

    original, tools._execute = tools._execute, fake_execute
    try:
        own = {"ai_run_id": RUN_ID}
        tools._execute_recorded("write", {"path": "w.txt"}, root, None, own)
        tools._execute_recorded("move", {"source_path": "keep.txt", "target_path": "moved.txt"}, root, None, own)
        tools._execute_recorded("write", {"path": "card.txt"}, root, None, CARD_TOKEN)
        tools._execute_recorded("remove", {"path": "gone.txt"}, root, None, None)
        tools._execute_recorded("read", {"path": "w.txt"}, root, None, own)
    finally:
        tools._execute = original
    assert seen == ["write", "move", "write", "remove", "read"]
    row = run_changes.finalize_run(run)
    assert {f["path"]: f["status"] for f in row["files"]} == {"w.txt": "A", "keep.txt": "D", "moved.txt": "A"}


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "timed_out"])
def test_failed_cancelled_or_timed_out_runs_still_record_real_changes(env, outcome):
    root = env["root"]
    run = _start(root)
    _tool(root, "new.txt", "x\n")
    run["outcome"] = outcome
    if outcome == "cancelled":
        run["cancel_event"].set()
    row = run_changes.finalize_run(run)
    assert row is not None and row["files"][0]["path"] == "new.txt"


def test_changes_after_a_run_ended_belong_to_no_run_change(env):
    root = env["root"]
    first = _start(root)
    _tool(root, "a.txt", "a\n")
    run_changes.finalize_run(first)
    (root / "between.txt").write_text("manual edit after the run\n", encoding="utf-8")
    _tool(root, "late.txt", "the first run's token, after its end\n")
    second = _start(root, run_id="aiv_second")
    _tool(root, "b.txt", "b\n", run_id="aiv_second")
    row = run_changes.finalize_run(second)
    assert [f["path"] for f in row["files"]] == ["b.txt"]
    assert [f["path"] for f in repo.get_run_change(RUN_ID)["files"]] == ["a.txt"]


def test_no_managed_worktree_keeps_the_run_and_skips_the_change(env, monkeypatch):
    bind_worktree(monkeypatch, None)
    run = make_run(source_root=env["root"])
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE
    assert run["source_change_capture_reason"].startswith("worktree_unavailable")
    assert not ledger.is_open(RUN_ID)
    assert run_changes.finalize_run(run) is None
    assert repo.list_run_changes_for_doc(CHAT_DOC) == []


def test_a_run_rooted_elsewhere_gets_no_change_summary(env):
    run = make_run(source_root=env["root"].parent)
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE


def test_start_failure_is_logged_not_raised(env, monkeypatch, caplog):
    def broken(run_id, root):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(ledger, "open_run", broken)
    run = make_run(source_root=env["root"])
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE
    assert "start_snapshot_failed" in run["source_change_capture_reason"]
    assert any("start snapshot failed" in r.getMessage() for r in caplog.records)
    assert run_changes.finalize_run(run) is None


def test_end_failure_skips_only_the_change(env, monkeypatch):
    root = env["root"]
    run = _start(root)
    _tool(root, "new.txt", "x\n")
    monkeypatch.setattr(ledger, "_tree", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert run_changes.finalize_run(run) is None
    assert "end_snapshot_failed" in run["source_change_capture_reason"]
    assert not ledger.is_open(RUN_ID)


def test_capture_tree_on_a_non_repository_fails_cleanly(tmp_path):
    with pytest.raises(run_changes.SnapshotError):
        run_changes.capture_tree(tmp_path / "missing")


# ── file detail from the stored trees (§3.8.3) ───────────────────────────────

def test_file_diff_reads_the_stored_trees_not_the_current_worktree(env):
    root = env["root"]
    run = _start(root)
    _tool(root, "keep.txt", "one\ntwo\nrun\n")
    run_changes.finalize_run(run)
    (root / "keep.txt").write_text("later edit\n", encoding="utf-8")
    body = run_changes.file_diff(CHAT_DOC, RUN_ID, "keep.txt")
    data = body["data"]
    assert body["ok"] is True and data["path"] == "keep.txt" and data["status"] == "M"
    assert "run" in data["new"]["content"] and "later edit" not in data["new"]["content"]
    assert "run" not in data["old"]["content"]


def test_file_diff_errors(env):
    root = env["root"]
    run = _start(root)
    _tool(root, "new.txt", "x\n")
    run_changes.finalize_run(run)
    with pytest.raises(c.ConversationError) as other_path:
        run_changes.file_diff(CHAT_DOC, RUN_ID, "keep.txt")
    assert (other_path.value.status, other_path.value.code) == (404, "chat_run_change_path_not_found")
    with pytest.raises(c.ConversationError) as other_doc:
        run_changes.file_diff("fgp.v02.0001.0009-CH", RUN_ID, "new.txt")
    assert other_doc.value.code == "chat_run_change_not_found"
    with pytest.raises(c.ConversationError) as escape:
        run_changes.file_diff(CHAT_DOC, RUN_ID, "../etc/passwd")
    assert escape.value.status == 400
