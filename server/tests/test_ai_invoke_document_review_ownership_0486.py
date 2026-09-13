from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from modules.flow_gate.services import ai_invoke_service as service


BASE = {
    "review_count": -1,
    "review_baseline_id": 10,
    "baseline_revision_no": 3,
    "starts_with_rework": False,
    "round_no": 1,
    "current_stage": "review",
    "attempts_used": 0,
    "failure_restart_max_attempts": 0,
    "review_run_id": "current-run",
    "review_attempt_no": 3,
    "doc": {"revision_no": 3, "doc_review_status": "pending_review"},
}


def review(rid, verdict, run_id="current-run", attempt=3, findings=None, revision=3):
    # `revision_no` is what tells one ROUND from the next: a rework hop only hands the loop
    # back to review after the document's revision has moved, so rows sharing a revision are
    # the same round and rows with different revisions are different rounds (0486 NR0028 F1).
    return {
        "id": rid,
        "verdict": verdict,
        "revision_no": revision,
        "review_run_id": run_id,
        "attempt_no": attempt,
        "findings": findings or [],
    }


@pytest.mark.parametrize("foreign_verdict", ["pass", "hold", "issues"])
def test_late_foreign_verdict_never_overrides_owned_issues(foreign_verdict):
    state = service.resolve_document_review_loop_gate({
        **BASE,
        "reviews": [
            review(99, foreign_verdict, run_id="zombie-run"),
            review(11, "issues"),
        ],
    })
    assert (state["current_stage"], state["round_no"]) == ("rework", 2)
    assert state["stop_reason"] is None


@pytest.mark.parametrize(("owned", "stop_reason"), [
    ("pass", "review_passed"),
    ("hold", "review_verdict_hold"),
])
def test_late_foreign_issues_never_overrides_owned_terminal_verdict(owned, stop_reason):
    state = service.resolve_document_review_loop_gate({
        **BASE,
        "reviews": [review(100, "issues", run_id="zombie-run"), review(11, owned)],
    })
    assert state["current_stage"] == "stopped"
    assert state["stop_reason"] == stop_reason


def test_previous_round_verdict_is_not_this_round_progress():
    # 0486 NR0028 F1 side effect 1: progress is "this ROUND left a verdict", counted against
    # round_no. The provenance shortcut this replaces asked only "does the loop own any
    # verdict at all", so round 2's review hop could produce nothing and still be read as
    # progress because round 1's row was still sitting there.
    doc = {"revision_no": 4, "doc_review_status": "pending_review"}
    rows = [review(99, "issues", revision=3)]
    bundle = {**BASE, "round_no": 2, "doc": doc, "last_hop_kind": "review"}
    assert service.check_expected_progress(bundle, doc, rows) is False
    state = service.resolve_document_review_loop_gate({**bundle, "reviews": rows})
    assert state["current_stage"] != "stopped"

    rows.append(review(11, "pass", revision=4))
    assert service.check_expected_progress(bundle, doc, rows) is True
    assert service.resolve_document_review_loop_gate(
        {**bundle, "reviews": rows}
    )["stop_reason"] == "review_passed"


def test_replayed_verdict_of_the_same_round_is_still_one_round():
    # The same round delivering its verdict twice (a retried hop, a duplicated POST) shares
    # the document revision it reviewed, so it stays ONE round and the higher attempt wins.
    rows = [
        review(11, "pass", attempt=1, revision=3),
        review(12, "issues", attempt=2, revision=3),
    ]
    bundle = {**BASE, "round_no": 1, "last_hop_kind": "review"}
    assert service.check_expected_progress(bundle, BASE["doc"], rows) is True
    state = service.resolve_document_review_loop_gate({**bundle, "reviews": rows})
    assert (state["current_stage"], state["round_no"]) == ("rework", 2)


def _same_findings(spaced=False):
    if spaced:
        return '[ { "note" : "same", "locus" : "x" } ]'
    return '[{"locus":"x","note":"same"}]'


