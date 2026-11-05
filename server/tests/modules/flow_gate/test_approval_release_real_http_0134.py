"""0134 T#2: real bare origin, production SQLite, HTTP and Runner.
Only selected Git command failures and a deleted-ref no-op are injected. The concurrency
case widens the real push window with Events and counts actual commit/merge/push calls.
"""
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.modules.flow_gate import test_freeze_stage_and_real_selfcheck_overlap_0671 as h
from tests.modules.flow_gate.test_freeze_stage_and_real_selfcheck_overlap_0671 import (  # noqa: F401
    db_path, patch_store, project, origin_repo,
)


@pytest.fixture(scope="module", autouse=True)
def live_instance(patch_store):
    """Bind the instance heartbeat to this module's SQLite store.

    Other suites shut down their isolated instance; its cached identity cannot be
    reused against this database. The real lease checks must stay enabled.
    """
    from modules.flow_gate.services.git import instance_registry as registry
    from modules.flow_gate.db import connection
    import sys
    # group_store may first import workflow modules while connection.get_store is
    # patched. Rebind imported getters to the real store, including HTTP threads.
    live_get_store = connection.get_store
    with pytest.MonkeyPatch.context() as fixture_patch:
        for name, module in list(sys.modules.items()):
            if name.startswith("modules.flow_gate.") and hasattr(module, "get_store"):
                fixture_patch.setattr(module, "get_store", live_get_store)
        registry.shutdown()
        registry.startup()
        try:
            yield
        finally:
            registry.shutdown()


def run_one(group):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner as runner
    job = h._job(group)
    get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                        ["2000-01-01T00:00:00+09:00", job["job_id"]])
    cr = runner.try_claim(job["job_id"])
    assert cr is not None
    runner.execute(cr)


def make_group(n):
    from modules.flow_gate.services import git_service as gs
    group = f"{h.PROJECT}.default.{n:04d}"
    _, ac, _ = h._seed_final_approval_group(group)
    assert gs.ensure_worktree(h.PROJECT, "default", group) == "ok"
    (h._worktree(group) / f"release_{n}.txt").write_text("work\n", encoding="utf8")
    return group, ac


def action(client, job_id, ac, name):
    return client.post(f"/api/v1/documents/approval-jobs/{job_id}/{name}", json={"doc_id": ac})


