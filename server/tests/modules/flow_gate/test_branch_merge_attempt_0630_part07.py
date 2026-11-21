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

def test_same_target_cannot_start_a_second_attempt(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    p.branch("feature2")
    p.commit_on("feature2", {"f2.txt": "x\n"})
    with pytest.raises(GitServiceError) as caught:
        svc.merge_branches(p.pid, "feature2", "develop", push=False)
    assert caught.value.code == "merge_target_busy"
    assert caught.value.details["merge_id"] == out["merge_id"]
def test_group_and_project_addressing_never_cross(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    for call in (
        lambda: svc.list_conflicts(f"{p.pid}.default.0001", merge_id),
        lambda: svc.list_conflicts(None, merge_id, project_id="someone-else"),
        lambda: svc.get_merge_review(f"{p.pid}.none.0000", merge_id),
        lambda: svc.abort_merge(f"{p.pid}.default.0001", merge_id),
    ):
        with pytest.raises(GitServiceError) as caught:
            call()
        assert caught.value.status == 404
    assert svc.merge_route_prefix(merge_id) == f"/projects/{p.pid}/git/merge/{merge_id}"
    assert svc.merge_session_owner_args(merge_id, "ignored") == (None, p.pid)
def test_project_resolve_token_route_binds_project_merge_and_no_group(proj, monkeypatch):
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    consumed = []
    monkeypatch.setattr(git_routes.token_service, "consume", lambda tid, proj_id: consumed.append(tid))
    client = _client(monkeypatch)
    url = f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/resolve-token"
    payload = {"files": [{"path": "same.txt", "content": "ai merged\n"}], "complete": True}
    for auth in (
        {"_is_user_jwt": True},
        {"action_scope": "resolve_conflict", "group_id": f"{p.pid}.default.0001",
         "project": p.pid, "merge_id": merge_id, "token_id": "t"},
        {"action_scope": "resolve_conflict", "group_id": None, "project": "other",
         "merge_id": merge_id, "token_id": "t"},
        {"action_scope": "resolve_conflict", "group_id": None, "project": p.pid,
         "merge_id": merge_id + 1000, "token_id": "t"},
    ):
        monkeypatch.setattr(git_routes, "verify_bearer", lambda request, _a=auth: _a)
        assert client.post(url, json=payload).status_code == 403
    monkeypatch.setattr(git_routes, "verify_bearer", lambda request: {
        "action_scope": "resolve_conflict", "group_id": None, "project": p.pid,
        "merge_id": merge_id, "token_id": "tok-ok", "ai_run_id": "run-tok",
    })
    resp = client.post(url, json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["status"] == "resolved_pending_review"
    assert consumed == ["tok-ok"]
    assert _view(p, merge_id)["resolver_type"] == "ai"
def test_24_server_state_transitions(proj, monkeypatch):
    from modules.flow_gate.services.git import branch_merge
    p = proj
    out, _before = p.conflict(files=("same.txt", "other.txt"))
    merge_id = out["merge_id"]
    states = [_view(p, merge_id)["state"]]                          # conflict
    monkeypatch.setattr(branch_merge, "_ai_run_status", lambda run_id: "running")
    branch_merge.start_resolver(p.pid, merge_id, start_run=lambda prov, msgs: "run-24")
    states.append(_view(p, merge_id)["state"])                      # ai_resolving
    monkeypatch.setattr(branch_merge, "_ai_run_status", lambda run_id: "finished")
    from modules.flow_gate.services import git_service as svc
    svc.resolve_conflicts(None, merge_id, [{"path": "same.txt", "content": "m\n"}], False,
                          resolver_run_id="run-24", project_id=p.pid)
    states.append(_view(p, merge_id)["state"])                      # conflict_remaining
    svc.resolve_conflicts(None, merge_id, [{"path": "other.txt", "content": "m\n"}], True,
                          project_id=p.pid)
    view = _view(p, merge_id)
    states.append(view["state"])                                    # resolved_pending_review
    assert view["resolver_type"] == "mixed"                         # AI + a person
    _approve(p, merge_id)
    states.append(_view(p, merge_id)["state"])                      # completed
    assert states == ["conflict", "ai_resolving", "conflict_remaining",
                      "resolved_pending_review", "completed"]
