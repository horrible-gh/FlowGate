"""Independently selectable scenarios; shared fixtures remain in test_graph_attempt_cleanup_preserve_0111.py."""
from __future__ import annotations

from tests.regression.graph_workflow.test_graph_attempt_cleanup_preserve_0111 import (
    COMMIT,
    KEPT,
    PROJECT,
    SimpleNamespace,
    _attempt,
    _branch,
    _broken_link,
    _commit_on_another_attempt_only,
    _commit_on_another_attempt_ref_only,
    _dirty_tracked,
    _fail_git,
    _git,
    _gone,
    _head_of,
    _integrated,
    _kept,
    _modes,
    _owner_mismatch,
    _preserved,
    _requested,
    _rev_list_fails,
    _running,
    _status_fails,
    _unique_commit,
    _untracked_only,
    harness,
    json,
    layout,
    ledger,
    prepare,
    pytest,
    repo,
    run_store,
    workspace,
)

@pytest.mark.parametrize("path", ["immediate", "server_start"])
def test_clean_tree_without_own_commit_is_removed(repo, path):
    """ⓖ a clean invalidated attempt whose branch is still its input (the group branch holds it)."""
    item = _attempt(repo)
    if path == "immediate":
        assert workspace.cleanup(item.attempt) is True
    else:
        workspace.cleanup_finished()
    assert _gone(repo, item) and not item.root.exists()
    assert _preserved(repo, item.attempt) == []
    assert not repo.held
def test_commit_merged_into_the_group_branch_and_ignored_files_do_not_keep_the_tree(repo):
    """ⓖ a commit the group branch holds is kept elsewhere; ignored files are not judged (they go with the tree)."""
    (repo.group / ".gitignore").write_text("*.log\n", encoding="utf-8")
    repo.git("add", ".gitignore")
    repo.git(*COMMIT, "-m", "ignore logs")
    item = _attempt(repo)
    _unique_commit(repo, item, None)
    sha = item.at("rev-parse", "HEAD")
    repo.git("merge", "--ff-only", sha)
    (item.root / "run.log").write_text("ignored output", encoding="utf-8")
    assert item.at("status", "--porcelain", "--untracked-files=all") == ""
    assert workspace.cleanup(item.attempt)
    assert _gone(repo, item)
    assert repo.git("rev-parse", "HEAD") == sha
@pytest.mark.parametrize("path", ["result_merge", "server_start"])
def test_integrated_clean_attempt_is_removed_and_its_result_stays_on_the_group_branch(repo, path):
    """ⓗ ``cleanup_ended`` (result merge) and ``cleanup_finished`` (start) remove it; the result stays a parent."""
    item, result = _integrated(repo)
    if path == "result_merge":
        assert workspace.cleanup_ended(item.attempt) is True
    else:
        workspace.cleanup_finished()
    assert _gone(repo, item) and not item.root.exists()
    assert workspace.git._run_git(["merge-base", "--is-ancestor", result, "group"], cwd=repo.group).returncode == 0
    assert (repo.group / "result.txt").read_text(encoding="utf-8") == "result"
    assert _preserved(repo, item.attempt) == [] and "cleanup_failed" not in _modes(repo, item.attempt)
