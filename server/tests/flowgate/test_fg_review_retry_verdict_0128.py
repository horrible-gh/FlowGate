"""0128 T#2: a failed review consumes its latest ended pass before opening a seq.

Real SQLite ledger, assignment CAS, review evidence and approval contacts. AI
launching alone is recorded; no production data or AI runs are used.
"""
import json
import os
from contextlib import contextmanager

import pytest

from test_fg_review_duplicate_start_0128 import (  # noqa: F401 -- shared fixtures
    B, FA, _LoopStarter, _pending, _review_status, _sql, assignments, b1,
    db_path, decided, env, node_run, one, wired,
)
from test_fg_ai_roles_l2_review import _loop_ended
from test_fg_ai_roles_l2_fixes import _read
from flowgate.card_settings import assigned_review as ar, legacy_adapter as legacy, stage_assignment as sa
from flowgate.graph_run import execution_store as ledger
from modules.flow_gate.db import document_reviews

RUN = "aiv_20261104_920001"


@pytest.fixture(scope="module", autouse=True)
def preserve_pepper_id():
    previous = os.environ.get("FLOWGATE_AGENT_PEPPER_ID")
    yield
    if previous is None:
        os.environ.pop("FLOWGATE_AGENT_PEPPER_ID", None)
    else:
        os.environ["FLOWGATE_AGENT_PEPPER_ID"] = previous


def _end(env, a, stop, run_id):
    # A non-pass stop requires its detail under the loop table's CHECK constraint.
    _loop_ended(env, a, "review_passed" if stop == "review_passed" else "awaiting_author_revision", run_id)
    if stop not in ("review_passed", "awaiting_author_revision"):
        _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET stop_reason = ? WHERE run_id = ?", (stop, run_id))


def _failed(env, monkeypatch, stage, ended=True, stop="review_passed", evidence="pass"):
    key, doc, a, _own, _replay = _pending(env, monkeypatch, stage)
    a = legacy.with_flow_guard(FA, lambda: sa.fail(a, "start_refused:run_in_progress"))
    ar._review_not_started(FA, a, legacy.document_row(doc))
    if ended:
        _end(env, a, stop, RUN)
        _sql(env["db"], "UPDATE graph_attempt_runs SET ended_at = ? WHERE exec_id = ?",
             ("2026-11-04T00:00:01Z", a["intent_exec_id"]))
    else:
        ledger.cancel(a["intent_exec_id"], None, "requested")
    if evidence:
        document_reviews.insert_review(doc, 0, "u_kim", evidence, "[]", "review", "t", review_run_id=RUN)
    return key, doc, a


@pytest.mark.parametrize("stage", ["result_review", "instruction_review"])
@pytest.mark.parametrize("author", ["person", "ai"])
def test_failed_ended_pass_uses_existing_claim_and_approval_once(env, decided, monkeypatch, stage, author):
    key, doc, a = _failed(env, monkeypatch, stage)
    _sql(env["db"], "UPDATE graph_stage_assignments SET author = ? WHERE attempt_id = ? AND stage = ? AND seq = 1",
         (author, a["attempt_id"], stage))
    launcher = _LoopStarter(monkeypatch)
    states, real_claim = [], ar._claim

    def claim(flow_id, current, verdict):
        states.append(ar.store.read(current["attempt_id"], current["stage"], current["seq"])["stage_state"])
        return real_claim(flow_id, current, verdict)

    monkeypatch.setattr(ar, "_claim", claim)
    monkeypatch.setattr(ar, "review_retry_due", lambda a: pytest.fail("valid ended pass must precede retry budget"))
    # The ordinary end consumer still cannot claim a failed assignment directly.
    assert not real_claim(FA, a, sa.PASSED)
    for _ in range(3):
        _read(env)
    current = one(env, key, stage, 1)
    assert (current["stage_state"], current["verdict"], current["fail_cause"]) == ("done", "passed", None)
    assert states == ["active"]
    assert current["intent_exec_id"] == a["intent_exec_id"]
    assert one(env, key, stage, 2) is None
    assert launcher.calls == []
    assert _review_status(doc) == "approved"
    assert len(_sql(env["db"], "SELECT * FROM document_reviews WHERE doc_id = ?", (doc,))) == 1
    assert len(b1(env)) == 1
    assert node_run(env, key)["dispatch_error_code"] is None
    if stage == "result_review":
        assert node_run(env, key)["status"] == "done"
        assert node_run(env, env["follow_key"])["status"] == "open"
        assert len(_sql(env["db"], "SELECT * FROM graph_card_completions WHERE node_key = ?", (key,))) == 1
    else:
        assert one(env, key, "result") is not None


@pytest.mark.parametrize("stage", ["result_review", "instruction_review"])
@pytest.mark.parametrize("case", ["no_loop", "issues", "exhausted", "no_evidence", "newer_issues",
                                 "wrong_stage", "wrong_seq", "wrong_doc", "wrong_revision", "not_ended",
                                 "not_stopped"])
