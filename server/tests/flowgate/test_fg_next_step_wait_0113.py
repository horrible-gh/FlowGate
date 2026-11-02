"""flowgate.v02.0113 T#3 rework (0025-T) — [다음 단계] never hides a card's wait as ``no_targets``.

On a separate SQLite DB (the ``test_fg_card_settings`` / ``test_fg_ai_roles_l2_assignment`` harness: real routes, Graph
engine, card settings / documents / approval ports; every AI start and review loop is a recorder, nothing calls an AI)
this suite rebuilds the state the reported Flow was read in with side-effect-free reads (test2.default.0084, never
touched here): an AI card of a pre-feature attempt open in its instruction stage, its instruction written and submitted
(``pending_review``) and judged by nobody, its writer's question unanswered after its responder failed, the run idle
``no_targets`` ("실행할 카드 없음"). Its own documents, Flow, runs, attempts and tokens are the fixture's.

- the question wait and the unjudged instruction are a person's wait (``waiting_approval``) and the asking card shows its
  question mark; a step command approves, starts and requests nothing for them;
- the assigned path of the same state (L-2 §2.7 / §2.8 / §2.11): an instruction review AI is asked once (its provider),
  'not used' goes on without a person's approval once the content is saved, an unreadable setting waits with its cause;
- an approved instruction whose result stage was lost starts it once, never while its writer's questions wait;
- a submitted instruction or result (``pending_review``) with no review assignment and no contact (it was lost): its
  review AI is asked once — the expected provider, and the model its launch resolves (the launch runs down to its
  process spawn, which is a spy: nothing is executed) —, 'not used' goes on without a person's approval, an unreadable
  setting waits with its cause; repeated contacts and reads ask and approve nothing more, and an existing assignment
  wins over a review AI changed since;
- a command's answer is the run as its post-commit jobs and settlement left it (never the ``no_targets`` of before);
- an empty or blank instruction keeps the manual write wait, a card waiting for its AI setup is no ``no_targets`` either;
- the run reads the investigation used (``GET /runs/{id}``, ``GET /flows/{id}/runs``) reconcile, settle, approve and start
  nothing.
"""
from __future__ import annotations

import threading
import time
import traceback
from types import SimpleNamespace as NS

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    A, B, FA, X, _review_status, _sql, _write_written, assignments, b1, db_path, decided, env, events, flow, line_doc,
    node_run, one, roles, save_instruction, start, starts, stored, tokens, wired, write_line,
)
from test_fg_card_settings import AI, _h, _run_one_card, _start, legacy_cards, legacy_setting, pre_feature_attempts  # noqa: F401
from test_fg_card_settings_review import INSTRUCTION_RUN, REVIEW_RUN, _admin_and_reviewer, _start_ai
from test_fg_question_pending_0111 import QUESTION, ROOT, _ai_run_finished, _ended, questions  # noqa: F401

BODY = "## 작업\n\n지시 AI가 쓴 지시서\n"


# ── helpers ──────────────────────────────────────────────────────────────────

def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _step(env):
    """[다음 단계] as the client sends it (one step command, ``flowRunControl``)."""
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": "step"}, headers=_h())
    assert res.status_code in (200, 201), res.text
    return res.json()["run"]


def _wire(run, key):
    return next(n for n in run["node_runs"] if n["node_key"] == key)


def _executions(env):
    return _sql(env["db"], "SELECT exec_id, status FROM graph_attempt_runs ORDER BY created_at, exec_id")


