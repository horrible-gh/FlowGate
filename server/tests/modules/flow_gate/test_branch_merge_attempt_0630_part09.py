"""Independently selectable scenarios; shared fixtures remain in test_branch_merge_attempt_0630.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_branch_merge_attempt_0630 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _OTHER_GROUP,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _approve,
    _busy,
    _client,
    _crashed_branch_merge,
    _ctx,
    _end_attempt_job,
    _git,
    _plan_finalize,
    _prepare_route_conflict,
    _resolve_all,
    _review_pending,
    _session,
    _view,
    base64,
    contextmanager,
    copy,
    itertools,
    mock_db,
    os,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    uuid,
)

def test_0683_approve_from_review_completes_and_releases_the_target(proj):
    """(5) review approve → merge complete → session closed → workspace released."""
    p = proj
    merge_id, before = _review_pending(p)
    result = _approve(p, merge_id)
    assert result["status"] == "merged"
    assert _session(merge_id)["status"] == "done"
    assert p.sha("refs/heads/develop") != before
    assert not (p.workspace("develop") / "tree").exists()
    assert _plan_finalize(p, "develop").target_branch == "develop"
def test_0683_abort_from_review_releases_the_target(proj):
    """(6) the review stage can abort: merge --abort → aborted → release → resolver stopped."""
    from modules.flow_gate.services import git_service as svc
    p = proj
    merge_id, before = _review_pending(p)
    result = svc.abort_merge(None, merge_id, project_id=p.pid)["result"]
    assert result["status"] == "aborted" and result["workspace_cleaned"] is True
    assert _session(merge_id)["status"] == "aborted"
    assert _ctx(merge_id)["attempt_state"] == "aborted"
    assert p.sha("refs/heads/develop") == before
    assert not (p.workspace("develop") / "tree").exists()
    assert _plan_finalize(p, "develop").target_branch == "develop"
def test_0683_reject_keeps_the_workspace_and_the_claim(proj):
    """(7) reject is resolver rework, not a release: the target stays claimed."""
    from modules.flow_gate.services import git_service as svc
    p = proj
    merge_id, _before = _review_pending(p)
    result = svc.reject_merge_review(
        None, merge_id, reason="again", provider_id="prov", provider_pinned=True,
        start_run=lambda first_message: "run-0683b", project_id=p.pid,
    )["result"]
    assert result["status"] == "returned_to_resolver"
    assert _session(merge_id)["status"] == "open"
    assert (p.workspace("develop") / "tree").is_dir()
    assert _busy(p).details["merge_id"] == merge_id
def test_0683_other_targets_are_not_blocked(proj):
    """(14) a claim on ``develop`` never blocks a plan onto ``release``."""
    p = proj
    _review_pending(p)
    p.branch("release")
    assert _plan_finalize(p, "release").target_branch == "release"
