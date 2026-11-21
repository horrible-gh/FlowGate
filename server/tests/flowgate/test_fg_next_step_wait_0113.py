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
    """The instruction submitted as the AI's inbox submission leaves it (``pending_review``)."""
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
    # (T-2 rework): its review that did not start is a system error of the instruction stage —
    # the node run tells it and the run is in its error family, never a person's wait
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_not_started"}

    run = _step(env)                                             # [다음 단계]
    assert (run["status"], run["idle_reason"]) == ("idle", "error")
    assert env["jobs"] == [] and _review_status(instruction) == "pending_review"
    assert node_run(env, key)["stage"] == "instruction"


# ── the lost result stage of an approved instruction ─────────────────────────



# ── the assigned path of the same state (L-2 §2.7 / §2.8 / §2.11) ────────────









# ── the Document window's [저장] (the content PATCH) ──────────────────────────



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