def test_same_owned_findings_stop_despite_json_order_and_whitespace():
    # Two CONSECUTIVE ROUNDS -- so two different revisions, because a rework landed between
    # them -- that came back with the same findings. The rework changed nothing the reviewer
    # cares about, and the loop stops instead of burning the rest of its total timeout.
    state = service.resolve_document_review_loop_gate({
        **BASE,
        "doc": {"revision_no": 4, "doc_review_status": "pending_review"},
        "reviews": [
            review(11, "issues", revision=3, findings=_same_findings()),
            review(12, "issues", revision=4, findings=_same_findings(True)),
        ],
    })
    assert state["current_stage"] == "stopped"
    assert state["stop_reason"] == service.REVIEW_STALLED_STOP_CODE


@pytest.mark.parametrize("rows", [
    [review(12, "issues")],
    [review(11, "issues", revision=3, findings='[{"locus":"x","note":"one"}]'),
     review(12, "issues", revision=4, findings='[{"locus":"x","note":"two"}]')],
    [review(11, "issues", revision=3, findings=_same_findings()),
     review(12, "pass", revision=4, findings=_same_findings())],
    [review(11, "issues", run_id="zombie-run", revision=3, findings=_same_findings()),
     review(12, "issues", revision=4, findings=_same_findings())],
    # One round that delivered the same verdict twice is not two rounds of no progress.
    [review(11, "issues", attempt=1, revision=3, findings=_same_findings()),
     review(12, "issues", attempt=2, revision=3, findings=_same_findings())],
])
def test_non_consecutive_or_non_owned_findings_do_not_stall(rows):
    state = service.resolve_document_review_loop_gate({**BASE, "reviews": rows})
    assert state.get("stop_reason") != service.REVIEW_STALLED_STOP_CODE


def _checkpoint(monkeypatch, rows):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews

    persisted = {
        **BASE,
        "run_id": "current-run",
        "group_id": "flowgate.default.0486",
        "doc_ref": "flowgate.default.0486.0021-T",
        "updated_at": "2026-09-10T00:00:00+00:00",
        "deadline_at": None,
    }
    monkeypatch.setattr(db_loops, "get", lambda _run_id: dict(persisted))
    monkeypatch.setattr(service, "get_store",
                        lambda: type("Store", (), {"transaction": lambda self: nullcontext(self)})())
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda _doc_id: dict(BASE["doc"]))
    monkeypatch.setattr(db_reviews, "list_by_doc", lambda _doc_id: rows)
    checkpoints = []
    monkeypatch.setattr(
        db_loops, "checkpoint",
        lambda _run_id, **kw: (True, checkpoints.append(kw) or {**persisted, **kw}),
    )
    rejects = []
    monkeypatch.setattr(
        service, "_auto_reject",
        lambda _slot, row, _bundle: rejects.append(row) or {"ok": True},
    )
    run = {
        "run_id": "current-run",
        "attempt_no": 3,
        "outcome": "complete",
        "issued_to": "owner",
        "document_review_loop": dict(persisted),
    }
    return service._checkpoint_document_review_loop(run), rejects, checkpoints


def test_checkpoint_uses_owned_pass_not_newer_zombie_issues(monkeypatch):
    latest, rejects, checkpoints = _checkpoint(monkeypatch, [
        review(100, "issues", run_id="zombie-run"),
        review(11, "pass"),
    ])
    assert rejects == []
    assert latest["current_stage"] == "stopped"
    assert latest["stop_reason"] == "review_passed"
    assert checkpoints[-1]["round_no"] == 1


def test_checkpoint_auto_rejects_owned_issues_not_newer_zombie_pass(monkeypatch):
    latest, rejects, _ = _checkpoint(monkeypatch, [
        review(100, "pass", run_id="zombie-run"),
        review(11, "issues"),
    ])
    assert [row["id"] for row in rejects] == [11]
    assert latest["current_stage"] == "rework"
    assert latest["round_no"] == 2


def test_general_review_gate_contract_is_unchanged(monkeypatch):
    from modules.flow_gate.services.ai_invoke import review as review_service

    slot = {
        "doc_id": "flowgate.default.0486.0021-T",
        "item_seq": 1,
        "revision_no": 3,
        "review_status": "pending_review",
    }
    monkeypatch.setattr(review_service, "_pending_review_slot", lambda _ref: slot)
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda _doc_id: [])
    state = service.resolve_review_gate({
        "doc_ref": slot["doc_id"], "review_count_overrides": {"1": 1},
    })
    assert state["stage"] == "review"
    assert state["round_no"] == 1
    assert state["rounds_used"] == 0
