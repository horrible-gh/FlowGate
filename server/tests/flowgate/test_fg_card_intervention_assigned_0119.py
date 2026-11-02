"""flowgate.v02.0119 T#3 (T-2 rework) — the assigned path (L-2) of a card nothing completes: its system errors retried
within their limit, the instruction stage's system errors shown, a system error never read as a person's wait, and the
whole intervention path of an AI card from its automatic rework to its completion after a memo.

The harness of ``test_fg_ai_roles_l2_assignment`` (real SQLite DB, routes and migrations, the stage assignment feature
on, recorders for every AI start; nothing calls a real AI). Fault injection happens in these fixtures only, never on an
operating run:

- a result reworked inside its review loop (a new revision) whose automatic completion keeps failing is tried again up to
  ``SYSTEM_ERROR_RETRY_LIMIT`` failures of that submission — the records of another submission count nothing — and a
  retry after the cause is gone completes it (review P1: the count followed the assignment's basis, so never stopped);
- an instruction review whose setting could not be read (``review_result_unknown``) is read again by the run reads; a
  setting fixed since starts its review (review P1: it was recorded once and never retried);
- an instruction whose automatic approval failed (``approval_refused``) is a ``system_error`` of the open card, the run's
  error family, retried and then completed (review P2: an open instruction stage told no intervention);
- the order of the idle judgement: an explicit system error first, a person's wait with none after it (review P2);
- the intervention path end to end (review P2): the automatic rework of the result's review runs first and is spent ->
  ``needs_intervention`` (only its descendants wait, the independent branch completes, the run idle
  ``needs_intervention``) -> a memo through the memo route -> a rework through the real ``POST /ai-invoke/start`` (edit,
  the memo in its mention) -> a re-review through the same route (review loop: the person's explicit request takes the
  idle run back to running) -> its pass completes the card automatically and opens the next card.
"""
from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, WORK_RUN, X, _review_status, _sql, ai_writes_result, assignments, b1, command, db_path, decided, env, flow,
    node_run, one, record_starts, roles, save_instruction, start, starts, stored, wired, write_line,
)
from test_fg_card_settings import _h, edge, node, put_draft
from test_fg_card_settings_review import REVIEW_RUN, _run_ended
from test_fg_ai_roles_l2_review import _review_end

LIMIT = 3                       # ``flowgate.card_intervention.reasons.SYSTEM_ERROR_RETRY_LIMIT`` (asserted below)
NOT_PASSED = "review_not_passed:review_count_exhausted"
REWORK_RUN, REREVIEW_RUN = "air_person_rework", "air_person_rereview"
MEMO = "Name every error code from table v2."


def _read(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _wire(env, key):
    return next(n for n in _read(env)["node_runs"] if n["node_key"] == key)


def _run_row(env):
    return _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE group_id = ? ORDER BY rowid DESC", (FA,))[0]


def _refused(doc):
    raise RuntimeError("the completion gate refused it")


def _reviewed_ai_result(env, monkeypatch, starts, key=None):
    """An AI card (result X, review B with X as its rework AI, count 1): X wrote its result and B's review loop of it
    was requested by the run end (the assigned path). -> (key, result, the review assignment)."""
    if key is None:
        key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
        roles(key, execution=X, review=B)
        start(env)
    [job] = [j for j in env["jobs"] if j.get("provider_id") == X]
    env["jobs"].clear()
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["rework_provider_id"], rr["author"]) == ("ai", B, X, "ai")
    return key, result, rr


def test_the_retry_limit_is_the_packages():
    from flowgate.card_intervention import reasons

    assert reasons.SYSTEM_ERROR_RETRY_LIMIT == LIMIT


# ── review P1: the retries of an approval count the submission the approval records for ──

