"""Inbox POST dry-run — T0009 (R0001, group 0050).

Implements the design set D0005/P0006/L0007/DB0008. The contract under test:

  * A dry-run (`"dry_run": true`) runs the SAME Step 1~5 validation as a real submit,
    then short-circuits at HTTP 200 with a `would_register` preview — creating no
    document/file/number/DB row/SSE and NOT consuming the token. Its only side effect
    is bumping the per-token `dry_run_count` by exactly 1 (L0007 §7 invariant I).
  * Validation *failures* never reach the dry-run branch, so a failed dry-run returns
    the exact same status/message as a real submit and is NOT counted (L0007 §5.1).
  * Per-token limit `FLOWGATE_INBOX_DRYRUN_MAX` (default 5); the (N+1)th attempt is 429
    with no counter bump (P0006 §3.5).
  * Omitting the flag leaves the real path byte-for-byte unchanged (backward compat).

The handler dependencies are monkeypatched, but requests enter through POST /api/v1/inbox so
the tests stay independent of the private handlers being synchronous, async, or threaded.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox

os.environ.setdefault("TESTING", "1")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


def _body(content: dict) -> dict:
    return json.loads(json.dumps(content))  # defensive copy


# ── Common dep patching ────────────────────────────────────────────────────────────────

def _token_rec(scope: str, doc_ref: str, dry_run_count: int = 0) -> dict:
    return {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "user-1",
        "action_scope": scope,
        "doc_ref": doc_ref,
        "dry_run_count": dry_run_count,
    }


def _patch_increment(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    inc = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", inc)
    return inc


# ───────────────────────────── new ─────────────────────────────

def _new_body(**over) -> dict:
    b = {
        "action": "new",
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0050",
        "prev_doc_id": "flowgate.default.0050.0001-R",
        "doc_type": "NR",
        "title": "t",
        "content": "body",
    }
    b.update(over)
    return _body(b)


def _patch_new_validation(monkeypatch, token_rec):
    from modules.flow_gate.api import inbox_routes
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda p, m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token_rec)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *a, **k: True)
    monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *a, **k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _code: False)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {"doc_id": _id})
    monkeypatch.setattr(
        inbox_routes, "_resolve_group", lambda *a, **k: {"group_id": "flowgate.default.0050"}
    )


def test_new_dry_run_success(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    _patch_new_validation(monkeypatch, _token_rec("new", "flowgate.default.0050.0001-R"))
    reserve = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(_new_body(dry_run=True))
    data = resp.json()

    assert resp.status_code == 200
    assert data["dry_run"] is True and data["ok"] is True
    assert data["would_register"]["action"] == "new"
    assert data["would_register"]["doc_id"] is None        # not numbered yet (P0006 §3.1)
    assert data["would_register"]["group_name"] == "flowgate.default.0050"
    assert data["dry_run_count"] == 1 and data["dry_run_remaining"] == 4
    # L0007 §1.1: _handle_new has no body-size check → content_size must NOT appear.
    assert "content_size" not in data["would_register"]["checks_passed"]
    # Side-effect-zero invariant: only the counter moved.
    inc.assert_called_once_with("tok-1")
    reserve.assert_not_called()
    consume.assert_not_called()


def test_new_design_dry_run_rejects_template_mismatch_before_counting(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    _patch_new_validation(monkeypatch, _token_rec("new", "flowgate.default.0050.0001-R"))
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda code: code == "P")
    monkeypatch.setattr(
        inbox_routes.template_provision,
        "validate_design_document_structure",
        lambda *_args: {"valid": False, "missing": ["2. Resources"], "out_of_order": []},
    )
    inc = _patch_increment(monkeypatch)
    request = MagicMock()
    request.headers = {}

    resp = post_inbox(
        _new_body(doc_type="P", dry_run=True)
    )
    data = resp.json()
    assert resp.status_code == 422
    assert data["help_url"] == "/help/items/design_template/P"
    inc.assert_not_called()


def test_new_design_dry_run_passes_after_template_structure_match(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    _patch_new_validation(monkeypatch, _token_rec("new", "flowgate.default.0050.0001-R"))
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda code: code == "P")
    monkeypatch.setattr(
        inbox_routes.template_provision,
        "validate_design_document_structure",
        lambda *_args: {"valid": True},
    )
    inc = _patch_increment(monkeypatch)
    request = MagicMock()
    request.headers = {}

    resp = post_inbox(
        _new_body(doc_type="P", dry_run=True)
    )
    assert resp.status_code == 200
    inc.assert_called_once_with("tok-1")


def test_new_dry_run_backward_compat_real_path(monkeypatch):
    """No dry_run flag → real path is reached (reserve_document runs), counter untouched."""
    from modules.flow_gate.api import inbox_routes
    _patch_new_validation(monkeypatch, _token_rec("new", "flowgate.default.0050.0001-R"))
    reserve = MagicMock(side_effect=RuntimeError("boom"))  # blow up just past the branch
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(_new_body())
    assert resp.status_code == 503   # numbering error → proves we passed the dry-run branch
    reserve.assert_called_once()
    inc.assert_not_called()


def test_new_dry_run_validation_failure_not_counted(monkeypatch):
    """A failing dry-run returns the real path's status and does NOT bump the counter."""
    from modules.flow_gate.api import inbox_routes
    # Wrong scope → Step 3 fails with 403 before the dry-run branch.
    _patch_new_validation(monkeypatch, _token_rec("edit", "flowgate.default.0050.0001-R"))
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(_new_body(dry_run=True))
    assert resp.status_code == 403
    assert resp.json()["error_message"] == "Context binding mismatch. Use the correct token."
    inc.assert_not_called()


