"""Independently selectable scenarios; shared fixtures remain in test_fg_card_settings_review.py."""
from __future__ import annotations

from test_fg_card_settings_review import (
    AI,
    FA,
    INSTRUCTION_RUN,
    Path,
    REVIEW_RUN,
    WORK_RUN,
    _SERVER_DIR,
    _admin_and_reviewer,
    _doc,
    _executions_ended,
    _h,
    _in_review,
    _loop_row,
    _node,
    _recorded_start_run,
    _result,
    _review_status,
    _run_card,
    _run_ended,
    _run_one_card,
    _source_scopes,
    _sql,
    _start,
    _start_ai,
    _through_instruction,
    _work_run_ended,
    _write_written,
    ast,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    legacy_cards,
    legacy_setting,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
)

def test_the_report_an_omitted_instruction_wrote_is_approved(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    _work_run_ended()
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")
def test_a_run_that_is_not_the_node_ai_changes_nothing(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch)
    ai_run_end_hooks.run_ended({"run_id": "air_someone_else", "group_id": FA, "action_scope": "new",
                                "mode": "single", "issued_to": "u_kim"})
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review")
@pytest.mark.parametrize("review_ai, reviewer", [("aip_review", "aip_review"), (None, "aip_default")])
def test_a_card_review_starts_the_review_loop_with_its_review_ai_count_and_basis(request, 
        env, decided, monkeypatch, review_ai, reviewer):
    if review_ai is None:
        request.getfixturevalue("pre_feature_attempts")
    from modules.flow_gate.settings import ai_settings_service

    monkeypatch.setattr(ai_settings_service, "resolve_effective",
                        lambda _project: {"providers": [{"id": "aip_default", "name": "default"}]})
    _sql_provider = "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) " \
                    "VALUES ('aip_default', 'aip_default', 'cli', 'claude', 't', 't')"
    _sql(env["db"], _sql_provider)
    settings = [("run_ai", "aip_card"), ("review_count", 2), ("review_basis", "last"), ("time_limit", 120),
                ("restart", 1)]
    if review_ai:
        settings.append(("review_ai", review_ai))
    key, result, _token = _in_review(env, monkeypatch, settings)
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [call] = calls
    assert {k: call[k] for k in ("project_id", "group_id", "doc_ref", "action_scope", "mode", "issued_to")} == {
        "project_id": "flowgate", "group_id": FA, "doc_ref": result, "action_scope": "review", "mode": "single",
        "issued_to": "u_kim"}
    assert call["document_review_loop"] == {
        "review_count": 2, "reviewer_provider_id": reviewer, "review_criteria": "last_rejection_only",
        "rework_provider_id": "aip_card", "rework_timeout_sec": 7200, "rework_message": "",
        "failure_restart_max_attempts": 1, "total_timeout_sec": 14400}
    assert callable(call["issue_builder"]) and call.get("provider_id") is None
    # the review decides now: nothing is approved before it ends
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review")
@pytest.mark.parametrize("basis", ["type", "last"])
def test_every_review_hop_tells_the_review_ai_the_card_review_basis(env, decided, monkeypatch, basis):
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review"),
                                                         ("review_basis", basis)))
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [call] = calls
    issue = call["issue_builder"]
    issue.loop_stage = "review"                        # admission: the loop's first hop is a review hop
    first = issue(ai_run_id=REVIEW_RUN)["mention"]     # the first hop's prompt is this mention as issued
    review_body = f"Review the target document ({result})"
    assert review_body in first and first.index("Review basis:") < first.index(review_body)
    if basis == "type":
        assert "Review basis: document type." in first
    else:
        assert "Review basis: last rejection only. The document has no rejection yet" in first
    # the loop rejected the result and its rework revised it: the next review hop (and a retry's
    # reissue) comes from the same issuer and reads the last rejection as it is then
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="Name every error code.")
    again = issue(ai_run_id=REVIEW_RUN)["mention"]
    assert review_body in again
    if basis == "type":
        assert "Review basis: document type." in again and "Name every error code." not in again
    else:
        assert "Last rejection:\nName every error code." in again
def test_the_review_basis_note_names_the_basis_and_the_missing_rejection():
    from flowgate.card_settings import fields as f

    assert f.review_basis_note("document_type_default", "ignored").startswith("Review basis: document type.")
    assert "has no rejection yet" in f.review_basis_note("last_rejection_only", None)
    assert "has no rejection yet" in f.review_basis_note("last_rejection_only", "  ")
    assert f.review_basis_note("last_rejection_only", " fix A ").endswith("Last rejection:\nfix A")
def test_the_review_loop_request_follows_the_card_within_the_loop_ranges():
    from flowgate.card_settings import fields as f

    base = f.review_loop({"review_count": -1}, reviewer="r", reworker="w")
    assert (base["review_count"], base["review_criteria"], base["rework_timeout_sec"],
            base["failure_restart_max_attempts"]) == (-1, "document_type_default", 7200, 2)
    assert f.review_loop({"review_count": 1, "time_limit": 30}, reviewer="r", reworker="w")["rework_timeout_sec"] == 1800
    assert f.review_loop({"review_count": 1, "restart": 5}, reviewer="r", reworker="w")[
        "failure_restart_max_attempts"] == 2
    assert f.review_loop({"review_count": 1, "restart": -1}, reviewer="r", reworker="w")[
        "failure_restart_max_attempts"] == -1
    assert [f.after_result(f.RUN, s) for s in ({}, {"after_review": "human"}, {"review_count": 1},
                                              {"review_count": 0}, {"review_count": -1})] == [
        f.APPROVE, f.APPROVE, f.REVIEW, f.APPROVE, f.REVIEW]
    assert f.after_result(f.TEST, {}) == f.APPROVE
    assert f.after_result(f.REQUIREMENT, {"review_count": 1}) is None
