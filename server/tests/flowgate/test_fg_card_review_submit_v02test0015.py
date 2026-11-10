"""flowgate.v02test.0015: a v0.2 card moves on by its own stage results, never by the v0.1 submit step.

Reported on flowgate.v02test.0012: an AI's result (0006-TR) with r1 and ``doc_review_status`` NULL. Its review loop
answered ``issues`` but no rejection could be written (no ``('', 'reject')``), and every later start of the card's
review was refused by the v0.1 loop admission (``review.compute_review_baseline``: ``document_not_submitted``) -- the card
stood still as ``review_not_started`` on every run, retry and command.

The harness of ``test_fg_ai_roles_l2_assignment`` (real SQLite DB, routes and migrations, recorders for every AI start;
nothing calls a real AI):

- the work stage ended -> its review starts: the result with no review status is put in review by the system
  (``pending_review``, ``card_review_submitted``) and the loop is requested, its first hop a review;
- the review answered ``issues`` on the current revision and nothing rejected it -> the rework starts: the system submits
  the result and turns that review into its rejection (``review_id`` kept), so the loop's first hop is the rework;
- an ``issues`` review of an earlier revision is not this revision's answer -> a review again;
- a refused start tells why: ``review_not_started: <code>`` in the run result.
"""
from __future__ import annotations

import json

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, WORK_RUN, X, _sql, ai_writes_result, b1, db_path, decided, env, flow, one, record_starts, roles, start,
    starts, stored, wired,
)
from test_fg_card_settings_review import _run_ended
from test_fg_command_rerequest_0124 import _first_hops


def _status(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return (db_documents.get_by_id(doc_id) or {}).get("doc_review_status")


def _unsubmitted_ai_result_ends(env, monkeypatch, starts, before_end=None):
    """An AI card (result X, review B with X as its rework AI, count 1): X wrote its result, which no submit step ever
    ran on (``doc_review_status`` NULL), and its run ended. -> (key, result, the first hops of the loops started)."""
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution=X, review=B)
    start(env)
    [job] = [j for j in env["jobs"] if j.get("provider_id") == X]
    env["jobs"].clear()
    result = ai_writes_result(env, monkeypatch, key, job)
    _sql(env["db"], "UPDATE documents SET doc_review_status = NULL, rejection_history = '[]' WHERE doc_id = ?", (result,))
    assert not _status(result)
    if before_end:
        before_end(result)
    record_starts(monkeypatch, starts)
    hops = _first_hops(monkeypatch)
    _run_ended(WORK_RUN, provider_id=X)
    return key, result, hops


def _issues_review(result, revision):
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.db.connection import now_iso

    return db_reviews.insert_review(result, revision, "ai:" + B, "issues",
                                    json.dumps([{"locus": "table v2", "note": "name every error code"}]),
                                    "Three findings.", now_iso(), review_run_id="aiv_earlier_loop",
                                    actual_provider_id=B)


def _submitted_events(env, doc_id):
    return [json.loads(r["note"]) for r in _sql(
        env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = 'card_review_submitted'", (doc_id,))]


def test_the_work_stage_s_end_starts_the_review_of_an_unsubmitted_ai_result(env, decided, monkeypatch, starts):
    key, result, hops = _unsubmitted_ai_result_ends(env, monkeypatch, starts)

    rr = one(env, key, "result_review", 1)
    assert rr["stage_state"] != "failed" and rr["fail_cause"] is None
    assert rr["intent_exec_id"]                                         # the loop's request committed
    assert _status(result) == "pending_review"
    [note] = _submitted_events(env, result)
    assert (note["actor"], note["actor_kind"]) == ("system", "system")
    assert hops == ["review"]
    assert "review_not_started" not in [r for _d, r, _m in b1(env)]


def test_a_finished_issues_review_nothing_rejected_starts_the_rework(env, decided, monkeypatch, starts):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history

    found = {}

    def reviewed(result):
        revision = int(db_documents.get_by_id(result)["revision_no"] or 0)
        found["review"] = _issues_review(result, revision)

    key, result, hops = _unsubmitted_ai_result_ends(env, monkeypatch, starts, reviewed)

    rr = one(env, key, "result_review", 1)
    assert rr["stage_state"] != "failed" and rr["intent_exec_id"]
    assert _status(result) == "rejected"
    history = parse_rejection_history(db_documents.get_by_id(result).get("rejection_history"))
    assert [item.get("review_id") for item in history] == [found["review"]["id"]]
    assert history[0]["rejected_by"] == B and history[0]["rejected_by_kind"] == "ai"
    assert "name every error code" in history[0]["reason"]
    assert hops == ["rework"]                                           # the rework the finished review asked for


def test_an_issues_review_of_an_earlier_revision_is_reviewed_again(env, decided, monkeypatch, starts):
    from modules.flow_gate.db import documents as db_documents

    def reviewed_earlier(result):
        revision = int(db_documents.get_by_id(result)["revision_no"] or 0)
        _issues_review(result, revision - 1)

    key, result, hops = _unsubmitted_ai_result_ends(env, monkeypatch, starts, reviewed_earlier)

    assert one(env, key, "result_review", 1)["intent_exec_id"]
    assert _status(result) == "pending_review"
    assert hops == ["review"]


def test_a_submitted_document_is_left_as_it_is():
    from flowgate.card_settings import legacy_adapter

    assert legacy_adapter.submit_for_card_review(None, "u_kim") is None


@pytest.mark.parametrize("data,expected", [
    ({"fail_cause": "review_not_started", "fail_detail": {"code": "document_not_submitted"}},
     "review_not_started: document_not_submitted"),
    ({"fail_cause": "start_refused:scratch_path_conflict", "fail_detail": {"code": "scratch_path_conflict"}},
     "start_refused:scratch_path_conflict"),
    ({"fail_cause": "review_not_started"}, "review_not_started"),
    ({}, None),
])
def test_a_stage_failure_tells_its_code(data, expected):
    from flowgate.graph_run import command_result

    assert command_result._stage_failed_detail(data, {"reason": None}) == expected
