"""Independently selectable scenarios; shared fixtures remain in test_approval_finalize_real_http_0671.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_approval_finalize_real_http_0671 import (
    PROJECT,
    PROJECT_NAME,
    Path,
    USER,
    _base,
    _branch,
    _drive_jobs,
    _git,
    _is_push_base,
    _job,
    _origin_head,
    _reject_push,
    _rev,
    _review_status,
    _seed_final_approval_group,
    _worktree,
    api,
    approve_http,
    needs_git,
    origin_repo,
    patch_store,
    project,
    pytest,
    subprocess,
    tmp_db,
)

@needs_git
class TestApprovalFinalizeRealHttp0671:

    def test_http_approve_queues_behind_push_rejection_then_real_runner_reclaims_it(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0802"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        (wt / "e.py").write_text("e = 1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", f"work {group}"], cwd=wt)

        base = _base()
        pre = _rev(base, "HEAD")
        origin_before = _origin_head(origin_repo)

        monkeypatch.setattr(svc, "_run_git", _reject_push(_is_push_base))
        client = api()
        status, payload = approve_http(client, ac_id)
        monkeypatch.undo()

        # a rejected push is a 5xx from origin, not a client error: the real route leaves the
        # approval queued (retry_wait), it does not fail the HTTP request or lose the intent.
        assert status == 200, payload
        assert payload["approval"]["approved"] is False
        assert payload["approval"]["stage"] == "queued" and payload["approval"]["deferred"] is True
        assert payload["block_reason"]["code"] == "push_rejected"
        assert _review_status(ac_id) == "pending_review"
        assert _origin_head(origin_repo) == origin_before
        assert _rev(base, "HEAD") == pre
        assert _job(group)["status"] == "retry_wait"

        # the real Job Runner's next claim (push works again) finishes the SAME approval --
        # the wait/blocked/reclaim contract, observed through real job state after the HTTP
        # request already returned.
        assert _drive_jobs(group) >= 1
        assert _job(group)["status"] == "succeeded"
        assert _review_status(ac_id) == "approved"
        assert _origin_head(origin_repo) == _rev(base, "HEAD") != origin_before
        assert not _worktree(group).exists()
