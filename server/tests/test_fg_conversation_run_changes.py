"""Run changes: start/end snapshots, diff, file detail (flowgate.v02.0032 T#1).

D0006 §3.8 (run changes vs Group changes, snapshot unavailable, finalize, file detail
from stored trees, before/after-run aggregation) and completion conditions 12-14.
Uses a real throwaway Git worktree.
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
    return {"root": root, "events": events}


def _start(root, run_id=RUN_ID, **kw):
    run = make_run(run_id=run_id, source_root=root, **kw)
    run_changes.start_snapshot(run)
    return run


def test_snapshot_never_touches_index_head_or_worktree(env):
    root = env["root"]
    (root / "keep.txt").write_text("one\nchanged\n", encoding="utf-8")
    (root / "staged.txt").write_text("s\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    before = (_git(root, "status", "--porcelain"), _git(root, "rev-parse", "HEAD"),
              _git(root, "diff", "--cached", "--name-only"))
    run = _start(root)
    assert run["source_start_tree"] and run["source_change_capture"] == c.CAPTURE_READY
    after = (_git(root, "status", "--porcelain"), _git(root, "rev-parse", "HEAD"),
             _git(root, "diff", "--cached", "--name-only"))
    assert before == after


def test_finalize_records_one_summary_with_per_file_stats(env):
    root = env["root"]
    run = _start(root)
    (root / "keep.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "gone.txt").unlink()
    (root / "new.txt").write_text("a\nb\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary\x00")
    (root / "ignored.log").write_text("noise\n", encoding="utf-8")
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
    # One row per run: a retried finalize refreshes it instead of adding another.
    run_changes.finalize_run(run)
    assert len(repo.list_run_changes_for_doc(CHAT_DOC)) == 1


def test_no_change_and_reverted_change_store_nothing(env):
    root = env["root"]
    run = _start(root)
    assert run_changes.finalize_run(run) is None
    run2 = _start(root, run_id="aiv_revert")
    (root / "keep.txt").write_text("tmp\n", encoding="utf-8")
    (root / "keep.txt").write_text("one\ntwo\n", encoding="utf-8")
    assert run_changes.finalize_run(run2) is None
    assert repo.list_run_changes_for_doc(CHAT_DOC) == []
    assert env["events"] == []


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "timed_out"])
def test_failed_cancelled_or_timed_out_runs_still_record_real_changes(env, outcome):
    root = env["root"]
    run = _start(root)
    (root / "new.txt").write_text("x\n", encoding="utf-8")
    run["outcome"] = outcome
    if outcome == "cancelled":
        run["cancel_event"].set()
    row = run_changes.finalize_run(run)
    assert row is not None and row["files"][0]["path"] == "new.txt"


def test_changes_after_a_run_ended_belong_to_no_run_change(env):
    root = env["root"]
    first = _start(root)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    run_changes.finalize_run(first)
    (root / "between.txt").write_text("manual edit after the run\n", encoding="utf-8")
    second = _start(root, run_id="aiv_second")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    row = run_changes.finalize_run(second)
    assert [f["path"] for f in row["files"]] == ["b.txt"]
    assert [f["path"] for f in repo.get_run_change(RUN_ID)["files"]] == ["a.txt"]


def test_no_managed_worktree_keeps_the_run_and_skips_the_change(env, monkeypatch):
    bind_worktree(monkeypatch, None)
    run = make_run(source_root=env["root"])
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE
    assert run["source_change_capture_reason"].startswith("worktree_unavailable")
    assert run_changes.finalize_run(run) is None
    assert repo.list_run_changes_for_doc(CHAT_DOC) == []


def test_a_run_rooted_elsewhere_gets_no_change_summary(env):
    run = make_run(source_root=env["root"].parent)
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE


def test_start_snapshot_failure_is_logged_not_raised(env, monkeypatch, caplog):
    def broken(root):
        raise run_changes.SnapshotError("git exploded")

    monkeypatch.setattr(run_changes, "capture_tree", broken)
    run = make_run(source_root=env["root"])
    assert run_changes.start_snapshot(run) is None
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE
    assert "start_snapshot_failed" in run["source_change_capture_reason"]
    assert any("start snapshot failed" in r.getMessage() for r in caplog.records)
    assert run_changes.finalize_run(run) is None


def test_end_snapshot_failure_skips_only_the_change(env, monkeypatch):
    root = env["root"]
    run = _start(root)
    (root / "new.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(run_changes, "capture_tree", lambda root: (_ for _ in ()).throw(run_changes.SnapshotError("x")))
    assert run_changes.finalize_run(run) is None
    assert "end_snapshot_failed" in run["source_change_capture_reason"]


def test_capture_tree_on_a_non_repository_fails_cleanly(tmp_path):
    with pytest.raises(run_changes.SnapshotError):
        run_changes.capture_tree(tmp_path / "missing")


# ── file detail from the stored trees (§3.8.3) ───────────────────────────────

def test_file_diff_reads_the_stored_trees_not_the_current_worktree(env):
    root = env["root"]
    run = _start(root)
    (root / "keep.txt").write_text("one\ntwo\nrun\n", encoding="utf-8")
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
    (root / "new.txt").write_text("x\n", encoding="utf-8")
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
