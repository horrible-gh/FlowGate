"""v02gui.0015 T2 (NR0006 §2.3, §6-5, §6-6): a new attempt does not take an earlier attempt's uncommitted changes --
and says so.

An attempt starts from its predecessors' results or the run base, never from an earlier attempt of its own node, and
its changes are committed only when its result is integrated. So when a card's attempt is invalidated (a reopen, a
recovery, a Time Machine rewind, a Stop) and a new one opens, what the old one left uncommitted stays where it is kept
(its dirty tree, kept by D-1a-0; a user_stop snapshot under ``refs/flowgate/preserved``) and is not in the new tree.
These tests pin that it is no longer silent: the new attempt's owner marker pins those attempts and their changed files
(``uncarried``), its preparation records them (``prior_changes_not_carried``, after the mode record) and the card's run
read counts them (``uncarried_changes``). Nothing is carried over. Reuses the 0061 attempt-workspace harness.
"""
import json

import pytest

from flowgate.graph_read import service as graph_read
from flowgate.graph_run import execution_store as ledger, workspace
from modules.flow_gate.graph_workflow.services import run_service
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services.git import merge_target
from test_graph_attempt_workspaces_0061 import PROJECT, harness, repo, prepare  # noqa: F401


@pytest.fixture(params=[workspace.BRANCH_LAYOUT, workspace.REF_LAYOUT])
def layout(request, monkeypatch):
    monkeypatch.setattr(workspace, "NEW_LAYOUT", request.param)
    return request.param


def _root(item):
    return workspace.location(workspace.attempt(item.attempt), PROJECT)[1]


def _invalidate(repo, item, reason="card_recovery"):
    """As a reopen / recovery does: the execution ends, the attempt is invalidated, its tree goes through cleanup."""
    ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL, "
                     "invalidated_reason=? WHERE attempt_id=?", [reason, item.attempt])


def _dirty(root):
    (root / "value.txt").write_text("work in progress", encoding="utf-8")
    (root / "added.txt").write_text("new file", encoding="utf-8")


def _records(item, mode=workspace.UNCARRIED):
    return [json.loads(e["payload_json"]) for e in run_store.events("run")
            if e["cause"] == workspace.CAUSE and json.loads(e["payload_json"]).get("attempt_id") == item.attempt
            and json.loads(e["payload_json"]).get("mode") == mode]


def _wire(item):
    return workspace.uncarried_wire("run", item.attempt)["uncarried_changes"]


def _again(repo, node="a"):
    """The node opened again as a new attempt (the 0061 harness names a node's token ``token-<node>``: the old
    attempt's mapping is renamed first)."""
    repo.db._execute("UPDATE graph_run_token_map SET token_id = token_id || '-' || attempt_id WHERE token_id = ?",
                     ["token-" + node])
    return repo.new(node)


# -- an invalidated attempt's kept dirty tree ---------------------------------------------------------------------------

def test_new_attempt_records_earlier_dirty_attempt_not_carried(repo, layout):
    first = repo.new("a")
    old_root = prepare(first)
    _dirty(old_root)
    _invalidate(repo, first)
    assert workspace.cleanup(first.attempt) is False          # D-1a-0 keeps the dirty tree (unchanged)
    second = _again(repo)
    new_root = prepare(second)
    # Not carried: the new tree starts from the input, the old one still holds the changes.
    assert (new_root / "value.txt").read_text(encoding="utf-8") == "initial"
    assert not (new_root / "added.txt").exists()
    assert (old_root / "value.txt").read_text(encoding="utf-8") == "work in progress"
    [record] = _records(second)
    assert record["changed_files"] == 2
    assert record["input_source"] == "run_base"
    assert record["input_commit"] == workspace.attempt(second.attempt)["input_commit"]
    [prior] = record["prior_attempts"]
    assert prior["attempt_id"] == first.attempt
    assert prior["invalidated_reason"] == "card_recovery"
    assert prior["kept"] == "worktree"
    assert prior["changed_files"] == 2
    assert sorted(prior["paths"]) == ["added.txt", "value.txt"]
    assert _wire(second) == {"attempts": 1, "files": 2, "unknown": False}
    # The mode record stays the attempt's first workspace event (``_mode``, ``_run_base`` read it).
    assert workspace._mode(workspace.attempt(second.attempt))["mode"] == "worktree"
    assert not repo.held


def test_marker_pins_the_notice_for_a_recovery(repo):
    first = repo.new("a")
    old_root = prepare(first)
    _dirty(old_root)
    _invalidate(repo, first)
    workspace.cleanup(first.attempt)
    second = _again(repo)
    workspace._materialize(workspace.attempt(second.attempt), PROJECT)   # Git done, journal not
    marker = merge_target.read_owner_marker(workspace.location(workspace.attempt(second.attempt), PROJECT)[0])
    assert [p["attempt_id"] for p in marker["uncarried"]] == [first.attempt]
    (old_root / "value.txt").write_text("initial", encoding="utf-8")    # the old tree changes meanwhile
    prepare(second)
    [record] = _records(second)
    assert record["prior_attempts"][0]["changed_files"] == 2            # as pinned with the input


def test_every_earlier_dirty_attempt_is_listed_oldest_first(repo):
    first = repo.new("a")
    _dirty(prepare(first))
    _invalidate(repo, first)
    workspace.cleanup(first.attempt)
    second = _again(repo)
    (prepare(second) / "other.txt").write_text("second", encoding="utf-8")
    _invalidate(repo, second, "stage_reopen")
    workspace.cleanup(second.attempt)
    third = _again(repo)
    prepare(third)
    [record] = _records(third)
    assert [(p["attempt_id"], p["changed_files"]) for p in record["prior_attempts"]] == [
        (first.attempt, 2), (second.attempt, 1)]
    assert record["changed_files"] == 3
    assert _wire(third) == {"attempts": 2, "files": 3, "unknown": False}


