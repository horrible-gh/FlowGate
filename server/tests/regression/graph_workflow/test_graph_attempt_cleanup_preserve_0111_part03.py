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
