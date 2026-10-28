"""0111 T#1 (NR 0003 §5.4 D-1a-0): attempt cleanup removes a tree and branch only once its source is kept elsewhere.

Real temporary Git repositories (the 0061 T#5 harness: SQLite ledger, real linked worktrees, mocked locks / Flow
guard). Kept: ⓐ a modified tracked file, ⓑ an untracked file only, ⓒ a commit no lasting ref reaches, ⓓ a requested /
running execution, ⓔ a failed status / rev-list (or a broken tree / owner marker) -- by the immediate cleanup and by
ⓕ two server starts (``cleanup_finished``), each a ``cleanup_preserved`` event. Removed: ⓖ a clean tree with no
commit of its own, ⓗ an integrated clean attempt, its result commit kept on the group branch.
"""
import json
from types import SimpleNamespace

import pytest

from flowgate.graph_run import execution_store as ledger, workspace
from modules.flow_gate.graph_workflow.store import run_store
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT  # noqa: F401 — the 0061 fixtures

COMMIT = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit")


def _git(cwd, *args):
    proc = workspace.git._run_git(list(args), cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _attempt(repo, *, status="invalidated", live=False):
    """A prepared attempt whose execution ended (``live`` keeps it requested), then invalidated (or ended)."""
    item = repo.new()
    item.root = prepare(item)
    if not live:
        ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET status=?, invalidated_reason=? WHERE attempt_id=?",
                     [status, "card_recovery" if status == "invalidated" else None, item.attempt])
    item.directory, _root, item.branch = workspace.location(workspace.attempt(item.attempt), PROJECT)
    item.at = lambda *args: _git(item.root, *args)
    return item


def _preserved(repo, attempt_id):
    payloads = [json.loads(e["payload_json"]) for e in run_store.events("run")]
    return [(p["reason"], p["detail"]) for p in payloads
            if p.get("mode") == workspace.PRESERVED and p.get("attempt_id") == attempt_id]


def _modes(repo, attempt_id):
    return [json.loads(e["payload_json"]).get("mode") for e in run_store.events("run")
            if json.loads(e["payload_json"]).get("attempt_id") == attempt_id]


def _branch(repo, item):
    return workspace.git._ref_exists(repo.group, "refs/heads/" + item.branch)


def _kept(repo, item):
    return item.directory.exists() and (item.directory / workspace.merge_target.OWNER_MARKER).exists() \
        and _branch(repo, item)


def _gone(repo, item):
    return not item.directory.exists() and not _branch(repo, item)


def _fail_git(monkeypatch, word):
    original = workspace.git._run_git

    def failing(args, **kw):
        if word in args:
            return SimpleNamespace(returncode=128, stdout="", stderr="injected " + word)
        return original(args, **kw)
    monkeypatch.setattr(workspace.git, "_run_git", failing)


def _dirty_tracked(repo, item, _mp):
    (item.root / "value.txt").write_text("work in progress", encoding="utf-8")


def _untracked_only(repo, item, _mp):
    (item.root / "notes" / "draft.txt").parent.mkdir()
    (item.root / "notes" / "draft.txt").write_text("draft", encoding="utf-8")


def _unique_commit(repo, item, _mp):
    (item.root / "value.txt").write_text("committed work", encoding="utf-8")
    item.at("add", "value.txt")
    item.at(*COMMIT, "-m", "attempt work")


def _commit_on_another_attempt_only(repo, item, mp):
    _unique_commit(repo, item, mp)
    _git(repo.group, "branch", workspace.merge_target.GRAPH_ATTEMPT_BRANCH_PREFIX + "other", item.at("rev-parse", "HEAD"))


def _requested(repo, item, _mp):
    repo.db._execute("UPDATE graph_attempt_runs SET status='requested' WHERE exec_id=?", [item.execution])


def _running(repo, item, _mp):
    repo.db._execute("UPDATE graph_attempt_runs SET status='running' WHERE exec_id=?", [item.execution])


def _status_fails(repo, item, mp):
    _fail_git(mp, "status")


def _rev_list_fails(repo, item, mp):
    _fail_git(mp, "rev-list")


def _broken_link(repo, item, _mp):
    (item.root / ".git").unlink()


def _owner_mismatch(repo, item, _mp):
    path = item.directory / workspace.merge_target.OWNER_MARKER
    marker = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**marker, "attempt_id": "foreign"}), encoding="utf-8")