def test_a_reworked_result_whose_completion_keeps_failing_is_tried_only_up_to_the_limit_of_its_own_submission(
        env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.db import documents as db_documents

    key, result, rr = _reviewed_ai_result(env, monkeypatch, starts)
    basis = {"doc_id": result, "revision_no": int(rr["basis_revision_no"]),
             "rejection_count": int(rr["basis_rejection_count"])}
    for _ in range(2):                          # an earlier submission's failures: never this submission's
        legacy_adapter.record_submission_wait(FA, key, result, "approval_refused", basis, attempt_id=rr["attempt_id"])
    db_documents.update(result, {"revision_no": int(rr["basis_revision_no"]) + 1})   # the loop's automatic rework
    current = sa.mark(db_documents.get_by_id(result))
    assert current != basis
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _review_end(env, rr, "review_passed")       # the pass of the reworked revision; its completion refused
    assert one(env, key, "result_review", 1)["verdict"] == "passed"
    for _ in range(LIMIT + 3):
        _read(env)
    mine = [m for _d, r, m in b1(env) if r == "approval_refused" and m == current]
    assert len(mine) == LIMIT                                         # the first try and its retries, then never
    assert len(b1(env)) == LIMIT + 2                                  # the earlier submission's two kept, unchanged
    assert node_run(env, key)["status"] == "review" and _review_status(result) != "approved"
    assert _wire(env, key)["intervention"] == {"kind": "system_error", "reason": "approval_refused"}
    assert _run_row(env)["idle_reason"] == "error"


def test_a_reworked_result_whose_completion_failed_once_completes_on_the_next_read(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import documents as db_documents

    key, result, rr = _reviewed_ai_result(env, monkeypatch, starts)
    db_documents.update(result, {"revision_no": int(rr["basis_revision_no"]) + 1})
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _review_end(env, rr, "review_passed")
    assert [r for _d, r, _m in b1(env)] == ["approval_refused"]
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)    # the cause is gone
    _read(env)
    assert node_run(env, key)["status"] == "done" and _review_status(result) == "approved"
    assert [r for _d, r, _m in b1(env)] == ["approval_refused"]       # nothing recorded by the successful retry


# ── review P1: a review setting that could not be read is read again ─────────

def test_an_unreadable_instruction_review_setting_fixed_since_is_read_again_and_its_review_starts(
        env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    stored(key, "instruction_review_count", "two")                    # a value that cannot be told
    start(env)
    instruction = save_instruction(env, key)
    assert (one(env, key, "instruction_review", 1)["actor_reason"]) == "setting_unreadable"
    wire = _wire(env, key)                                            # a read: the setting read again, still unreadable
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_result_unknown"}
    assert _run_row(env)["idle_reason"] == "error"
    stored(key, "instruction_review_count", 1)                        # the setting fixed
    stored(key, "instruction_review_ai", B)
    _read(env)
    reviews = sorted((a for a in assignments(env, key) if a["stage"] == "instruction_review"), key=lambda a: a["seq"])
    assert [(a["actor"], a["stage_state"]) for a in reviews[:-1]] == [("wait_setup", "failed")] * (len(reviews) - 1)
    latest = reviews[-1]
    assert (latest["actor"], latest["provider_id"], latest["stage_state"], latest["intent_kind"]) == \
        ("ai", B, "active", "execution")
    assert latest["basis_doc_id"] == instruction and len(reviews) <= LIMIT
    loops = [c for c in starts if c.get("document_review_loop")]
    assert len(loops) == 1 and loops[0]["doc_ref"] == instruction
    assert _wire(env, key)["intervention"] is None                    # its review works on it


# ── review P2: the instruction stage's system error, shown and retried ───────

def _instruction_reviewed(env, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["intent_kind"]) == ("ai", "execution")
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    return key, instruction, ir


def test_an_instruction_whose_automatic_approval_keeps_failing_is_a_system_error_retried_up_to_the_limit(
        env, decided, monkeypatch, starts):
    from flowgate.card_approval import legacy_adapter as approvals

    key, instruction, ir = _instruction_reviewed(env, monkeypatch, starts)
    monkeypatch.setattr(approvals, "approve_document", lambda *a, **k: _refused(None))
    _review_end(env, ir, "review_passed")
    row = node_run(env, key)
    assert (row["status"], row["stage"]) == ("open", "instruction")
    shown = _wire(env, key)
    assert shown["intervention"] == {"kind": "system_error", "reason": "approval_refused"}
    assert shown["approval_wait_reason"] == "approval_refused"
    assert _run_row(env)["idle_reason"] == "error"                    # never a person's wait
    for _ in range(LIMIT + 2):
        _read(env)
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "approval_refused")] * LIMIT
    assert _review_status(instruction) != "approved" and one(env, key, "result") is None


