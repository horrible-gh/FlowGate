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
def test_F_workspace_has_a_single_owner(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    merge_a = _conflict_on(proj, gid_a, "develop")["merge_id"]
    ws = proj.workspace("develop")
    marker_before = (ws / "owner.json").read_text(encoding="utf-8")
    conflict_before = (ws / "tree" / "shared.txt").read_text(encoding="utf-8")

    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_busy"
    assert caught.value.details["merge_id"] == merge_a

    # 2nd check (post-lock) is the real guard: let the pre-lock check pass once.
    calls = {"n": 0}
    real = mt.raise_if_workspace_unavailable

    def racing(project_id, branch):
        calls["n"] += 1
        if calls["n"] == 1:
            return "free"
        return real(project_id, branch)

    monkeypatch.setattr(mt, "raise_if_workspace_unavailable", racing)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_busy"
    assert calls["n"] == 2
    assert (ws / "owner.json").read_text(encoding="utf-8") == marker_before
    assert (ws / "tree" / "shared.txt").read_text(encoding="utf-8") == conflict_before
    assert _session(merge_a)["status"] == "open"
    from modules.flow_gate.db import git_integration as db_git
    assert db_git.get_open_session_by_group(gid_b) is None     # no attempt was written