# (case, arrange, reason, judgements): the immediate cleanup and both starts judge it -- except a marker naming another
# attempt, which no start reads as this attempt's tree (it is never removed either).
KEPT = [
    ("a_dirty_tracked", _dirty_tracked, ("dirty", "status"), 3),
    ("b_untracked_only", _untracked_only, ("dirty", "status"), 3),
    ("c_unique_commit", _unique_commit, ("unpreserved_commits", "rev_list"), 3),
    ("c_commit_on_another_attempt_only", _commit_on_another_attempt_only, ("unpreserved_commits", "rev_list"), 3),
    ("d_requested", _requested, ("executing", "execution_active"), 3),
    ("d_running", _running, ("executing", "execution_active"), 3),
    ("e_status_fails", _status_fails, ("unverifiable", "status_failed"), 3),
    ("e_rev_list_fails", _rev_list_fails, ("unverifiable", "rev_list_failed"), 3),
    ("e_broken_worktree_link", _broken_link, ("unverifiable", "worktree_missing"), 3),
    ("e_owner_marker_mismatch", _owner_mismatch, ("unverifiable", "owner_mismatch"), 1),
]


@pytest.mark.parametrize("case,arrange,reason,judgements", KEPT, ids=[k[0] for k in KEPT])
def test_kept_by_the_immediate_cleanup_and_by_two_server_starts(repo, monkeypatch, case, arrange, reason, judgements):
    """ⓐ-ⓔ, ⓕ: nothing is removed, each judgement is one ``cleanup_preserved`` with its reason."""
    item = _attempt(repo)
    arrange(repo, item, monkeypatch)
    assert workspace.cleanup(item.attempt) is False
    assert _kept(repo, item) and item.root.exists()
    for _start in range(2):
        workspace.cleanup_finished()
        assert _kept(repo, item) and item.root.exists()
    assert _preserved(repo, item.attempt) == [reason] * judgements
    assert "cleanup_failed" not in _modes(repo, item.attempt)
    assert workspace.attempt(item.attempt)["status"] == "invalidated"
    assert not repo.held
    if case == "a_dirty_tracked":
        assert (item.root / "value.txt").read_text(encoding="utf-8") == "work in progress"


def test_kept_attempt_is_removed_once_its_source_is_kept_elsewhere(repo):
    """ⓕ the retry is the same check only: committed and then held by a user branch, the next start removes it."""
    item = _attempt(repo)
    _unique_commit(repo, item, None)
    workspace.cleanup_finished()
    assert _kept(repo, item)
    sha = item.at("rev-parse", "HEAD")
    _git(repo.group, "branch", "user/keep", sha)
    workspace.cleanup_finished()
    assert _gone(repo, item)
    assert _git(repo.group, "rev-parse", "user/keep") == sha
    assert _preserved(repo, item.attempt) == [("unpreserved_commits", "rev_list")]


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


def _integrated(repo, *, dirty=False):
    item = _attempt(repo, status="ended")
    (item.root / "result.txt").write_text("result", encoding="utf-8")
    item.at("add", "result.txt")
    item.at(*COMMIT, "-m", "attempt result")
    result = item.at("rev-parse", "HEAD")
    repo.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "merge", "--no-ff", "-m", "integrate attempt", item.branch)
    repo.db._execute("UPDATE graph_node_attempts SET integration_status='integrated', result_commit=? "
                     "WHERE attempt_id=?", [result, item.attempt])
    if dirty:
        (item.root / "result.txt").write_text("edited after the result", encoding="utf-8")
    return item, result


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


def test_integrated_dirty_attempt_is_preserved_not_a_cleanup_failure(repo):
    """ⓐ on the integrated path: ``cleanup_ended`` keeps it as ``cleanup_preserved(dirty)``, never ``cleanup_failed``;
    two starts keep it too."""
    item, _result = _integrated(repo, dirty=True)
    assert workspace.cleanup_ended(item.attempt) is False
    workspace.cleanup_finished()
    workspace.cleanup_finished()
    assert _kept(repo, item)
    assert (item.root / "result.txt").read_text(encoding="utf-8") == "edited after the result"
    assert _preserved(repo, item.attempt) == [("dirty", "status")] * 3
    assert "cleanup_failed" not in _modes(repo, item.attempt)


def test_active_and_ended_unintegrated_attempts_are_not_candidates(repo):
    """D-1a-0 does not widen what is a candidate: an active or ended (not integrated) attempt records nothing."""
    active = repo.new("active")
    prepare(active)
    ended = _attempt(repo, status="ended")
    for item in (active, ended):
        assert workspace.cleanup(item.attempt) is False
        assert _preserved(repo, item.attempt) == []
    workspace.cleanup_finished()
    assert _kept(repo, ended)


def test_git_busy_keeps_the_attempt_as_unverifiable(repo, monkeypatch):
    """The Git locks not taken: nothing is checked, nothing removed, ``cleanup_preserved(unverifiable)``."""
    item = _attempt(repo)
    monkeypatch.setattr(workspace.locks, "acquire", lambda *a, **kw: SimpleNamespace(ok=False, lock_key=None))
    assert workspace.cleanup(item.attempt) is False
    assert _kept(repo, item)
    assert _preserved(repo, item.attempt) == [("unverifiable", "git_busy")]
