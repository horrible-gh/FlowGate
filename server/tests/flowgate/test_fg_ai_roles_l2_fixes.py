"""(TR rework) — regressions of the review findings on the assigned path.

The harness of ``test_fg_ai_roles_l2_assignment`` (real SQLite DB and routes, the feature on, recorders for every AI
start; nothing calls a real AI). One block per finding:

- T-2: a person's instruction line write registers with the instruction stage token (L-2 §2.6, T §4.3);
- the explicit command's attempt replacement under the exclusive Stop gate (T §4.5);
- a failed person stage is never progressed by a save or a read (E-15a / E-15b / E-40);
- only the reviewed revision is approved (K-6, L-2 §2.8.4) — results and instructions;
- the written-by evidence of the current revision (T §4.2, K-8, E-44);
- an unknown invalidated-document set judges nothing (L-2 §2.7.2 C2, §2.14);
- ``assignment_unreadable`` once per submission after a commit, none from the reconciliation (T §4.6);
- an unreadable assignment is the node's error family on the run read (T §4.6);
- an admission refusal of a review loop keeps its own code (L-2 §2.10, E-13).

A test that breaks a store read puts back only that read (``_break_reads``' repair), never ``monkeypatch.undo()``:
the suite's AI recorders stay patched to the end.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    A, B, FA, REVIEW_RUN, STAGE_ASSIGNMENTS, WORK_RUN, X, Y, _doc, _failing_issue, _review_status, _sql,
    _write_written, ai_writes_result, assignments, b1, command, db_path, decided, env, events, flow, line_doc, node_run,
    one, record_starts, roles, save_instruction, start, starts, stored, tokens, wired, write_line,
)
from test_fg_card_settings_review import _run_ended  # noqa: E402
from test_fg_ai_roles_l2_review import _loop_ended, _review_end  # noqa: E402


def _read(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _wire(env, key):
    return next(n for n in _read(env)["node_runs"] if n["node_key"] == key)


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status FROM graph_node_attempts WHERE node_key = ? ORDER BY created_at",
                (key,))


def _run_row(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason FROM graph_runs")[0]


def _unreadable(env):
    return [(p["doc_id"], p["stage"], p["attempt_id"], p.get("submission_mark"))
            for r, p in events(env, "auto_approval_skipped") if r == "assignment_unreadable"]


def _break_reads(monkeypatch, *names):
    """The assignment store's reads fail (a store outage): ``AssignmentUnreadable`` for every consumer. Returns the
    repair (only these reads put back)."""
    from flowgate.ai_roles import assignment_store

    saved = {name: getattr(assignment_store, name) for name in names or ("read", "latest", "for_node")}
    for name in saved:
        monkeypatch.setattr(assignment_store, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store down")))
    return lambda: [monkeypatch.setattr(assignment_store, name, real) for name, real in saved.items()]


def _person_card(env, doc_type="T", **settings):
    key = flow(env, doc_type, extra_settings=tuple(settings.items()))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    return key


def _set_revision(doc_id, revision):
    """The document's revision moved (an edit), in the caller's transaction when there is one."""
    from modules.flow_gate.db.connection import get_store

    get_store()._execute("UPDATE documents SET revision_no = ? WHERE doc_id = ?", [revision, doc_id])


# ── T-2: a person's instruction line write (L-2 §2.6, T §4.3) ─────────────────

@pytest.mark.parametrize("doc_type, ins_type", [("T", "T"), ("TS", "TS")])
def test_t2_a_person_instruction_line_write_registers_with_the_instruction_stage_token(env, decided, starts, doc_type,
                                                                                      ins_type):
    """RUN (instruction T) and TEST (its TS line): [작성] on the instruction line with a live T-2 — the document made, T-2
    consumed (CAS), the card's empty draft superseded, the binder told after the commit (REGISTERED) — so the person's
    instruction is judged (no review: the system approves it) and the result stage starts; the node token stays."""
    key = _person_card(env, doc_type)
    draft = line_doc(env, key, "instruction")
    [t2] = [t for t in tokens(env, key) if t["stage"] == "instruction"]
    [t1] = [t for t in tokens(env, key) if t["stage"] is None]
    answer = write_line(env, key, "instruction", "## 작업\n\n사람이 [작성]으로 쓴 지시서\n")
    doc_id = answer["doc_id"]
    assert (answer["doc_type"], answer["bound_as_result"]) == (ins_type, False) and doc_id != draft
    spent = {t["token_id"]: t["consumed_at"] for t in tokens(env, key)}
    assert spent[t2["token_id"]] is not None and spent[t1["token_id"]] is None      # T-2 spent, T-1 kept
    assert _sql(env["db"], "SELECT superseded_by FROM documents WHERE doc_id = ?", (draft,))[0]["superseded_by"] == doc_id
    assert line_doc(env, key, "instruction") == doc_id
    row = node_run(env, key)
    assert (row["instruction_doc_id"], row["stage"]) == (doc_id, "result")
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["verdict"], ir["author"], ir["basis_doc_id"]) == ("no_review", "auto_approved", "person",
                                                                              doc_id)
    assert _review_status(doc_id) == "approved"
    assert one(env, key, "result")["actor"] == "person"
    live = [t["token_id"] for t in tokens(env, key) if t["stage"] is None and t["consumed_at"] is None]
    assert live == [t1["token_id"]]                                                 # no T-3: the node token lives
    assert starts == [] and env["jobs"] == []
    again = write_line(env, key, "instruction", "다시", expect=409)                 # T-2 spent: the existing rule
    assert again["error"]["code"] == "slot_has_document"


def test_t2_missing_keeps_the_draft_path_and_its_save_is_judged(env, decided, monkeypatch):
    """E-15: T-2's issue failed — the line write is the existing one (the card's draft on the line: 409), and the
    draft's save is judged (CONTENT_SAVED)."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    _failing_issue(monkeypatch, {2})
    start(env)
    assert [t["stage"] for t in tokens(env, key)] == [None]
    refused = write_line(env, key, "instruction", "## 작업\n\n본문\n", expect=409)
    assert refused["error"]["code"] == "slot_has_document"
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"


