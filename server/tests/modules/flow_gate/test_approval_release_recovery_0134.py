"""0134 T#2: real SQLite job/claim/lease/locks; Git bodies are explicit stand-ins.
Real Git, pin deletion and concurrent HTTP requests are covered by the companion 0671 tests.
"""
import json
from types import SimpleNamespace

import pytest
from group_lock_stub import group_store, hold_lock  # noqa: F401
from approval_job_fakes import install


@pytest.fixture(scope="module", autouse=True)
def isolate_instance_and_imported_store_getters():
    """Do not leave a stopped instance or a lazily imported fixture DB behind."""
    import sys
    from modules.flow_gate.db import connection
    from modules.flow_gate.services.git import instance_registry as registry
    live_get_store = connection.get_store
    registry.shutdown()
    try:
        yield
    finally:
        registry.shutdown()
        # group_store restores getters it patched. Modules first imported inside
        # its scope can retain that lambda without an undo entry of their own.
        for name, module in list(sys.modules.items()):
            getter = getattr(module, "get_store", None)
            if (name.startswith("modules.flow_gate.")
                    and getattr(getter, "__module__", None) == "group_lock_stub"):
                setattr(module, "get_store", live_get_store)


@pytest.fixture
def frozen(group_store, monkeypatch):
    from modules.flow_gate.services.git import approval_freeze as af, approval_publish as ap
    from modules.flow_gate.services.git import job_store as jobs
    from modules.flow_gate.db import operation_job as db
    doc = {"doc_id": "flowgate.default.0134.0002-AC", "group_id": "flowgate.default.0134",
           "project_id": "flowgate", "type_code": "AC", "revision_no": 0,
           "doc_review_status": "pending_review"}
    fx = install(monkeypatch, group_store, project_id="flowgate", group_id=doc["group_id"], doc=doc)
    req = dict(doc_id=doc["doc_id"], doc_revision_no=0, git_action="merge", group_id=doc["group_id"])
    cr = jobs.create_or_get_job(ap.KIND, "flowgate", req, group_id=doc["group_id"],
                                payload={"doc_id": doc["doc_id"], "git_action": "merge",
                                         "approval_intent_id": "intent-0134"})
    assert cr.outcome == jobs.CREATED
    ctx = cr.ctx
    af.run_freeze(ctx)
    jobs.fenced_write(ctx, dict(status="retry_wait", phase="fetched", attempt_count=jobs.retry_max_attempts(),
                                freeze_record={"sha": "a" * 40, "completed": 1},
                                **jobs._LEASE_CLEARED), allow_freeze=True)
    # Unit-level settlement result; never stand in for the Git/pin integration assertions.
    monkeypatch.setattr(ap, "_resume", lambda ctx: None)
    monkeypatch.setattr(jobs, "next_delay", lambda a: 1)
    return SimpleNamespace(s=group_store, jobs=jobs, af=af, ap=ap, db=db, fx=fx, doc=doc,
                           job_id=ctx.job_id, monkeypatch=monkeypatch)


def row(f):
    return f.jobs._fresh_job(f.job_id)


def due(f):
    f.s._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", f.job_id])


def execute_release(f):
    from modules.flow_gate.services.git import job_runner as runner
    due(f)
    cr = runner.try_claim(f.job_id)
    assert cr is not None and cr.phase_entry == f.jobs.RELEASE
    runner.execute(cr)


def assert_locked(f):
    from modules.flow_gate.services import git_service as gs
    assert gs.approval_git_in_flight(f.doc["group_id"]) is True
    assert f.db.get_group_freeze_claim(f.doc["group_id"])["job_id"] == f.job_id
    assert f.ap.active_job_of_group(f.doc["group_id"])["job_id"] == f.job_id


def test_cleared_is_the_only_cancel_terminal_and_claim_clear(frozen):
    f = frozen
    assert f.ap.job_view(row(f))["cancelable"]
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    assert_locked(f)
    execute_release(f)
    assert row(f)["status"] == "cancelled"
    assert f.db.get_group_freeze_claim(f.doc["group_id"]) is None
    from modules.flow_gate.services import git_service as gs
    assert gs.approval_git_in_flight(f.doc["group_id"]) is False
    assert f.fx.git_actions == [] and f.fx.commits == []


