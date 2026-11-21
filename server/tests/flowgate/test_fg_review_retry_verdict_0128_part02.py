"""Independently selectable scenarios; shared fixtures remain in test_fg_review_retry_verdict_0128.py."""
from __future__ import annotations

from test_fg_review_retry_verdict_0128 import (
    B,
    FA,
    RUN,
    _LoopStarter,
    _another_end,
    _end,
    _failed,
    _loop_ended,
    _pending,
    _read,
    _review_status,
    _sql,
    ar,
    assignments,
    b1,
    contextmanager,
    db_path,
    decided,
    document_reviews,
    env,
    json,
    ledger,
    legacy,
    node_run,
    one,
    os,
    preserve_pepper_id,
    pytest,
    sa,
    wired,
)

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