def test_t2_a_lost_token_race_leaves_no_document_no_consume_and_no_file(env, decided, monkeypatch):
    """Two writers: the CAS consume lost (the token spent by another) — 409 ``node_token_unavailable``, the made document
    rolled back with its file removed, the line and the draft untouched."""
    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_documents import repository as card_repository
    from modules.flow_gate.storage import paths as storage_paths

    key = _person_card(env)
    draft = line_doc(env, key, "instruction")
    made = []
    real_create = documents.create_document

    def create(*a, **k):
        doc_id = real_create(*a, **k)
        doc = documents.document(doc_id)
        made.append(storage_paths.resolve_storage_path(doc["file_path"], "flowgate",
                                                       branch=card_repository.project_branch("flowgate")))
        return doc_id

    monkeypatch.setattr(documents, "create_document", create)
    monkeypatch.setattr(documents, "register_result", lambda doc_id, token_id, user_id: False)
    before = _sql(env["db"], "SELECT COUNT(*) AS n FROM documents")[0]["n"]
    lost = write_line(env, key, "instruction", "## 작업\n\n늦은 쓰기\n", expect=409)
    assert lost["error"]["code"] == "node_token_unavailable"
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM documents")[0]["n"] == before
    assert line_doc(env, key, "instruction") == draft
    assert len(made) == 1 and made[0] is not None and not made[0].exists()        # its file removed too
    assert [t["consumed_at"] for t in tokens(env, key) if t["stage"] == "instruction"] == [None]
    assert one(env, key, "instruction_review", 1) is None


# ── T §4.5: the replacement waits for the old attempt's side effect ──────────

def test_t45_reopen_waits_out_the_old_attempt_s_side_effect_and_its_late_contact_touches_nothing(env, decided,
                                                                                               monkeypatch, starts):
    """An old attempt's side effect in flight (the Stop gate's shared hold) holds the explicit command back: the command
    takes the gate exclusively before its guard, so the old attempt stays active until the side effect ends; after the
    replacement the old attempt's late contact is refused and changes nothing of the new attempt."""
    from flowgate.card_settings import legacy_adapter, manual_stage

    # an unavailable review wait is resolved as the next review seq of its attempt, not a replacement; the
    # replacement this test fences is a failed stage's (here the person's instruction stage, its token not issued).
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    _failing_issue(monkeypatch, {1})
    start(env)
    instruction = save_instruction(env, key)
    assert one(env, key, "instruction")["stage_state"] == "failed"
    run_id = _run_row(env)["run_id"]
    [old] = [a["attempt_id"] for a in _attempts(env, key)]
    entered, release, seen = threading.Event(), threading.Event(), {}

    def old_side_effect():
        # a person's step of the old attempt (the idle run taken, as a save's judgement is): the gate's shared hold
        with legacy_adapter.person_hold(FA, run_id, key, old, "late_side_effect") as owned:
            seen["owned"] = owned
            entered.set()
            release.wait(20)
            seen["during"] = [a["status"] for a in _attempts(env, key)]

    def commanding():
        try:
            seen["run"] = command(env)
        except BaseException as exc:  # noqa: BLE001 — reported by the assertion below
            seen["error"] = exc

    worker = threading.Thread(target=old_side_effect)
    worker.start()
    assert entered.wait(10)
    commander = threading.Thread(target=commanding)
    commander.start()
    time.sleep(1.0)
    assert commander.is_alive(), seen.get("error")                      # waiting for the side effect to end
    assert [a["status"] for a in _attempts(env, key)] == ["active"]
    release.set()
    worker.join(20)
    commander.join(30)
    assert not commander.is_alive() and "error" not in seen, seen.get("error")
    assert seen["owned"] is True and seen["during"] == ["active"]
    attempts = _attempts(env, key)
    assert [a["status"] for a in attempts] == ["invalidated", "active"]
    new = attempts[1]["attempt_id"]
    after = [a for a in assignments(env, key) if a["attempt_id"] == new]
    assert after and all(a["attempt_id"] == new for a in after)
    with legacy_adapter.person_hold(FA, run_id, key, old, "late_side_effect") as owned:
        assert owned is False                                           # the old attempt's late contact: refused
    manual_stage.follow_person_instruction(FA, run_id, key, instruction, old, "content_saved")
    assert [a for a in assignments(env, key) if a["attempt_id"] == new] == after


