"""flowgate.v02.0113 T#3 (TR rework) — regressions of the review findings on the assigned path.

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

    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", "aip_gone")))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    assert one(env, key, "instruction_review", 1)["actor"] == "wait_unavailable"
    stored(key, "instruction_review_ai", Y)
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
    save_instruction(env, key)
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


def test_k6_a_pass_of_revision_0_never_approves_revision_1_edited_after_the_check(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    r1 = one(env, key, "result_review", 1)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    sent = []
    real_approve = legacy_adapter.approve
    monkeypatch.setattr(legacy_adapter, "approve", lambda *a, **k: sent.append(k.get("expected_revision"))
                        or real_approve(*a, **k))
    _edit_between(monkeypatch, report)
    _review_end(env, r1, "review_passed")
    assert one(env, key, "result_review", 1)["verdict"] == "passed"
    assert _review_status(report) != "approved" and sent == []            # never asked to approve revision 1
    r2 = one(env, key, "result_review", 2)
    assert (r2["basis_revision_no"], r2["actor"], r2["author"]) == (1, "ai", "person")   # the newer submission's own
    assert b1(env) == []                                                  # no refusal recorded for it


def test_k6_a_pass_of_an_instruction_never_approves_its_revision_edited_after_the_check(env, decided, monkeypatch,
                                                                                       starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    i1 = one(env, key, "instruction_review", 1)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _edit_between(monkeypatch, instruction)
    _review_end(env, i1, "review_passed")
    assert _review_status(instruction) != "approved" and one(env, key, "result") is None
    i2 = one(env, key, "instruction_review", 2)
    assert (i2["basis_revision_no"], i2["author"]) == (1, "unknown")     # no write record of revision 1 (K-8)


@pytest.mark.parametrize("path", ["result", "instruction"])
def test_k6_the_approval_checks_its_expected_revision_in_its_own_transaction(env, decided, monkeypatch, path):
    """An edit landing while the approval writes rolls the approval back (``REVISION_MOVED``); one committed before it
    approves nothing either. No B-1: a moved submission is no refusal."""
    from flowgate.card_settings import legacy_adapter, review_stage
    from modules.flow_gate.workflow import pipeline_service

    key = flow(env, "T")
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    doc_id = _doc(FA, 72, "TR" if path == "result" else "T", review="pending_review")
    _write_written(doc_id, "## 본문\n\n내용\n")
    real = pipeline_service.transition_document_review

    def racing(**kw):
        out = real(**kw)
        _set_revision(doc_id, 1)                                       # an edit inside the approval's span
        return out

    def approve():
        if path == "result":
            return legacy_adapter.approve(FA, doc_id, "u_kim", legacy_adapter.AUTOMATION, node_key=key,
                                          expected_revision=0)
        return review_stage.approve_instruction(FA, {"node_key": key}, doc_id, "u_kim", expected_revision=0)

    monkeypatch.setattr(pipeline_service, "transition_document_review", racing)
    assert approve() == legacy_adapter.REVISION_MOVED
    assert _review_status(doc_id) != "approved"                       # rolled back with its check
    monkeypatch.setattr(pipeline_service, "transition_document_review", real)
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (doc_id,))   # committed before it
    assert approve() == legacy_adapter.REVISION_MOVED
    assert _review_status(doc_id) != "approved" and b1(env) == []


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


def test_k8_a_current_revision_with_no_edit_record_is_unknown_never_an_earlier_revision_s_ai(env, decided):
    from flowgate.card_settings import assigned_review, legacy_adapter

    doc_id = _doc(FA, 80, "T")
    _sql(env["db"], "UPDATE documents SET revision_no = 2 WHERE doc_id = ?", (doc_id,))
    token = _ai_token()
    _evidence(env, doc_id, ("doc_edited", 1, "2026-10-30T01:00:00+00:00"),
              ("token_consumed", token, "2026-10-30T01:00:01+00:00"))       # revision 2's own record is missing
    unknown = {"author": "unknown", "provider_id": None}
    assert legacy_adapter.written_by(doc_id) == unknown
    work = {"run_id": "air_writer", "provider_id": A}
    assert assigned_review._ai_author(work, doc_id) == unknown              # the ended run fills in nothing
    _evidence(env, doc_id, ("doc_edited", 2, "2026-10-30T02:00:00+00:00"),
              ("token_consumed", token, "2026-10-30T02:00:01+00:00"))
    assert legacy_adapter.written_by(doc_id) == {"author": "ai", "provider_id": A}
    assert assigned_review._ai_author(work, doc_id) == {"author": "ai", "provider_id": A}


@pytest.mark.parametrize("saved_at, expected", [
    ("2026-10-30T03:00:00+00:00", "person"),                # a later console save of the current body
    ("2026-10-30T03:00:00", "unknown"),                     # a time zone known on one side only: no order
    ("2026-10-30T02:00:00+00:00", "unknown"),               # the same instant: no order
])
def test_k8_a_later_person_save_or_an_order_that_cannot_be_told(env, decided, saved_at, expected):
    from flowgate.card_settings import assigned_review, legacy_adapter

    doc_id = _doc(FA, 81, "T")
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (doc_id,))
    token = _ai_token()
    _evidence(env, doc_id, ("doc_edited", 1, "2026-10-30T02:00:00+00:00"),
              ("token_consumed", token, "2026-10-30T02:00:01+00:00"))
    _console_save(env, doc_id, saved_at)
    assert legacy_adapter.written_by(doc_id)["author"] == expected
    assert assigned_review._ai_author({"run_id": "air_writer", "provider_id": A}, doc_id)["author"] == expected


def test_k8_an_evidence_read_failure_is_unknown(env, decided, monkeypatch):
    from flowgate.card_settings import assigned_review, legacy_adapter

    doc_id = _doc(FA, 82, "T")
    token = _ai_token()
    _evidence(env, doc_id, ("token_consumed", token, "2026-10-30T02:00:01+00:00"))
    assert legacy_adapter.written_by(doc_id) == {"author": "ai", "provider_id": A}
    monkeypatch.setattr(legacy_adapter, "_token_author", lambda t: (_ for _ in ()).throw(RuntimeError("tokens down")))
    assert legacy_adapter.written_by(doc_id) == {"author": "unknown", "provider_id": None}
    assert assigned_review._ai_author({"run_id": "air_writer", "provider_id": A}, doc_id)["author"] == "unknown"


# ── C2 / §2.14: an unknown invalidated set judges nothing ────────────────────

def test_c2_an_invalidated_documents_read_failure_judges_nothing_until_it_reads(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key = _person_card(env)
    instruction = line_doc(env, key, "instruction")
    real = legacy_adapter.invalidated_documents
    monkeypatch.setattr(legacy_adapter, "invalidated_documents",
                        lambda f, k: (_ for _ in ()).throw(RuntimeError("history down")))
    save_instruction(env, key)
    _read(env)
    assert one(env, key, "instruction_review", 1) is None and _review_status(instruction) != "approved"
    monkeypatch.setattr(legacy_adapter, "invalidated_documents", real)
    _read(env)                                                      # read again: RC-6 judges the current one
    assert _review_status(instruction) == "approved"


def test_c2_an_old_attempt_s_instruction_left_on_the_line_is_never_taken_while_the_history_fails(env, decided,
                                                                                               monkeypatch):
    from flowgate.card_settings import legacy_adapter, manual_stage
    from modules.flow_gate.graph_workflow.store import run_store

    key = _person_card(env)
    old_doc = _doc(FA, 83, "T")
    _write_written(old_doc, "## 작업\n\n이전 시도의 지시서\n")
    run_id = _run_row(env)["run_id"]
    attempt = one(env, key, "instruction")["attempt_id"]
    monkeypatch.setattr(legacy_adapter, "registered_instructions", lambda *a: [{"doc_id": old_doc}])
    monkeypatch.setattr(legacy_adapter, "invalidated_documents",
                        lambda f, k: (_ for _ in ()).throw(RuntimeError("history down")))
    with pytest.raises(RuntimeError):
        manual_stage.required_instruction(FA, run_store.run_by_id(run_id), key, node_run(env, key))
    manual_stage.follow_person_instruction(FA, run_id, key, old_doc, attempt, "content_saved")
    assert one(env, key, "instruction_review", 1) is None and _review_status(old_doc) != "approved"
    monkeypatch.setattr(legacy_adapter, "invalidated_documents", lambda f, k: {old_doc})
    assert manual_stage.required_instruction(FA, run_store.run_by_id(run_id), key, node_run(env, key)) is None


# ── T §4.6: assignment_unreadable after a commit, once per submission ────────

def test_t46_an_instruction_contact_with_an_unreadable_assignment_records_it_once_and_starts_nothing(env, decided,
                                                                                                   monkeypatch):
    key = _person_card(env)
    attempt = one(env, key, "instruction")["attempt_id"]
    repair = _break_reads(monkeypatch, "read")
    instruction = save_instruction(env, key)
    save_instruction(env, key)                                       # the same submission's contact again
    assert _unreadable(env) == [(instruction, "instruction", attempt,
                                 {"doc_id": instruction, "revision_no": 0, "rejection_count": 0})]
    _read(env)                                                       # the reconciliation only skips the node
    assert len(_unreadable(env)) == 1
    repair()
    _read(env)                       # readable again: that submission has its B-1 (L-2 §2.7.2 C5: the card's [승인])
    assert one(env, key, "instruction_review", 1) is None and _review_status(instruction) != "approved"
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (instruction,))
    save_instruction(env, key, "## 작업\n\n고쳐 낸 지시서\n")       # a new submission is judged
    assert _review_status(instruction) == "approved" and len(_unreadable(env)) == 1


def test_t46_a_result_contact_with_an_unreadable_assignment_records_it_once_and_approves_nothing(env, decided,
                                                                                               monkeypatch):
    from flowgate.card_settings import manual_stage

    key = _person_card(env, instruction="omit")
    real = manual_stage.person_result
    monkeypatch.setattr(manual_stage, "person_result", lambda *a, **k: None)     # the bound result's contact lost
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    monkeypatch.setattr(manual_stage, "person_result", real)
    run_id, attempt = _run_row(env)["run_id"], one(env, key, "result")["attempt_id"]
    repair = _break_reads(monkeypatch, "read", "latest")
    manual_stage.follow_person_result(FA, run_id, key, report, attempt)
    manual_stage.follow_person_result(FA, run_id, key, report, attempt)
    assert [(d, s, a) for d, s, a, _m in _unreadable(env)] == [(report, "result", attempt)]
    _read(env)
    assert len(_unreadable(env)) == 1 and _review_status(report) != "approved"
    repair()
    _read(env)                       # readable again: that submission has its B-1 (the card's [승인] takes it)
    assert _review_status(report) != "approved" and [r for _d, r, _m in b1(env)] == ["assignment_unreadable"]
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (report,))
    _read(env)                                                       # RC-3: a new submission is judged
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"


def test_t46_a_loop_end_contact_with_an_unreadable_assignment_records_it_once_and_approves_nothing(env, decided,
                                                                                                 monkeypatch, starts):
    from flowgate.card_settings import assigned_review, legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    r1 = one(env, key, "result_review", 1)
    _loop_ended(env, r1, "review_passed")
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    run, ref = {"run_id": REVIEW_RUN}, (r1["attempt_id"], "result_review", 1)
    repair = _break_reads(monkeypatch, "read")
    assigned_review.after_loop_end(FA, run, ref, False)
    assigned_review.after_loop_end(FA, run, ref, False)
    assert [(d, s) for d, s, _a, _m in _unreadable(env)] == [(report, "result")]
    assert _review_status(report) != "approved"
    repair()
    assert one(env, key, "result_review", 1)["verdict"] is None
    _read(env)                                                       # RC-1 once readable: consumed once
    assert one(env, key, "result_review", 1)["verdict"] == "passed" and _review_status(report) == "approved"
    assert len(_unreadable(env)) == 1


# ── T §4.6: an unreadable assignment is the node's error family on the run read ──

@pytest.mark.parametrize("stage", ["instruction", "result", "instruction_review"])
def test_t46_an_open_node_whose_assignment_cannot_be_read_is_an_error_and_the_read_goes_on(env, decided, monkeypatch,
                                                                                          stage):
    from flowgate.card_settings import legacy_adapter

    if stage == "instruction":
        key = _person_card(env)
    elif stage == "result":
        key = _person_card(env, instruction="omit")
    else:
        key = flow(env, extra_settings=(("instruction_review_count", 1),))
        roles(key, "not_used", "not_used")                          # no reviewer of any kind: it waits for setup
        start(env)
        save_instruction(env, key)
        assert one(env, key, "instruction_review", 1)["actor"] == "wait_setup"
    legacy_adapter.settle_graph_run(FA)
    assert _run_row(env)["idle_reason"] == "waiting_approval"
    repair = _break_reads(monkeypatch)
    legacy_adapter.settle_graph_run(FA)
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "error")      # never no_targets
    wire = _wire(env, key)
    assert (wire["wait_reason"], wire["stage_assignments"]) == (None, None)
    repair()
    legacy_adapter.settle_graph_run(FA)
    assert _run_row(env)["idle_reason"] == "waiting_approval"


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


def test_e13_a_refused_result_review_loop_fails_with_its_own_code_and_no_other_ai(env, decided, monkeypatch):
    calls: list = []
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    _refusing_loops(monkeypatch, calls)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    rr = one(env, key, "result_review", 1)
    assert (rr["stage_state"], rr["fail_cause"]) == ("failed", "start_refused:no_enabled_provider")
    loops = [c for c in calls if c.get("document_review_loop")]
    assert len(loops) == 1 and loops[0]["document_review_loop"]["reviewer_provider_id"] == B
    assert [(d, r) for d, r, _m in b1(env)] == [(report, "review_not_started")]
    _read(env)
    _read(env)                                                       # no read restarts it, no other AI asked
    assert len([c for c in calls if c.get("document_review_loop")]) == 1
    assert len(b1(env)) == 1 and _review_status(report) != "approved"


def test_e13_a_refused_instruction_review_loop_records_its_code_on_the_node(env, decided, monkeypatch):
    calls: list = []
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    _refusing_loops(monkeypatch, calls, "provider_inactive")
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["stage_state"], ir["fail_cause"]) == ("failed", "start_refused:provider_inactive")
    assert node_run(env, key)["dispatch_error_code"] == "review_not_started:start_refused:provider_inactive"
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "review_not_started")]
    assert _wire(env, key)["wait_reason"]["detail"] == "start_refused:provider_inactive"
    _read(env)
    assert len([c for c in calls if c.get("document_review_loop")]) == 1
    assert _review_status(instruction) != "approved" and one(env, key, "result") is None


# ── RC-1: a lost end of an assigned review that did not pass (0105 B-1 with its submission mark) ──

@pytest.mark.parametrize("stop_reason", ["review_count_exhausted", "review_verdict_hold", "retry_exhausted"])
def test_rc1_a_lost_end_of_a_review_that_did_not_pass_records_its_b1_once(env, decided, monkeypatch, starts,
                                                                          stop_reason):
    """The AI result's review loop stopped without passing and its run-end contact was lost: the next reads consume the
    verdict once — B-1 ``review_not_passed:<stop>`` with the submission's mark, verdict ``not_passed:<stop>``, nothing
    approved (an AI author: no takeover)."""
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review=B)
    start(env)
    [job] = env["jobs"]
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    r1 = one(env, key, "result_review", 1)
    _loop_ended(env, r1, "review_passed")                            # its run-end contact is lost
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET stop_reason = ?, stop_detail = 'spent' WHERE run_id = ?",
         (stop_reason, REVIEW_RUN))
    _read(env)
    _read(env)
    r1 = one(env, key, "result_review", 1)
    assert (r1["verdict"], r1["stage_state"]) == ("not_passed:" + stop_reason, "done")
    assert b1(env) == [(result, "review_not_passed:" + stop_reason,
                        {"doc_id": result, "revision_no": 0, "rejection_count": 0})]
    assert _review_status(result) != "approved" and node_run(env, key)["status"] == "review"
