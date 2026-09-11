"""0544: top-level document review admission contract regressions.

Rejection rev1: the five tests below this docstring only inspect
``admission.start_run``'s SOURCE (string/order checks via ``inspect.getsource``) — none
of them actually call ``start_run`` and observe a real 409. Swapping the two
``review_intent`` comparisons in the gate (making it refuse the OPPOSITE cases) left
every one of them green. The tests after ``# -- real admission execution --`` close that
hole: they call ``admission.start_run`` for real, with every dependency up to the gate
stubbed to a trivial pass, and assert the actual HTTPException (or, for the pass-through
case, that execution reaches token issuance) the gate produces.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from modules.flow_gate.api.v1 import ai_invoke_routes as routes
from modules.flow_gate.db import document_reviews
from modules.flow_gate.services.ai_invoke import admission


def test_review_intent_defaults_to_normal_and_accepts_explicit_rerun():
    normal = routes.AiInvokeStartRequest(project="flowgate", action_scope="review")
    rerun = routes.AiInvokeStartRequest(
        project="flowgate", action_scope="review", review_intent="rerun"
    )
    assert normal.review_intent == "normal"
    assert rerun.review_intent == "rerun"


def test_revision_lookup_is_exact_and_read_only(monkeypatch):
    calls = []

    class Store:
        def _fetch_one(self, sql, params):
            calls.append((sql, params))
            return {"id": 7, "revision_no": params[1]}

    monkeypatch.setattr(document_reviews, "get_store", lambda: Store())
    row = document_reviews.get_latest_for_revision("flowgate.default.0544.0001-B", 3)
    assert row == {"id": 7, "revision_no": 3}
    sql, params = calls[0]
    assert "doc_id = ? AND revision_no = ?" in sql
    assert params == ["flowgate.default.0544.0001-B", 3]
    assert "UPDATE" not in sql.upper() and "DELETE" not in sql.upper()


def test_gate_is_inside_durable_lease_and_before_token_issue():
    source = inspect.getsource(admission.start_run)
    acquire = source.index("db_group_ai_leases.acquire(")
    gate = source.index('if action_scope == "review" and review_intent is not None:')
    issue = source.index("issue = _call_issue_builder(issue_builder, run_id)")
    assert acquire < gate < issue
    assert "get_latest_for_revision(doc_ref, revision_no)" in source
    assert '"review_already_completed"' in source
    assert '"review_rerun_not_available"' in source


def test_internal_review_loop_hops_are_outside_top_level_intent_gate():
    signature = inspect.signature(admission.start_run)
    assert signature.parameters["review_intent"].default is None
    source = inspect.getsource(admission.start_run)
    assert 'if action_scope == "review" and review_intent is not None:' in source


def test_admission_does_not_mutate_review_history():
    source = inspect.getsource(admission.start_run)
    gate = source[source.index('if action_scope == "review" and review_intent is not None:'):
                  source.index("if document_review_loop is not None and issue_builder is not None:")]
    assert "insert_review" not in gate
    assert "UPDATE document_reviews" not in gate
    assert "DELETE FROM document_reviews" not in gate


# -- real admission execution -------------------------------------------------
#
# Everything `start_run` touches BEFORE the review-intent gate is stubbed to a trivial
# pass (one enabled provider, no capability restriction, non-integrated project so the
# worktree/source-sync guards no-op, no active group lease, a lease acquire that hands
# back the minted run_id). `issue_builder` is a function that raises a marker exception
# the instant it is called — the first thing that happens strictly AFTER the gate lets a
# request through — so a passing case is observed by that marker firing, and a blocked
# case is observed by the real 409 HTTPException the gate itself raises.


class _ReachedIssueBuilder(Exception):
    """Raised by the stub issue_builder: proof start_run passed the review-intent gate."""


def _boom_issue_builder(**_kwargs):
    raise _ReachedIssueBuilder()


def _stub_admission_prelude(monkeypatch, *, run_id: str, latest_review):
    from modules.flow_gate.services import provider_capability_service

    monkeypatch.setattr(
        admission.ai_settings_service, "resolve_effective",
        lambda _p: {"providers": [{"id": "prov-1", "name": "P1"}], "source": "project",
                    "registered_count": 1},
    )
    monkeypatch.setattr(
        admission.db_docs, "get_by_id",
        lambda _d: {"type": "TR", "revision_no": 3, "branch": "main"},
    )
    monkeypatch.setattr(admission.db_docs, "get_group_max_seq", lambda _g: 0)
    monkeypatch.setattr(
        provider_capability_service, "capability_finding", lambda *_a, **_kw: None,
    )
    # Non-integrated project: both worktree and initial-source-sync guards no-op on
    # this, before either touches real git state.
    monkeypatch.setattr(admission.db_git, "get_config", lambda _p: None)
    monkeypatch.setattr(
        admission.git_service, "ensure_initial_group_source_sync",
        lambda *_a, **_kw: {"reason": "already_synced"},
    )
    monkeypatch.setattr(admission.db_group_ai_leases, "get_active", lambda _g: None)
    monkeypatch.setattr(
        admission.db_group_ai_leases, "acquire",
        lambda **kwargs: {"run_id": kwargs["run_id"]},
    )
    released: list = []
    monkeypatch.setattr(
        admission.db_group_ai_leases, "release",
        lambda _g, _r, reason=None: released.append(reason),
    )
    monkeypatch.setattr(admission._svc(), "_next_run_id", lambda: run_id)
    lookups: list = []

    def _get_latest_for_revision(doc_id, revision_no):
        lookups.append((doc_id, revision_no))
        return latest_review

    monkeypatch.setattr(
        admission.db_document_reviews, "get_latest_for_revision", _get_latest_for_revision,
    )
    return released, lookups


def _start_run_kwargs(review_intent):
    return dict(
        project_id="p1", module="default", group_id="p1.default.0544",
        doc_ref="flowgate.default.0544.0006-TR", action_scope="review", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="reviewer", api_base_url="http://localhost/api/v1",
        mention_builder=lambda *_a: (_ for _ in ()).throw(
            AssertionError("mention_builder must not run when issue_builder is set")
        ),
        review_intent=review_intent,
        issue_builder=_boom_issue_builder,
    )


def test_start_run_rejects_normal_review_when_revision_already_reviewed(monkeypatch):
    """(a): a completed review on this revision + a `normal` request -> 409."""
    released, lookups = _stub_admission_prelude(
        monkeypatch, run_id="aiv_20260911_000001", latest_review={"id": 42},
    )
    with pytest.raises(HTTPException) as excinfo:
        admission.start_run(**_start_run_kwargs("normal"))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "review_already_completed"
    assert excinfo.value.detail["review_id"] == 42
    assert excinfo.value.detail["revision_no"] == 3
    assert lookups == [("flowgate.default.0544.0006-TR", 3)]
    assert released == ["review_admission_completed"]


def test_start_run_rejects_rerun_review_when_no_completed_review_exists(monkeypatch):
    """(b): no completed review on this revision + a `rerun` request -> 409."""
    released, lookups = _stub_admission_prelude(
        monkeypatch, run_id="aiv_20260911_000002", latest_review=None,
    )
    with pytest.raises(HTTPException) as excinfo:
        admission.start_run(**_start_run_kwargs("rerun"))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "review_rerun_not_available"
    assert excinfo.value.detail["revision_no"] == 3
    assert lookups == [("flowgate.default.0544.0006-TR", 3)]
    assert released == ["review_admission_no_completed_review"]


def test_start_run_admits_normal_review_when_no_completed_review_exists(monkeypatch):
    """(c): no completed review on this revision + a `normal` request -> admitted."""
    released, lookups = _stub_admission_prelude(
        monkeypatch, run_id="aiv_20260911_000003", latest_review=None,
    )
    with pytest.raises(_ReachedIssueBuilder):
        admission.start_run(**_start_run_kwargs("normal"))
    assert lookups == [("flowgate.default.0544.0006-TR", 3)]
    assert released == []