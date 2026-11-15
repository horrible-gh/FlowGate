"""0029: real assigned review/approval with durable exhaustion evidence."""
import json
import pytest
from test_fg_ai_roles_l2_assignment import (
    A, B, FA, WORK_RUN, _sql, _run_ended, ai_writes_result, b1, db_path, decided, env,
    flow, node_run, one, record_starts, roles, start, starts, wired,
)
from test_fg_ai_roles_l2_review import _review_end, _ai_writes_instruction, _review_status
from test_fg_card_intervention_assigned_0119 import fork_join


@pytest.mark.parametrize("policy,reason,done", [
    (None, "review_count_exhausted", True), ("continue", "review_count_exhausted", True),
    ("stop", "review_count_exhausted", False), ("continue", "total_timeout", False),
    ("continue", "retry_exhausted", False), ("continue", "review_verdict_hold", False),
    ("continue", "review_stalled", False),
])
def test_result_exhaustion_policy(env, decided, monkeypatch, starts, policy, reason, done):
    from flowgate.card_settings import assigned_review, review_exhaustion
    settings = [("instruction", "omit"), ("review_count", 1)]
    if policy is not None:
        settings.append(("after_review_exhausted", policy))
    key = flow(env, extra_settings=tuple(settings))
    roles(key, execution=A, review=B)
    start(env)
    result = ai_writes_result(env, monkeypatch, key, env["jobs"][0])
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=A)
    a = one(env, key, "result_review", 1)
    _sql(env["db"], "UPDATE documents SET revision_no = 2 WHERE doc_id = ?", (result,))
    _review_end(env, a, reason)
    assert (node_run(env, key)["status"] == "done") is done
    assert (_review_status(result) == "approved") is done
    current = one(env, key, "result_review", 1)
    assert current["verdict"] == ("auto_approved" if done else "not_passed:" + reason)
    assert _sql(env["db"], "SELECT stop_reason FROM ai_invoke_document_review_loops")[0]["stop_reason"] == reason
    evidence = review_exhaustion.records(a["run_id"], key, a["attempt_id"], review_exhaustion.APPROVED)
    assert len(evidence) == int(done)
    if done:
        assert evidence[0]["revision_no"] == 2
        assert b1(env) == []
        assigned_review.after_verdict(FA, current, assigned_review.HELD, [])
        assert len(review_exhaustion.records(a["run_id"], key, a["attempt_id"], review_exhaustion.APPROVED)) == 1


@pytest.mark.parametrize("policy,approved", [("continue", True), ("stop", False)])
def test_instruction_exhaustion_policy(env, decided, monkeypatch, starts, policy, approved):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B),
                                    ("after_review_exhausted", policy)))
    roles(key, A, "not_used", B)
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch)
    a = one(env, key, "instruction_review", 1)
    _review_end(env, a, "review_count_exhausted")
    assert (_review_status(instruction) == "approved") is approved
    assert (one(env, key, "result") is not None) is approved


def test_failed_approval_retry_never_approves_newer_revision(env, decided, monkeypatch, starts):
    from flowgate.card_settings import assigned_review, legacy_adapter
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution=A, review=B)
    start(env)
    result = ai_writes_result(env, monkeypatch, key, env["jobs"][0])
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=A)
    a = one(env, key, "result_review", 1)
    real = legacy_adapter.approve
    monkeypatch.setattr(legacy_adapter, "approve", lambda *args, **kw: False)
    _review_end(env, a, "review_count_exhausted")
    assert one(env, key, "result_review", 1)["verdict"] == "auto_approved"
    monkeypatch.setattr(legacy_adapter, "approve", real)
    _sql(env["db"], "UPDATE documents SET revision_no = revision_no + 1 WHERE doc_id = ?", (result,))
    assigned_review.after_verdict(FA, one(env, key, "result_review", 1), assigned_review.HELD, [])
    assert _review_status(result) != "approved"
    assert node_run(env, key)["status"] != "done"


def test_explicit_completion_repair_uses_final_rework_revision(env, decided, monkeypatch, starts):
    from flowgate.card_settings import assigned_review, legacy_adapter
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution=A, review=B)
    start(env)
    result = ai_writes_result(env, monkeypatch, key, env["jobs"][0])
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=A)
    a = one(env, key, "result_review", 1)
    _sql(env["db"], "UPDATE documents SET revision_no = 2 WHERE doc_id = ?", (result,))
    real = legacy_adapter.approve
    monkeypatch.setattr(legacy_adapter, "approve", lambda *args, **kw: False)
    _review_end(env, a, "review_count_exhausted")
    monkeypatch.setattr(legacy_adapter, "approve", real)
    current = one(env, key, "result_review", 1)
    assigned_review._repair_passed_completion(FA, current)
    assert _review_status(result) == "approved"
    assert node_run(env, key)["status"] == "done"


@pytest.mark.parametrize("kind,author,detail,allowed", [
    ("T", "ai", None, True), ("WP", "ai", None, True), ("TS", "ai", None, False),
    ("WP", "person", None, False), ("T", "ai", "awaiting_author_revision", False),
])
def test_eligibility_excludes_tests_and_human_revision(monkeypatch, kind, author, detail, allowed):
    from flowgate.card_settings import review_exhaustion as e
    monkeypatch.setattr(e.repository, "values", lambda *args: {})
    assert e.eligible("flow", {"node_key": "n", "node_type": "document", "doc_type": kind},
                      {"stop_reason": e.EXHAUSTED, "stop_detail": detail}, author=author) is allowed


@pytest.mark.parametrize("policy,next_status", [("continue", "open"), ("stop", "pending")])
def test_only_continue_opens_the_next_card(env, fork_join, monkeypatch, starts, policy, next_status):
    from test_fg_card_intervention_assigned_0119 import _branches, _reviewed_ai_result, command, stored
    a, next_key, _independent = _branches(env)
    stored(a, "after_review_exhausted", policy)
    command(env, "full")
    _, result, review = _reviewed_ai_result(env, monkeypatch, starts, a)
    _review_end(env, review, "review_count_exhausted")
    assert node_run(env, next_key)["status"] == next_status