def _run_row(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason, last_event_seq FROM graph_runs")[0]


def _submitted(doc_id):
    """The instruction submitted as the AI's inbox submission leaves it (0084: 0004-T ``pending_review``)."""
    from modules.flow_gate.db import documents as db_documents

    db_documents.update(doc_id, {"doc_review_status": "pending_review"})


def _ai_instruction(env, monkeypatch, *, settings=(), end=True):
    """An AI card whose instruction AI wrote the card's instruction (an edit start into the line's empty document) and
    ended on its own; ``end``: its run-end contact delivered (else lost). -> (node key, instruction, execution id)."""
    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    for field, value in settings:
        legacy_setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert (job["stage"], job["action_scope"]) == ("instruction", "edit")
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"], BODY)
    _ai_run_finished(env, INSTRUCTION_RUN, job["graph_exec_id"], doc_ref=job["target_doc_id"])
    if end:
        _ended(env, INSTRUCTION_RUN, job["graph_exec_id"], action_scope="edit", doc_ref=job["target_doc_id"])
    return key, job["target_doc_id"], job["graph_exec_id"]


# ── 0084 state 1: the writer's question, its responder failed ────────────────

@pytest.mark.parametrize("pre_feature", [True, False])
def test_a_card_whose_ai_question_waits_after_its_responder_failed_is_a_person_s_wait_never_no_targets(
        legacy_cards, pre_feature_attempts, env, decided, monkeypatch, questions, pre_feature):
    from modules.flow_gate.db import questions as db_questions

    if not pre_feature:
        pre_feature_attempts()                                   # the feature on: the assigned path of the same card
    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"], BODY)
    questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION)]       # asked on the R document, as 0084's item 51
    _ai_run_finished(env, INSTRUCTION_RUN, job["graph_exec_id"], doc_ref=job["target_doc_id"])
    _ended(env, INSTRUCTION_RUN, job["graph_exec_id"], action_scope="edit", doc_ref=job["target_doc_id"])
    instruction = job["target_doc_id"]
    assert questions["responders"] == [(901, "aip_review")]
    questions["asked"][INSTRUCTION_RUN][0].update(responder_state="failed", responder_error_code="provider_failed")
    _submitted(instruction)
    assert (node_run(env, key)["status"], node_run(env, key)["stage"]) == ("open", "instruction")
    execs = len(_executions(env))

    run = _step(env)                                             # [다음 단계]
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    assert _current(env)["idle_reason"] == "needs_intervention"   # a read settles it the same
    assert env["jobs"] == [] and len(_executions(env)) == execs  # no AI start, no execution request
    assert _review_status(instruction) == "pending_review"       # nothing approved it
    assert questions["responders"] == [(901, "aip_review")]      # the failed responder is not asked again
    assert node_run(env, key)["dispatch_error_code"] is None

    monkeypatch.setattr(db_questions, "list_open_doc_ids_by_group", lambda group_id: [ROOT])
    read = env["client"].get(f"/api/v1/runs/{run['run_id']}", headers=_h()).json()["run"]
    assert _wire(read, key)["question"] is True                  # the asking card shows 응답 대기, not 작성 대기


# ── 0084 state 2: a written instruction no AI judges any more ────────────────

def test_a_pre_feature_instruction_nobody_judges_is_a_person_s_wait_and_the_reads_change_nothing(
        legacy_cards, pre_feature_attempts, env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.graph_workflow.services import card_settings_ports, result_binder, run_service

    monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)
    key, instruction, _exec = _ai_instruction(env, monkeypatch, settings=(("instruction_review_count", 1),))
    pre_feature_attempts()                       # the feature on, the attempt pre-feature to its end (as 0084's)
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "review_not_started")]   # nobody reviews it
    _submitted(instruction)
    assert node_run(env, key)["stage"] == "instruction" and node_run(env, key)["dispatch_error_code"] is None

    # the investigation's reads: no reconciliation, no settlement, no approval, no start; nothing written
    calls = []
    for owner, name in ((card_settings_ports, "reconcile_stage"), (result_binder, "reconcile_if_needed"),
                        (run_service, "settle_if_needed"), (legacy_adapter, "approve"),
                        (legacy_adapter, "start_result_stage")):
        real = getattr(owner, name)
        monkeypatch.setattr(owner, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))
    before = (_run_row(env), _sql(env["db"], "SELECT * FROM graph_node_runs ORDER BY node_key"),
              _sql(env["db"], "SELECT doc_id, doc_review_status, revision_no FROM documents ORDER BY doc_id"))
    run_id = before[0]["run_id"]
    listed = env["client"].get(f"/api/v1/flows/{FA}/runs?limit=20&offset=0", headers=_h())
    detail = env["client"].get(f"/api/v1/runs/{run_id}", headers=_h())
    assert listed.status_code == 200 and detail.status_code == 200
    assert calls == []
    assert before == (_run_row(env), _sql(env["db"], "SELECT * FROM graph_node_runs ORDER BY node_key"),
                      _sql(env["db"], "SELECT doc_id, doc_review_status, revision_no FROM documents ORDER BY doc_id"))
    wire = _wire(detail.json()["run"], key)
    assert (wire["stage"], wire["instruction_doc_id"], wire["stage_assignments"]) == ("instruction", instruction, [])
    # flowgate.v02.0119 T#3 (T-2 rework): its review that did not start is a system error of the instruction stage —
    # the node run tells it and the run is in its error family, never a person's wait
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_not_started"}

    run = _step(env)                                             # [다음 단계]
    assert (run["status"], run["idle_reason"]) == ("idle", "error")
    assert env["jobs"] == [] and _review_status(instruction) == "pending_review"
    assert node_run(env, key)["stage"] == "instruction"