def test_t45_the_fence_is_taken_only_for_a_due_replacement_and_an_unfenced_one_is_skipped(env, decided):
    from flowgate.graph_run import execution, fencing
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    key = _person_card(env)
    assert execution.reopen_due(FA) is False                          # a person's waiting stage: nothing to replace
    with execution.command_fence(FA) as fenced:
        assert fenced is False and not fencing.exclusive_held(FA)
    stored(key, "instruction_review_count", 1)
    stored(key, "instruction_review_ai", "aip_gone")
    # a waiting review is no replacement (the command asks the next seq of the same attempt); a failed
    # write stage is (the person's instruction stage, marked failed as a refused token does)
    [attempt] = [a["attempt_id"] for a in _attempts(env, key)]
    _sql(env["db"], "UPDATE graph_stage_assignments SET stage_state = 'failed', fail_cause = 'token_issue_failed' "
                    "WHERE attempt_id = ? AND stage = 'instruction'", (attempt,))
    assert execution.reopen_due(FA) is True
    with execution.command_fence(FA) as fenced:
        assert fenced is True and fencing.exclusive_held(FA)
    assert not fencing.exclusive_held(FA)

    def body():                                                       # inside a guard with no fence: nothing
        run = run_store.active_run(FA)
        ctx = run_apply.load_context(run, "flowgate", None, [])
        row = next(r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == key)
        return execution.reopen_attempt(ctx, row)

    assert with_flow_guard(FA, body) is None
    assert [a["status"] for a in _attempts(env, key)] == ["active"]


# ── E-15a / E-15b / E-40: a failed person stage is recovered by a new attempt only ──

def test_e40_a_t1_failed_person_instruction_is_judged_by_no_save_and_no_read(env, decided, monkeypatch, starts):
    from flowgate.card_settings import manual_stage

    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    _failing_issue(monkeypatch, {1})
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["stage_state"], ia["fail_cause"]) == ("person", "failed", "token_issue_failed")
    instruction = save_instruction(env, key)                       # its save (CONTENT_SAVED) judges nothing
    _read(env)
    _read(env)                                                     # nor does a read (RC-6)
    assert one(env, key, "instruction_review", 1) is None and _review_status(instruction) != "approved"
    assert one(env, key, "result") is None and node_run(env, key)["dispatch_error_code"] == "token_issue_failed"
    run_id = _run_row(env)["run_id"]
    verdict, _a = manual_stage.manual_judgeable(FA, run_id, key, ia["attempt_id"], "instruction",
                                                {"doc_id": instruction, "revision_no": 0})
    assert verdict == manual_stage.STAGE_FAILED
    command(env)                                                   # only the explicit command's new attempt recovers
    assert [a["status"] for a in _attempts(env, key)] == ["invalidated", "active"]
    assert _review_status(instruction) == "approved"               # the draft written meanwhile: reused, approved


def test_e40_a_t1_failed_reused_instruction_is_not_progressed_by_reads(env, decided, monkeypatch, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    instruction = line_doc(env, key, "instruction")
    _write_written(instruction, "## 작업\n\n미리 쓴 지시서\n")
    _failing_issue(monkeypatch, {1})
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["reused_doc_id"], ia["stage_state"]) == (instruction, "failed")
    _read(env)
    _read(env)                                                     # RC-5 never takes a failed stage
    assert one(env, key, "instruction_review", 1) is None and _review_status(instruction) != "approved"
    assert one(env, key, "result") is None