# -- nothing to tell ------------------------------------------------------------------------------------------------------

def test_clean_earlier_attempt_tells_nothing(repo):
    first = repo.new("a")
    prepare(first)
    _invalidate(repo, first)
    assert workspace.cleanup(first.attempt) is True           # a clean tree is removed (unchanged)
    second = _again(repo)
    prepare(second)
    assert _records(second) == []
    assert _wire(second) is None
    marker = merge_target.read_owner_marker(workspace.location(workspace.attempt(second.attempt), PROJECT)[0])
    assert "uncarried" not in marker


def test_another_nodes_dirty_attempt_is_not_this_cards(repo):
    other = repo.new("b")
    _dirty(prepare(other))
    _invalidate(repo, other)
    workspace.cleanup(other.attempt)
    card = repo.new("a")
    prepare(card)
    assert _records(card) == []


def test_rework_of_the_same_attempt_tells_nothing(repo):
    item = repo.new("a")
    root = prepare(item)
    _dirty(root)
    owner = ledger.acquire(item.execution, "ai")
    ledger.end(item.execution, owner)
    item.execution = ledger.request(item.attempt, "run", "rework", {})
    assert prepare(item) == root
    assert _records(item) == []
    assert _wire(item) is None


# -- a user_stop snapshot -------------------------------------------------------------------------------------------------

def test_user_stop_snapshot_is_told_when_its_tree_is_gone(repo, layout):
    first = repo.new("a")
    old_root = prepare(first)
    _dirty(old_root)
    _invalidate(repo, first, "user_stop")
    assert workspace.cleanup_for_stop(first.attempt)
    assert not old_root.exists()                               # snapshot taken, tree removed (v02gui.0013)
    second = _again(repo)
    prepare(second)
    [record] = _records(second)
    [prior] = record["prior_attempts"]
    assert prior["kept"] == "preserved_ref"
    assert prior["ref"].startswith(workspace.PRESERVED_REF_PREFIX + merge_target.workspace_key(first.attempt) + "/")
    assert prior["changed_files"] == 2
    assert sorted(prior["paths"]) == ["added.txt", "value.txt"]
    assert repo.git("show", prior["ref"] + ":value.txt") == "work in progress"
    assert _wire(second) == {"attempts": 1, "files": 2, "unknown": False}


def test_clean_user_stop_tells_nothing(repo):
    first = repo.new("a")
    prepare(first)
    _invalidate(repo, first, "user_stop")
    assert workspace.cleanup_for_stop(first.attempt)
    second = _again(repo)
    prepare(second)
    assert _records(second) == []


# -- a read that fails is told as unknown, never stops the preparation ---------------------------------------------------

def test_unreadable_earlier_attempt_is_unknown_and_preparation_goes_on(repo, monkeypatch):
    first = repo.new("a")
    _dirty(prepare(first))
    _invalidate(repo, first)
    workspace.cleanup(first.attempt)
    monkeypatch.setattr(workspace, "_left_over", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    second = _again(repo)
    assert prepare(second) is not None
    [record] = _records(second)
    assert record["prior_attempts"][0]["kept"] == "unknown"
    assert record["prior_attempts"][0]["changed_files"] is None
    assert record["changed_files"] == 0
    assert _wire(second) == {"attempts": 1, "files": 0, "unknown": True}


def test_failed_status_is_unknown(repo, monkeypatch):
    first = repo.new("a")
    old_root = prepare(first)
    _dirty(old_root)
    _invalidate(repo, first)
    workspace.cleanup(first.attempt)
    real = workspace.git._run_git

    def failing(args, cwd=None, **kw):
        if "status" in args and cwd is not None and str(old_root) in str(cwd):
            return type("P", (), {"returncode": 128, "stdout": "", "stderr": "boom"})()
        return real(args, cwd=cwd, **kw)
    monkeypatch.setattr(workspace.git, "_run_git", failing)
    second = _again(repo)
    prepare(second)
    [prior] = _records(second)[0]["prior_attempts"]
    assert (prior["kept"], prior["changed_files"]) == ("worktree", None)


# -- the run read ----------------------------------------------------------------------------------------------------------

def test_run_read_carries_the_count(repo):
    first = repo.new("a")
    _dirty(prepare(first))
    _invalidate(repo, first)
    workspace.cleanup(first.attempt)
    second = _again(repo)
    prepare(second)
    row = {"run_id": "run", "node_key": "a"}
    assert run_service.uncarried_changes(row, second.attempt) == {
        "uncarried_changes": {"attempts": 1, "files": 2, "unknown": False}}
    assert run_service.uncarried_changes(row, first.attempt) == {"uncarried_changes": None}
    assert run_service.uncarried_changes(row, None) == {"uncarried_changes": None}
    out = graph_read.node_run({"node_key": "a", "status": "open",
                               "uncarried_changes": {"attempts": 1, "files": 2, "unknown": False}}, set())
    assert out["uncarried_changes"] == {"attempts": 1, "files": 2, "unknown": False}
    assert graph_read.node_run({"node_key": "a", "status": "open"}, set())["uncarried_changes"] is None


def test_porcelain_paths_reads_renames_and_spaces():
    text = "R  new name.txt\0old name.txt\0 M value.txt\0?? dir/added file.txt\0"
    assert workspace._porcelain_paths(text) == ["new name.txt", "value.txt", "dir/added file.txt"]
    assert workspace._porcelain_paths("") == []
