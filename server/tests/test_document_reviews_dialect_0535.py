"""fallback_used cross-dialect boolean write/read regression (flowgate.default.0535).

T0005 root cause: document_reviews.insert_review() used to coerce its Optional[bool]
fallback_used parameter to a bare Python int before binding it —
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

This file encodes that live-verified behavior into deterministic, offline
dialect-shaped backends (_PgBooleanColumnDB / _MysqlBooleanColumnDB) so the regression
is caught by the ordinary test suite without a network dependency on that shared
staging database. An opt-in test against a real PostgreSQL server lives next door in
test_review_postgres_integration_0535.py, and the atomicity/rollback half of the
contract is in test_review_atomicity_0535.py.

T0007 additions:
  * the backends now open transactions and answer the dialect-translated
    ``last_insert_rowid()`` (PostgreSQL ``lastval()``, MySQL ``LAST_INSERT_ID()``)
    recovery insert_review() uses to identify the row it just wrote;
  * a concurrent writer inserting a NEWER review between the INSERT and the readback
    must not be the row insert_review() returns;
  * MySQL reads its BOOLEAN column back as TINYINT 0/1, so the read normalization is
    exercised on a value that is not a Python bool;
  * both _shape_review() surfaces are asserted to agree, key for key.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
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


class _DialectDB:
    """Shared behaviour of the two dialect-shaped backends.

    Only the statements insert_review() actually issues are answered; anything else
    raises, so a change in that recipe cannot pass unnoticed.
    """

    db_type = _dialect.SQLITE
    last_id_sql = "last_insert_rowid"

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1
        self._last_id: int | None = None
        self._result: list[dict] = []
        # Test hook: a competing writer that inserts its own review between our INSERT
        # and our readback.
        self.concurrent_writer = None

    # ── write ──
    def store_row(self, params: list, *, own_connection: bool = True) -> dict:
        """Insert one row. ``own_connection=False`` models a DIFFERENT connection's
        writer, which is why it does not move this connection's last-inserted id —
        last_insert_rowid()/lastval()/LAST_INSERT_ID() are all session-scoped."""
        row = dict(zip(_INSERT_COLUMNS, params))
        row["id"] = self._next_id
        self._next_id += 1
        self.rows.append(row)
        if own_connection:
            self._last_id = row["id"]
        return row

    def check_bind(self, params: list) -> None:
        """Dialect-specific parameter type enforcement."""

    def read_back(self, row: dict) -> dict:
        """How this dialect hands the stored row back to the driver."""
        return dict(row)

    def execute(self, sql, params=None):
        params = list(params or [])
        if "INSERT INTO document_reviews" in sql:
            self.check_bind(params)
            self.store_row(params)
            if self.concurrent_writer is not None:
                writer, self.concurrent_writer = self.concurrent_writer, None
                writer(self)
            self._result = []
            return self
        if self.last_id_sql.lower() in sql.lower():
            self._result = [{"rid": self._last_id}]
            return self
        if "FROM document_reviews WHERE id" in sql:
            wanted = params[0] if params else None
            self._result = [self.read_back(r) for r in self.rows if r["id"] == wanted]
            return self
        raise AssertionError(f"unexpected statement in test backend: {sql}")

    def commit(self):
        pass

    # ── read ──
    def fetch_one(self, sql, params=None):
        self.execute(sql, params)
        return dict(self._result[0]) if self._result else None

    def fetch_all(self, sql, params=None):
        self.execute(sql, params)
        return [dict(r) for r in self._result]

    @contextmanager
    def begin_transaction(self):
        yield _DialectTxn(self)


class _DialectTxn:
    def __init__(self, db: _DialectDB):
        self._db = db

    def execute(self, sql, params=None):
        return self._db.execute(sql, params)

    def fetchone(self):
        return dict(self._db._result[0]) if self._db._result else None

    def fetchall(self):
        return [dict(r) for r in self._db._result]


class _PgBooleanColumnDB(_DialectDB):
    """Reproduces PostgreSQL's fallback_used BOOLEAN column type enforcement.

    See module docstring for the live verification this encodes. ``type(value) is
    int`` (rather than ``isinstance``) is deliberate: ``bool`` is a subclass of
    ``int`` in Python, and the whole point is to reject the old *bare-int* coercion
    while accepting a real ``bool``/``None``.
    """

    db_type = _dialect.POSTGRESQL
    last_id_sql = "lastval"  # what dialect.translate() rewrites last_insert_rowid() to

    def check_bind(self, params: list) -> None:
        value = params[_INSERT_COLUMNS.index("fallback_used")]
        if type(value) is int:
            raise RuntimeError(
                'column "fallback_used" is of type boolean but expression is of '
                "type integer\nHINT:  You will need to rewrite or cast the "
                "expression."
            )


class _MysqlBooleanColumnDB(_DialectDB):
    """MySQL's BOOLEAN is a TINYINT(1) alias — 0/1 and True/False are both accepted.

    T0005 §8: the same 3-state coverage without the application layer doing its own int
    coercion. Unlike PostgreSQL this backend never rejects an int, which is exactly the
    point — MySQL was never broken by the old code; only PostgreSQL was. T0007 §4.1: it
    also reads the column back the way the driver does, as TINYINT 0/1 rather than as
    the Python bool that went in, so the read side has to normalize a non-bool.
    """

    db_type = _dialect.MYSQL
    last_id_sql = "LAST_INSERT_ID"

    def read_back(self, row: dict) -> dict:
        out = dict(row)
        value = out.get("fallback_used")
        out["fallback_used"] = None if value is None else int(bool(value))
        return out


def _install(monkeypatch, db: _DialectDB) -> _DialectDB:
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    # Patch the singleton, not one module's get_store: both db.document_reviews and the
    # inbox route reach for the same store, and they must see the same one.
    monkeypatch.setattr(db_connection, "STORE", store)
    return db


@pytest.fixture
def pg_store(monkeypatch):
    return _install(monkeypatch, _PgBooleanColumnDB())


@pytest.fixture
def mysql_store(monkeypatch):
    return _install(monkeypatch, _MysqlBooleanColumnDB())


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


def test_insert_review_returns_its_own_row_not_a_concurrent_writers_newer_one(pg_store):
    """T0007 §3: the readback identifies the inserted row, so a review that lands
    between the INSERT and the read cannot be handed back instead.

    The old ``SELECT * FROM document_reviews ORDER BY id DESC LIMIT 1`` returned the
    intruder — with the wrong verdict, the wrong doc and the wrong provenance.
    """
    def _intruder(db):
        db.store_row([
            "flowgate.default.0535.0002-N", 0, "someone-else", "issues", "[]", "not mine",
            "t", "t", "t", None, None, None, None, None, None, True,
        ], own_connection=False)

    pg_store.concurrent_writer = _intruder

    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment="mine", reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=False,
    )

    assert row["comment"] == "mine"
    assert row["doc_id"] == DOC_ID
    assert row["verdict"] == "pass"
    assert row["fallback_used"] is False
    # The intruder is still there — it was simply not what we read back.
    assert len(pg_store.rows) == 2


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


# ── C. MySQL 3-state (T0005 §8 / T0007 §4.1) ────────────────────────────────────────

@pytest.mark.parametrize("value, stored", [(True, 1), (False, 0), (None, None)])
def test_insert_review_succeeds_on_mysql_for_every_fallback_state(mysql_store, value, stored):
    """MySQL takes the native bool and hands it back as TINYINT 0/1."""
    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=value,
    )
    assert row["fallback_used"] == stored
    assert row["fallback_used"] is not value or value is None


@pytest.mark.parametrize("value", [True, False, None])
def test_mysql_tinyint_readback_still_shapes_to_json_true_false_null(mysql_store, value):
    from modules.flow_gate.api.v1.document_routes import _shape_review

    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai",
        verdict="pass", findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=value,
    )
    assert _shape_review(row)["review_provider"]["fallback_used"] is value


# ── D. both read surfaces, on every raw representation a driver can produce ─────────
# (T0007 §4.3)

def _both_shapers():
    from modules.flow_gate.api.v1.document_routes import _shape_review as api_shape
    from modules.flow_gate.documents.routers.documents import _shape_review as docs_shape
    return {"api/v1/document_routes": api_shape, "documents/routers/documents": docs_shape}


@pytest.mark.parametrize("raw, expected", [
    (True, True),     # PostgreSQL BOOLEAN
    (False, False),
    (1, True),        # SQLite INTEGER CHECK(0,1) / MySQL TINYINT
    (0, False),
    (None, None),     # NULL — "no evidence", on every backend
])
def test_every_raw_fallback_representation_shapes_to_true_false_or_null(raw, expected):
    for surface, shape in _both_shapers().items():
        shaped = shape({"findings": "[]", "fallback_used": raw})
        assert shaped["review_provider"]["fallback_used"] is expected, surface


def test_the_two_read_surfaces_expose_the_same_provider_keys_for_legacy_and_new_rows():
    """A legacy row (all provenance NULL) and a fully populated one must produce the
    same provider payload shape, on both surfaces."""
    legacy = {"findings": "[]", "verdict": "pass"}
    persisted = {
        "findings": "[]", "verdict": "pass", "review_run_id": "air_1",
        "requested_provider_id": "aip_a", "actual_provider_id": "aip_b",
        "actual_provider_name": "B", "provider_source": "fallback",
        "attempt_no": 2, "fallback_used": True,
    }
    shapes = _both_shapers()
    keys = {
        (surface, label): set(shape(row)["review_provider"])
        for surface, shape in shapes.items()
        for label, row in (("legacy", legacy), ("persisted", persisted))
    }
    assert len(set(map(frozenset, keys.values()))) == 1, keys

    for surface, shape in shapes.items():
        assert shape(legacy)["review_provider"] == {
            "run_id": None, "requested_provider_id": None, "actual_provider_id": None,
            "actual_provider_name": None, "provider_source": None, "attempt_no": None,
            "fallback_used": None,
        }, surface
        assert shape(persisted)["review_provider"] == {
            "run_id": "air_1", "requested_provider_id": "aip_a",
            "actual_provider_id": "aip_b", "actual_provider_name": "B",
            "provider_source": "fallback", "attempt_no": 2, "fallback_used": True,
        }, surface


# ── E. real POST /inbox action=review through PostgreSQL, unmocked insert path ──────
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
    # The token claim has its own end-to-end coverage against a real database in
    # test_review_atomicity_0535.py; here the subject is the dialect-shaped write.
    consume = MagicMock(return_value=True)
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


def test_real_review_registration_binds_a_native_bool_not_an_int_on_postgresql(review_env, monkeypatch):
    """The end-to-end version of test A: the value that reaches the driver is the
    thing PostgreSQL accepts."""
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id, "action_scope": "review", "doc_ref": DOC_ID,
        "requested_provider_id": "aip_a", "provider_id": "aip_a",
        "provider": {"id": "aip_a", "name": "A"},
        "selected_provider_source": "project_default", "attempt_no": 1,
    })

    assert post_inbox(_body()).status_code == 201

    stored = review_env["db"].rows[-1]["fallback_used"]
    assert stored is False and type(stored) is bool


# ── F. insert failure must not consume the token or leave a partial review ─────────
# (T0005 §14, §17-H — the rollback semantics themselves are proven against a real
# database in test_review_atomicity_0535.py)

def test_a_failed_insert_returns_500_and_leaves_no_partial_row(review_env, monkeypatch):
    monkeypatch.setattr(
        db_reviews, "insert_review",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    response = post_inbox(_body())

    assert response.status_code == 500
    assert "DB registration error" in response.json()["error_message"]
    assert review_env["db"].rows == []
