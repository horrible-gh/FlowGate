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
def test_P_default_merge_target_route(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user

    proj.group(1)
    proj.local_branch("integration-v2")
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)

    response = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target",
        json={"branch": "integration-v2"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["default_merge_target"] == "integration-v2"
    catalog = client.get(f"/api/v1/projects/{proj.pid}/git/branches")
    assert catalog.json()["default_merge_target"] == "integration-v2"

    bad = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target",
        json={"branch": "does-not-exist"},
    )
    assert bad.status_code == 404, bad.text

    cleared = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target", json={},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["default_merge_target"] is None