def test_an_instruction_whose_automatic_approval_failed_once_goes_on_to_its_result_stage(env, decided, monkeypatch, starts):
    from flowgate.card_approval import legacy_adapter as approvals

    key, instruction, ir = _instruction_reviewed(env, monkeypatch, starts)
    real = approvals.approve_document
    monkeypatch.setattr(approvals, "approve_document", lambda *a, **k: _refused(None))
    _review_end(env, ir, "review_passed")
    assert _wire(env, key)["intervention"]["kind"] == "system_error"
    monkeypatch.setattr(approvals, "approve_document", real)          # the cause is gone
    _read(env)
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"
    assert _wire(env, key)["intervention"] is None


@pytest.mark.parametrize("reason", ["issuer_missing", "approval_refused", "review_not_started", "review_result_unknown"])
def test_each_system_error_of_an_open_instruction_stage_is_shown_unless_an_ai_works_on_it_or_it_is_a_gate(
        monkeypatch, reason):
    from flowgate.card_intervention import reasons
    from modules.flow_gate.graph_workflow.services import card_settings_ports, node_approval

    row = {"node_key": "gn_c", "run_id": "run_x", "status": "open", "stage": "instruction", "instruction_doc_id": "doc_i"}
    working = {"now": False}
    monkeypatch.setattr(card_settings_ports, "intervention", lambda flow_id, r: reasons.state(reason))
    monkeypatch.setattr(card_settings_ports, "ai_working", lambda flow_id, key, doc_id: working["now"])
    assert node_approval.intervention(FA, row, "T") == {"kind": "system_error", "reason": reason}
    assert node_approval.intervention(FA, row, "gate") is None                      # a confirm gate: never
    assert node_approval.intervention(FA, {**row, "stage": "result"}, "T") is None   # another stage: as before
    assert node_approval.intervention(FA, {**row, "instruction_doc_id": None}, "T") is None
    working["now"] = True
    assert node_approval.intervention(FA, row, "T") is None                         # its AI still works on it
    working["now"] = False
    monkeypatch.setattr(card_settings_ports, "intervention",
                        lambda flow_id, r: reasons.state("instruction_not_approved"))
    assert node_approval.intervention(FA, row, "T") is None                         # a person's wait: judged as before


# ── review P2: a system error is never read as a person's wait ───────────────

@pytest.mark.parametrize("status", ["review", "open"])
def test_a_stage_wait_with_a_system_error_is_the_error_family_and_one_without_is_a_person_s_wait(monkeypatch, status):
    from flowgate.graph_run import settle
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    ctx = SimpleNamespace(flow_id=FA, run_id="run_x", node_rows={"gn_c": {"node_type": "T"}})
    row = {"node_key": "gn_c", "run_id": "run_x", "status": status, "stage": "instruction", "result_doc_id": "doc_r",
           "instruction_doc_id": "doc_i", "dispatch_token_issued": 1, "dispatch_error_code": None}
    monkeypatch.setattr(card_settings_ports, "stage_waits", lambda flow_id, r: True)   # its assignment waits
    monkeypatch.setattr(settle, "_failed", lambda c, r: False)
    monkeypatch.setattr(settle, "_asks", lambda c, r: False)
    found = {"kind": "system_error"}
    monkeypatch.setattr(settle.engine, "intervention", lambda flow_id, r, t: {"kind": found["kind"], "reason": "x"})
    assert (settle._needs_intervention(ctx, row), settle._system_error(ctx, row)) == (False, True)
    found["kind"] = "needs_intervention"                                  # no system error: the person's wait
    assert (settle._needs_intervention(ctx, row), settle._system_error(ctx, row)) == (True, False)
    monkeypatch.setattr(settle.engine, "intervention", lambda flow_id, r, t: None)
    assert (settle._needs_intervention(ctx, row), settle._system_error(ctx, row)) == (True, False)


# ── review P2: the intervention path end to end ──────────────────────────────

@pytest.fixture()
def fork_join(decided):
    """The production branch and join rules (``flowgate.graph_policies``), as ``test_fg_card_intervention_0119``."""
    from flowgate.graph_policies import fork, join
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    slots = fork.SLOTS + join.SLOTS
    validators = dict(policies._registered)
    old = {}
    for key, value, version, impl in slots:
        run_policies.register(key, value, version, impl)
        old[key] = policies.set_system_default(key, value, version)
    saved = policies._registered
    policies._registered = {**validators, **{key: impl for key, _v, _ver, impl in slots}}
    yield
    policies._registered = saved
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


