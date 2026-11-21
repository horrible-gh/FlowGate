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

def test_0683_live_in_progress_attempt_is_left_alone_at_plan_time(proj, monkeypatch):
    """(8) a running attempt (its job is not terminal) still serializes the target."""
    from modules.flow_gate.services.git import merge_target
    p = proj
    session = _crashed_branch_merge(p, monkeypatch)
    assert merge_target.attempt_phase(session) == merge_target.PHASE_IN_PROGRESS
    exc = _busy(p)
    assert exc.details["merge_id"] == session["merge_id"]
    assert exc.details["blocking_state"] == "starting"
    assert _session(session["merge_id"])["status"] == "open"
def test_0683_interrupted_attempt_is_reclaimed_at_plan_time(proj, monkeypatch):
    """(11) runner gone, no conflict: the next plan settles it at once (no sweep wait)."""
    from modules.flow_gate.services.git import merge_target
    p = proj
    session = _crashed_branch_merge(p, monkeypatch)
    _end_attempt_job(session)
    assert merge_target.attempt_phase(_session(session["merge_id"])) == merge_target.PHASE_INTERRUPTED
    planned = _plan_finalize(p, "develop")
    assert planned.target_branch == "develop"
    assert _session(session["merge_id"])["status"] == "aborted"
    assert _ctx(session["merge_id"])["attempt_state"] == "interrupted"
    assert not (p.workspace("develop") / "tree").exists()
    assert _view(p, session["merge_id"])["state"] == "interrupted"
def test_0683_interrupted_attempt_with_a_foreign_marker_fails_closed(proj, monkeypatch):
    """(13) ownership mismatch is never taken over, even for an interrupted attempt."""
    import json as _json
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    session = _crashed_branch_merge(p, monkeypatch)
    _end_attempt_job(session)
    marker_path = p.workspace("develop") / "owner.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(_json.dumps({
        "merge_id": session["merge_id"], "owner": "someone-else", "project_id": p.pid,
        "target_branch": "develop",
    }), encoding="utf-8")
    with pytest.raises(GitServiceError) as caught:
        _plan_finalize(p, "develop")
    assert caught.value.code == "merge_target_owner_mismatch"
    assert _session(session["merge_id"])["status"] == "open"
    assert marker_path.exists()
def test_0683_busy_lock_on_the_claim_skips_plan_time_recovery(proj, monkeypatch):
    """(13) recovery never waits for or steals a lock: a held domain keeps the refusal."""
    from modules.flow_gate.services.git import cleanup
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    session = _crashed_branch_merge(p, monkeypatch)
    _end_attempt_job(session)

    def busy_lock(*_a, **_k):
        raise GitServiceError(409, "git_busy", "busy")

    monkeypatch.setattr(cleanup, "session_lock", busy_lock)
    exc = _busy(p)
    assert exc.details["merge_id"] == session["merge_id"]
    assert _session(session["merge_id"])["status"] == "open"
@pytest.mark.parametrize("push", [False, True])
@pytest.mark.parametrize("resolver", ["manual", "ai"])
def test_0530_recovered_review_human_api_finishes_original_plan(proj, monkeypatch, push, resolver):
    p = proj
    out, before = p.conflict(push=push, publish=True)
    merge_id = out["merge_id"]
    run_id = "run-0530" if resolver == "ai" else None
    resolved = _resolve_all(p, merge_id, run_id=run_id)
    assert resolved["status"] == "resolved_pending_review"
    client = _client(monkeypatch)
    base = f"/api/v1/projects/{p.pid}/git/merge/{merge_id}"
    # Re-entry reads stored state, then the candidate; neither read moves refs.
    response = client.get(base)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["state"] == "resolved_pending_review"
    response = client.get(f"{base}/review")
    assert response.status_code == 200, response.text
    review = response.json()["result"]
    assert review["can_approve"] is True
    assert p.sha("refs/heads/develop") == before
    fingerprint = review["review_fingerprint"]
    stale = client.post(f"{base}/approve", json={
        "attempt_id": str(uuid.uuid4()), "review_fingerprint": "stale-0530"})
    assert stale.status_code == 409, stale.text
    assert p.sha("refs/heads/develop") == before
    response = client.post(f"{base}/approve", json={
        "attempt_id": str(uuid.uuid4()), "review_fingerprint": fingerprint})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == "merged" and result["pushed"] is push
    view = client.get(base).json()["result"]
    assert view["state"] == "completed"
    assert view["source_branch"] == "feature" and view["target_branch"] == "develop"
    assert view["push"] is push
    assert _session(merge_id)["status"] == "done"
    assert not (p.workspace("develop") / "tree").exists()
