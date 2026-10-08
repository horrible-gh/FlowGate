"""Failure and cancel going through the real FlowGate job pipeline (flowgate.default.0671
T0002 / 0669 TR's last rejection, review 1737 item b).

The rejected test (``test_locks_are_released_after_failure_and_after_cancel`` in
test_git_parallel_0669.py) only exercised ``lock_manager.acquire``/``release`` directly —
no ``job_store``, no ``job_runner``, no ``finalize_publish`` request/admit/release cycle.
Real production failures and cancellations go through that job layer, not a bare
try/finally around ``lm.acquire``. This file drives the same guarantee (locks never leak)
through the actual handler:

* failure — ``finalize_publish.request`` with a Git body that raises a real
  ``GitServiceError``; the job's own ``_fail``/``finish_run``(PERMANENT) path must mark the
  job ``failed`` and release every lock the attempt took (job_runner.execute's
  ``locks.unbind`` in its ``finally``), not a manual release in the test.
* cancel — ``job_store.request_cancel`` on a job queued behind another holder; the real
  cancel path (``operation_job.cancel_unfrozen_waiting``) must mark it ``cancelled`` and
  ``job_runner.try_claim`` must then refuse to ever run it, so the stubbed Git body is
  never invoked for a cancelled job.

Only the Git body (``git_service.finalize``) is stubbed — the same scope the existing
``test_git_selfcheck_overlap_0669.py`` stubs at (its module docstring names this limit
explicitly). Everything else — ``lock_manager``, ``job_store``, ``job_runner``,
``finalize_publish`` — is the real module.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import git_concurrency as gc  # noqa: E402
from modules.flow_gate.db import operation_job as db_jobs  # noqa: E402
from modules.flow_gate.services.git import base_publish  # noqa: E402
from modules.flow_gate.services.git import finalize_publish as fp  # noqa: E402
from modules.flow_gate.services.git import instance_registry as reg  # noqa: E402
from modules.flow_gate.services.git import job_runner as runner  # noqa: E402
from modules.flow_gate.services.git import job_store as jobs  # noqa: E402
from modules.flow_gate.services.git import lock_manager as lm  # noqa: E402
from modules.flow_gate.services.git import merge_target  # noqa: E402
from modules.flow_gate.services.git.credentials import GitServiceError  # noqa: E402

NOW = "2026-10-05T13:00:00+09:00"
P = "p_0671fc"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_git_failure_cancel_real_handlers_0671.db")


@pytest.fixture
def env(db_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(db_path)
    s._sq = None
    import sys as _sys
    for name, module in list(_sys.modules.items()):
        if name.startswith("modules.flow_gate.") and hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda s=s: s)
    with s.transaction():
        for table in ("resource_lock", "operation_job", "server_instance"):
            s._execute(f"DELETE FROM {table}")
        s._execute(
            "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
            "VALUES (?, 'P 0671 fc', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
            [P, NOW, NOW],
        )
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    monkeypatch.setattr(lm, "wait_budget", lambda domain, mode: 0.0 if mode in (lm.NO_WAIT, "job") else 0.3)
    monkeypatch.setattr(gc, "_current_instance_id", None)
    reg.register()

    from modules.flow_gate.services import git_service as gs
    monkeypatch.setattr(gs, "_project_of_group", lambda g: P)
    monkeypatch.setattr(gs, "complete_approve_git_action", lambda *a, **k: None)
    monkeypatch.setattr(gs, "realize_wf_done_transition", lambda *a, **k: None)
    monkeypatch.setattr(gs.db_git, "get_config", lambda _p: {})
    monkeypatch.setattr(
        merge_target, "plan_finalize_target",
        lambda *a, **k: SimpleNamespace(is_project_base=True, target_branch="main"),
    )

    yield SimpleNamespace(store=s, gs=gs)
    gc.set_current_instance_id(None)


def _finalize_jobs(group_id: str) -> list[dict]:
    return db_jobs.jobs_of_group(group_id, base_publish.KIND, db_jobs.TERMINAL_STATUSES)


def test_finalize_failure_is_handled_by_the_real_pipeline_and_releases_all_locks(env, monkeypatch):
    """A Git body failure (real GitServiceError, not a mocked lock release) must leave the
    job 'failed' with the error code recorded, and every lock the attempt took (G, B, R)
    released by the real finish_run(PERMANENT) + job_runner.execute(finally: unbind) path."""
    def boom(group_id, action, message=None, **kw):
        raise GitServiceError(500, "simulated_git_failure", "boom")

    monkeypatch.setattr(env.gs, "finalize", boom)

    assert _finalize_jobs("g_fail") == []

    with pytest.raises(GitServiceError) as excinfo:
        fp.request(P, "g_fail", "merge", None, None)
    assert excinfo.value.code == "simulated_git_failure"

    done = _finalize_jobs("g_fail")
    assert len(done) == 1
    job = done[0]
    assert job["status"] == "failed"
    assert job["last_error_code"] == "simulated_git_failure"
    assert gc.list_locks_in_scope(P) == []


def test_queued_finalize_is_cancelled_by_the_real_handler_and_never_runs_its_body(env, monkeypatch):
    """A finalize queued behind another group's publish hold (git_busy) is cancelled through
    job_store.request_cancel (operation_job.cancel_unfrozen_waiting) — the real cancel path —
    not a thread Event the test wires up itself. Once cancelled, job_runner.try_claim must
    refuse it even after the blocker releases, and the stubbed Git body must never run."""
    calls: list[str] = []

    def fake_finalize(group_id, action, message=None, **kw):
        calls.append(group_id)
        return {"ok": True, "result": {"action": action, "status": "merged",
                                       "merge_commit": "c" * 40, "pushed": True,
                                       "merge_id": None, "conflict_files": []}}

    monkeypatch.setattr(env.gs, "finalize", fake_finalize)

    holder = lm.new_context()
    held = [
        lm.acquire("G", P, group_id="g_block", holder_kind="publish", ctx=holder),
        lm.acquire("B", P, holder_kind="publish", ctx=holder),
        lm.acquire("R", P, holder_kind="publish", ctx=holder),
    ]
    assert all(h.ok for h in held), held

    with pytest.raises(GitServiceError) as busy:
        fp.request(P, "g_cancel", "merge", None, None)
    assert busy.value.code == "git_busy"
    job_id = busy.value.details["job_id"]

    outcome = jobs.request_cancel(job_id)
    assert outcome == jobs.CANCELLED
    job = jobs.get_job(job_id)
    assert job["status"] == "cancelled"

    for h in reversed(held):
        lm.release(holder, h.lock_key)

    cr = runner.try_claim(job_id)
    assert cr is None, "a cancelled job must never be claimable again"
    assert calls == [], "the Git body must never run for a cancelled job"
    assert gc.list_locks_in_scope(P) == []
