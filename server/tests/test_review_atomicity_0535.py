"""POST /inbox action=review is one durable unit: token claim + review row + event.

flowgate.default.0535 T0007 §3/§4.4/§4.5. Everything here runs against a REAL SQLite
database built from this repo's migrations, through the real HTTP route: nothing that
decides the outcome (the review INSERT, its readback, the token CAS, the token_consumed
event, the rollback) is mocked, because a mock cannot show a rollback. Only the three
things outside the subject are patched — who the token belongs to (token_service.verify),
the permission check, and the ai-invoke run record the provenance is read from — plus a
spy on the SSE publisher, which is the one side effect that is deliberately *outside* the
commit boundary.

Failures are injected at the driver seam (LiveSqliteDB.fail_on), so an "INSERT failed"
case really is a statement that raised on its way to SQLite, with the transaction state
that follows from it.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

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
from modules.flow_gate.api.v1.events import publisher as sse_publisher  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402

_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

PROJECT = "flowgate"
GROUP_ID = "flowgate.default.0535"
DOC_ID = "flowgate.default.0535.0001-T"
USER = "usr_review_0535"
TOKEN_ID = "tok-review-0535"
RUN_ID = "air_review_0535"


# ── a real SQLite backend with a statement log and one injectable failure ───────────

class _Txn:
    """The transaction handle FlowGateStore drives (execute/fetchone/fetchall)."""

    def __init__(self, db: "LiveSqliteDB"):
        self._db = db
        self._cur = None

    def execute(self, sql, params=None):
        self._db.note(sql)
        self._cur = self._db.conn.execute(sql, params or [])
        return self._cur  # carries .rowcount, which _execute_affected reads

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class LiveSqliteDB:
    """sqloader-shaped adapter over a real sqlite3 connection.

    ``fail_on`` makes the next statement containing that substring raise, which is how
    the failure-injection tests reach the driver instead of a mocked function.
    """

    db_type = 1  # dialect.SQLITE

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.log: list[str] = []
        self.fail_on: str | None = None

    def note(self, sql: str) -> None:
        flat = " ".join(sql.split())
        self.log.append(flat)
        if self.fail_on and self.fail_on in flat:
            raise sqlite3.OperationalError(f"injected failure on: {self.fail_on}")

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self)
        except BaseException:
            self.conn.rollback()
            self.log.append("ROLLBACK")
            raise
        self.conn.commit()
        self.log.append("COMMIT")

    # ── autocommit path (reads outside a transaction) ──
    def execute(self, sql, params=None):
        self.note(sql)
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def commit(self):
        self.conn.commit()

    def fetch_one(self, sql, params=None):
        self.note(sql)
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        self.note(sql)
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    # ── assertions read committed state straight off the connection ──
    def rows(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def reviews(self) -> list[dict]:
        return self.rows("SELECT * FROM document_reviews ORDER BY id")

    def token(self) -> dict:
        return self.rows("SELECT * FROM tokens WHERE token_id = ?", (TOKEN_ID,))[0]

    def consumed_events(self) -> list[dict]:
        return self.rows(
            "SELECT * FROM workflow_events WHERE event_type = 'token_consumed' ORDER BY id"
        )


def _build_db(path: str) -> LiveSqliteDB:
    db = LiveSqliteDB(path)
    for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            db.conn.executescript(migration.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            # Same convention as test_document_reviews.py: migrations that do not apply
            # to a fresh file (re-adds, backfills) are skipped, the tables are what count.
            pass
    now = "2026-09-06T00:00:00+09:00"
    db.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (PROJECT, "FlowGate", now, now),
    )
    db.conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (USER, "reviewer", "reviewer@example.com", "hashed", now, now),
    )
    db.conn.execute(
        "INSERT INTO documents (doc_id, project_id, group_id, type_code, seq, title, "
        "revision_no, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (DOC_ID, PROJECT, None, "T", 1, "작업지시", 0, "2026-09-06", "2026-09-06"),
    )
    db.conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
        "issued_to, created_at, expires_at, ai_run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TOKEN_ID, "hash-0535", "p1", PROJECT, DOC_ID, "review", USER,
         "2026-09-06T00:00:00+09:00", "2036-09-06T00:00:00+09:00", RUN_ID),
    )
    db.conn.commit()
    return db


def _token_rec(**overrides) -> dict:
    rec = {
        "token_id": TOKEN_ID,
        "project": PROJECT,
        "issued_to": USER,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "ai_run_id": RUN_ID,
        "dry_run_count": 0,
    }
    rec.update(overrides)
    return rec


def _run_record(**overrides) -> dict:
    run = {
        "run_id": RUN_ID,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_sonnet",
        "provider": {"id": "aip_sonnet", "name": "Sonnet"},
        "selected_provider_source": "project_default",
        "attempt_no": 1,
    }
    run.update(overrides)
    return run


class _SseSpy:
    def __init__(self):
        self.events: list[str] = []
        self.raise_on_publish = False

    def __call__(self, event):
        if self.raise_on_publish:
            raise RuntimeError("SSE broker is down")
        self.events.append(getattr(event.event_type, "value", str(event.event_type)))


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = _build_db(str(tmp_path / "flowgate.db"))
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)

    token_rec = _token_rec()
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: dict(token_rec))
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 0, "title": "작업지시",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: _run_record(run_id=run_id))

    sse = _SseSpy()
    monkeypatch.setattr(sse_publisher, "broadcast_event_threadsafe", sse)
    return {"db": db, "sse": sse, "store": store, "token_rec": token_rec}


def _body(**overrides) -> dict:
    body = {"action": "review", "project": PROJECT, "doc_id": DOC_ID,
            "verdict": "pass", "findings": [], "comment": "ok"}
    body.update(overrides)
    return body


def _index_of(log: list[str], needle: str) -> int:
    for i, sql in enumerate(log):
        if needle in sql:
            return i
    raise AssertionError(f"statement not found in log: {needle}\n" + "\n".join(log))


# ── 1. the success path: one row, one claim, one event, SSE after the commit ────────

def test_a_successful_review_stores_one_row_claims_the_token_and_publishes_after_commit(env):
    db = env["db"]

    response = post_inbox(_body())

    assert response.status_code == 201, response.text
    reviews = db.reviews()
    assert len(reviews) == 1
    assert reviews[0]["doc_id"] == DOC_ID and reviews[0]["verdict"] == "pass"
    assert db.token()["consumed_at"] is not None
    assert len(db.consumed_events()) == 1
    # Both screen pushes ride the same best-effort block.
    assert len(env["sse"].events) == 2

    # Observable ordering: claim, review row and event are all inside the transaction
    # that commits; the SSE publish is the only thing after it.
    log = db.log
    commit = _index_of(log, "COMMIT")
    assert _index_of(log, "UPDATE tokens SET consumed_at") < commit
    assert _index_of(log, "INSERT INTO workflow_events") < commit
    assert _index_of(log, "INSERT INTO document_reviews") < commit
    assert _index_of(log, "SELECT * FROM document_reviews WHERE id") < commit
    assert "ROLLBACK" not in log


def test_the_provenance_snapshot_is_written_with_the_row_it_belongs_to(env):
    post_inbox(_body())

    row = env["db"].reviews()[0]
    assert row["review_run_id"] == RUN_ID
    assert row["requested_provider_id"] == "aip_sonnet"
    assert row["actual_provider_id"] == "aip_sonnet"
    assert row["actual_provider_name"] == "Sonnet"
    assert row["provider_source"] == "project_default"
    assert row["attempt_no"] == 1
    # SQLite stores the Optional[bool] in an INTEGER CHECK(0,1) column.
    assert row["fallback_used"] == 0


def test_the_route_passes_the_snapshot_to_insert_review_and_stores_exactly_that(env, monkeypatch):
    """T0007 §4.2: the insert arguments and the persisted row are checked together, so a
    correct call that stores something else (or a wrong call that happens to read back
    plausibly) cannot pass."""
    import modules.flow_gate.db.document_reviews as reviews_module

    captured: list[dict] = []
    real_insert = reviews_module.insert_review

    def _spy(**kwargs):
        captured.append(dict(kwargs))
        return real_insert(**kwargs)

    monkeypatch.setattr(reviews_module, "insert_review", _spy)

    assert post_inbox(_body()).status_code == 201

    assert len(captured) == 1
    call = captured[0]
    assert call["doc_id"] == DOC_ID
    assert call["verdict"] == "pass"
    assert call["reviewed_at"]
    assert call["review_run_id"] == RUN_ID
    assert call["requested_provider_id"] == "aip_sonnet"
    assert call["actual_provider_id"] == "aip_sonnet"
    assert call["provider_source"] == "project_default"
    assert call["attempt_no"] == 1
    assert call["fallback_used"] is False  # a real bool, not 0
    row = env["db"].reviews()[0]
    assert row["fallback_used"] == 0 and row["provider_source"] == "project_default"


@pytest.mark.parametrize("value, stored", [(True, 1), (False, 0), (None, None)])
def test_the_three_states_round_trip_through_a_real_sqlite_store(env, value, stored):
    """T0007 §4.1: SQLite keeps the Optional[bool] in an INTEGER CHECK(0,1) column; the
    write must survive the CHECK and the read must shape back to true/false/null."""
    from modules.flow_gate.api.v1.document_routes import _shape_review
    from modules.flow_gate.db import document_reviews as db_reviews

    row = db_reviews.insert_review(
        doc_id=DOC_ID, revision_no=0, reviewer_id="ai", verdict="pass",
        findings_json="[]", comment=None, reviewed_at="2026-09-06T22:00:00",
        attempt_no=1, fallback_used=value,
    )

    assert row["fallback_used"] == stored
    assert _shape_review(row)["review_provider"]["fallback_used"] is value
    assert env["db"].reviews()[-1]["fallback_used"] == stored


# ── 2. rollback boundaries: every durable failure leaves nothing behind ─────────────

@pytest.mark.parametrize("fail_on, what", [
    ("INSERT INTO document_reviews", "the review INSERT itself"),
    ("SELECT * FROM document_reviews WHERE id", "the readback of the inserted row"),
    ("INSERT INTO workflow_events", "the token_consumed event"),
])
def test_a_failure_inside_the_boundary_leaves_no_review_and_an_unconsumed_token(
    env, fail_on, what,
):
    """T0007 §4.4. The readback case is the one TR0006 could not reach: the INSERT
    succeeds and only the read after it fails, which used to leave the review row
    committed behind a 500."""
    db = env["db"]
    db.fail_on = fail_on

    response = post_inbox(_body())

    assert response.status_code == 500, what
    assert "DB registration error" in response.json()["error_message"]
    assert db.reviews() == [], what
    assert db.token()["consumed_at"] is None, what
    assert db.consumed_events() == [], what
    assert env["sse"].events == [], what
    assert "ROLLBACK" in db.log
    assert "COMMIT" not in db.log


def test_the_token_is_still_usable_after_a_rolled_back_attempt(env):
    """The point of rolling the claim back: a retry of the same token must work."""
    db = env["db"]
    db.fail_on = "SELECT * FROM document_reviews WHERE id"
    assert post_inbox(_body()).status_code == 500

    db.fail_on = None
    retry = post_inbox(_body())

    assert retry.status_code == 201, retry.text
    assert len(db.reviews()) == 1
    assert db.token()["consumed_at"] is not None
    assert len(db.consumed_events()) == 1


# ── 3. the claim decides who owns the submission ───────────────────────────────────

def test_a_token_another_request_already_consumed_is_refused_without_a_review(env):
    """The race window, reproduced: token_service.verify() rejects an already-consumed
    token with 401 on its own, so this 409 is what a request sees when the row was
    consumed AFTER its verify read it (a concurrent submit, or the auth cache handing
    out a row that was still unconsumed when it was cached). The patched verify in this
    fixture is exactly that window held open."""
    db = env["db"]
    db.conn.execute(
        "UPDATE tokens SET consumed_at = ? WHERE token_id = ?",
        ("2026-09-06T01:00:00+09:00", TOKEN_ID),
    )
    db.conn.commit()

    response = post_inbox(_body())

    assert response.status_code == 409
    assert "already been consumed" in response.json()["error_message"]
    assert db.reviews() == []
    # The pre-existing consumed_at is untouched and no second event was written.
    assert db.token()["consumed_at"] == "2026-09-06T01:00:00+09:00"
    assert db.consumed_events() == []
    assert env["sse"].events == []


def test_two_real_submits_with_the_same_token_register_exactly_one_review(env):
    """The interleaved double submit. Both requests get past verify() with the same
    unconsumed token record (the fixture's verify patch holds that window open, which is
    what a genuine concurrent pair sees), so both reach the claim; only one can win it,
    and the loser adds no review."""
    db = env["db"]

    first = post_inbox(_body())
    second = post_inbox(_body(comment="second attempt"))

    assert [first.status_code, second.status_code] == [201, 409]
    assert len(db.reviews()) == 1
    assert db.reviews()[0]["comment"] == "ok"
    assert len(db.consumed_events()) == 1


def test_a_concurrent_claim_of_one_token_has_exactly_one_winner(env, tmp_path, monkeypatch):
    """The claim is decided by the database, not by this process.

    Two threads on two separate connections run db_tokens.consume_claim() against the
    same row; SQLite serializes the guarded UPDATE, so exactly one call can see an
    affected row count of 1.
    """
    db_path = str(tmp_path / "flowgate.db")
    local = threading.local()

    def _thread_store():
        store = getattr(local, "store", None)
        if store is None:
            backend = LiveSqliteDB(db_path)
            store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
            store._db, store._sq = backend, None
            local.store = store
        return store

    monkeypatch.setattr(db_tokens, "get_store", _thread_store)

    start = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def claim():
        start.wait()
        won = db_tokens.consume_claim(TOKEN_ID)
        with lock:
            results.append(won)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == [False, True]
    assert env["db"].token()["consumed_at"] is not None


# ── 4. the SSE publish is outside the boundary on purpose ──────────────────────────

def test_an_sse_failure_after_the_commit_keeps_the_201_and_the_stored_review(env):
    db = env["db"]
    env["sse"].raise_on_publish = True

    response = post_inbox(_body())

    assert response.status_code == 201, response.text
    assert len(db.reviews()) == 1
    assert db.token()["consumed_at"] is not None
    assert len(db.consumed_events()) == 1
    assert "COMMIT" in db.log and "ROLLBACK" not in db.log


# ── 5. dry-run reproduces the checks and writes nothing ────────────────────────────

def test_a_dry_run_validates_without_any_durable_side_effect_and_the_real_submit_follows(env):
    db = env["db"]
    seen_runs: list[str] = []
    original = ai_runtime.get_run_record

    def _counting(run_id):
        seen_runs.append(run_id)
        return original(run_id)

    ai_runtime.get_run_record = _counting
    try:
        dry = post_inbox(_body(dry_run=True))
    finally:
        ai_runtime.get_run_record = original

    assert dry.status_code == 200
    payload = dry.json()
    assert payload["dry_run"] is True
    assert payload["would_register"] == {
        "action": "review",
        "doc_id": DOC_ID,
        "verdict": "pass",
        "finding_count": 0,
        "checks_passed": ["auth", "context_binding", "permission", "referential_integrity"],
    }
    # The dry-run resolved the same server-owned provenance evidence a real submit does…
    assert seen_runs == [RUN_ID]
    # …and wrote none of it.
    assert db.reviews() == []
    assert db.token()["consumed_at"] is None
    assert db.consumed_events() == []
    assert env["sse"].events == []
    assert db.token()["dry_run_count"] == 1

    real = post_inbox(_body())

    assert real.status_code == 201, real.text
    assert real.json()["verdict"] == payload["would_register"]["verdict"]
    assert real.json()["finding_count"] == payload["would_register"]["finding_count"]
    assert len(db.reviews()) == 1
    assert db.token()["consumed_at"] is not None
    assert len(db.consumed_events()) == 1


# ── 6. the three provenance states, end to end through the real route ──────────────

def test_fallback_true_is_stored_when_the_two_provider_ids_differ(env, monkeypatch):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: _run_record(
        run_id=run_id, requested_provider_id="aip_sonnet", provider_id="aip_opus",
        provider={"id": "aip_opus", "name": "Opus"}, attempt_no=2,
    ))

    assert post_inbox(_body()).status_code == 201

    row = env["db"].reviews()[0]
    assert row["fallback_used"] == 1
    assert row["provider_source"] == "fallback"
    assert row["requested_provider_id"] == "aip_sonnet"
    assert row["actual_provider_id"] == "aip_opus"
    assert row["attempt_no"] == 2


def test_fallback_false_is_stored_only_when_both_ids_are_present_and_equal(env):
    assert post_inbox(_body()).status_code == 201

    row = env["db"].reviews()[0]
    assert row["fallback_used"] == 0
    assert row["requested_provider_id"] == row["actual_provider_id"] == "aip_sonnet"


_PROVENANCE_COLUMNS = (
    "review_run_id", "requested_provider_id", "actual_provider_id",
    "actual_provider_name", "provider_source", "attempt_no", "fallback_used",
)


def _raise(_run_id):
    raise RuntimeError("run registry unavailable")


@pytest.mark.parametrize("case, token_overrides, run_record", [
    ("legacy token with no ai_run_id", {"ai_run_id": None}, None),
    ("run lookup raises", {}, _raise),
    ("run is missing", {}, lambda _run_id: None),
    ("run belongs to another action", {}, lambda run_id: _run_record(
        run_id=run_id, action_scope="new")),
    ("run belongs to another document", {}, lambda run_id: _run_record(
        run_id=run_id, doc_ref="flowgate.default.0535.0002-N")),
    ("requested provider id missing", {}, lambda run_id: _run_record(
        run_id=run_id, requested_provider_id=None)),
    ("actual provider id missing", {}, lambda run_id: _run_record(
        run_id=run_id, provider_id=None)),
])
def test_unprovable_provenance_is_stored_as_null_not_as_false(
    env, monkeypatch, case, token_overrides, run_record,
):
    """T0007 §2 row 3: NULL means "no evidence to decide", and False must never stand in
    for it. `bool(requested and actual and requested != actual)` used to answer False for
    every one of these."""
    if token_overrides:
        monkeypatch.setattr(
            inbox_routes.token_service, "verify",
            lambda _raw: _token_rec(**token_overrides),
        )
    if run_record is not None:
        monkeypatch.setattr(ai_runtime, "get_run_record", run_record)

    response = post_inbox(_body())

    assert response.status_code == 201, f"{case}: {response.text}"
    row = env["db"].reviews()[0]
    for column in _PROVENANCE_COLUMNS:
        assert row[column] is None, f"{case}: {column} should be NULL, got {row[column]!r}"


def test_a_submitted_payload_cannot_forge_the_provenance(env, monkeypatch):
    """Provenance is server-owned: the fields are read off the run record only."""
    monkeypatch.setattr(inbox_routes.token_service, "verify",
                        lambda _raw: _token_rec(ai_run_id=None))

    response = post_inbox(_body(
        fallback_used=True, review_run_id="air_forged",
        requested_provider_id="aip_forged", actual_provider_id="aip_forged2",
        provider_source="fallback", attempt_no=99,
    ))

    assert response.status_code == 201, response.text
    row = env["db"].reviews()[0]
    for column in _PROVENANCE_COLUMNS:
        assert row[column] is None, column
