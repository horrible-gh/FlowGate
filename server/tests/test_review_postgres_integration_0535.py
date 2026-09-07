"""POST /inbox action=review against a REAL PostgreSQL server (opt-in).

flowgate.default.0535 T0007 §4.1 last bullet. The offline suite proves the contract
against dialect-shaped fakes (test_document_reviews_dialect_0535.py) and against a real
SQLite database (test_review_atomicity_0535.py). Neither can prove that PostgreSQL
itself accepts the ``fallback_used`` bind and hands the three states back — the bug this
group exists for was exactly a PostgreSQL type rejection nothing offline reproduced.

There is no PostgreSQL in the ordinary test environment, so this file is opt-in and
SKIPS by default. It does not replace the offline tests; it is the live confirmation of
what they encode.

Run it against a database that has migration 104 applied::

    # PowerShell, from server/
    $env:FLOWGATE_PG_TEST_DSN = "postgresql://flowgate:<password>@192.168.0.250:5432/flowgate"
    python -m pytest tests/test_review_postgres_integration_0535.py -v -rs

Everything it writes — the project, user and document it needs for the foreign keys, the
token, the reviews — happens inside ONE transaction that is rolled back in teardown, so
a run against the staging database leaves no row behind (the convention TR0006 used for
its live reproduction). The route's own ``store.transaction()`` runs as a SAVEPOINT
inside it, so a rollback under test really is PostgreSQL rolling the statement back.
"""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from inbox_client import post_inbox  # noqa: E402

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.api.v1.events import publisher as sse_publisher  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import dialect as _dialect  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402

DSN_ENV = "FLOWGATE_PG_TEST_DSN"
DSN = os.environ.get(DSN_ENV)

psycopg2 = pytest.importorskip(
    "psycopg2",
    reason="psycopg2 is not installed; the live PostgreSQL check cannot run here",
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DSN,
        reason=(
            f"{DSN_ENV} is not set — this is the opt-in live PostgreSQL check "
            "(see the module docstring for how to run it)"
        ),
    ),
]


