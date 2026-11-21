"""attempt cleanup removes a tree and branch only once its source is kept elsewhere.

Real temporary Git repositories (the the earlier implementation harness: SQLite ledger, real linked worktrees, mocked locks / Flow
guard). Kept: ⓐ a modified tracked file, ⓑ an untracked file only, ⓒ a commit no lasting ref reaches, ⓓ a requested /
running execution, ⓔ a failed status / rev-list (or a broken tree / owner marker) -- by the immediate cleanup and by
ⓕ two server starts (``cleanup_finished``), each a ``cleanup_preserved`` event. Removed: ⓖ a clean tree with no
commit of its own, ⓗ an integrated clean attempt, its result commit kept on the group branch.

every case runs in both attempt layouts (``layout``) -- the ``graph_attempt_*`` branch an attempt prepared
before keeps, and the dedicated ``refs/flowgate/attempts/<key>`` ref with a detached tree a new one takes. ``item.head``
is the ref the attempt's commits are on: a removal deletes it, a preservation keeps it.
"""
import json
from types import SimpleNamespace

import pytest

from flowgate.graph_run import execution_store as ledger, workspace
from modules.flow_gate.graph_workflow.store import run_store
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT  # noqa: F401 — the 0061 fixtures

COMMIT = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit")


@pytest.fixture(autouse=True, params=[workspace.BRANCH_LAYOUT, workspace.REF_LAYOUT])
def layout(request, monkeypatch):
    monkeypatch.setattr(workspace, "NEW_LAYOUT", request.param)
    return request.param


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
    item.head = _head_of(item)
    item.at = lambda *args: _git(item.root, *args)
    return item


def _head_of(item):
    """the attempt's own ref, read from its owner marker while it is there."""
    return workspace.attempt_head(workspace.attempt(item.attempt), PROJECT) or "refs/heads/" + item.branch


def _preserved(repo, attempt_id):
    payloads = [json.loads(e["payload_json"]) for e in run_store.events("run")]
    return [(p["reason"], p["detail"]) for p in payloads
            if p.get("mode") == workspace.PRESERVED and p.get("attempt_id") == attempt_id]


def _modes(repo, attempt_id):
    return [json.loads(e["payload_json"]).get("mode") for e in run_store.events("run")
            if json.loads(e["payload_json"]).get("attempt_id") == attempt_id]


def _branch(repo, item):
    return workspace.git._ref_exists(repo.group, item.head)


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


def _commit_on_another_attempt_ref_only(repo, item, mp):
    """a ref of the new attempt namespace is another attempt's too -- it keeps nothing."""
    _unique_commit(repo, item, mp)
    _git(repo.group, "update-ref", workspace.ATTEMPT_REF_PREFIX + "other", item.at("rev-parse", "HEAD"))


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
    ("c_commit_on_another_attempt_ref_only", _commit_on_another_attempt_ref_only,
     ("unpreserved_commits", "rev_list"), 3),
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


@pytest.mark.parametrize("repository_state", ["missing", "file"])
def test_invalid_marker_repository_preserves_without_starting_git(repo, monkeypatch, caplog, repository_state):
    item = _attempt(repo)
    marker_path = item.directory / workspace.merge_target.OWNER_MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    invalid_repo = item.directory / "stale-repository"
    if repository_state == "file":
        invalid_repo.write_text("keep this file", encoding="utf-8")
    marker_path.write_text(json.dumps({**marker, "repository": str(invalid_repo)}), encoding="utf-8")
    original = workspace.git._run_git
    calls = []

    def checked(args, **kwargs):
        if str(kwargs.get("cwd")) == str(invalid_repo):
            calls.append(args)
            raise NotADirectoryError("invalid repository cwd")
        return original(args, **kwargs)

    monkeypatch.setattr(workspace.git, "_run_git", checked)
    assert workspace.cleanup(item.attempt) is False
    for _start in range(2):
        workspace.cleanup_finished()
        assert _kept(repo, item) and item.root.exists()
    detail = "repository_missing" if repository_state == "missing" else "repository_not_directory"
    assert _preserved(repo, item.attempt) == [("unverifiable", detail)] * 3
    assert not calls
    assert "cleanup check failed" not in caplog.text
    assert "cleanup_failed" not in _modes(repo, item.attempt)
    if repository_state == "file":
        assert invalid_repo.read_text(encoding="utf-8") == "keep this file"
    assert not repo.held


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






def _integrated(repo, *, dirty=False):
    item = _attempt(repo, status="ended")
    (item.root / "result.txt").write_text("result", encoding="utf-8")
    item.at("add", "result.txt")
    item.at(*COMMIT, "-m", "attempt result")
    result = item.at("rev-parse", "HEAD")
    repo.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "merge", "--no-ff", "-m", "integrate attempt", result)
    repo.db._execute("UPDATE graph_node_attempts SET integration_status='integrated', result_commit=? "
                     "WHERE attempt_id=?", [result, item.attempt])
    if dirty:
        (item.root / "result.txt").write_text("edited after the result", encoding="utf-8")
    return item, result