@pytest.mark.parametrize("result", ["busy", "retry"])
def test_exhausted_publish_has_independent_bounded_release_budget(frozen, result):
    f = frozen
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (result, "pin_delete_not_applied"))
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    execute_release(f)
    assert row(f)["status"] == "freeze_wait"
    assert row(f)["attempt_count"] == f.jobs.retry_max_attempts()
    assert json.loads(row(f)["release_intent"])["retry_count"] == 1
    assert_locked(f)
    for _ in range(f.jobs.retry_max_attempts()):
        execute_release(f)
    parked = row(f)
    assert parked["status"] == "recovery_required"
    assert parked["last_error_code"] == "freeze_release_retry_exhausted"
    assert parked["lease_token"] is None
    assert_locked(f)
    assert f.fx.git_actions == []


@pytest.mark.parametrize("result", ["mismatch", "unverified"])
def test_uncertain_pin_keeps_claim_and_automatic_window_is_not_renewed(frozen, result):
    from modules.flow_gate.services.git import job_runner as runner, job_sweeper as sweeper
    f = frozen
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (result, "pin_points_elsewhere"))
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    execute_release(f)
    parked = row(f)
    assert parked["status"] == "recovery_required"
    assert_locked(f)
    due(f)
    assert sweeper.step_recovery_required_retry() == 1
    assert row(f)["recovery_since"] == parked["recovery_since"]
    assert row(f)["lease_token"] is None
    # At the end of the automatic window only an explicit manual judgement reopens it.
    intent = json.loads(row(f)["release_intent"])
    intent["recovery_since"] = "2000-01-01T00:00:00+09:00"
    f.s._execute("UPDATE operation_job SET recovery_since = ?, release_intent = ?, available_at = ? WHERE job_id = ?",
                 [intent["recovery_since"], json.dumps(intent), intent["recovery_since"], f.job_id])
    assert sweeper.step_recovery_required_retry() == 0
    assert f.jobs.request_approval_rejudge(f.job_id)
    assert not f.jobs.request_approval_rejudge(f.job_id)
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (f.af.RETRY, "pin_delete_not_applied"))
    execute_release(f)
    assert row(f)["status"] == "freeze_wait"
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (result, "pin_points_elsewhere"))
    execute_release(f)
    assert row(f)["status"] == "recovery_required"
    assert row(f)["recovery_since"] == "2000-01-01T00:00:00+09:00"
    assert sweeper.step_recovery_required_retry() == 0
    assert f.jobs.request_approval_rejudge(f.job_id)
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (f.af.CLEARED, None))
    execute_release(f)
    assert row(f)["status"] == "cancelled"


def test_g_and_m_busy_preserve_lock_until_same_release_finishes(frozen):
    f = frozen
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    with hold_lock("G", "flowgate", group_id=f.doc["group_id"]):
        execute_release(f)
        assert row(f)["status"] == "freeze_wait"
        assert_locked(f)
    # Pin release acquires M under G; the actual M acquisition, not a fabricated BUSY.
    f.monkeypatch.setattr(f.af, "_refs_repo", lambda project: "unused-by-stubbed-read")
    f.monkeypatch.setattr(f.af, "_read_pin", lambda repo, name: "a" * 40)
    # Restore the real release_pin (fixture install replaced it).
    # The real function is saved on the module before install by this test's wrapper below.
    f.monkeypatch.setattr(f.af, "release_pin", REAL_RELEASE_PIN)
    with hold_lock("M", "flowgate", holder_kind="approval_freeze"):
        execute_release(f)
        assert row(f)["status"] == "freeze_wait"
        assert row(f)["last_error_code"] == "pin_release_m_busy"
        assert_locked(f)
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (f.af.CLEARED, None))
    execute_release(f)
    assert row(f)["status"] == "cancelled"


# Capture before the fixture installs its no-repository stand-in.
from modules.flow_gate.services.git.approval_freeze import release_pin as REAL_RELEASE_PIN


def test_running_and_any_retained_lease_cannot_be_cancelled(frozen):
    f = frozen
    for phase in ("fetched", "push_started", "merged", "db_finalize"):
        f.s._execute("UPDATE operation_job SET status = 'running', phase = ? WHERE job_id = ?", [phase, f.job_id])
        assert not f.ap.job_view(row(f))["cancelable"]
        assert f.jobs.request_cancel(f.job_id) == "running"
        assert not row(f)["cancel_requested"]
    for field in ("lease_token", "lease_owner", "lease_until"):
        f.s._execute("UPDATE operation_job SET status = 'retry_wait', phase = 'fetched', " + field + " = ? WHERE job_id = ?",
                     ["retained", f.job_id])
        assert not f.ap.job_view(row(f))["cancelable"]
        assert f.jobs.request_cancel(f.job_id) == "retry_wait"
        f.s._execute("UPDATE operation_job SET " + field + " = NULL WHERE job_id = ?", [f.job_id])
    assert_locked(f)


