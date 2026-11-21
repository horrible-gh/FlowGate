"""Independently selectable scenarios; shared fixtures remain in test_fg_ai_roles_l2_fixes.py."""
from __future__ import annotations

from test_fg_ai_roles_l2_fixes import (
    A,
    B,
    FA,
    LIMIT,
    REVIEW_RUN,
    STAGE_ASSIGNMENTS,
    WORK_RUN,
    X,
    Y,
    _ai_token,
    _attempts,
    _break_reads,
    _console_save,
    _doc,
    _edit_between,
    _evidence,
    _failing_issue,
    _loop_ended,
    _person_card,
    _read,
    _refusing_loops,
    _review_end,
    _review_status,
    _run_ended,
    _run_row,
    _set_revision,
    _sql,
    _unreadable,
    _wire,
    _write_written,
    ai_writes_result,
    assignments,
    b1,
    command,
    db_path,
    decided,
    env,
    events,
    flow,
    json,
    line_doc,
    node_run,
    one,
    pytest,
    record_starts,
    roles,
    save_instruction,
    start,
    starts,
    stored,
    threading,
    time,
    tokens,
    wired,
    write_line,
)

def test_k6_a_pass_of_revision_0_never_approves_revision_1_edited_after_the_check(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
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
    from flowgate.card_completion import completion
    from flowgate.card_settings import legacy_adapter, review_stage
    from modules.flow_gate.graph_workflow.store import run_store

    key = flow(env, "T")
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    doc_id = _doc(FA, 72, "TR" if path == "result" else "T", review="pending_review")
    _write_written(doc_id, "## 본문\n\n내용\n")
    raced = []
    if path == "result":
        # (T-1): a result's automatic completion takes no v0.1 transition; its write (the completion
        # record and the document's status display) is the span the edit lands in. The node takes the document as its
        # result in review (``completion.target_of``) for this check alone.
        run = run_store.active_run(FA)
        monkeypatch.setattr(completion, "target_of", lambda flow_id, d, node_key=None: {
            "run": run, "node_key": key, "node_run": {}, "attempt_id": run_store.active_attempt_id(run["run_id"], key)})
        real = legacy_adapter.show_approved
        target, name = legacy_adapter, "show_approved"

        def racing(*a, **kw):
            out = real(*a, **kw)
            raced.append(doc_id)
            _set_revision(doc_id, 1)                                   # an edit inside the completion's span
            return out
    else:
        # T-3 owns the instruction write. Inject inside its transaction; the removed v0.1
        # transition is never called, and wrapping the whole write would race after its commit.
        from flowgate.card_completion import legacy_adapter as completion_records

        real = completion_records.db_documents.update
        target, name = completion_records.db_documents, "update"

        def racing(updated_doc_id, fields):
            out = real(updated_doc_id, fields)
            if updated_doc_id == doc_id and fields.get("doc_review_status") == "approved":
                raced.append(doc_id)
                _set_revision(doc_id, 1)                               # before the transaction's second check
            return out

    def approve():
        if path == "result":
            return legacy_adapter.approve(FA, doc_id, "u_kim", legacy_adapter.AUTOMATION, node_key=key,
                                          expected_revision=0)
        return review_stage.approve_instruction(FA, {"node_key": key}, doc_id, "u_kim", expected_revision=0)

    monkeypatch.setattr(target, name, racing)
    assert approve() == legacy_adapter.REVISION_MOVED
    assert raced == [doc_id]                                         # the fault reached the write exactly once
    assert _review_status(doc_id) != "approved"                       # rolled back with its check
    assert _sql(env["db"], "SELECT 1 FROM graph_card_completions WHERE result_doc_id = ?", (doc_id,)) == []
    monkeypatch.setattr(target, name, real)
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (doc_id,))   # committed before it
    assert approve() == legacy_adapter.REVISION_MOVED
    assert _review_status(doc_id) != "approved" and b1(env) == []
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
