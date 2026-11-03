"""flowgate.v02.0128 T#1: duplicate review-loop starts preserve their assignment.

Real SQLite assignments, ledger admission, replay and run-end contacts; only the AI
launcher and the current revision's pass evidence are stand-ins. No AI is invoked.
"""
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 -- shared fixtures
    B, FA, _h, _review_status, _sql, assignments, b1, db_path, decided, edge, env, flow,
    node, node_run, one, put_draft, roles, save_instruction, start, stored, wired, write_line,
)
from test_fg_ai_roles_l2_review import _review_end
from test_fg_ai_roles_l2_fixes import _read
from flowgate.ai_roles import invocations
from flowgate.card_settings import assigned_review, legacy_adapter
from flowgate.graph_run import execution_store as ledger, scheduling
from modules.flow_gate.services import ai_invoke_service, graph_execution_hooks

WIN = "aiv_20261104_910001"


def _pending(env, monkeypatch, stage):
    """Commit the assignment and request, retaining both post-commit starts."""
    own_starts = []
    real_start, real_queue = assigned_review._start_loop, scheduling.queue_requests
    monkeypatch.setattr(assigned_review, "_start_loop", lambda *a: own_starts.append(a))
    monkeypatch.setattr(scheduling, "queue_requests", lambda ctx: None)
    if stage == "result_review":
        draft = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", "T"), node("tmp_next", "T")],
                          [edge("tmp_e", "tmp_r", "tmp_c"), edge("tmp_after", "tmp_c", "tmp_next")])
        key, follow = draft["key_map"]["tmp_c"], draft["key_map"]["tmp_next"]
        for k in (key, follow):
            stored(k, "instruction", "omit")
        res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
        assert res.status_code == 201, res.text
        roles(follow, execution="not_used", review="not_used")
        env["follow_key"] = follow
        roles(key, execution="not_used", review=B)
        start(env)
        doc = write_line(env, key, "report", "## Result\n\nBody\n")["doc_id"]
    else:
        key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
        roles(key, "not_used", "not_used", "not_used")
        start(env)
        doc = save_instruction(env, key)
    [args] = own_starts
    a = one(env, key, stage, 1)
    monkeypatch.setattr(assigned_review, "_start_loop", real_start)
    monkeypatch.setattr(scheduling, "queue_requests", real_queue)
    ctx = SimpleNamespace(run={"status": "running"}, run_id=a["run_id"], flow_id=FA, jobs=[])
    scheduling.queue_requests(ctx)
    [replay] = ctx.jobs
    return key, doc, a, lambda: real_start(*args), replay


class _LoopStarter:
    def __init__(self, monkeypatch, refusal=None):
        self.admitted, self.calls = [], []
        ids = (f"aiv_20261104_{n:06d}" for n in count(910001))

        @invocations.admission
        @graph_execution_hooks.admission
        def start_run(**kw):
            self.calls.append(kw)
            run_id = next(ids)
            if refusal:
                raise refusal
            graph_execution_hooks.call("admit", kw["graph_exec_id"], run_id, kw["group_id"])
            self.admitted.append(run_id)
            return {"run_id": run_id, "status": "running"}

        monkeypatch.setattr(ai_invoke_service, "start_run", start_run)


@pytest.mark.parametrize("stage", ["result_review", "instruction_review"])
@pytest.mark.parametrize("winner", ["assigned", "replay"])
def test_duplicate_loop_start_preserves_assignment_and_consumes_owner_pass(env, decided, monkeypatch, stage, winner):
    key, doc, a, own, replay = _pending(env, monkeypatch, stage)
    launcher = _LoopStarter(monkeypatch)
    first, second = (own, replay) if winner == "assigned" else (replay, own)
    first()
    second()
    assert launcher.admitted == [WIN]
    assert len(launcher.calls) == 2
    assert ledger.get(a["intent_exec_id"])["ai_run_id"] == WIN
    current = one(env, key, stage, 1)
    assert (current["stage_state"], current["fail_cause"]) == ("active", None)
    assert b1(env) == []
    assert node_run(env, key)["dispatch_error_code"] is None
    for _ in range(2):
        _read(env)
    assert one(env, key, stage, 2) is None
    assert len(launcher.admitted) == 1
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, current, "review_passed", WIN)
    assert one(env, key, stage, 1)["stage_state"] == "done"
    assert one(env, key, stage, 1)["verdict"] == "passed"
    assert _review_status(doc) == "approved"
    if stage == "result_review":
        assert node_run(env, key)["status"] == "done"
        _read(env)
        assert node_run(env, env["follow_key"])["status"] == "open"
        assert len(_sql(env["db"], "SELECT * FROM graph_card_completions WHERE node_key = ?", (key,))) == 1
    else:
        _read(env)
        assert one(env, key, "result") is not None
    assert b1(env) == []


@pytest.mark.parametrize("blocker", ["other_lease", "no_slot"])
def test_ownerless_busy_keeps_failure_and_three_retry_limit(env, decided, monkeypatch, blocker):
    key, doc, a, own, _replay = _pending(env, monkeypatch, "result_review")
    if blocker == "other_lease":
        monkeypatch.setattr(scheduling.group_ai_leases, "get_active", lambda flow: {"run_id": "other"})
    else:
        monkeypatch.setattr(scheduling, "available", lambda *args, **kwargs: 0)
    launcher = _LoopStarter(monkeypatch)
    own()
    assert ledger.get(a["intent_exec_id"])["ai_run_id"] is None
    assert one(env, key, "result_review", 1)["fail_cause"] == "start_refused:run_in_progress"
    assert ledger.get(a["intent_exec_id"])["status"] == "requested"
    assert assigned_review.review_retry_due(one(env, key, "result_review", 1))
    # Busy admission deliberately keeps its durable request. Retire each blocked request
    # as a stopped launcher would before exercising the existing assignment retry budget.
    for seq in (1, 2):
        cur = one(env, key, "result_review", seq)
        ledger.cancel(cur["intent_exec_id"], None, "requested")
        _read(env)
    for _ in range(2):
        _read(env)
    assert not assigned_review.review_retry_due(one(env, key, "result_review", 3))
    reviews = [r for r in assignments(env, key) if r["stage"] == "result_review"]
    assert [(r["seq"], r["stage_state"], r["fail_cause"]) for r in reviews] == [
        (seq, "failed", "start_refused:run_in_progress") for seq in (1, 2, 3)]
    assert len(b1(env)) == 1
    assert _review_status(doc) == "pending_review"
    assert launcher.admitted == []


@pytest.mark.parametrize("error", [HTTPException(409, {"code": "provider_inactive", "message": "run_in_progress"}),
                                   RuntimeError("run_in_progress")])
def test_an_owned_execution_does_not_hide_other_start_failures(env, decided, monkeypatch, error):
    key, doc, a, own, replay = _pending(env, monkeypatch, "result_review")
    _LoopStarter(monkeypatch)
    replay()
    _LoopStarter(monkeypatch, refusal=error)
    own()
    expected = "start_refused:provider_inactive" if isinstance(error, HTTPException) else "start_failed"
    assert (one(env, key, "result_review", 1)["stage_state"],
            one(env, key, "result_review", 1)["fail_cause"]) == ("failed", expected)
    assert len(b1(env)) == 1