def test_new_dry_run_limit_exceeded(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    monkeypatch.setenv("FLOWGATE_INBOX_DRYRUN_MAX", "5")
    _patch_new_validation(
        monkeypatch, _token_rec("new", "flowgate.default.0050.0001-R", dry_run_count=5)
    )
    reserve = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(_new_body(dry_run=True))
    data = resp.json()
    assert resp.status_code == 429
    assert data["dry_run_count"] == 5 and data["dry_run_remaining"] == 0
    inc.assert_not_called()       # 429 itself is not counted (L0007 §5.3)
    reserve.assert_not_called()


# ───────────────────────────── edit ─────────────────────────────

def _edit_body(**over) -> dict:
    b = {
        "action": "edit",
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0050",
        "doc_id": "flowgate.default.0050.0003-NR",
        "edit_reason": "rejected",
        "content": "revised",
    }
    b.update(over)
    return _body(b)


def _patch_edit_validation(monkeypatch, token_rec):
    from modules.flow_gate.api import inbox_routes
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda p, m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token_rec)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *a, **k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": _id, "status": "open", "revision_no": 1, "file_path": "x.md",
    })
    monkeypatch.setattr(inbox_routes.document_service, "is_final_approved", lambda _d: False)
    monkeypatch.setattr(inbox_routes.document_service, "is_document_editable", lambda *a, **k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _code: False)
    monkeypatch.setattr(
        inbox_routes, "_resolve_group", lambda *a, **k: {"group_id": "flowgate.default.0050"}
    )


def test_edit_dry_run_success(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    _patch_edit_validation(monkeypatch, _token_rec("edit", "flowgate.default.0050.0003-NR"))
    consume = MagicMock()
    copy2 = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    monkeypatch.setattr(inbox_routes.shutil, "copy2", copy2)
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(_edit_body(dry_run=True))
    data = resp.json()

    assert resp.status_code == 200
    assert data["would_register"]["action"] == "edit"
    assert data["would_register"]["doc_id"] == "flowgate.default.0050.0003-NR"  # echoed
    # content supplied → content_size ran; editable always runs.
    assert "content_size" in data["would_register"]["checks_passed"]
    assert "editable" in data["would_register"]["checks_passed"]
    inc.assert_called_once_with("tok-1")
    consume.assert_not_called()
    copy2.assert_not_called()       # no backup/replace side effect


# ───────────────────────────── review ─────────────────────────────

def _review_body(**over) -> dict:
    b = {
        "action": "review",
        "project": "flowgate",
        "doc_id": "flowgate.default.0050.0005-D",
        "verdict": "pass",
        "findings": [],
        "comment": "ok",
    }
    b.update(over)
    return _body(b)


def test_review_dry_run_success(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import document_reviews as db_reviews
    monkeypatch.setattr(
        inbox_routes.token_service, "verify",
        lambda _raw: _token_rec("review", "flowgate.default.0050.0005-D"),
    )
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *a, **k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": _id, "group_id": "flowgate.default.0050", "revision_no": 0, "title": "t",
    })
    insert = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(db_reviews, "insert_review", insert)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    inc = _patch_increment(monkeypatch)

    resp = post_inbox(
        _review_body(dry_run=True, findings=[{"locus": "a", "note": "b"}])
    )
    data = resp.json()

    assert resp.status_code == 200
    assert data["would_register"]["action"] == "review"
    assert data["would_register"]["verdict"] == "pass"
    assert data["would_register"]["finding_count"] == 1
    inc.assert_called_once_with("tok-1")
    insert.assert_not_called()
    consume.assert_not_called()


