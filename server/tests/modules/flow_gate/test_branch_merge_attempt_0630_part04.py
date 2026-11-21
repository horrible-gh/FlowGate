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

def test_11_approve_push_false_is_local_only(proj):
    p = proj
    out, before = p.conflict(push=False, publish=True)
    merge_id = out["merge_id"]
    remote_before = p.remote_sha("develop")
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged" and result["pushed"] is False
    head = p.sha("refs/heads/develop")
    assert head != before and _git(["rev-parse", f"{head}^1"], cwd=p.base).strip() == before
    assert _git(["show", f"{head}:same.txt"], cwd=p.base) == "same.txt merged\n"
    assert p.remote_sha("develop") == remote_before
    assert _session(merge_id)["status"] == "done"
    assert not (p.workspace("develop") / "tree").exists()
    view = _view(p, merge_id)
    assert view["state"] == "completed" and view["pushed"] is False and view["local_applied"] is True
def test_12_approve_push_true_publishes(proj):
    p = proj
    out, _before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged" and result["pushed"] is True
    assert p.remote_sha("develop") == p.sha("refs/heads/develop")
    assert _view(p, merge_id)["remote_applied"] is True
def test_12b_base_target_conflict_lives_in_a_detached_workspace_and_applies_on_approval(proj):
    p = proj
    out, before = p.conflict(target="main", push=False)
    merge_id = out["merge_id"]
    # the shared base checkout is untouched while the conflict waits
    assert _git(["status", "--porcelain"], cwd=p.base).strip() == ""
    assert p.sha("refs/heads/main") == before
    assert (p.workspace("main") / "tree").is_dir()
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged"
    assert p.sha("refs/heads/main") != before
    assert (p.base / "same.txt").read_text(encoding="utf-8") == "same.txt merged\n"
    assert _git(["status", "--porcelain"], cwd=p.base).strip() == ""
def test_13_stale_target_is_blocked_and_nothing_moves(proj):
    p = proj
    out, before = p.conflict(target="main", push=False)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    moved = p.commit_on("main", {"late.txt": "someone else\n"}, "concurrent change")
    from modules.flow_gate.services.git.credentials import GitServiceError as Err
    with pytest.raises(Err) as caught:
        _approve(p, merge_id)
    assert caught.value.code == "stale_target"
    assert p.sha("refs/heads/main") == moved
    assert _ctx(merge_id)["review_state"] == "resolved_pending_review"   # never left pending