def test_publish_claim_winning_race_refuses_cancel_and_stale_writer_is_fenced(frozen):
    f = frozen
    due(f)
    cr = f.jobs.claim(f.job_id, f.jobs.PUBLISH)
    assert cr.outcome == f.jobs.WON
    assert f.jobs.request_cancel(f.job_id) == "running"
    f.jobs.job_write(cr.ctx, status="retry_wait", **f.jobs._LEASE_CLEARED)
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    assert f.jobs.claim(f.job_id, f.jobs.PUBLISH).outcome == f.jobs.LOST
    with pytest.raises(f.jobs.LeaseLost):
        f.jobs.job_write(cr.ctx, phase="push_started")
    execute_release(f)
    assert f.fx.git_actions == []


def test_release_settles_landed_git_and_never_republishes(frozen):
    f = frozen
    f.monkeypatch.setattr(f.ap, "_resume", lambda ctx: {"ok": True, "terminal": True,
                      "result": {"status": "merged", "pushed": True, "merge_commit": "b" * 40}})
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    execute_release(f)
    assert row(f)["status"] == "succeeded"
    assert f.fx.commits == [f.doc["doc_id"]]
    assert f.fx.git_actions == []
    assert f.jobs.claim(f.job_id, f.jobs.PUBLISH).outcome == f.jobs.LOST


def test_unsettled_git_and_busy_publish_locks_never_drop_claim(frozen):
    f = frozen
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    with hold_lock("R", "flowgate"):
        execute_release(f)
        assert row(f)["status"] == "freeze_wait"
        assert_locked(f)
    f.monkeypatch.setattr(f.ap, "_resume", lambda ctx: {"ok": False, "recovery": True,
                                                    "error": {"code": "approval_attempt_unsettled"}})
    execute_release(f)
    assert row(f)["status"] == "recovery_required"
    assert_locked(f)
    assert f.fx.git_actions == [] and f.fx.commits == []


def test_release_intent_survives_restart_and_only_release_is_claimable(frozen):
    from modules.flow_gate.services.git import job_runner as runner
    f = frozen
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    due(f)
    cr = f.jobs.claim(f.job_id, f.jobs.RELEASE)
    assert cr.outcome == f.jobs.WON
    intent = row(f)["release_intent"]
    # A different token won reconcile after the old owner died. Model the won CAS.
    f.s._execute("UPDATE operation_job SET lease_token = 'reconcile' WHERE job_id = ?", [f.job_id])
    fresh = row(f)
    ctx = f.jobs._job_context(fresh, "reconcile", fresh["lease_until"])
    ctx.expected_statuses = frozenset(("freezing",))
    f.af.reconcile_freeze_expired(ctx)
    assert row(f)["release_intent"] == intent
    assert_locked(f)
    assert f.jobs.claim(f.job_id, f.jobs.PUBLISH).outcome == f.jobs.LOST
    execute_release(f)
    assert row(f)["status"] == "cancelled" and row(f)["lease_token"] is None
    assert f.fx.git_actions == []


def test_manual_routes_require_permission_document_binding_and_idle_recovery(frozen):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow
    from modules.flow_gate.services.git import job_sweeper as sweeper
    f = frozen
    app = FastAPI()
    app.include_router(workflow.router)
    user = {"user_id": "reviewer", "is_admin": False}
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    url = f"/api/v1/documents/approval-jobs/{f.job_id}/"
    body = {"doc_id": f.doc["doc_id"]}
    assert client.post(url + "cancel", json=body).status_code == 403
    user["is_admin"] = True
    assert client.post(url + "discard", json={"doc_id": "other-AC"}).status_code == 409
    assert client.post(url + "rejudge", json=body).status_code == 409
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', recovery_since = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", f.job_id])
    assert sweeper.step_recovery_required_retry() == 0
    response = client.post(url + "rejudge", json=body)
    assert response.status_code == 202 and response.json()["job"]["job_id"] == f.job_id
    assert row(f)["status"] == "retry_wait" and row(f)["phase"] == "fetched"
    assert_locked(f)
    f.s._execute("UPDATE operation_job SET status = 'recovery_required' WHERE job_id = ?", [f.job_id])
    response = client.post(url + "discard", json=body)
    assert response.status_code == 202 and response.json()["job"]["cancel_requested"]
    assert client.post(url + "cancel", json=body).status_code == 202
    assert_locked(f)
    execute_release(f)
    assert row(f)["status"] == "cancelled"


