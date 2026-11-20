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
def test_target_carrier_through_route_and_approval_seam(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import inbox_routes  # noqa: F401 — installs the archive seam
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.workflow.routers.workflow import DocumentBodyRequest

    assert getattr(svc, "_flowgate_git_archive_installed", False)
    assert DocumentBodyRequest(doc_id="x", git_action="merge", git_target_branch="develop").git_target_branch == "develop"

    gid1 = proj.group(1)
    gid2 = proj.group(2)
    proj.local_branch("develop")
    proj.local_branch("release")
    (proj.wt(gid1) / "r1.txt").write_text("1\n", encoding="utf-8")
    (proj.wt(gid2) / "r2.txt").write_text("2\n", encoding="utf-8")
    proj.ready(gid1)
    proj.ready(gid2)

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/api/v1/groups/{gid1}/git/finalize",
        json={"action": "merge", "git_target_branch": "develop"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["target_branch"] == "develop"
    assert "r1.txt" in proj.origin_files("develop")

    assert svc.precheck_approve_git_target(gid2, "merge", "release") == "release"
    outcome = svc.run_approve_git_action(gid2, "merge", "release")
    assert outcome["ok"] is True, outcome
    assert outcome["result"]["target_branch"] == "release"
    assert "r2.txt" in proj.origin_files("release")
    assert "r1.txt" not in proj.origin_files("main") and "r2.txt" not in proj.origin_files("main")