# ── the lost result stage of an approved instruction ─────────────────────────

@pytest.mark.parametrize("asked", [False, True])
def test_an_approved_instruction_whose_result_stage_was_lost_starts_it_once_never_while_its_questions_wait(
        legacy_cards, pre_feature_attempts, env, decided, monkeypatch, questions, asked):
    from modules.flow_gate.db import documents as db_documents

    if asked:
        questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION, responder_state="failed")]
    key, instruction, _exec = _ai_instruction(env, monkeypatch, end=False)      # its run-end contact lost
    db_documents.update(instruction, {"doc_review_status": "approved"})           # approved, the stage not moved
    assert node_run(env, key)["stage"] == "instruction"

    for _ in range(3):                                           # every read: once
        _current(env)
    if asked:                                                    # its questions first (0111): nothing starts
        assert env["jobs"] == [] and node_run(env, key)["stage"] == "instruction"
        assert _current(env)["idle_reason"] == "needs_intervention"
        return
    [job] = env["jobs"]
    assert (job["stage"], job["ref_doc_ids"]) == ("result", [instruction])
    row = node_run(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", instruction)
    assert len([e for e in _executions(env) if e["status"] == "requested"]) == 1


# ── the assigned path of the same state (L-2 §2.7 / §2.8 / §2.11) ────────────

def test_an_assigned_instruction_saved_without_its_contact_is_reviewed_once_by_its_review_ai(env, decided, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    _write_written(instruction, BODY)                            # saved; its contact (CONTENT_SAVED) lost
    _submitted(instruction)
    for _ in range(3):
        run = _current(env)                                      # RC-6: judged once
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["provider_id"], ir["author"]) == ("ai", B, "person")
    loops = [c for c in starts if c.get("document_review_loop")]
    assert len(loops) == 1                                       # one request, to the assigned provider
    assert (loops[0]["document_review_loop"]["reviewer_provider_id"], loops[0]["doc_ref"]) == (B, instruction)
    assert [c for c in starts if not c.get("document_review_loop")] == []
    assert _review_status(instruction) != "approved" and run["idle_reason"] != "no_targets"


def test_not_used_roles_go_on_without_a_person_s_approval_once_the_content_is_saved(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    for blank in ("", "   \n\t\n"):                             # nothing written: the manual write wait stays
        _write_written(instruction, blank)
        run = _step(env)
        assert run["idle_reason"] == "needs_intervention" and node_run(env, key)["stage"] == "instruction"
        assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"
    _write_written(instruction, BODY)                            # saved, its contact lost: the read judges it
    _submitted(instruction)
    _current(env)
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"
    assert one(env, key, "instruction_review", 1)["verdict"] == "auto_approved"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"     # the report now
    report = write_line(env, key, "report", "## 결과\n\n사람이 쓴 레포트\n")["doc_id"]
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"
    assert one(env, key, "result_review", 1)["verdict"] == "auto_approved"
    assert starts == [] and env["jobs"] == []                    # no AI at all
    before = assignments(env, key)
    save_instruction(env, key)                                   # the same contact again, then reads: nothing more
    _current(env)
    assert assignments(env, key) == before


def test_an_unreadable_instruction_review_setting_waits_with_its_cause_and_approves_nothing(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    stored(key, "instruction_review_count", "two")               # a value that cannot be told
    start(env)
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["actor_reason"]) == ("wait_setup", "setting_unreadable")
    # flowgate.v02.0119 T#3 (T-2 rework): its ``review_result_unknown`` is a system error — the error family (never
    # "nothing to run", never a person's wait), the setting read again by the reads up to the limit
    assert _current(env)["idle_reason"] == "error"
    wire = _wire(_current(env), key)
    wait = wire["wait_reason"]
    assert (wait["code"], wait["role"]) == ("ai_setup_required", "review")
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_result_unknown"}

    # [다음 단계]: the explicit command opens the waiting attempt again (L-2 §2.10 REOPEN_NEW_ATTEMPT); the new attempt
    # reuses the written instruction, whose review the same setting keeps waiting — and once that post-commit step is
    # over the run is judged again (it stayed ``no_targets`` before).
    run = _step(env)
    [active] = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                    (key,))
    assert active["attempt_id"] != ir["attempt_id"]
    agains = [a for a in assignments(env, key)
              if a["attempt_id"] == active["attempt_id"] and a["stage"] == "instruction_review"]
    again = max(agains, key=lambda a: a["seq"])
    assert all((a["actor"], a["actor_reason"], a["basis_doc_id"]) == ("wait_setup", "setting_unreadable", instruction)
               for a in agains)
    # the command's answer itself (the client puts it in its run state at once): the wait of the new attempt, its
    # review assignment and its cause — never the ``no_targets`` it told before its post-commit step had run; its
    # ``review_result_unknown`` is the error family (0119 T-2 rework)
    assert (run["status"], run["idle_reason"]) == ("idle", "error")
    wire = _wire(run, key)
    assert wire["attempt_id"] == active["attempt_id"]
    shown = max((x for x in wire["stage_assignments"] if x["stage"] == "instruction_review"), key=lambda x: x["seq"])
    assert (shown["actor"], shown["actor_reason"]) == ("wait_setup", "setting_unreadable")
    assert (wire["wait_reason"]["code"], wire["wait_reason"]["role"]) == ("ai_setup_required", "review")
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "error")
    assert _review_status(instruction) != "approved" and starts == [] and env["jobs"] == []