@pytest.fixture()
def memo_routes(env, monkeypatch):
    """The card memo port and route, as ``startup`` / ``main`` wire them (their RBAC answer: every permission)."""
    from flowgate.card_memos import integration as memos
    from flowgate.card_memos import legacy_adapter as memo_adapter
    from flowgate.card_memos import routes as memo_route

    monkeypatch.setattr(memo_adapter, "has_permission", lambda u, p, perm: True)
    memos.register()
    env["client"].app.include_router(memo_route.router)
    yield
    memos.unregister()


def _branches(env):
    """R -> a -> a2 and R -> b. a: an AI card (X writes, B reviews, X reworks, count 1); a2 and b: a person's report
    with no review. -> (a, a2, b)."""
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_a2", "T"), node("tmp_b", "T")],
                     [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_a2"),
                      edge("tmp_e3", "tmp_r", "tmp_b")])
    a, a2, b = (body["key_map"][ref] for ref in ("tmp_a", "tmp_a2", "tmp_b"))
    for key in (a, a2, b):
        stored(key, "instruction", "omit")
    stored(a, "review_count", 1)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(a, execution=X, review=B)
    for key in (a2, b):
        roles(key, execution="not_used", review="not_used")
    return a, a2, b


def _loop_row(doc_id, run_id, stage):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    db_loops.insert({"run_id": run_id, "group_id": FA, "doc_ref": doc_id, "review_count": 1,
                     "reviewer_provider_id": B, "review_criteria": "document_type_default", "rework_provider_id": X,
                     "rework_timeout_sec": 3600, "failure_restart_max_attempts": 0, "total_timeout_sec": 14400,
                     "review_baseline_id": 0, "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t",
                     "deadline_at": "t", "current_stage": stage})


def _loop_end(env, exec_id, doc_id, run_id, stop_reason):
    """The loop of ``run_id`` stopped with ``stop_reason``, its Graph execution ended, its run ended on its own."""
    _sql(env["db"], "UPDATE graph_attempt_runs SET ai_run_id = ?, status = 'ended', ended_at = 't' WHERE exec_id = ?",
         (run_id, exec_id))
    detail = None if stop_reason == "review_passed" else "ended"     # the loop row's CHECK (migration 107)
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', stop_reason = ?, "
                    "stop_detail = ? WHERE run_id = ?", (stop_reason, detail, run_id))
    _run_ended(run_id, action_scope="review", doc_ref=doc_id, graph_exec_id=exec_id,
               document_review_loop={"current_stage": "stopped"})


