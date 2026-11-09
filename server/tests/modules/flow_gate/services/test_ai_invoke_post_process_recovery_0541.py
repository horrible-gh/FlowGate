"""flowgate.default.0541 T0004 R4 — provider exit must not leave a live run forever."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

import pytest  # noqa: E402

from modules.flow_gate.db import ai_run_identity  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services.ai_invoke import review, worker  # noqa: E402


@pytest.fixture(autouse=True)
def _owned_run(monkeypatch):
    """These runs are hand-built dicts with no `execution_nonce` and no `ai_run_owners` row,
    so the real ownership check (terminal.check_owner -> ai_run_identity.assert_owner)
    refused them and `terminal.cleanup` returned before persisting anything. That was the
    harness, not the recovery under test (the same isolation as 0573's `env` fixture; the
    real nonce/DB guard is covered by integration/ai_run_identity/0138). The refusal itself
    is pinned separately below in test_r4_ownership_unconfirmed_*."""
    monkeypatch.setattr(ai_run_identity, "assert_owner", lambda *_a, **_k: None)


def _run():
    return {
        "run_id": "aiv_20260909_054100", "group_id": "flowgate.default.0541",
        "project_id": "flowgate", "doc_ref": "flowgate.default.0541.0005-TR",
        "mode": "single", "status": "running", "cancel_event": threading.Event(),
        "started_mono": time.monotonic(), "started_at": "2026-09-09T07:00:00+09:00",
        "docs_target": 1, "chain_id": "aiv_20260909_054100", "chain_docs_target": 1,
        "chain_docs_reached": 0, "attempt_no": 1, "exit_code": None,
    }


def test_r4_provider_exit_stuck_in_judge_is_reconciled_to_terminal(monkeypatch):
    """The precise Case C boundary: CLI returned 0, then judge blocks indefinitely."""
    run = _run()
    entered = threading.Event()
    release = threading.Event()
    persisted = threading.Event()
    broadcasts = []

    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.02)
    monkeypatch.setattr(worker.oracle_module, "_provider_brief", lambda provider: dict(provider))
    monkeypatch.setattr(svc, "_broadcast", lambda *args: broadcasts.append(args[1]))

    def execute(target, chain, prompt):
        target["exit_code"] = 0
        return True

    def judge(_run):
        entered.set()
        release.wait(1)

    monkeypatch.setattr(svc, "_execute_provider_chain", execute)
    monkeypatch.setattr(svc, "_classify_end_reason", lambda target, ok: target.update(end_reason="exited"))
    monkeypatch.setattr(svc, "_judge_hop", judge)
    monkeypatch.setattr(svc, "_persist_run_record", lambda target: persisted.set())
    monkeypatch.setattr(svc, "finished_payload", lambda target: {"status": target["status"]})
    monkeypatch.setattr(svc, "_finalize_run", lambda target: target.update(status="finished"))
    monkeypatch.setattr(svc, "_maybe_auto_resume_hop", lambda target: None)
    monkeypatch.setattr(worker.db_group_ai_leases, "release", lambda *args, **kwargs: None)

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p", "name": "P"}], "prompt"))
    thread.start()
    assert entered.wait(0.3)
    assert persisted.wait(0.5)
    assert run["status"] == "finished"
    assert run["end_reason"] == "post_process_timeout"
    assert run["stop_code"] == "post_process_timeout"
    assert run["post_process_phase"] == "reconciled_timeout"
    assert "ai_invoke_finished" in broadcasts

    release.set()
    thread.join(1)
    assert not thread.is_alive()


def test_completed_post_process_cancels_its_recovery_timer(monkeypatch):
    run = _run()
    calls = []
    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.02)
    monkeypatch.setattr(svc, "_persist_run_record", lambda target: calls.append(target))

    completed, timer = worker._start_post_process_recovery(run)
    worker._stop_post_process_recovery(completed, timer)
    time.sleep(0.05)

    assert calls == []
    assert run["status"] == "running"


def test_r4_retry_cancels_prior_attempt_timer_before_long_second_attempt(monkeypatch):
    """A no-output retry must not inherit attempt one's post-process deadline."""
    run = _run()
    second_attempt_started = threading.Event()
    release_second_attempt = threading.Event()
    persisted = []

    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.02)
    monkeypatch.setattr(worker.oracle_module, "_provider_brief", lambda provider: dict(provider))
    monkeypatch.setattr(svc, "_broadcast", lambda *args: None)

    executions = []

    def execute(target, chain, prompt):
        executions.append(target["attempt_no"])
        target["exit_code"] = 0
        if len(executions) == 2:
            second_attempt_started.set()
            release_second_attempt.wait(1)
        return True

    retry_eligible = iter((True, False))
    monkeypatch.setattr(svc, "_execute_provider_chain", execute)
    monkeypatch.setattr(svc, "_classify_end_reason", lambda target, ok: target.update(end_reason="exited"))
    monkeypatch.setattr(svc, "_judge_hop", lambda target: target.update(outcome="none"))
    monkeypatch.setattr(worker, "_retry_eligible", lambda target: next(retry_eligible))
    monkeypatch.setattr(worker, "_recheck_no_output", lambda target: True)
    monkeypatch.setattr(worker, "_retry_provider_chain", lambda target: [{"id": "p2", "name": "P2"}])
    monkeypatch.setattr(svc, "_prepare_retry_token", lambda target: {"mention": "retry", "token_id_before": "t", "token_id": "t2", "reissued": True})
    monkeypatch.setattr(worker, "_archive_attempt", lambda target, *args: target.setdefault("fallback_history", []).append({"detail": "retry"}))
    monkeypatch.setattr(svc, "_reset_attempt_state", lambda target: None)
    monkeypatch.setattr(svc, "_persist_run_record", lambda target: persisted.append(dict(target)))
    monkeypatch.setattr(svc, "_finalize_run", lambda target: target.update(status="finished"))
    monkeypatch.setattr(svc, "_maybe_auto_resume_hop", lambda target: None)
    monkeypatch.setattr(worker.db_group_ai_leases, "release", lambda *args, **kwargs: None)

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p1", "name": "P1"}], "prompt"))
    thread.start()
    assert second_attempt_started.wait(0.3)
    time.sleep(0.06)  # exceeds attempt one's recovery window while attempt two is live
    assert run["status"] == "running"
    assert not run.get("post_process_recovered")
    assert persisted == []

    release_second_attempt.set()
    thread.join(1)
    assert not thread.is_alive()
    assert executions == [1, 2]
    assert run["status"] == "finished"