@pytest.mark.parametrize("uncertainty", ["foreign_session", "push_started"])
def test_release_without_proof_stays_in_recovery(frozen, uncertainty):
    from modules.flow_gate.services import git_service as gs
    f = frozen
    assert f.jobs.request_cancel(f.job_id) == f.jobs.CANCEL_PENDING
    if uncertainty == "foreign_session":
        f.monkeypatch.setattr(gs.db_git, "get_open_session_by_group", lambda group: {"merge_id": 123})
    else:
        f.s._execute("UPDATE operation_job SET phase = 'push_started' WHERE job_id = ?", [f.job_id])
    execute_release(f)
    assert row(f)["status"] == "recovery_required"
    assert row(f)["last_error_code"] == "approval_release_unsettled"
    assert_locked(f)
    assert f.fx.git_actions == [] and f.fx.commits == []


def test_rejudge_preserves_phase_evidence_and_fences_expired_recovery_writer(frozen):
    f = frozen
    before = row(f)
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', phase = 'F3', recovery_since = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", f.job_id])
    assert f.jobs.request_approval_rejudge(f.job_id)
    assert row(f)["phase"] == "F3" and f.jobs.mode_for(row(f)) == f.jobs.FREEZE
    assert_locked(f)
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', phase = 'fetched', "
                 "lease_token = 'other', lease_owner = 'other', lease_until = ? WHERE job_id = ?",
                 ["2999-01-01T00:00:00+09:00", f.job_id])
    assert not f.jobs.request_approval_rejudge(f.job_id)
    f.s._execute("UPDATE operation_job SET lease_until = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", f.job_id])
    assert f.jobs.request_approval_rejudge(f.job_id)
    after = row(f)
    assert after["phase"] == "fetched" and f.jobs.mode_for(after) == f.jobs.PUBLISH
    assert after["lease_token"] is None
    assert after["attempt_count"] == before["attempt_count"]
    assert after["evidence"] == before["evidence"]
    assert after["frozen_sha"] == before["frozen_sha"]
    assert after["freeze_record"] == before["freeze_record"]
    assert_locked(f)

def test_prefreeze_recovery_discard_uses_release_without_a_freeze_record(frozen):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow
    from modules.flow_gate.services import git_service as gs
    f = frozen
    # G admission can require recovery before F1 writes a freeze record or claim.
    f.db.clear_group_freeze_claim(f.doc["group_id"], f.job_id)
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', phase = 'freeze_pending', "
                 "freeze_record = NULL, freeze_completed = 0, frozen_sha = NULL, frozen_tree = NULL, "
                 "pin_ref = NULL, recovery_since = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", f.job_id])
    assert f.ap.job_view(row(f))["cancelable"]
    assert gs.approval_git_in_flight(f.doc["group_id"])
    app = FastAPI()
    app.include_router(workflow.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "operator", "is_admin": True}
    client = TestClient(app)
    response = client.post(f"/api/v1/documents/approval-jobs/{f.job_id}/discard",
                           json={"doc_id": f.doc["doc_id"]})
    assert response.status_code == 202, response.text
    assert row(f)["status"] == "freeze_wait" and row(f)["release_intent"]
    assert gs.approval_git_in_flight(f.doc["group_id"])
    execute_release(f)
    assert row(f)["status"] == "cancelled" and row(f)["lease_token"] is None
    assert not gs.approval_git_in_flight(f.doc["group_id"])
    assert f.fx.git_actions == [] and f.fx.commits == []


