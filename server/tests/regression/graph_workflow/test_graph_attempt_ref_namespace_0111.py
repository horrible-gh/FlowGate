"""0111 T#8 (NR 0003 §5.4 D-1c): a new attempt's dedicated ref and detached worktree, beside the branch layout.

Real temporary Git repositories on the 0061 T#5 harness (SQLite ledger, real linked worktrees, mocked locks / Flow
guard). A new attempt has no ``refs/heads`` entry from its preparation to its removal (``for-each-ref refs/heads`` and
``git branch --all`` unchanged; the catalog never lists it); its commit, result merge and removal succeed as the branch
layout's do (its Self-check: test_graph_attempt_workspaces_0061, both layouts). An attempt prepared before keeps its
``graph_attempt_*`` branch beside it and is never migrated. D-1a-0 ⓐ-ⓕ in the new namespace: the immediate cleanup and
two starts in test_graph_attempt_cleanup_preserve_0111 (both layouts), the invalidation entries here.
"""
import json
from pathlib import Path

import pytest

from flowgate.graph_run import execution_store as ledger, result_merge, workspace
from modules.flow_gate.services import graph_source_hooks as port, remote_tool_service as remote
from modules.flow_gate.services.git import branches, commit as git_commit
from modules.flow_gate.services.git.credentials import GitServiceError
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT, GROUP  # noqa: F401 — the 0061 fixtures
from test_graph_attempt_cleanup_preserve_0111 import (
    COMMIT, _git, _preserved, _kept, _gone, _head_of, _dirty_tracked, _unique_commit,
)
from test_graph_attempt_invalidation_catalog_0111 import PATHS, _invalidate, catalog  # noqa: F401

REF, BRANCH = workspace.REF_LAYOUT, workspace.BRANCH_LAYOUT


@pytest.fixture(autouse=True)
def ref_layout(monkeypatch):
    monkeypatch.setattr(workspace, "NEW_LAYOUT", REF)
    monkeypatch.setattr(git_commit, "_author_env_from_cfg", lambda _cfg: None)


def _item(repo, node="a"):
    item = repo.new(node)
    item.root = prepare(item)
    item.directory, _root, item.branch = workspace.location(workspace.attempt(item.attempt), PROJECT)
    item.head = _head_of(item)
    item.input = workspace.attempt(item.attempt)["input_commit"]
    item.at = lambda *args: _git(item.root, *args)
    return item


def _invalidated(repo, item):
    ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET status='invalidated', invalidated_reason='card_recovery' "
                     "WHERE attempt_id=?", [item.attempt])


def _heads(repo):
    return repo.git("for-each-ref", "--format=%(refname)", "refs/heads")


def _attempt_refs(repo):
    return repo.git("for-each-ref", "--format=%(refname)", workspace.ATTEMPT_REF_PREFIX)


def _detached(root):
    return workspace.git._run_git(["symbolic-ref", "-q", "HEAD"], cwd=root).returncode == 1


def _group_commit(repo, name, text):
    (repo.group / name).write_text(text, encoding="utf-8")
    repo.git("add", name)
    repo.git(*COMMIT, "-m", "group " + name)
    return repo.git("rev-parse", "HEAD")


def _commit(item, layout=REF):
    return git_commit.create_attempt_commit(
        PROJECT, item.root, item.branch, item.input, item.attempt, "doc", "feat(graph): attempt result",
        **({"ref": item.head} if layout == REF else {}))


def _reason(call):
    with pytest.raises(port.GraphSourceError) as refused:
        call()
    return refused.value.details.get("reason")


def test_new_attempt_takes_a_dedicated_ref_and_a_detached_tree_never_a_branch(repo):
    heads, listed = _heads(repo), repo.git("branch", "--all", "--format=%(refname)")
    item = _item(repo)
    assert item.head == workspace.ATTEMPT_REF_PREFIX + item.directory.name
    assert repo.git("rev-parse", item.head) == item.input == item.at("rev-parse", "HEAD")
    assert _detached(item.root)
    marker = json.loads((item.directory / workspace.merge_target.OWNER_MARKER).read_text(encoding="utf-8"))
    assert marker["ref"] == item.head and "branch" not in marker
    assert _heads(repo) == heads and repo.git("branch", "--all", "--format=%(refname)") == listed
    assert not workspace.git._ref_exists(repo.group, "refs/heads/" + item.branch)
    owned = [e for e in branches._registered_worktrees_for_delete(repo.group)
             if Path(e["path"]).resolve() == item.root.resolve()]
    assert len(owned) == 1 and owned[0]["branch"] is None
    # Source routing on the detached tree, as on a branch-layout one.
    assert port.source_root(item.token) == item.root
    remote._execute("write", {"path": "value.txt", "content": "ref layout"}, port.source_root(item.token))
    assert (item.root / "value.txt").read_text(encoding="utf-8") == "ref layout"
    assert (repo.group / "value.txt").read_text(encoding="utf-8") == "initial"
    assert ("W", item.branch) in repo.lock_calls and not repo.held