def _configure_terminal_tail_stall(monkeypatch, run, persisted):
    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.02)
    monkeypatch.setattr(worker.oracle_module, "_provider_brief", lambda provider: dict(provider))
    monkeypatch.setattr(svc, "_broadcast", lambda *args: None)
    monkeypatch.setattr(svc, "_execute_provider_chain",
                        lambda target, chain, prompt: (target.update(exit_code=0) or True))
    monkeypatch.setattr(svc, "_classify_end_reason",
                        lambda target, ok: target.update(end_reason="exited"))
    monkeypatch.setattr(svc, "_judge_hop", lambda target: target.update(outcome="complete"))
    monkeypatch.setattr(worker, "_retry_eligible", lambda target: False)
    monkeypatch.setattr(svc, "_persist_run_record", lambda target: persisted.set())
    monkeypatch.setattr(worker.db_group_ai_leases, "release", lambda *args, **kwargs: None)


def test_r4_status_finished_does_not_hide_stuck_finalize(monkeypatch):
    """Finalize may set status first; recovery must await lifecycle terminalization."""
    run = _run()
    entered = threading.Event()
    release = threading.Event()
    persisted = threading.Event()
    _configure_terminal_tail_stall(monkeypatch, run, persisted)

    def finalize(target):
        target["status"] = "finished"
        entered.set()
        release.wait(1)

    monkeypatch.setattr(svc, "_finalize_run", finalize)
    monkeypatch.setattr(svc, "_maybe_auto_resume_hop", lambda target: None)

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p", "name": "P"}], "prompt"))
    thread.start()
    assert entered.wait(0.3)
    assert run["status"] == "finished"
    assert persisted.wait(0.5)
    assert run["post_process_recovered"]
    assert run["end_reason"] == "post_process_timeout"

    release.set()
    thread.join(1)
    assert not thread.is_alive()


def test_r4_status_finished_does_not_hide_stuck_handoff(monkeypatch):
    """Handoff is after finalize and remains inside the same bounded lifecycle tail."""
    run = _run()
    entered = threading.Event()
    release = threading.Event()
    persisted = threading.Event()
    _configure_terminal_tail_stall(monkeypatch, run, persisted)

    monkeypatch.setattr(svc, "_finalize_run", lambda target: target.update(status="finished"))

    def handoff(_run):
        entered.set()
        release.wait(1)

    monkeypatch.setattr(svc, "_maybe_auto_resume_hop", handoff)

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p", "name": "P"}], "prompt"))
    thread.start()
    assert entered.wait(0.3)
    assert run["status"] == "finished"
    assert persisted.wait(0.5)
    assert run["post_process_recovered"]
    assert run["end_reason"] == "post_process_timeout"

    release.set()
    thread.join(1)
    assert not thread.is_alive()