@pytest.fixture()
def invoke(env, monkeypatch):
    """``POST /ai-invoke/start`` over the real route (its validation, its explicit-request span, its mention builder).
    The admission is a recorder that does what the engine does for the Graph: a review loop's first hop asks the
    Graph for its execution (``graph_run.execution.request_hop``, as ``ai_invoke.review`` does) inside the route's
    explicit request — the scheduler's replays of that execution are not recorded."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from flowgate.graph_run import execution
    from modules.flow_gate.api import token_routes
    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(token_routes, "_build_mention_for_token", lambda **kw: "STANDARD MENTION")
    requested: list[dict] = []

    def start_run(**kw):
        loop = kw.get("document_review_loop")
        if any(frame.name == "replay" for frame in traceback.extract_stack()):
            return {"run_id": REREVIEW_RUN if loop else REWORK_RUN, "status": "running"}
        entry = dict(kw)
        if kw.get("mention_builder"):
            entry["mention"] = kw["mention_builder"]("RAW", "scratch")
        if loop:
            entry["graph_exec_id"] = execution.request_hop(FA, {}, {"slot": {"doc_id": kw["doc_ref"]}}, "review")
        requested.append(entry)
        return {"run_id": REREVIEW_RUN if loop else REWORK_RUN, "status": "running"}

    client = TestClient(_app(FastAPI, ai_invoke_routes), raise_server_exceptions=False)

    def post(doc_ref, action_scope, **extra):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
        body = {"project": "flowgate", "module": "v02", "group": FA.rsplit(".", 1)[-1], "doc_ref": doc_ref,
                "action_scope": action_scope, "mode": "single", **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers={"Authorization": "Bearer tok"})

    return post, requested


def _app(FastAPI, routes):
    app = FastAPI()
    app.include_router(routes.router)
    return app


def test_an_ai_card_whose_rework_is_spent_needs_intervention_and_a_memo_rework_and_re_review_complete_it(
        env, decided, monkeypatch, starts, fork_join, memo_routes, invoke):
    from modules.flow_gate.db import documents as db_documents

    post, requested = invoke
    a, a2, b = _branches(env)
    command(env, "full")
    assert (node_run(env, a)["status"], node_run(env, b)["status"], node_run(env, a2)["status"]) == \
        ("open", "open", "pending")
    key, result, rr = _reviewed_ai_result(env, monkeypatch, starts, a)

    # 1) the automatic rework comes first: the loop asked for B's review with X's rework, and while it works the card
    #    is no intervention
    [loop] = [c for c in starts if c.get("document_review_loop")]
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["document_review_loop"]["rework_provider_id"],
            loop["doc_ref"]) == (B, X, result)
    _loop_row(result, REVIEW_RUN, "rework")                            # the loop's automatic rework hop works
    assert _wire(env, a)["intervention"] is None and b1(env) == []
    _loop_end(env, rr["intent_exec_id"], result, REVIEW_RUN, "review_count_exhausted")    # its rework spent

    # 2) needs_intervention: only a's descendant waits; the independent branch b completes
    assert one(env, a, "result_review", 1)["verdict"] == "not_passed:review_count_exhausted"
    assert [(d, r) for d, r, _m in b1(env)] == [(result, NOT_PASSED)]
    assert len([c for c in starts if c.get("document_review_loop")]) == 1      # no automatic retry of it
    write_line(env, b, "report", "## 결과\n\n본문\n")
    run = _read(env)
    shown = {n["node_key"]: n for n in run["node_runs"]}
    assert shown[a]["intervention"] == {"kind": "needs_intervention", "reason": NOT_PASSED}
    assert (shown[a]["status"], shown[b]["status"], shown[a2]["status"]) == ("review", "done", "pending")
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "needs_intervention")

    # 3) a memo, then the person's rework through the real route: the memo is in the AI's mention; its run's end
    #    completes nothing
    res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{a}/memos", json={"title": MEMO, "body": "v2 only",
                                                                           "ai_ref": True}, headers=_h())
    assert res.status_code == 201, res.text
    res = post(result, "edit", messages=["Apply the memo."])
    assert res.status_code == 200, res.text
    [edit] = requested
    assert edit["action_scope"] == "edit" and edit["document_review_loop"] is None
    assert "## User memos" in edit["mention"] and MEMO in edit["mention"]
    db_documents.update(result, {"revision_no": int(db_documents.get_by_id(result).get("revision_no") or 0) + 1})
    _run_ended(REWORK_RUN, action_scope="edit", doc_ref=result)        # the rework written, its run ended
    assert node_run(env, a)["status"] == "review" and _review_status(result) != "approved"
    assert _wire(env, a)["intervention"]["kind"] == "needs_intervention"

    # 4) the re-review through the real route: the explicit request takes the idle run back to running
    res = post(result, "review", document_review_loop={
        "review_count": 1, "review_criteria": "document_type_default", "rework_timeout_sec": 3600,
        "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "reviewer_provider_id": B,
        "rework_provider_id": X})
    assert res.status_code == 200, res.text
    review = requested[-1]
    assert (review["action_scope"], review["doc_ref"]) == ("review", result) and review["graph_exec_id"]
    assert _run_row(env)["status"] == "running"

    # 5) its pass completes the card automatically and opens the next card
    _loop_row(result, REREVIEW_RUN, "review")
    _loop_end(env, review["graph_exec_id"], result, REREVIEW_RUN, "review_passed")
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "open"
    run = _read(env)
    shown = {n["node_key"]: n for n in run["node_runs"]}
    assert shown[a]["intervention"] is None and shown[a]["awaiting_approval"] is False
    assert len([c for c in starts if c.get("document_review_loop")]) == 1     # the card stage asked no other review
