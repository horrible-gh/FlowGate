"""Selected finalize target: isolated scenario from the shared 0594 harness."""
from __future__ import annotations

from test_git_merge_target_0594 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _arm_crash,
    _assert_off_branch,
    _check_view,
    _conflict_on,
    _crash_finalize,
    _ctx,
    _end_job,
    _feature_group,
    _git,
    _judge,
    _legacy_group,
    _live_job,
    _merge_head,
    _merge_rows,
    _open_attempt,
    _recover_job_after_crash,
    _refused,
    _seed_wf_done_root,
    _session,
    base64,
    check_budget,
    contextmanager,
    held_locks,
    itertools,
    json,
    needs_git,
    os,
    patch_store,
    proj,
    pytest,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    uuid,
)


@needs_git
def test_J_non_base_unmerge_fails_closed_service_and_route(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "j.txt").write_text("j\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only", target_branch="develop")
    assert out["result"]["status"] == "merged" and out["result"]["pushed"] is False
    sha = out["result"]["merge_commit"]
    base_before = proj.base_sha()
    develop_before = _git(["rev-parse", "develop"], cwd=proj.base).strip()
    with pytest.raises(svc.GitServiceError) as caught:
        svc.unmerge(gid, sha)
    assert caught.value.code == "unmerge_unsupported_target"
    assert caught.value.details["target_branch"] == "develop"

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/api/v1/groups/{gid}/git/unmerge", json={"merge_commit": sha})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unmerge_unsupported_target"
    assert proj.base_sha() == base_before and proj.base_clean()
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() == develop_before
    # the base unmerge list never offers it
    status = svc.project_git_status(proj.pid)["status"]
    assert all(m.get("group_id") != gid for m in status["unpushed"]["merges"])

