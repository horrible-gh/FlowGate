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


    @pytest.mark.parametrize("case,stage,returncode,stderr,code,cause,limit", [
        (3, "fetch", -1, "timeout_expired", "git_error", "timeout", None),
        (4, "fetch", 128, "fatal: Could not resolve host: git.example.invalid", "git_error", "network", None),
        (5, "merge", -1, "timeout_expired", "git_error", "timeout", None),
        (6, "merge", 128, "fatal: cannot lock ref 'refs/heads/main'", "git_error", "ref_lock", None),
        (7, "sync", -1, "timeout_expired", "git_error", "timeout", None),
        (8, "sync", 128, "fatal: Not possible to fast-forward, aborting.", "base_diverged", "conflict_other", 2),
        (9, "fetch", 128, "fatal: Authentication failed for 'https://example.invalid/repo'", "git_error", "auth", 2),
        (10, "merge", 128, "fatal: unexplained merge failure", "git_error", "unknown", 3),
        (11, "fetch", 128, "fatal: cannot lock ref 'refs/remotes/origin/main'", "git_error", "ref_lock", None),
    ])
    def test_http_stage_cause_retry_limits_and_per_attempt_audit(
        self, origin_repo, monkeypatch, case, stage, returncode, stderr, code, cause, limit,
    ):
        """real approval route, attempt ledger, claim/release and runner.

        Only the selected Git command's failure is injected. Both fetch and merge
        git_error remain retryable; ff-only timeout must not become divergence.
        """
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.{800 + case:04d}"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        ( _worktree(group) / f"policy_case_{case}.txt").write_text("work\n", encoding="utf-8")
        original = svc._run_git

        def fail_selected(args, **kwargs):
            selected = ((stage == "fetch" and args[:2] == ["fetch", "origin"])
                        or (stage == "sync" and args[:2] == ["merge", "--ff-only"])
                        or (stage == "merge" and "merge" in args and "--no-ff" in args))
            if selected:
                return subprocess.CompletedProcess(args, returncode, "", stderr)
            return original(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", fail_selected)
        status, payload = approve_http(api(), ac_id)
        assert status == 200, payload
        assert _job(group)["status"] == "retry_wait"
        assert payload["block_reason"]["code"] == code
        assert svc.approval_git_in_flight(group)
        if limit:
            assert _drive_jobs(group) == limit - 1
            assert (_job(group)["status"], _job(group)["last_error_code"]) == ("failed", code)
            assert not svc.approval_git_in_flight(group)
            assert _review_status(ac_id) == "pending_review"
        else:
            assert _review_status(ac_id) == "pending_review"

        attempts = [svc.db_git.session_context(row)["attempt_error"]
                    for row in svc.db_git.sessions_by_group(group)
                    if "attempt_error" in svc.db_git.session_context(row)]
        assert len(attempts) == (limit or 1)
        for error in attempts:
            assert (error["code"], error["stage"], error["cause"], error["diagnostic"]) == (
                code, "merge" if stage == "sync" else stage, cause, stderr)
        if limit is None:
            monkeypatch.setattr(svc, "_run_git", original)
            assert _drive_jobs(group) >= 1
            assert _job(group)["status"] == "succeeded"
            assert _review_status(ac_id) == "approved"

    def test_http_expired_retry_deadline_releases_without_another_publish_attempt(self, origin_repo, monkeypatch):
        import json
        from modules.flow_gate.db.connection import get_store
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0812"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        (_worktree(group) / "deadline.txt").write_text("work\n", encoding="utf-8")
        original = svc._run_git
        fetches = []

        def timeout_fetch(args, **kwargs):
            if args[:2] == ["fetch", "origin"]:
                fetches.append(args)
                return subprocess.CompletedProcess(args, -1, "", "timeout_expired")
            return original(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", timeout_fetch)
        status, payload = approve_http(api(), ac_id)
        assert status == 200, payload
        job = _job(group)
        assert job["status"] == "retry_wait"
        evidence = json.loads(job["evidence"])
        evidence["publish_retry"]["deadline_at"] = "2000-01-01T00:00:00+00:00"
        with get_store().transaction():
            get_store()._execute("UPDATE operation_job SET evidence = ? WHERE job_id = ?",
                                 [json.dumps(evidence), job["job_id"]])
        assert _drive_jobs(group) == 1
        assert len(fetches) == 1
        job = _job(group)
        assert job["status"] == "failed"
        assert json.loads(job["evidence"])["publish_retry"]["stop_reason"] == "deadline_exceeded"
        assert not svc.approval_git_in_flight(group)
        assert _review_status(ac_id) == "pending_review"
        attempts = [row for row in svc.db_git.sessions_by_group(group)
                    if "attempt_error" in svc.db_git.session_context(row)]
        assert len(attempts) == 1
