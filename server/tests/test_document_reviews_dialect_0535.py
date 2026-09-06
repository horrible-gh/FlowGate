"""fallback_used cross-dialect boolean write/read regression (flowgate.default.0535 T0005).

Root cause (T0005 §2): document_reviews.insert_review() used to coerce its
Optional[bool] fallback_used parameter to a bare Python int before binding it —
``None if fallback_used is None else (1 if fallback_used else 0)`` — a conversion
written for SQLite's ``fallback_used INTEGER CHECK(0,1)`` column. PostgreSQL's
``fallback_used BOOLEAN`` column (migration 104) does not accept an integer
parameter for a boolean column at all.

Verified against the live PostgreSQL backend (192.168.0.250 flowgate, inside a
transaction rolled back at the end, same convention as
test_group_ai_lease_events_0502.py's _PgLikeDB):

  * binding fallback_used as a Python int (0 or 1) against the live BOOLEAN column
    raises exactly: column "fallback_used" is of type boolean but expression is of
    type integer
  * binding it as a native Python bool, or None, succeeds for all three states,
    including the reported failure case attempt_no=1/fallback_used=False.

This file encodes that live-verified behavior into a deterministic, offline
PostgreSQL-shaped backend (_PgBooleanColumnDB) so the regression is caught by the
ordinary test suite without a network dependency on that shared staging database.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from inbox_client import post_inbox  # noqa: E402

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import dialect as _dialect  # noqa: E402
from modules.flow_gate.db import document_reviews as db_reviews  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402

DOC_ID = "flowgate.default.0535.0001-T"
GROUP_ID = "flowgate.default.0535"
PROJECT = "flowgate"
USER = "user-1"

_INSERT_COLUMNS = (
    "doc_id", "revision_no", "reviewer_id", "verdict", "findings", "comment",
    "reviewed_at", "created_at", "updated_at", "review_run_id", "requested_provider_id",
    "actual_provider_id", "actual_provider_name", "provider_source", "attempt_no",
    "fallback_used",
)


class _PgBooleanColumnDB:
    """Reproduces PostgreSQL's fallback_used BOOLEAN column type enforcement.

    See module docstring for the live verification this encodes. ``type(value) is
    int`` (rather than ``isinstance``) is deliberate: ``bool`` is a subclass of
    ``int`` in Python, and the whole point is to reject the old *bare-int* coercion
    while accepting a real ``bool``/``None``.
    """

    db_type = _dialect.POSTGRESQL

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1

    def execute(self, sql, params=None):
        params = list(params or [])
        if "INSERT INTO document_reviews" in sql:
            value = params[_INSERT_COLUMNS.index("fallback_used")]
            if type(value) is int:
                raise RuntimeError(
                    'column "fallback_used" is of type boolean but expression is of '
                    "type integer\nHINT:  You will need to rewrite or cast the "
                    "expression."
                )
            row = dict(zip(_INSERT_COLUMNS, params))
            row["id"] = self._next_id
            self._next_id += 1
            self.rows.append(row)
            return self
        raise AssertionError(f"unexpected statement in test backend: {sql}")

    def fetch_one(self, sql, params=None):
        if "FROM document_reviews" in sql:
            return dict(self.rows[-1]) if self.rows else None
        raise AssertionError(f"unexpected statement in test backend: {sql}")

    def fetch_all(self, sql, params=None):
        raise AssertionError(f"unexpected statement in test backend: {sql}")


class _MysqlBooleanColumnDB(_PgBooleanColumnDB):
    """MySQL's BOOLEAN is a TINYINT(1) alias — 0/1 and True/False are both accepted.

    T0005 §8: "가능하면" the same 3-state coverage, without the application layer
    doing its own int coercion. Unlike PostgreSQL this backend never rejects an int,
    which is exactly the point — MySQL was never broken by the old code; only
    PostgreSQL was.
    """

    db_type = _dialect.MYSQL

    def execute(self, sql, params=None):
        params = list(params or [])
        if "INSERT INTO document_reviews" in sql:
            row = dict(zip(_INSERT_COLUMNS, params))
            row["id"] = self._next_id
            self._next_id += 1
            self.rows.append(row)
            return self
        raise AssertionError(f"unexpected statement in test backend: {sql}")


@pytest.fixture
def pg_store(monkeypatch):
    db = _PgBooleanColumnDB()
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_reviews, "get_store", lambda: store)
    return db


@pytest.fixture
def mysql_store(monkeypatch):
    db = _MysqlBooleanColumnDB()
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_reviews, "get_store", lambda: store)
    return db


# ── A. the historical bug, locked in as a regression guard (T0005 §2, §17-B) ────────

def test_the_old_int_coercion_shape_is_what_broke_postgresql(pg_store):
    """insert_review() itself must never produce this shape any more (tests below) —
    this test exists so a future regression reintroducing the 1/0 coercion fails here,
    against the live-verified error text, instead of only in production."""
    with pytest.raises(RuntimeError, match="is of type boolean but expression is of type integer"):
        pg_store.execute(
            "INSERT INTO document_reviews ("
            + ", ".join(_INSERT_COLUMNS) + ") VALUES ("
            + ", ".join(["%s"] * len(_INSERT_COLUMNS)) + ")",
            ["d", 0, "r", "pass", "[]", None, "t", "t", "t", None, None, None, None, None, 1, 0],
        )


# ── B. document_reviews.insert_review() on PostgreSQL: True/False/None all succeed ──
# (T0005 §7, §17-B)

@pytest.mark.parametrize("value", [True, False, None])
def test_insert_review_succeeds_on_postgresql_for_every_fallback_state(pg_store, value):
    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=value,
    )
    assert row["fallback_used"] is value


def test_insert_review_postgresql_attempt_no_1_fallback_false_the_reported_failure_case(pg_store):
    """T0005 §7: this exact combination (attempt_no=1, fallback_used=False) is the
    reproduction case quoted in the bug report."""
    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment="ok", reviewed_at="2026-09-06T22:00:00",
        review_run_id="air_1", requested_provider_id="aip_a", actual_provider_id="aip_a",
        actual_provider_name="A", provider_source="project_default",
        attempt_no=1, fallback_used=False,
    )
    assert row["attempt_no"] == 1
    assert row["fallback_used"] is False


def test_insert_review_postgresql_fallback_true_also_succeeds(pg_store):
    """T0005 §11: the actual-fallback case must also insert successfully."""
    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment="ok", reviewed_at="2026-09-06T22:00:00",
        review_run_id="air_2", requested_provider_id="aip_a", actual_provider_id="aip_b",
        actual_provider_name="B", provider_source="fallback",
        attempt_no=2, fallback_used=True,
    )
    assert row["fallback_used"] is True


def test_insert_review_readback_normalizes_to_json_true_false_null_regardless_of_dialect(pg_store):
    """T0005 §9: shaping normalizes the raw DB representation to true/false/null."""
    from modules.flow_gate.api.v1.document_routes import _shape_review

    for value in (True, False, None):
        row = db_reviews.insert_review(
            doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
            verdict="pass", findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
            attempt_no=1, fallback_used=value,
        )
        shaped = _shape_review(row)
        assert shaped["review_provider"]["fallback_used"] is value


# ── C. MySQL 3-state (T0005 §8, best-effort) ─────────────────────────────────────────

@pytest.mark.parametrize("value", [True, False, None])
def test_insert_review_succeeds_on_mysql_for_every_fallback_state(mysql_store, value):
    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=value,
    )
    assert row["fallback_used"] is value


# ── D. real POST /inbox action=review through PostgreSQL, unmocked insert path ──────
# (T0005 §10, §11, §12 — the previous review-doc-path test suite mocks insert_review
# out entirely and so never exercised a real dialect-shaped write.)

def _review_token_rec(scratch, ai_run_id="air_review_0535"):
    return {
        "token_id": "tok-review-0535",
        "project": PROJECT,
        "issued_to": USER,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "scratch_dir": str(scratch),
        "ai_run_id": ai_run_id,
    }


@pytest.fixture
def review_env(monkeypatch, tmp_path, pg_store):
    monkeypatch.setattr(
        inbox_routes.token_service, "verify", lambda _raw: _review_token_rec(tmp_path)
    )
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 0, "title": "t",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    return {"db": pg_store, "consume": consume}


def _body(**overrides) -> dict:
    body = {"action": "review", "project": PROJECT, "doc_id": DOC_ID,
            "verdict": "pass", "findings": [], "comment": "ok"}
    body.update(overrides)
    return body


def test_real_review_registration_non_fallback_succeeds_on_postgresql(review_env, monkeypatch):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id, "action_scope": "review", "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet", "provider_id": "aip_sonnet",
        "provider": {"id": "aip_sonnet", "name": "Sonnet"},
        "selected_provider_source": "project_default", "attempt_no": 1,
    })

    response = post_inbox(_body())

    assert response.status_code == 201, response.text
    assert review_env["db"].rows[-1]["fallback_used"] is False
    review_env["consume"].assert_called_once()


def test_real_review_registration_with_fallback_succeeds_on_postgresql(review_env, monkeypatch):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id, "action_scope": "review", "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet", "provider_id": "aip_opus",
        "provider": {"id": "aip_opus", "name": "Opus"},
        "selected_provider_source": "reviewer_override", "attempt_no": 2,
    })

    response = post_inbox(_body())

    assert response.status_code == 201, response.text
    assert review_env["db"].rows[-1]["fallback_used"] is True
    review_env["consume"].assert_called_once()


def test_real_review_registration_without_ai_run_succeeds_with_null_provenance(review_env, monkeypatch):
    """T0005 §12: legacy/non-AI review — provenance is absent, fallback_used stays NULL."""
    token_rec = dict(_review_token_rec(""))
    token_rec.pop("ai_run_id")
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token_rec)

    response = post_inbox(_body())

    assert response.status_code == 201, response.text
    assert review_env["db"].rows[-1]["fallback_used"] is None
    review_env["consume"].assert_called_once()


# ── E. insert failure must not consume the token or leave a partial review ─────────
# (T0005 §14, §17-H)

def test_failed_insert_returns_500_without_consuming_the_token_or_leaving_a_partial_row(
    review_env, monkeypatch,
):
    monkeypatch.setattr(
        db_reviews, "insert_review",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    response = post_inbox(_body())

    assert response.status_code == 500
    assert "DB registration error" in response.json()["error_message"]
    review_env["consume"].assert_not_called()
    assert review_env["db"].rows == []