def test_a_card_waiting_for_its_ai_setup_is_no_no_targets(env, decided, starts):
    """pending_init / wait_setup (T#1's first setup): the account has no role defaults and AIs are selectable — the card
    waits for its setup (the client's [다음 단계] asks for it first, ``beforeRunPrepare``); the run never says
    "nothing to run", and nothing is written to the account."""
    key = flow(env)
    roles(key, init="pending_init")
    run = _step(env)
    ia = one(env, key, "instruction")
    assert ia["actor"] == "wait_setup"
    assert run["idle_reason"] == "needs_intervention"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "ai_setup_required"
    assert starts == [] and env["jobs"] == []
    assert _sql(env["db"], "SELECT * FROM user_ai_role_defaults") == []


# ── the Document window's [저장] (the content PATCH) ──────────────────────────

def test_a_content_save_judges_the_person_s_instruction_once_its_flow_guard_is_left(env, decided, starts, monkeypatch):
    """The content PATCH saves under the Flow guard and its contact (CONTENT_SAVED) takes that guard to judge the person's
    instruction stage. Run from the save's commit callbacks — the guard's lock still held — it waited the lock out
    (``FlowLockTimeout``, found on flowgate.test 0090) and judged nothing until a run read; now it runs once the guard is
    left: a blank save keeps the wait, a written one is approved and its result stage starts at the save."""
    import time

    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.graph_workflow.services import flow_guard

    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 2)            # a contact inside the guard fails fast
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    user = {"user_id": "u_kim", "is_admin": True}

    documents.update_document_content(instruction, documents.DocumentContentUpdate(
        content="---\ntitle: 지시서\n---\n   \n  "), current_user=user)
    assert node_run(env, key)["stage"] == "instruction"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"

    began = time.monotonic()
    documents.update_document_content(instruction, documents.DocumentContentUpdate(
        content="---\ntitle: 지시서\n---\n## 작업\n\n화면에서 저장한 지시서\n"), current_user=user)
    assert time.monotonic() - began < 2                                  # no lock wait
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"   # at the save
    assert one(env, key, "instruction_review", 1)["verdict"] == "auto_approved"
    assert starts == [] and env["jobs"] == []