def _configure_stuck_judge(monkeypatch, run, released, persisted):
    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.02)
    monkeypatch.setattr(worker.oracle_module, "_provider_brief", lambda provider: dict(provider))
    monkeypatch.setattr(svc, "_broadcast", lambda *args: None)
    entered, release = threading.Event(), threading.Event()

    def execute(target, chain, prompt):
        target["exit_code"] = 0
        return True

    def judge(_run):
        entered.set()
        release.wait(1)

    monkeypatch.setattr(svc, "_execute_provider_chain", execute)
    monkeypatch.setattr(svc, "_classify_end_reason", lambda target, ok: target.update(end_reason="exited"))
    monkeypatch.setattr(svc, "_judge_hop", judge)
    monkeypatch.setattr(svc, "_persist_run_record", lambda target: persisted.set())
    monkeypatch.setattr(svc, "finished_payload", lambda target: {"status": target["status"]})
    monkeypatch.setattr(svc, "_finalize_run", lambda target: target.update(status="finished"))
    monkeypatch.setattr(svc, "_maybe_auto_resume_hop", lambda target: None)
    monkeypatch.setattr(worker.db_group_ai_leases, "release",
                        lambda *args, **kwargs: released.append(kwargs.get("reason")))
    return entered, release


def test_r4_recovery_closes_the_document_review_loop_before_the_lease_release(monkeypatch):
    """flowgate.v02test.0014: a recovered run never checkpoints its loop again, so the row
    used to stay `current_stage='review'` (review_loop_active: True, card locked) after the
    lease was gone. Recovery now closes it -- before the release, once."""
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    run = {**_run(), "document_review_loop": {"current_stage": "review", "round_no": 1}}
    released, persisted, order = [], threading.Event(), []
    entered, release = _configure_stuck_judge(monkeypatch, run, released, persisted)
    monkeypatch.setattr(db_loops, "stop_for_abandoned_run", lambda run_id, *, detail: (
        order.append(("loop", run_id, detail, list(released))) or True))
    monkeypatch.setattr(db_loops, "get", lambda run_id: {
        "run_id": run_id, "current_stage": "stopped", "stop_reason": "retry_exhausted"})

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p", "name": "P"}], "prompt"))
    thread.start()
    assert entered.wait(0.3)
    assert persisted.wait(0.5)
    release.set()
    thread.join(1)
    assert not thread.is_alive()

    assert len(order) == 1
    _, run_id, detail, released_before = order[0]
    assert run_id == run["run_id"]
    assert detail == f"{review.ABANDONED_RUN_STOP_DETAIL}: post_process_timeout"
    assert released_before == [], "the loop must be closed before the lease is released"
    assert released == ["post_process_timeout"]
    assert run["document_review_loop"]["current_stage"] == "stopped"


def test_r4_ownership_unconfirmed_keeps_records_and_lease_and_leaves_the_loop(monkeypatch):
    """The refusal the fixture above bypasses, pinned: with ownership unconfirmed (no
    execution_nonce), recovery claims the abandon but cleanup returns before the lease
    release, the run record and the loop row -- and says so in
    `terminal_cleanup_errors.identity` instead of failing silently. That is the
    "running + locked" shape an operator must look for in such a run's record."""
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    def refuse(*_a, **_k):
        raise ai_run_identity.RunIdentityConflict()

    monkeypatch.setattr(ai_run_identity, "assert_owner", refuse)
    run = {**_run(), "document_review_loop": {"current_stage": "review", "round_no": 1}}
    released, persisted, loop_calls = [], threading.Event(), []
    entered, release = _configure_stuck_judge(monkeypatch, run, released, persisted)
    monkeypatch.setattr(db_loops, "stop_for_abandoned_run",
                        lambda *a, **k: loop_calls.append(a) or True)

    thread = threading.Thread(target=worker._worker, args=(run, [{"id": "p", "name": "P"}], "prompt"))
    thread.start()
    assert entered.wait(0.3)
    deadline = time.monotonic() + 0.5
    while not run.get("post_process_recovered") and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert run.get("post_process_recovered")
    assert run["end_reason"] == "post_process_timeout"
    assert run["terminal_cleanup_errors"]["identity"] == "ownership_unconfirmed"
    assert not persisted.is_set()
    assert released == []
    assert loop_calls == []
    assert run["status"] != "finished"

    release.set()
    thread.join(1)
    assert not thread.is_alive()