@pytest.mark.parametrize("phase", ["F3", "fetched"])
def test_new_release_opens_own_window_after_old_freeze_or_publish_recovery(frozen, phase):
    from modules.flow_gate.services.git import job_sweeper as sweeper
    f = frozen
    due(f)
    cr = f.jobs.claim(f.job_id, f.jobs.PUBLISH)
    assert cr.outcome == f.jobs.WON
    f.jobs.job_write(cr.ctx, phase=phase)
    real_now = f.af.now_iso
    f.monkeypatch.setattr(f.af, "now_iso", lambda: "2000-01-01T00:00:00+09:00")
    f.af.finish_freeze_recovery(cr.ctx, "publish_R_recovery_required")
    f.monkeypatch.setattr(f.af, "now_iso", real_now)
    assert row(f)["recovery_since"] == "2000-01-01T00:00:00+09:00"
    due(f)
    assert sweeper.step_recovery_required_retry() == 0
    assert f.jobs.request_approval_rejudge(f.job_id)
    assert row(f)["recovery_since"] is None
    mode = f.jobs.FREEZE if phase == "F3" else f.jobs.PUBLISH
    assert f.jobs.mode_for(row(f)) == mode
    cr = f.jobs.claim(f.job_id, mode)
    assert cr.outcome == f.jobs.WON
    # The resumed attempt progresses, then a permanent error starts a new release.
    f.jobs.job_write(cr.ctx, phase="fetched")
    f.monkeypatch.setattr(f.af, "release_pin", lambda ctx: (f.af.MISMATCH, "pin_points_elsewhere"))
    f.ap.release(cr.ctx, "failed", "target_diverged")
    parked = row(f)
    assert parked["status"] == "recovery_required"
    assert parked["recovery_since"] != "2000-01-01T00:00:00+09:00"
    assert json.loads(parked["release_intent"])["recovery_since"] == parked["recovery_since"]
    assert_locked(f)
    due(f)
    assert sweeper.step_recovery_required_retry() == 1
    assert row(f)["recovery_since"] == parked["recovery_since"]


def test_expired_recovery_lease_is_rejudgeable_and_http_accepts(frozen):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow
    f = frozen
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', recovery_since = ?, "
                 "lease_owner = 'stopped', lease_token = 'stale', lease_until = ? WHERE job_id = ?",
                 ["2000-01-01T00:00:00+09:00", "2000-01-01T00:00:00+09:00", f.job_id])
    stale = row(f)
    stale_ctx = f.jobs._job_context(stale, "stale", stale["lease_until"])
    stale_ctx.expected_statuses = frozenset(("recovery_required",))
    assert not f.ap.job_view(stale)["cancelable"]
    assert f.ap.job_view(stale)["rejudgeable"]
    app = FastAPI()
    app.include_router(workflow.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "operator", "is_admin": True}
    response = TestClient(app).post(f"/api/v1/documents/approval-jobs/{f.job_id}/rejudge",
                                    json={"doc_id": f.doc["doc_id"]})
    assert response.status_code == 202, response.text
    assert row(f)["status"] == "retry_wait" and row(f)["lease_token"] is None
    with pytest.raises(f.jobs.LeaseLost):
        f.jobs.job_write(stale_ctx, phase="push_started")
    assert_locked(f)


@pytest.mark.parametrize("offset,allowed", [(-1, True), (0, False), (1, False)])
def test_rejudge_view_and_cas_share_strict_skew_boundary(frozen, offset, allowed):
    from modules.flow_gate.services.git import instance_registry as registry
    f = frozen
    now = f.jobs.now_iso()
    threshold = registry.add_seconds(now, -registry.clock_skew_margin_sec())
    f.monkeypatch.setattr(f.jobs, "now_iso", lambda: now)
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', lease_owner = 'other', "
                 "lease_token = 'other', lease_until = ? WHERE job_id = ?",
                 [registry.add_seconds(threshold, offset), f.job_id])
    assert f.ap.job_view(row(f))["rejudgeable"] is allowed
    assert f.jobs.request_approval_rejudge(f.job_id) is allowed
    assert not f.jobs.approval_rejudgeable(dict(row(f), kind="other"))
    assert_locked(f)


@pytest.mark.parametrize("phase", ["merge_inputs_recorded", "merged", "push_started", "pushed"])
def test_discard_outside_safe_phases_returns_409_and_keeps_claim(frozen, phase):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow
    f = frozen
    f.s._execute("UPDATE operation_job SET status = 'recovery_required', phase = ?, "
                 "last_error_code = 'approval_attempt_unsettled' WHERE job_id = ?", [phase, f.job_id])
    before = row(f)
    assert not f.ap.job_view(before)["cancelable"]
    assert f.ap.job_view(before)["rejudgeable"]
    app = FastAPI()
    app.include_router(workflow.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "operator", "is_admin": True}
    response = TestClient(app).post(f"/api/v1/documents/approval-jobs/{f.job_id}/discard",
                                    json={"doc_id": f.doc["doc_id"]})
    assert response.status_code == 409, response.text
    assert row(f) == before
    assert_locked(f)
    assert f.fx.git_actions == [] and f.fx.commits == []