# ── a submission in review with no review assignment and no contact (L-2 §2.7 / §2.8 / §2.11) ──

REVIEW_MODEL = "rv-model-b"
SAVED_REPORT = "## 결과\n\n사람이 쓴 레포트\n"


@pytest.fixture()
def launches(env, monkeypatch, tmp_path):
    """The review AI's start as far as its process spawn, nothing executed. The AI invoke admission's entry
    (``ai_invoke_service.start_run``) is a spy that records each request (an engine replay of the same requested
    execution is no new request, as ``starts`` counts) and launches it as the admission and its worker do: the loop's
    provider by admission's own resolver (``review.resolve_loop_provider`` on the loop and its baseline), that provider
    out of the project's effective chain (``ai_settings_service.resolve_effective``, the pinned pick), and the worker's
    real launch of it (``worker._execute_provider_chain`` -> ``provider_cli._cli_execute``, which resolves the requested
    model, ``invocations.cli_prepared``). Only the process spawn (``provider_cli._execute_local``) is a spy: it records
    the command and answers a finished process. The review AI (B) is a CLI provider whose command names its model.
    Not run here: the admission's lease, token, scratch and worktree preparation."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.ai_invoke import admission, provider_cli, worker
    from modules.flow_gate.services.ai_invoke import review as loop_review
    from modules.flow_gate.settings import ai_settings_service

    _sql(env["db"], "UPDATE ai_providers SET kind = 'copilot', cli_command = ? WHERE provider_id = ?",
         (f"copilot --model {REVIEW_MODEL} --no-ask-user", B))
    out = {"requests": [], "spawns": [], "runs": []}

    def spawn(exec_id, cwd, prompt, env_, *, command, run, decision, started, stopped):
        out["spawns"].append({"command": command, "provider_id": run.get("provider_id"), "exec_id": exec_id})
        output = ('{"type":"assistant.message","data":{"model":"%s"}}' % REVIEW_MODEL).encode()
        return NS(proc=NS(returncode=0), launched_mono=time.monotonic()), (output, b"", False, None)

    def start_run(**kw):
        if any(frame.name == "replay" for frame in traceback.extract_stack()):
            return {"run_id": "rv_replay", "status": "running"}
        out["requests"].append(kw)
        loop = dict(kw["document_review_loop"])
        loop.update(admission._svc().compute_review_baseline(kw["doc_ref"]))
        stage = admission.REWORK_HOP_KIND if loop["starts_with_rework"] else admission.REVIEW_HOP_KIND
        provider_id = loop_review.resolve_loop_provider(loop, stage)
        chain = ai_settings_service.resolve_effective(kw.get("project_id")).get("providers") or []
        [selected] = [p for p in chain if p.get("id") == provider_id]
        run_id = f"rv_run_{len(out['requests'])}"
        scratch = tmp_path / run_id
        scratch.mkdir()
        run = {"run_id": run_id, "invocation_origin": kw.get("invocation_origin"),
               "graph_exec_id": kw.get("graph_exec_id"), "provider_id": provider_id,
               "requested_provider_id": provider_id, "document_review_loop": loop, "hop_kind": stage,
               "fallback_history": [], "cancel_event": threading.Event(), "timed_out": False,
               "scratch_dir": str(scratch), "token_scratch_dir": str(scratch), "raw_token": "fake-token-only",
               "api_base_url": ""}
        assert worker._execute_provider_chain(run, [selected], "fake prompt") is True
        out["runs"].append(run)
        return {"run_id": REVIEW_RUN, "status": "running"}

    monkeypatch.setattr(provider_cli, "_execute_local", spawn)
    monkeypatch.setattr(provider_cli, "_recover_cli_last_message", lambda *a: None)
    monkeypatch.setattr(worker, "_provider_prompt", lambda *a: "fake prompt")
    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    return out


def _lost_instruction(env, review):
    """A person's instruction saved and submitted (``pending_review``), its contact lost: no review assignment.
    ``review``: ``B`` (its review AI), ``not_used`` or ``unreadable`` (a review count that cannot be told)."""
    extra = (("instruction_review_count", 1), ("instruction_review_ai", B)) if review == B else ()
    key = flow(env, extra_settings=extra)
    roles(key, "not_used", "not_used", "not_used")
    if review == "unreadable":
        stored(key, "instruction_review_count", "two")
    start(env)
    instruction = line_doc(env, key, "instruction")
    _write_written(instruction, BODY)
    _submitted(instruction)
    assert _review_status(instruction) == "pending_review"
    assert one(env, key, "instruction_review", 1) is None                # nothing judged it
    assert node_run(env, key)["stage"] == "instruction"
    return key, instruction


def _lost_result(env, monkeypatch, review):
    """A person's report registered and submitted (``pending_review``, the node in review), its contact lost
    (``manual_stage.person_result`` answered no job): no review assignment. ``review``: ``B`` (its review role),
    ``not_used`` or ``unreadable`` (no review slot on a card made before the role feature whose review count cannot be
    told: the legacy review fields' R-2, ``store_error``)."""
    from flowgate.card_settings import manual_stage

    key = flow(env)
    if review == "unreadable":
        roles(key, "not_used", "not_used", init="legacy")
        stored(key, "review_count", "two")
    else:
        roles(key, "not_used", "not_used", "not_used" if review == "not_used" else B)
    start(env)
    save_instruction(env, key)                                   # approved (no instruction review): the result stage
    assert node_run(env, key)["stage"] == "result"
    with monkeypatch.context() as m:
        m.setattr(manual_stage, "person_result", lambda *a, **k: None)
        report = write_line(env, key, "report", SAVED_REPORT)["doc_id"]
    assert _review_status(report) == "pending_review"
    row = node_run(env, key)
    assert (row["status"], row["result_doc_id"]) == ("review", report)
    assert one(env, key, "result_review", 1) is None                     # nothing judged it
    return key, report


def _again(env, key, stage, doc_id):
    """The same contact again, the reconciliation of three run reads, the contact once more; the last run read."""
    from flowgate.card_settings import manual_stage
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    def contact():
        if stage == "instruction":
            card_settings_ports.instruction_saved(FA, doc_id)
            return
        manual_stage.follow_person_result(FA, node_run(env, key)["run_id"], key, doc_id,
                                          one(env, key, "result")["attempt_id"])

    contact()
    for _ in range(3):
        _current(env)
    contact()
    return _current(env)


@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_is_reviewed_once_by_its_review_ai_and_its_model(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, B)
    else:
        key, doc_id = _lost_result(env, monkeypatch, B)
    review = "instruction_review" if stage == "instruction" else "result_review"

    run = _current(env)                                          # the run read's reconciliation (RC-6 / RC-3)
    a = one(env, key, review, 1)
    assert (a["actor"], a["provider_id"], a["author"]) == ("ai", B, "person")
    assert a["basis_doc_id"] == doc_id and a["intent_kind"] == "execution"
    [request] = launches["requests"]                             # one request, to the assigned provider
    assert (request["document_review_loop"]["reviewer_provider_id"], request["doc_ref"]) == (B, doc_id)
    [spawn] = launches["spawns"]                                 # one launch, down to the (spy) process spawn
    assert spawn["provider_id"] == B and f"--model {REVIEW_MODEL}" in spawn["command"]
    [record] = launches["runs"][0]["invocations"]                # the launch's own evidence of what it asked for
    assert (record["requested_ai_id"], record["actual_ai_id"]) == (B, B)
    assert (record["requested_model_state"], record["requested_model_value"]) == ("value", REVIEW_MODEL)
    assert record["purpose"] == ("instruction_review_round" if stage == "instruction" else "result_review_round")
    assert (record["target_stage"], record["target_seq"], record["target_doc_id"]) == (review, 1, doc_id)
    assert _review_status(doc_id) == "pending_review" and run["idle_reason"] != "no_targets"

    # an existing assignment wins: the card's review AI changed since asks nobody else
    if stage == "instruction":
        stored(key, "instruction_review_ai", A)
    else:
        roles(key, "not_used", "not_used", A)
    before = assignments(env, key)
    run = _again(env, key, stage, doc_id)
    assert assignments(env, key) == before
    assert len(launches["requests"]) == 1 and len(launches["spawns"]) == 1
    assert _review_status(doc_id) == "pending_review" and run["idle_reason"] != "no_targets"


@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_whose_review_is_not_used_goes_on_without_a_person(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, "not_used")
    else:
        key, doc_id = _lost_result(env, monkeypatch, "not_used")
    review = "instruction_review" if stage == "instruction" else "result_review"

    _current(env)
    a = one(env, key, review, 1)
    assert (a["actor"], a["verdict"]) == ("no_review", "auto_approved")
    assert _review_status(doc_id) == "approved"
    row = node_run(env, key)
    if stage == "instruction":
        assert (row["status"], row["stage"]) == ("open", "result")   # the result stage, waiting for its report
    else:
        assert row["status"] == "done"

    def state():
        return (assignments(env, key), _sql(env["db"], "SELECT doc_id, doc_review_status FROM documents ORDER BY doc_id"),
                _sql(env["db"], "SELECT node_key, status, stage FROM graph_node_runs ORDER BY node_key"))

    before = state()
    _again(env, key, stage, doc_id)
    assert state() == before                                     # no second approval, assignment or node change
    assert launches["requests"] == [] and launches["spawns"] == [] and env["jobs"] == []


@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_whose_setting_is_unreadable_waits_with_its_cause(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, "unreadable")
    else:
        key, doc_id = _lost_result(env, monkeypatch, "unreadable")
    review = "instruction_review" if stage == "instruction" else "result_review"

    run = _current(env)
    a = one(env, key, review, 1)
    if stage == "instruction":
        assert (a["actor"], a["actor_reason"]) == ("wait_setup", "setting_unreadable")
    else:
        assert (a["actor"], a["source"]) == ("wait_setup", "store_error")
    # a wait with its cause, never "nothing to run"; flowgate.v02.0119 T#3 (T-2 rework): the instruction's
    # ``review_result_unknown`` is a system error (the error family), the result's store error a setup wait
    assert run["idle_reason"] == ("error" if stage == "instruction" else "needs_intervention")
    wait = _wire(run, key)["wait_reason"]
    assert (wait["code"], wait["role"]) == ("ai_setup_required", "review")
    assert stage == "instruction" or wait["detail"] == "store_error"
    before = assignments(env, key)
    run = _again(env, key, stage, doc_id)
    if stage == "instruction":                                   # read again up to the limit, then never
        reviews = [a for a in assignments(env, key) if a["stage"] == review]
        assert [(a["seq"], a["actor"], a["actor_reason"], a["stage_state"]) for a in reviews] == \
            [(1, "wait_setup", "setting_unreadable", "failed"), (2, "wait_setup", "setting_unreadable", "failed"),
             (3, "wait_setup", "setting_unreadable", "waiting")]
        assert run["idle_reason"] == "error"
        assert _wire(run, key)["intervention"] == {"kind": "system_error", "reason": "review_result_unknown"}
        after = assignments(env, key)
        _current(env)
        assert assignments(env, key) == after
    else:
        assert assignments(env, key) == before and run["idle_reason"] == "needs_intervention"
        assert _wire(run, key)["intervention"] == {"kind": "needs_intervention", "reason": "completion_missing"}
    assert _review_status(doc_id) == "pending_review"            # nothing approved it, no person's approval added
    assert launches["requests"] == [] and launches["spawns"] == [] and env["jobs"] == []