@h.needs_git
def test_real_pin_retry_after_publish_exhaustion_then_same_release_clears(origin_repo, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import approval_freeze as af, job_store as jobs
    from modules.flow_gate.db import operation_job as db
    group, ac = make_group(1134)
    origin_before = h._origin_head(origin_repo)
    original = gs._run_git
    pin_command = af.run_in_m
    deletes = []
    calls = []

    def fail_auth(args, **kw):
        calls.append(list(args))
        if list(args[:2]) == ["fetch", "origin"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: Authentication failed")
        return original(args, **kw)

    def no_delete(ctx, project, repo, args, allowed=("update-ref",)):
        if args[:2] == ["update-ref", "-d"]:
            deletes.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        return pin_command(ctx, project, repo, args, allowed)

    monkeypatch.setattr(gs, "_run_git", fail_auth)
    monkeypatch.setattr(af, "run_in_m", no_delete)
    status, payload = h.approve_http(h.api(), ac)
    assert status == 200 and h._job(group)["status"] == "retry_wait", payload
    # Exhaust publish's count independently of the persistent error's second observation.
    from modules.flow_gate.db.connection import get_store
    get_store()._execute("UPDATE operation_job SET attempt_count = ? WHERE job_id = ?",
                        [jobs.retry_max_attempts(), h._job(group)["job_id"]])
    run_one(group)
    job = h._job(group)
    assert job["status"] == "freeze_wait" and job["attempt_count"] == jobs.retry_max_attempts()
    assert json.loads(job["release_intent"])["retry_count"] == 1
    assert deletes == [["update-ref", "-d", job["pin_ref"], job["frozen_sha"]]]
    assert af._read_pin(h._base(), job["pin_ref"]) == job["frozen_sha"]
    assert gs.approval_git_in_flight(group) is True
    assert db.get_group_freeze_claim(group)["job_id"] == job["job_id"]
    assert h._origin_head(origin_repo) == origin_before
    n_calls = len(calls)
    monkeypatch.setattr(af, "run_in_m", pin_command)
    run_one(group)
    assert h._job(group)["status"] == "failed"
    assert af._read_pin(h._base(), job["pin_ref"]) is None
    assert db.get_group_freeze_claim(group) is None
    assert gs.approval_git_in_flight(group) is False
    # Settlement and pin deletion don't enter the publish body again.
    assert not any(a[:2] == ["fetch", "origin"] or a[:1] in (["commit"], ["push"])
                   for a in calls[n_calls:])
    assert h._review_status(ac) == "pending_review"


@h.needs_git
def test_duplicate_requests_and_concurrent_cancel_of_fetched_wait_have_no_second_effect(origin_repo, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import approval_freeze as af
    from modules.flow_gate.db import operation_job as db
    group, ac = make_group(1135)
    origin_before = h._origin_head(origin_repo)
    original = gs._run_git
    calls = []

    def fail_fetch(args, **kw):
        calls.append(list(args))
        # The fetched phase is durable before base ff-only sync; a fetch failure itself
        # is still in admission. Fail the sync to exercise the incident's fetched wait.
        if list(args[:2]) == ["merge", "--ff-only"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: Could not resolve host: example.invalid")
        return original(args, **kw)

    monkeypatch.setattr(gs, "_run_git", fail_fetch)
    client = h.api()
    status, payload = h.approve_http(client, ac)
    assert status == 200, payload
    job = h._job(group)
    assert (job["status"], job["phase"]) == ("retry_wait", "fetched")
    assert payload["job"]["cancelable"] is True
    before = len(calls)
    status2, payload2 = h.approve_http(client, ac)
    assert status2 == 200 and payload2["job"]["job_id"] == job["job_id"]
    assert len(calls) == before
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: action(h.api(), job["job_id"], ac, "cancel"), range(2)))
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert db.get_group_freeze_claim(group)["job_id"] == job["job_id"]
    assert gs.approval_git_in_flight(group) is True
    run_one(group)
    done = h._job(group)
    assert done["job_id"] == job["job_id"] and done["status"] == "cancelled"
    assert done["lease_token"] is None
    assert af._read_pin(h._base(), job["pin_ref"]) is None
    assert h._origin_head(origin_repo) == origin_before
    assert h._review_status(ac) == "pending_review"
    assert not any(a[:1] == ["push"] for a in calls)
    log = h._git(["log", "--all", "--format=%H", "--fixed-strings", "--grep=FlowGate-Freeze: " + job["job_id"]], cwd=h._base())
    assert len(log.strip().splitlines()) == 1


@h.needs_git
def test_live_push_refuses_cancel_and_duplicate_approval_commits_and_pushes_once(origin_repo, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.db import operation_job as db
    from modules.flow_gate.db import git_concurrency as gc
    group, ac = make_group(1136)
    original = gs._run_git
    entered, proceed = threading.Event(), threading.Event()
    calls = []

    def pause_push(args, **kw):
        calls.append(list(args))
        if list(args[:3]) == ["push", "origin", "main"]:
            entered.set()
            assert proceed.wait(60), "test did not release the live push"
        return original(args, **kw)

    monkeypatch.setattr(gs, "_run_git", pause_push)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(h.approve_http, h.api(), ac)
        try:
            assert entered.wait(120), "approval did not reach real push"
            job = h._job(group)
            assert job["status"] == "running" and job["phase"] == "push_started"
            assert job["lease_token"] and gs.approval_git_in_flight(group)
            refused = action(h.api(), job["job_id"], ac, "cancel")
            assert refused.status_code == 409, refused.text
            assert not h._job(group)["cancel_requested"]
            # The second real HTTP request sees the same unfinished job.
            status2, payload2 = h.approve_http(h.api(), ac)
            assert status2 == 200 and payload2["job"]["job_id"] == job["job_id"], payload2
            assert db.get_group_freeze_claim(group)["job_id"] == job["job_id"]
        finally:
            proceed.set()
        status, payload = first.result(timeout=120)
    assert status == 200 and payload["approval"]["approved"] is True, payload
    assert h._job(group)["status"] == "succeeded"
    assert len([a for a in calls if a[:3] == ["push", "origin", "main"]]) == 1
    assert len([a for a in calls if "commit" in a]) == 1  # F2 absorb
    assert len([a for a in calls if "merge" in a and "--no-ff" in a]) == 1
    assert h._origin_head(origin_repo) == h._rev(h._base(), "HEAD")
    assert gc.list_locks_in_scope(h.PROJECT) == []
    h._drive_jobs(group)
