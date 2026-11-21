"""Independently selectable scenarios; shared fixtures remain in test_merge_abort_unblock_v02test_0052.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_merge_abort_unblock_v02test_0052 import (
    ADMIN,
    MEMBER,
    Path,
    Proj,
    _COUNTER,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _age_session,
    _assert_abort_audited,
    _conflict_in_group_worktree,
    _ctx,
    _git,
    _git_path,
    _group_branch_sha,
    _hold_index_lock,
    _merge_head,
    _resolve_everything,
    _rewrite_same_bytes_later,
    _routes_client,
    _seed_wf_done_root,
    _session,
    _stale_state,
    _stat_dirty,
    _two_file_conflict,
    base64,
    contextmanager,
    itertools,
    os,
    patch_store,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    time,
)

def test_force_cleanup_admin_only_resets_target_workspace_and_keeps_group_branch(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError

    gid = proj.group(8)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], False)
    # A real local edit on top of the staged resolution: `merge --abort` (reset --merge)
    # refuses to lose it, and no stat refresh changes that.
    (root / "shared.txt").write_bytes(b"hand edit\n")
    with pytest.raises(GitServiceError) as caught:
        svc.abort_merge(gid, merge_id)
    assert "not uptodate" in caught.value.details["reason"]

    head = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()
    group_sha = _group_branch_sha(proj, gid)
    origin_develop = proj.origin_sha("develop")
    url = f"/api/v1/groups/{gid}/git/merge/{merge_id}/force-cleanup"

    assert _routes_client(MEMBER).get(url).status_code == 403
    assert _routes_client(MEMBER).post(url).status_code == 403
    assert _session(merge_id)["status"] == "open"

    preview = _routes_client(ADMIN).get(url)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["result"]
    assert plan["workspace"] == str(root) and plan["head"] == head
    assert plan["target_branch"] == "develop" and plan["merge_head"]
    assert "shared.txt" in plan["discarded_paths"] and plan["unmerged_paths"] == ["other.txt"]
    assert "not uptodate" in plan["abort_failure"]["reason"]

    done = _routes_client(ADMIN).post(url)
    assert done.status_code == 200, done.text
    result = done.json()["result"]
    assert result["status"] == "aborted" and result["forced"] is True
    assert result["workspace"] == str(root) and result["reset_head"] == head
    assert result["target_branch"] == "develop" and result["branch_preserved"] is True
    assert "not uptodate" in result["abort_reason"]
    assert set(result["discarded_paths"]) >= {"shared.txt", "other.txt"}
    session = _session(merge_id)
    assert session["status"] == "aborted"
    assert db_git.get_state(gid)["status"] == "waiting"
    assert _group_branch_sha(proj, gid) == group_sha          # group branch untouched
    assert proj.origin_sha("develop") == origin_develop       # nothing pushed
    assert not root.exists() or _merge_head(root) is None
    # a closed session cannot be force-cleaned again
    assert _routes_client(ADMIN).post(url).status_code == 409
def test_force_cleanup_refuses_the_shared_base_checkout(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(9)
    merge_id = _two_file_conflict(proj, gid, branch="main")["merge_id"]
    response = _routes_client(ADMIN).post(f"/api/v1/groups/{gid}/git/merge/{merge_id}/force-cleanup")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "force_cleanup_unsupported_target"
    assert _session(merge_id)["status"] == "open"
    svc.abort_merge(gid, merge_id)
def test_list_conflicts_does_not_touch_the_session(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(10)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    _age_session(merge_id, hours=30)
    before = _session(merge_id)["touched_at"]
    listed = svc.list_conflicts(gid, merge_id)
    assert listed["abort_failure"] is None
    assert _session(merge_id)["touched_at"] == before
    svc.abort_merge(gid, merge_id)
def test_opening_the_merge_review_does_not_touch_the_session(proj):
    """task 4(c): the review GET (``get_merge_review``) is a read — touched_at, the
    sweep TTL basis, stays where the last real operation put it; a review conversation
    turn (a real operation) does move it."""
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(11)
    merge_id, _root = _resolve_everything(proj, gid)
    _age_session(merge_id, hours=30)
    before = _session(merge_id)["touched_at"]

    for _ in range(3):                                    # a screen left open and polled
        review = svc.get_merge_review(gid, merge_id)
        assert review["result"]["review_state"] == svc.REVIEW_STATE_PENDING
        assert review["result"]["can_approve"] is True
    assert _session(merge_id)["touched_at"] == before

    svc.send_review_message(
        gid, merge_id, message="왜 이렇게 합쳤나요?", provider_id=None,
        provider_pinned=True, apply_requested=False, start_run=lambda: "aiv_t0052_review",
    )
    assert _session(merge_id)["touched_at"] != before