def test_tree_ahead_of_its_ref_is_valid_a_diverged_attached_or_refless_one_is_not(repo):
    item = _item(repo)
    _unique_commit(repo, item, None)                       # a commit made in the tree, the ref still at the input
    assert port.source_root(item.token) == item.root
    moved = _group_commit(repo, "other.txt", "elsewhere")
    repo.git("update-ref", item.head, moved)               # the ref where HEAD does not descend from
    assert _reason(lambda: port.source_root(item.token)) == "ref_mismatch"
    repo.git("update-ref", item.head, item.input)
    item.at("checkout", "-q", "-b", "user/attached")       # HEAD on a branch again
    assert _reason(lambda: port.source_root(item.token)) == "ref_mismatch"
    item.at("checkout", "-q", "--detach")
    assert port.source_root(item.token) == item.root
    repo.git("update-ref", "-d", item.head)
    assert _reason(lambda: port.source_root(item.token)) == "ref_missing"


def test_attempt_commit_moves_the_ref_and_recovers_a_lost_update(repo):
    item = _item(repo)
    heads = _heads(repo)
    (item.root / "value.txt").write_text("result", encoding="utf-8")
    first = _commit(item)
    assert first["committed"] and first["committed_paths"] == ["value.txt"]
    assert repo.git("rev-parse", item.head) == first["commit_sha"] == item.at("rev-parse", "HEAD")
    assert _detached(item.root) and item.at("rev-parse", "HEAD^") == item.input
    again = _commit(item)
    assert again["recovered"] and again["commit_sha"] == first["commit_sha"]
    # A lost ref update (the commit made, update-ref not): the tree stays valid and the retry moves the ref.
    repo.git("update-ref", item.head, item.input)
    assert port.source_root(item.token) == item.root
    retry = _commit(item)
    assert retry["recovered"] and repo.git("rev-parse", item.head) == first["commit_sha"]
    assert int(item.at("rev-list", "--count", item.input + "..HEAD")) == 1
    assert _heads(repo) == heads
    assert not repo.held


def test_attempt_commit_refuses_a_ref_it_does_not_follow_and_layouts_do_not_cross(repo):
    item = _item(repo)
    (item.root / "value.txt").write_text("result", encoding="utf-8")
    moved = _group_commit(repo, "other.txt", "elsewhere")
    repo.git("update-ref", item.head, moved)
    assert _commit(item)["skipped_reason"] == "attempt_ref_mismatch"
    repo.git("update-ref", item.head, item.input)
    # Without its ref (the branch-layout call), a detached tree is no attempt branch.
    assert _commit(item, layout=BRANCH)["skipped_reason"] == "attempt_branch_mismatch"
    assert item.at("rev-parse", "HEAD") == item.input == repo.git("rev-parse", item.head)
    assert item.at("status", "--porcelain") == "M value.txt"


def _merge_ready(repo, monkeypatch):
    monkeypatch.setattr(result_merge, "_group_root", lambda row: (PROJECT, repo.group, "group"))
    monkeypatch.setattr(result_merge.git_commit, "_acquire_group_lock", lambda *a, **kw: ("ctx", "G"))
    monkeypatch.setattr(result_merge.git_commit, "_release_group_lock", lambda lock: None)
    monkeypatch.setattr(result_merge.git.db_git, "get_open_session_by_group", lambda _flow: None)
    monkeypatch.setattr(result_merge.git, "get_branch_merge_group_claim", lambda _flow: None)
    monkeypatch.setattr(result_merge, "_gate_snapshot_reserved", lambda _flow: False)


