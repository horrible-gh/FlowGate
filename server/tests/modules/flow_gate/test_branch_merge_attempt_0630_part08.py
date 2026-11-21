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

def test_24b_reject_returns_to_resolver_with_a_new_run(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id, run_id="run-a")
    result = svc.reject_merge_review(
        None, merge_id, reason="keep both lines", provider_id="prov", provider_pinned=True,
        start_run=lambda first_message: "run-b", project_id=p.pid,
    )["result"]
    assert result["status"] == "returned_to_resolver" and result["resolver_run_id"] == "run-b"
    ctx = _ctx(merge_id)
    assert ctx["review_state"] is None and ctx["ai"]["run_id"] == "run-b"
    tree = p.workspace("develop") / "tree"
    assert "<<<<<<<" in (tree / "same.txt").read_text(encoding="utf-8")   # conflict restored
    assert "git_branch_merge_review_rejected" in [e["type"] for e in ctx["attempt_events"]]
def test_0683_review_pending_branch_merge_blocks_same_target_and_names_itself(proj):
    """(8, 9, 12) a live review wait keeps the same-target serialization; the refusal says
    WHICH merge holds the target, as a branch merge, with what the screen needs to open it."""
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    merge_id, _before = _review_pending(p)
    exc = _busy(p)
    d = exc.details
    assert d["merge_id"] == merge_id and d["target_branch"] == "develop"
    assert d["blocking_owner_type"] == "branch_merge" and d["blocking_group_id"] is None
    assert d["source_branch"] == "feature" and d["started_at"]
    assert d["blocking_state"] == "resolved_pending_review"
    assert d["review_state"] == "resolved_pending_review" and d["attempt_state"] == "conflict"
    assert d["project_id"] == p.pid
    assert d["route"] == f"/api/v1/projects/{p.pid}/git/merge/{merge_id}"
    # the wording follows the owner type — never "another finalize attempt" for a branch merge
    assert "finalize" not in exc.message and f"branch merge #{merge_id}" in exc.message
    # (12) the plan-time re-check never removes a person's pending review
    assert _session(merge_id)["status"] == "open"
    assert (p.workspace("develop") / "tree").is_dir()
    # a second branch merge into the same target is refused the same way
    p.branch("feature2")
    p.commit_on("feature2", {"f2.txt": "x\n"})
    with pytest.raises(GitServiceError) as caught:
        svc.merge_branches(p.pid, "feature2", "develop", push=False)
    assert caught.value.code == "merge_target_busy"
    assert caught.value.details["blocking_state"] == "resolved_pending_review"
def test_0683_conflict_wait_is_not_reclaimed_at_plan_time(proj):
    """(12) an unresolved conflict waiting for a resolver is a live attempt, not a dead one."""
    p = proj
    out, _before = p.conflict()
    exc = _busy(p)
    assert exc.details["blocking_state"] == "conflict"
    assert _session(out["merge_id"])["status"] == "open"
def test_0683_review_pending_persists_the_finished_ai_status(proj):
    """(16) entering review settles the stored AI status instead of leaving ``running``."""
    p = proj
    merge_id, _before = _review_pending(p)
    ai = _ctx(merge_id)["ai"]
    assert ai["status"] == "finished" and ai["run_id"] == "run-0683" and ai.get("finished_at")
    assert _view(p, merge_id)["ai"]["status"] == "finished"