# ───────────────────────────── helpers ─────────────────────────────

def test_truthy_normalization():
    from modules.flow_gate.api.inbox_routes import _truthy
    assert _truthy(True) is True
    assert _truthy(1) is True
    assert _truthy("true") is True and _truthy("YES") is True and _truthy("1") is True
    # The trap bool() falls into: a non-empty "false" string must be False.
    assert _truthy("false") is False
    assert _truthy(False) is False and _truthy(0) is False
    assert _truthy(None) is False and _truthy("") is False


def test_dryrun_max_env(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    monkeypatch.delenv("FLOWGATE_INBOX_DRYRUN_MAX", raising=False)
    assert inbox_routes._dryrun_max() == 5
    monkeypatch.setenv("FLOWGATE_INBOX_DRYRUN_MAX", "12")
    assert inbox_routes._dryrun_max() == 12
    monkeypatch.setenv("FLOWGATE_INBOX_DRYRUN_MAX", "not-an-int")
    assert inbox_routes._dryrun_max() == 5     # falls back to default


# ───────────────────────────── DB layer (migration 043 + CRUD) ─────────────────────────────

def test_migration_043_adds_dry_run_count(all_migrations_db):
    """tokens.dry_run_count exists as INTEGER NOT NULL DEFAULT 0, and backfills to 0."""
    conn = all_migrations_db
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(tokens)").fetchall()}
    assert "dry_run_count" in cols
    col = cols["dry_run_count"]
    assert col["type"].upper() == "INTEGER"
    assert col["notnull"] == 1
    assert str(col["dflt_value"]) == "0"

    # A row inserted without the column gets 0 by default (DB0008 §2.3 backfill).
    conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, "
        " action_scope, issued_to, created_at, expires_at) "
        "VALUES ('tok_dr', 'h_dr', 'p1', '__SYSTEM__', NULL, 'd', 'new', 'usr_admin', "
        " datetime('now'), datetime('now','+8 hours'))",
    )
    row = conn.execute(
        "SELECT dry_run_count FROM tokens WHERE token_id='tok_dr'"
    ).fetchone()
    assert row["dry_run_count"] == 0
    # The increment SQL (mirrors db_tokens.increment_dry_run) bumps by exactly 1.
    conn.execute("UPDATE tokens SET dry_run_count = dry_run_count + 1 WHERE token_id='tok_dr'")
    row = conn.execute("SELECT dry_run_count FROM tokens WHERE token_id='tok_dr'").fetchone()
    assert row["dry_run_count"] == 1
    conn.execute("DELETE FROM tokens WHERE token_id='tok_dr'")
    conn.commit()


def test_db_increment_dry_run_issues_atomic_update(monkeypatch):
    """db_tokens.increment_dry_run runs a single atomic +1 UPDATE keyed by token_id."""
    from modules.flow_gate.db import tokens as db_tokens

    calls = []

    class _FakeStore:
        def _execute(self, sql, params):
            calls.append((sql, params))

    monkeypatch.setattr(db_tokens, "get_store", lambda: _FakeStore())
    db_tokens.increment_dry_run("tok-xyz")

    assert len(calls) == 1
    sql, params = calls[0]
    assert "dry_run_count = dry_run_count + 1" in sql
    assert "WHERE token_id = ?" in sql
    assert params == ["tok-xyz"]


def test_token_service_increment_delegates(monkeypatch):
    """token_service.increment_dry_run delegates to db_tokens (dependency boundary)."""
    from modules.flow_gate.services import token_service
    spy = MagicMock()
    monkeypatch.setattr(token_service.db_tokens, "increment_dry_run", spy)
    token_service.increment_dry_run("tok-abc")
    spy.assert_called_once_with("tok-abc")
