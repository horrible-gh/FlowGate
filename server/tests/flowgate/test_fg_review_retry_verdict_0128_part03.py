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