@pytest.mark.parametrize("layout", [REF, BRANCH])
def test_commit_result_merge_and_removal_match_between_layouts(repo, monkeypatch, layout):
    """The same attempt life in both layouts: prepared, committed, merged into the group branch with its result a
    parent, then removed by ``cleanup_ended``. Only the branch layout ever shows in ``refs/heads``."""
    monkeypatch.setattr(workspace, "NEW_LAYOUT", layout)
    _merge_ready(repo, monkeypatch)
    heads = _heads(repo)
    item = _item(repo)
    (item.root / "result.txt").write_text("result\n", encoding="utf-8")
    outcome = _commit(item, layout=layout)
    result = outcome["commit_sha"]
    assert outcome["committed"] and repo.git("rev-parse", item.head) == result
    assert _heads(repo) == (heads if layout == REF else "\n".join(sorted([heads, "refs/heads/" + item.branch])))
    ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET result_commit=?, integration_status='integrating' "
                     "WHERE attempt_id=?", [result, item.attempt])
    status, merged = result_merge._merge(workspace.attempt(item.attempt))
    assert status == result_merge.INTEGRATED
    assert result in repo.git("rev-list", "--parents", "-n", "1", merged).split()[1:]
    assert repo.git("log", "-1", "--format=%s", merged) == (
        f"Merge attempt ref '{item.head}' into group" if layout == REF else f"Merge branch '{item.branch}' into group")
    repo.db._execute("UPDATE graph_node_attempts SET status='ended', integration_status='integrated' "
                     "WHERE attempt_id=?", [item.attempt])
    assert workspace.cleanup_ended(item.attempt) is True
    assert _gone(repo, item) and not item.root.exists() and not item.directory.exists()
    assert _heads(repo) == heads and _attempt_refs(repo) == ""
    assert workspace.git._is_ancestor(repo.group, result, "group") is True
    assert (repo.group / "result.txt").read_text(encoding="utf-8") == "result\n"
    assert _preserved(repo, item.attempt) == [] and not repo.held


def test_branch_layout_attempt_keeps_working_beside_a_new_one(repo, monkeypatch):
    """3단계: an attempt prepared before T#8 is re-prepared, committed and removed on its branch; never moved to a
    ref, renamed or given one. The new attempt beside it takes the ref."""
    monkeypatch.setattr(workspace, "NEW_LAYOUT", BRANCH)
    old = _item(repo, "old")
    marker_path = old.directory / workspace.merge_target.OWNER_MARKER
    old_marker = marker_path.read_text(encoding="utf-8")
    monkeypatch.setattr(workspace, "NEW_LAYOUT", REF)
    new = _item(repo, "new")
    assert old.head == "refs/heads/" + old.branch and new.head.startswith(workspace.ATTEMPT_REF_PREFIX)
    owner = ledger.acquire(old.execution, "ai")
    ledger.end(old.execution, owner)
    old.execution = ledger.request(old.attempt, "run", "review", {})
    assert prepare(old) == old.root                        # the review reuses it as it was
    assert marker_path.read_text(encoding="utf-8") == old_marker
    assert old.at("symbolic-ref", "--short", "HEAD") == old.branch
    assert not workspace.git._ref_exists(repo.group, workspace.ATTEMPT_REF_PREFIX + old.directory.name)
    assert workspace.attempt_head(workspace.attempt(old.attempt), PROJECT) is None
    for item, layout in ((old, BRANCH), (new, REF)):
        (item.root / (item.branch + ".txt")).write_text("result", encoding="utf-8")
        outcome = _commit(item, layout=layout)
        assert outcome["committed"] and repo.git("rev-parse", item.head) == outcome["commit_sha"]
        repo.git(*COMMIT[:-1], "merge", "--no-ff", "-m", "integrate", outcome["commit_sha"])
    assert _heads(repo) == "\n".join(sorted(["refs/heads/group", "refs/heads/" + old.branch]))
    for item in (old, new):
        _invalidated(repo, item)
    workspace.cleanup_finished()
    assert _gone(repo, old) and _gone(repo, new)
    assert _heads(repo) == "refs/heads/group" and _attempt_refs(repo) == ""


@pytest.mark.parametrize("dirty", [True, False], ids=["a_dirty_kept", "g_clean_removed"])
@pytest.mark.parametrize("path", PATHS)
def test_invalidation_entries_judge_a_ref_layout_attempt_by_d1a0(repo, monkeypatch, path, dirty):
    """D-1a-0 at each invalidation entry (T#7) for the new namespace: kept with its ref, or removed with it."""
    item = _item(repo)
    heads = _heads(repo)
    if dirty:
        _dirty_tracked(repo, item, monkeypatch)
    _invalidate(repo, item, monkeypatch, path)
    assert workspace.attempt(item.attempt)["status"] == "invalidated"
    if dirty:
        assert _kept(repo, item) and repo.git("rev-parse", item.head) == item.input
        assert _preserved(repo, item.attempt) == [("dirty", "status")]
        assert (item.root / "value.txt").read_text(encoding="utf-8") == "work in progress"
    else:
        assert _gone(repo, item) and _attempt_refs(repo) == ""
    assert _heads(repo) == heads and not repo.held