def test_missing_nonpass_or_mismatched_end_keeps_existing_retry(env, decided, monkeypatch, stage, case):
    stop = "review_count_exhausted" if case == "exhausted" else (
        "awaiting_author_revision" if case == "issues" else "review_passed")
    key, doc, a = _failed(env, monkeypatch, stage, ended=case != "no_loop", stop=stop,
                          evidence=None if case == "no_evidence" else "pass")
    if case == "newer_issues":
        document_reviews.insert_review(doc, 0, "u_kim", "issues", "[]", "newer", "t")
    elif case in ("wrong_stage", "wrong_seq", "not_ended"):
        column, value = {"wrong_stage": ("assignment_stage", "result"), "wrong_seq": ("assignment_seq", 9),
                         "not_ended": ("status", "cancelled")}[case]
        _sql(env["db"], f"UPDATE graph_attempt_runs SET {column} = ? WHERE exec_id = ?", (value, a["intent_exec_id"]))
    elif case == "not_stopped":
        _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'review', "
             "stop_reason = NULL, stop_detail = NULL WHERE run_id = ?", (RUN,))
    elif case in ("wrong_doc", "wrong_revision"):
        column, value = {"wrong_doc": ("doc_ref", "other-document"), "wrong_revision": ("baseline_revision_no", 1),
                         "not_stopped": ("current_stage", "review")}[case]
        _sql(env["db"], f"UPDATE ai_invoke_document_review_loops SET {column} = ? WHERE run_id = ?", (value, RUN))
    launcher = _LoopStarter(monkeypatch)
    _read(env)
    assert one(env, key, stage, 1)["stage_state"] == "failed"
    assert one(env, key, stage, 1)["verdict"] is None
    assert one(env, key, stage, 2)["stage_state"] == "active"
    assert len(launcher.admitted) == 1
    assert _review_status(doc) != "approved"
    assert len(b1(env)) == 1


def _another_end(env, a, stop, ended_at):
    request = ledger.get(a["intent_exec_id"])
    exec_id = legacy.with_flow_guard(FA, lambda: ledger.request(a["attempt_id"], a["run_id"], "review",
        json.loads(request["request_json"]), assignment=(a["stage"], a["seq"])))
    _end(env, {**a, "intent_exec_id": exec_id}, stop, "aiv_20261104_920002")
    _sql(env["db"], "UPDATE graph_attempt_runs SET ended_at = ? WHERE exec_id = ?", (ended_at, exec_id))


@pytest.mark.parametrize("latest_pass", [False, True])
def test_only_latest_ended_loop_is_considered_without_searching_for_any_pass(env, decided, monkeypatch, latest_pass):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    # Creation order differs from end order: choose by ended_at, not by the first pass found.
    _another_end(env, a, "retry_exhausted", "2026-11-04T00:00:00Z" if latest_pass else "2026-11-04T00:00:02Z")
    launcher = _LoopStarter(monkeypatch)
    _read(env)
    assert (one(env, key, "result_review", 1)["verdict"] == "passed") == latest_pass
    assert (one(env, key, "result_review", 2) is None) == latest_pass
    assert len(launcher.admitted) == (0 if latest_pass else 1)
    assert (_review_status(doc) == "approved") == latest_pass


def test_ai_author_current_revision_or_submission_must_equal_failed_basis(env, decided, monkeypatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    _sql(env["db"], "UPDATE graph_stage_assignments SET author = 'ai' WHERE attempt_id = ? AND stage = ?",
         (a["attempt_id"], a["stage"]))
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (doc,))
    document_reviews.insert_review(doc, 1, "u_kim", "pass", "[]", "new revision", "t")
    launcher = _LoopStarter(monkeypatch)
    _read(env)
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert one(env, key, "result_review", 2) is not None
    assert _review_status(doc) != "approved"
    assert len(launcher.admitted) == 1


def test_same_revision_resubmission_is_not_the_failed_basis(env, decided, monkeypatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    _sql(env["db"], "UPDATE graph_stage_assignments SET author = 'ai' WHERE attempt_id = ? AND stage = ?",
         (a["attempt_id"], a["stage"]))
    _sql(env["db"], "UPDATE documents SET rejection_history = ? WHERE doc_id = ?",
         (json.dumps([{"reason": "issues", "review_id": 1}]), doc))
    launcher = _LoopStarter(monkeypatch)
    _read(env)
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert one(env, key, "result_review", 2) is not None
    assert _review_status(doc) != "approved"


def test_refused_hold_does_not_restore_or_consume_failed_assignment(env, decided, monkeypatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")

    @contextmanager
    def refused(*args, **kwargs):
        yield False

    monkeypatch.setattr(ar, "hold_of", refused)
    launcher = _LoopStarter(monkeypatch)
    ar.drive_review_assignment(FA, a, ar.HELD, [], retry=True)
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert one(env, key, "result_review", 2) is None
    assert _review_status(doc) != "approved"
    assert launcher.calls == []


def test_retry_false_does_not_restore_failed_assignment(env, decided, monkeypatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    ar.drive_review_assignment(FA, a, ar.HELD, [])
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert _review_status(doc) != "approved"



@pytest.mark.parametrize("mismatch", ["doc_ref", "baseline_revision_no"])
def test_newest_mismatched_loop_does_not_fall_back_to_older_matching_pass(env, decided, monkeypatch, mismatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    _another_end(env, a, "review_passed", "2026-11-04T00:00:02Z")
    _sql(env["db"], f"UPDATE ai_invoke_document_review_loops SET {mismatch} = ? WHERE run_id = ?",
         ("other" if mismatch == "doc_ref" else 1, "aiv_20261104_920002"))
    launcher = _LoopStarter(monkeypatch)
    _read(env)
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert one(env, key, "result_review", 2) is not None
    assert _review_status(doc) != "approved"
    assert len(launcher.admitted) == 1


def test_stale_failed_assignment_cannot_consume_pass_after_new_seq(env, decided, monkeypatch):
    key, doc, a = _failed(env, monkeypatch, "result_review")
    legacy.with_flow_guard(FA, lambda: ar.store.insert_if_absent({**a, "seq": 2, "intent_kind": None,
                                                                "intent_exec_id": None}))
    launcher = _LoopStarter(monkeypatch)
    ar.drive_review_assignment(FA, a, ar.HELD, [], retry=True)
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    assert one(env, key, "result_review", 2)["stage_state"] == "failed"
    assert one(env, key, "result_review", 3) is None
    assert _review_status(doc) != "approved"
    assert launcher.calls == []
