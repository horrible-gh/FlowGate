"""Regression: review-request tokens must be scoped "review", not "edit".

Covers B0057.0001 ("copying a review mention let you modify the document") whose
root cause (NR0057.0003) was that request_review() issued an action_scope="edit"
token while advertising "review", so the recipient could replay it as action:edit
and overwrite the target document.

Two invariants are locked here:
  1. request_review() issues an action_scope="review" token (matches its advertised
     scope), so it can no longer pass _handle_edit's `!= "edit"` scope guard.
  2. _handle_review enforces `action_scope == "review"`, so an edit/new token can no
     longer be replayed as a review (and the normal review path still works).
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock

os.environ.setdefault("TESTING", "1")


# ── Invariant 1: request_review issues a review-scoped token ───────────────────────────
def test_request_review_issues_review_scope(monkeypatch):
    from modules.flow_gate.services import workflow_decision_service as service

    doc = {
        "doc_id": "flowgate.default.0057.0001-B",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0057",
        "module": "default",
        "type_code": "B",
        "seq": 1,
        "title": "Bug",
    }
    monkeypatch.setattr(service.db_documents, "get_by_id", lambda _doc_id: doc)
    monkeypatch.setattr(service.db_documents, "fetch_recent_group_docs", lambda **_kw: [])
    monkeypatch.setattr(service.mention_service, "build_review_mention", lambda **_kw: "MENTION")
    issue = MagicMock(return_value={
        "raw_token": "review-token",
        "token_id": "tok-1",
        "expires_at": "2026-06-15T00:00:00+00:00",
        "scratch_dir": "C:/scratch/tok-1",
    })
    monkeypatch.setattr(service.token_service, "issue", issue)

    result = service.request_review(
        doc_id=doc["doc_id"],
        issued_to="user-1",
        api_base_url="http://localhost/flowgate/api/v1",
    )

    # The issued scope must be "review" — NOT "edit" (the B0057.0001 defect).
    assert issue.call_args.kwargs["action_scope"] == "review"
    # And the advertised scope in the response must match what was actually issued.
    assert result["action_scope"] == "review"


# ── Invariant 2: _handle_review enforces the review scope ───────────────────────────────
def _review_body() -> dict:
    return {
        "project": "flowgate",
        "doc_id": "flowgate.default.0057.0001-B",
        "verdict": "pass",
        "findings": [],
        "comment": "ok",
    }


def test_handle_review_rejects_non_review_scope_token(monkeypatch):
    """An edit-scoped token replayed as action:review is now rejected (403)."""
    from modules.flow_gate.api import inbox_routes

    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "user-1",
        "action_scope": "edit",  # the dangerous case from NR0057.0003
        "doc_ref": "flowgate.default.0057.0001-B",
    })

    resp = asyncio.run(
        inbox_routes._handle_review(MagicMock(), "raw", _review_body())
    )
    assert resp.status_code == 403


def test_handle_review_accepts_review_scope_token(monkeypatch):
    """The normal review path still works with a review-scoped token (no regression)."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import document_reviews as db_reviews

    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "user-1",
        "action_scope": "review",
        "doc_ref": "flowgate.default.0057.0001-B",
    })
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": "flowgate.default.0057.0001-B",
        "group_id": "flowgate.default.0057",
        "revision_no": 0,
        "title": "Bug",
    })
    insert = MagicMock()
    monkeypatch.setattr(db_reviews, "insert_review", insert)
    monkeypatch.setattr(inbox_routes.token_service, "consume", MagicMock())

    resp = asyncio.run(
        inbox_routes._handle_review(MagicMock(), "raw", _review_body())
    )
    assert resp.status_code == 201
    insert.assert_called_once()


# ── Invariant 3: the tokens schema actually persists a "review" scope ───────────────────
def test_tokens_table_accepts_review_scope(all_migrations_db):
    """A "review" action_scope must survive the real INSERT (migration 042).

    Invariant 1 above mocks token_service.issue, so it proves request_review *asks* for
    "review" but never touches the DB. The live defect was downstream: the tokens
    table CHECK from migration 036 only allowed ('new','edit','workflow_decide'), so the
    real INSERT raised IntegrityError → /documents/review-request returned 500. This
    exercises the migrated schema end-to-end so the schema/code mismatch can't recur.
    """
    conn = all_migrations_db
    # __SYSTEM__ project + usr_admin user are seeded by the conftest fixture; group_id
    # is nullable, so this is a minimal FK-satisfying token row.
    conn.execute(
        "INSERT INTO tokens "
        "(token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, "
        " issued_to, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, NULL, ?, 'review', ?, datetime('now'), datetime('now', '+8 hours'))",
        ["tok_review_scope_test", "hash_review_scope_test", "p1",
         "__SYSTEM__", "flowgate.default.0057.0005-TR", "usr_admin"],
    )
    row = conn.execute(
        "SELECT action_scope FROM tokens WHERE token_id = ?",
        ["tok_review_scope_test"],
    ).fetchone()
    assert row is not None and row["action_scope"] == "review"
    # Clean up so the session-scoped fixture stays reusable.
    conn.execute("DELETE FROM tokens WHERE token_id = ?", ["tok_review_scope_test"])
    conn.commit()


# ── Invariant 2 (reverse direction): a review token cannot edit ────────────────────────
def test_handle_edit_rejects_review_scope_token(monkeypatch):
    """The original exploit: a review token used for action:edit is rejected (403).

    This is what closes B0057.0001 end-to-end — _handle_edit's existing `!= "edit"`
    scope guard now sees a "review" scope and refuses the overwrite.
    """
    from modules.flow_gate.api import inbox_routes

    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "user-1",
        "action_scope": "review",
        "doc_ref": "flowgate.default.0057.0001-B",
    })
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)

    body = {
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0057",
        "doc_id": "flowgate.default.0057.0001-B",
        "edit_reason": "worker_self",
        "content": "ATTACKER OVERWRITE",
    }
    resp = asyncio.run(inbox_routes._handle_edit(MagicMock(), "raw", body))
    assert resp.status_code == 403