def test_e40_a_t3_failed_person_result_is_not_judged_and_its_bound_result_waits_for_a_person(env, decided,
                                                                                                monkeypatch):
    """E-15b / E-40: the result stage's T-3 failed; a result bound to the node anyway (the card's [승인] takes it) is
    neither judged nor approved by a read — its B-1 ``stage_not_person`` once."""
    from flowgate.card_settings import manual_stage
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    key = _person_card(env)
    [node_token] = [t["token_id"] for t in tokens(env, key) if t["stage"] is None]
    instruction = _doc(FA, 70, "T")
    _write_written(instruction)
    _failing_issue(monkeypatch, {1})
    v01_contact.after_inbox_registered(FA, instruction, "T", node_token)       # spent on the instruction; T-3 fails
    token_service.consume(node_token, "flowgate", instruction)
    ra = one(env, key, "result")
    assert (ra["actor"], ra["stage_state"]) == ("person", "failed")
    report = _doc(FA, 71, "TR", review="pending_review")
    _write_written(report, "## 결과\n\n본문\n")
    _sql(env["db"], "UPDATE graph_node_runs SET status = 'review', result_doc_id = ? WHERE node_key = ?", (report, key))
    run_id = _run_row(env)["run_id"]
    verdict, _a = manual_stage.manual_judgeable(FA, run_id, key, ra["attempt_id"], "result",
                                                {"doc_id": report, "revision_no": 0})
    assert verdict == manual_stage.STAGE_FAILED
    _read(env)
    _read(env)
    assert _review_status(report) != "approved" and one(env, key, "result_review", 1) is None
    assert [(d, r) for d, r, _m in b1(env)] == [(report, "stage_not_person")]


# ── K-6: only the reviewed revision is approved (results and instructions) ───

def _edit_between(monkeypatch, doc_id):
    """A person's edit committed right after the verdict was claimed (between the check and the approval)."""
    from flowgate.card_settings import assigned_review

    real = assigned_review._claim

    def claim(flow_id, a, verdict):
        won = real(flow_id, a, verdict)
        _set_revision(doc_id, 1)
        return won

    monkeypatch.setattr(assigned_review, "_claim", claim)








# ── T §4.2 / K-8 / E-44: the current revision's write evidence ───────────────

def _evidence(env, doc_id, *rows):
    """workflow_events rows of the document's group, in order: ("doc_edited", revision, at) / ("token_consumed", token,
    at)."""
    from modules.flow_gate.db import workflow_events

    for kind, value, at in rows:
        meta = {"doc_id": doc_id, "revision_no": value} if kind == "doc_edited" else {"doc_id": doc_id, "token_id": value}
        workflow_events.create({"event_type": kind, "project_id": "flowgate", "group_id": FA, "document_id": None,
                                "actor_user_id": "u_kim", "metadata": json.dumps(meta), "created_at": at})


def _ai_token(provider=A, run_id="air_writer"):
    from modules.flow_gate.services import token_service

    issued = token_service.issue(project="flowgate", group_id=FA, action_scope="edit", doc_ref=None, issued_to="u_kim",
                                 provider_id=provider, ai_run_id=run_id)
    return issued["token_id"]


def _console_save(env, doc_id, at):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.db.events import insert_event

    with get_store().transaction():
        insert_event(doc_id, "doc_body_saved", note=json.dumps({"source": "console_save"}))
    _sql(env["db"], "UPDATE events SET created_at = ? WHERE doc_id = ? AND event_type = 'doc_body_saved'", (at, doc_id))








# ── C2 / §2.14: an unknown invalidated set judges nothing ────────────────────





# ── T §4.6: assignment_unreadable after a commit, once per submission ────────







# ── T §4.6: an unreadable assignment is the node's error family on the run read ──



# ── L-2 §2.10 / E-13: an admission refusal of a review loop keeps its code ───

def _refusing_loops(monkeypatch, calls, code="no_enabled_provider"):
    """The AI invoke admission refusing a review loop as it refuses one (``runtime._http_error``: 409 + its code)."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.ai_invoke import runtime

    def start_run(**kw):
        calls.append(kw)
        if kw.get("document_review_loop"):
            raise runtime._http_error(409, code, "refused by the admission")
        return {"run_id": WORK_RUN, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)


LIMIT = 3          # ``flowgate.card_intervention.reasons.SYSTEM_ERROR_RETRY_LIMIT``










# ── RC-1: a lost end of an assigned review that did not pass (0105 B-1 with its submission mark) ──