@pytest.mark.parametrize("broken,detail", [("diverged", "ref_mismatch"), ("attached", "ref_mismatch"),
                                           ("ref_missing", "ref_missing")])
def test_ref_layout_tree_that_does_not_match_its_ref_is_preserved(repo, broken, detail):
    """ⓔ in the new namespace: a tree whose HEAD left its ref (or the ref itself gone) cannot be told -- kept."""
    item = _item(repo)
    _invalidated(repo, item)
    if broken == "diverged":
        repo.git("update-ref", item.head, _group_commit(repo, "other.txt", "elsewhere"))
    elif broken == "attached":
        item.at("checkout", "-q", "-b", "user/attached")
    else:
        repo.git("update-ref", "-d", item.head)
    for _judgement in range(2):
        assert workspace.cleanup(item.attempt) is False
        assert item.root.exists() and (item.directory / workspace.merge_target.OWNER_MARKER).exists()
    assert _preserved(repo, item.attempt) == [("unverifiable", detail)] * 2


def test_a_foreign_ref_in_the_namespace_is_never_taken_or_removed(repo):
    """A ref already at the attempt's name with no marker: ``workspace_occupied`` -- it is neither reused nor deleted."""
    item = repo.new()
    key = workspace.merge_target.workspace_key(item.attempt)
    foreign = _group_commit(repo, "foreign.txt", "foreign")
    repo.git("update-ref", workspace.ATTEMPT_REF_PREFIX + key, foreign)
    with pytest.raises(port.GraphSourceError):
        prepare(item)
    assert workspace.attempt(item.attempt)["status"] == "invalidated"
    assert repo.git("rev-parse", workspace.ATTEMPT_REF_PREFIX + key) == foreign
    assert not (workspace.git.get_storage_root() / "graph_attempts" / PROJECT / key).exists()


@pytest.mark.parametrize("conflict", [False, True], ids=["merged", "integration_head"])
def test_join_moves_the_ref_with_the_tree(repo, conflict):
    """T#10's Join in the new namespace: the ref ends at the final input -- the merged results, or the group branch
    HEAD after a conflict (not a descendant of the first input) -- and the tree is valid there."""
    item = _item(repo)
    sides = []
    for text in ("left", "right"):
        name = "value.txt" if conflict else text + ".txt"
        (item.root / name).write_text(text, encoding="utf-8")
        item.at("add", name)
        item.at(*COMMIT, "-m", "join side " + text)
        sides.append(item.at("rev-parse", "HEAD"))
        item.at("reset", "-q", "--hard", item.input)
    moved = _group_commit(repo, "group.txt", "moved")
    directory, root, name, marker = workspace._marker(workspace.attempt(item.attempt), PROJECT)
    marker = {**marker, "input_commit": sides[0], "join_inputs": [sides[1]]}
    final = workspace._join(workspace.attempt(item.attempt), directory, root, name, marker, repo.group)
    head = item.at("rev-parse", "HEAD")
    assert repo.git("rev-parse", item.head) == final["input_commit"] == head
    if conflict:
        assert final["input_source"] == "integration_head" and head == moved
    else:
        assert item.at("rev-parse", "HEAD^1") == sides[0] and item.at("rev-parse", "HEAD^2") == sides[1]
        assert repo.git("log", "-1", "--format=%s", head) == f"Merge join input {sides[1]} into {item.head}"
    assert "join_inputs" not in final and _detached(item.root)
    workspace._validate_tree(root, name, final)


def test_catalog_and_branch_inputs_never_see_a_new_attempt(catalog):
    workspace.git.create_branch(PROJECT, "feature/one", "group")
    names = {r["name"] for r in branches.list_branches(PROJECT)["branches"]}
    listed = catalog.git("branch", "--all", "--format=%(refname)")
    item = _item(catalog)
    _unique_commit(catalog, item, None)
    rows = branches.list_branches(PROJECT)["branches"]
    assert {r["name"] for r in rows} == names == {"group", "feature/one"}
    assert not any(r["kind"] == "internal_attempt" for r in rows)
    assert catalog.git("branch", "--all", "--format=%(refname)") == listed
    # Neither the dedicated ref nor the attempt's W name is a branch a person can pick.
    for name in (item.head, item.branch, item.head[len("refs/"):]):
        with pytest.raises(GitServiceError):
            workspace.git.create_branch(PROJECT, "must-not-exist", name)
        with pytest.raises(GitServiceError):
            branches._validate_merge_branch(PROJECT, catalog.group, name, "source")
    assert not workspace.git._ref_exists(catalog.group, "refs/heads/must-not-exist")
    assert catalog.git("rev-parse", item.head) == item.input