class _PgTxn:
    """One transaction handle over a psycopg2 cursor."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._cursor.execute(sql, list(params or []) or None)
        self.rowcount = self._cursor.rowcount
        return self._cursor  # carries .rowcount for _execute_affected

    def fetchone(self):
        row = self._cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cursor.fetchall()]


class PgDB:
    """sqloader-shaped adapter over one psycopg2 connection held in a test transaction.

    ``begin_transaction`` is a SAVEPOINT, not a real BEGIN: the outer transaction is the
    test's, and rolling back to the savepoint is exactly what the product's rollback
    does to its own statements — including recovering from PostgreSQL's "current
    transaction is aborted" state after a failed statement.
    """

    db_type = _dialect.POSTGRESQL

    def __init__(self, conn):
        self.conn = conn
        self._depth = 0
        self.fail_on: str | None = None

    def _cursor(self):
        from psycopg2.extras import RealDictCursor
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def _guard(self, sql: str) -> None:
        if self.fail_on and self.fail_on in " ".join(sql.split()):
            raise RuntimeError(f"injected failure on: {self.fail_on}")

    @contextmanager
    def begin_transaction(self):
        self._depth += 1
        name = f"fg_review_{self._depth}"
        cursor = self._cursor()
        cursor.execute(f"SAVEPOINT {name}")
        try:
            yield _PgGuardedTxn(self, cursor)
        except BaseException:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {name}")
            cursor.close()
            self._depth -= 1
            raise
        cursor.execute(f"RELEASE SAVEPOINT {name}")
        cursor.close()
        self._depth -= 1

    # ── autocommit-path shims (reads outside a transaction stay in the test txn) ──
    def execute(self, sql, params=None):
        self._guard(sql)
        cursor = self._cursor()
        cursor.execute(sql, list(params or []) or None)
        return cursor

    def commit(self):
        pass  # the test transaction is never committed

    def fetch_one(self, sql, params=None):
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        cursor = self.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    # ── assertions ──
    def rows(self, sql, params=()):
        cursor = self._cursor()
        cursor.execute(sql, list(params) or None)
        out = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return out


class _PgGuardedTxn(_PgTxn):
    def __init__(self, db: PgDB, cursor):
        super().__init__(cursor)
        self._db = db

    def execute(self, sql, params=None):
        self._db._guard(sql)
        return super().execute(sql, params)


@pytest.fixture(scope="module")
def pg_conn():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    yield conn
    # Nothing this file wrote is ever committed.
    conn.rollback()
    conn.close()


@pytest.fixture
def pg_env(pg_conn, monkeypatch):
    suffix = uuid.uuid4().hex[:10]
    project = f"pgt_{suffix}"
    user_id = f"usr_{suffix}"
    doc_id = f"pgtest.0535.{suffix}-T"
    token_id = f"tok_{suffix}"
    run_id = f"air_{suffix}"
    now = "2026-09-06T00:00:00+09:00"

    db = PgDB(pg_conn)
    cur = pg_conn.cursor()
    cur.execute("SAVEPOINT fg_case")
    cur.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s)", (project, f"pg test {suffix}", now, now))
    cur.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, f"u{suffix}", f"{suffix}@example.com", "x", now, now))
    cur.execute(
        "INSERT INTO documents (doc_id, project_id, type_code, seq, title, revision_no, "
        "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (doc_id, project, "T", 1, "pg integration", 0, now, now))
    cur.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
        "issued_to, created_at, expires_at, ai_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (token_id, f"hash_{suffix}", "p1", project, doc_id, "review", user_id,
         now, "2036-09-06T00:00:00+09:00", run_id))
    cur.close()

    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)

    token_rec = {
        "token_id": token_id, "project": project, "issued_to": user_id,
        "action_scope": "review", "doc_ref": doc_id, "ai_run_id": run_id,
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: dict(token_rec))
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    # NOTE: this patches the shared db.documents module, so register_binding's own
    # group lookup sees it too — both sides of the group axis resolve to this value.
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": doc_id, "group_id": f"pgtest.0535.{suffix}", "revision_no": 0,
        "title": "pg integration",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda rid: {
        "run_id": rid, "action_scope": "review", "doc_ref": doc_id,
        "requested_provider_id": "aip_a", "provider_id": "aip_a",
        "provider": {"id": "aip_a", "name": "A"},
        "selected_provider_source": "project_default", "attempt_no": 1,
    })
    monkeypatch.setattr(sse_publisher, "broadcast_event_threadsafe", MagicMock())

    yield {"db": db, "doc_id": doc_id, "token_id": token_id, "project": project,
           "user_id": user_id, "run_id": run_id}

    cur = pg_conn.cursor()
    cur.execute("ROLLBACK TO SAVEPOINT fg_case")
    cur.close()


def _body(doc_id, **overrides):
    body = {"action": "review", "project": None, "doc_id": doc_id,
            "verdict": "pass", "findings": [], "comment": "ok"}
    body.update(overrides)
    return body


def _reviews(env):
    return env["db"].rows(
        "SELECT * FROM document_reviews WHERE doc_id = %s ORDER BY id", (env["doc_id"],))


def _token(env):
    return env["db"].rows(
        "SELECT * FROM tokens WHERE token_id = %s", (env["token_id"],))[0]


# ── 1. the three states really do write and read back on PostgreSQL ────────────────

@pytest.mark.parametrize("requested, actual, expected", [
    ("aip_a", "aip_b", True),    # fallback
    ("aip_a", "aip_a", False),   # the reported failure case
    (None, "aip_a", None),       # incomplete evidence -> NULL, never False
])
def test_a_real_review_registration_stores_the_expected_fallback_state(
    pg_env, monkeypatch, requested, actual, expected,
):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda rid: {
        "run_id": rid, "action_scope": "review", "doc_ref": pg_env["doc_id"],
        "requested_provider_id": requested, "provider_id": actual,
        "provider": {"id": actual, "name": "A"},
        "selected_provider_source": "project_default", "attempt_no": 1,
    })

    response = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"]))

    assert response.status_code == 201, response.text
    rows = _reviews(pg_env)
    assert len(rows) == 1
    stored = rows[0]["fallback_used"]
    assert stored is expected, f"PostgreSQL BOOLEAN round-trip returned {stored!r}"
    assert _token(pg_env)["consumed_at"] is not None

    from modules.flow_gate.api.v1.document_routes import _shape_review
    assert _shape_review(rows[0])["review_provider"]["fallback_used"] is expected


def test_the_review_row_is_the_one_this_request_inserted(pg_env):
    """lastval() identifies our row on PostgreSQL — the readback is not "the newest
    review in the table", which on a live server belongs to whoever wrote last."""
    response = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"], comment="mine"))

    assert response.status_code == 201, response.text
    rows = _reviews(pg_env)
    assert len(rows) == 1 and rows[0]["comment"] == "mine"


# ── 2. atomicity, decided by PostgreSQL's own rollback ─────────────────────────────

def test_a_readback_failure_after_the_insert_leaves_no_row_and_an_unconsumed_token(pg_env):
    pg_env["db"].fail_on = "SELECT * FROM document_reviews WHERE id"

    response = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"]))

    assert response.status_code == 500
    assert _reviews(pg_env) == []
    assert _token(pg_env)["consumed_at"] is None

    # And the token is still usable afterwards.
    pg_env["db"].fail_on = None
    retry = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"]))
    assert retry.status_code == 201, retry.text
    assert len(_reviews(pg_env)) == 1


def test_a_second_submit_of_the_same_token_is_refused_by_the_postgres_cas(pg_env):
    """Both requests pass the fixture's verify() with the same unconsumed record — the
    window a concurrent pair is really in — so PostgreSQL's own guarded UPDATE is what
    picks the winner."""
    first = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"]))
    second = post_inbox(_body(pg_env["doc_id"], project=pg_env["project"], comment="again"))

    assert [first.status_code, second.status_code] == [201, 409]
    assert len(_reviews(pg_env)) == 1
