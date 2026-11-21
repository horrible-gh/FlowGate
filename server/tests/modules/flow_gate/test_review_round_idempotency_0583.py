"""One durable verdict per AI review round (flowgate.default.0583 T0004).

The incident this pins: `flowgate.default.0579.0005-TR` collected two AI verdicts for
revision 1, 92 seconds apart, and the live `document_reviews` rows show why. Both carried
`review_run_id = NULL`, both were submitted with review-scope tokens bound to the SAME run
`aiv_20260917_000131`, and that run's document review loop stopped as
`retry_exhausted / "review hop produced no expected durable progress"`.

The chain, end to end:

  1. a rejected document starts its loop with the rework stage, and the UI posts that
     start as `action_scope=rework` -> folded to the `edit` token scope, so the RUN's
     admission scope is `edit` for its whole life;
  2. `_review_provenance` gated on that admission scope, so every verdict the loop
     registered stored NULL provenance - including the run id;
  3. `_document_loop_review_view` attributes rows by `review_run_id`, so the loop could
     not see its own verdict;
  4. `check_expected_progress` therefore judged the review hop to have produced nothing,
     and the gate reserved the REVIEW stage again;
  5. the retry minted a second review token and a second reviewer wrote a second verdict
     for the same revision.

Three things close it, and all three are exercised here:

  * the run id is stamped from the verified run itself, on its own axis, and a loop's
     REVIEW stage is a review submitter whatever scope the run was admitted under (§3.1);
  * `document_review_round_claims` makes a second durable verdict for one
    `(review_run_id, doc_id, revision_no)` impossible rather than merely unlikely (§4);
  * the worker asks once more, after the checkpoint's own read, before launching the
    reviewer a reserved retry would run (§5).

The route cases run against a REAL SQLite database built from this repo's migrations,
through the real HTTP route: the claim, the CAS, the INSERT and the rollback are all the
production ones, because a mock cannot show a barrier holding. The loop cases reuse
`LoopWorld` from test_ai_invoke_review_loop_durable_0486.py, which drives the real gate
and the real `_checkpoint_document_review_loop` over a real loop row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inbox_client import post_inbox as _raw_post_inbox  # noqa: E402

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.api.v1.events import publisher as sse_publisher  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import document_review_rounds as db_rounds  # noqa: E402
from modules.flow_gate.db import document_reviews as db_reviews  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as service  # noqa: E402
from modules.flow_gate.services.ai_invoke import review as review_module  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402

from test_ai_invoke_review_loop_durable_0486 import (  # noqa: E402
    DOC as LOOP_DOC,
    GROUP as LOOP_GROUP,
    LOOP_MIGRATIONS_107,
    OWNER as LOOP_OWNER,
    LoopWorld,
    _connect as _connect_loop_db,
)

_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

PROJECT = "flowgate"
GROUP_ID = "flowgate.default.0583"
DOC_ID = "flowgate.default.0583.0004-T"
USER = "usr_review_0583"
RUN_A = "aiv_0583_a"
RUN_B = "aiv_0583_b"


# ── a real SQLite backend, shaped the way FlowGateStore drives one ─────────────────

class _Txn:
    def __init__(self, db):
        self._db = db
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._db.conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class LiveSqliteDB:
    db_type = 1  # dialect.SQLITE

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self)
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def commit(self):
        self.conn.commit()

    def fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def rows(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def reviews(self) -> list[dict]:
        return self.rows("SELECT * FROM document_reviews ORDER BY id")

    def claims(self) -> list[dict]:
        return self.rows(
            "SELECT * FROM document_review_round_claims ORDER BY review_run_id, revision_no"
        )


TOKENS = {
    # Two review tokens bound to ONE run: the shape the loop's stage retry minted for
    # 0579.0005-TR (tok_20260917_000095 / _000096, both ai_run_id=aiv_20260917_000131).
    "tok-a1": RUN_A,
    "tok-a2": RUN_A,
    # A third run: what an explicit [재검수] admits.
    "tok-b1": RUN_B,
    # Human/legacy review tokens that name no run at all.
    "tok-legacy": None,
    "tok-legacy2": None,
}


CLAIMS_MIGRATION = _MIGRATIONS_DIR / "113_document_review_round_claims.sql"


def _build_db(path: str, *, with_claims_migration: bool = True) -> LiveSqliteDB:
    """A real database from this repo's migrations.

    ``with_claims_migration=False`` stops one file short of 113, which is the state every
    existing deployment is in the moment before this change ships: review rows, some of
    them already carrying a run id, and no claim table at all.
    """
    db = LiveSqliteDB(path)
    for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if not with_claims_migration and migration == CLAIMS_MIGRATION:
            continue
        try:
            db.conn.executescript(migration.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            # Same convention as test_review_atomicity_0535.py: migrations that do not
            # apply to a fresh file (re-adds, backfills) are skipped; the tables count.
            pass
    now = "2026-09-19T00:00:00+09:00"
    db.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)", (PROJECT, "FlowGate", now, now))
    db.conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (USER, "reviewer", "reviewer@example.com", "hashed", now, now))
    db.conn.execute(
        "INSERT INTO documents (doc_id, project_id, group_id, type_code, seq, title, "
        "revision_no, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (DOC_ID, PROJECT, None, "T", 4, "작업지시 승인", 1, now, now))
    for token_id, run_id in TOKENS.items():
        db.conn.execute(
            "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
            "issued_to, created_at, expires_at, ai_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (token_id, f"hash-{token_id}", "p1", PROJECT, DOC_ID, "review", USER,
             now, "2036-09-19T00:00:00+00:00", run_id))
    db.conn.commit()
    return db


def _run_record(run_id: str, **overrides) -> dict:
    run = {
        "run_id": run_id,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_sonnet",
        "provider": {"id": "aip_sonnet", "name": "Sonnet"},
        "selected_provider_source": "project_default",
        "attempt_no": 1,
        "review_intent": "normal",
    }
    run.update(overrides)
    return run


class Env:
    """The live database plus the three knobs a submission turns."""

    def __init__(self, db, monkeypatch):
        self.db = db
        self.monkeypatch = monkeypatch
        self.token_id = "tok-a1"
        self.doc = {"doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 1,
                    "title": "작업지시 승인"}
        self.runs = {RUN_A: _run_record(RUN_A), RUN_B: _run_record(RUN_B, review_intent="rerun")}

    def token_rec(self) -> dict:
        return {
            "token_id": self.token_id,
            "project": PROJECT,
            "issued_to": USER,
            "action_scope": "review",
            "doc_ref": DOC_ID,
            "ai_run_id": TOKENS[self.token_id],
            "dry_run_count": 0,
            "expires_at": "2036-09-19T00:00:00+00:00",
        }

    def as_(self, token_id: str) -> "Env":
        self.token_id = token_id
        return self

    def run_record(self, run_id):
        return self.runs.get(run_id)


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = _build_db(str(tmp_path / "flowgate.db"))
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)

    state = Env(db, monkeypatch)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: state.token_rec())
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: dict(state.doc))
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    monkeypatch.setattr(ai_runtime, "get_run_record", state.run_record)
    monkeypatch.setattr(sse_publisher, "broadcast_event_threadsafe", lambda _event: None)
    return state


def _body(**overrides) -> dict:
    body = {"action": "review", "project": PROJECT, "doc_id": DOC_ID,
            "verdict": "issues", "findings": [{"locus": "x", "note": "n"}],
            "comment": "review comment"}
    body.update(overrides)
    return body


def submit(body: dict | None = None, headers: dict | None = None):
    """One review submission, preflight receipt included (0545 admission is mandatory)."""
    body = dict(body or _body())
    dry = _raw_post_inbox(dict(body, dry_run=True), headers=headers)
    if dry.status_code != 200:
        return dry
    return _raw_post_inbox(dict(body, receipt=dry.json()["receipt"]), headers=headers)


# ── TC-1: a normal review registers exactly once, and claims its round ─────────────

def test_tc1_one_review_registers_one_row_and_one_round_claim(env):
    response = submit()

    assert response.status_code == 201, response.text
    rows = env.db.reviews()
    assert len(rows) == 1
    assert (rows[0]["revision_no"], rows[0]["verdict"]) == (1, "issues")
    assert rows[0]["review_run_id"] == RUN_A
    claims = env.db.claims()
    assert len(claims) == 1
    assert (claims[0]["review_run_id"], claims[0]["doc_id"], claims[0]["revision_no"]) == (
        RUN_A, DOC_ID, 1
    )
    assert claims[0]["token_id"] == "tok-a1"


# ── TC-2: a second attempt of the SAME round adds nothing ──────────────────────────

@pytest.mark.parametrize("second_verdict", ["issues", "pass", "hold"])
def test_tc2_a_second_attempt_of_the_same_round_registers_no_second_row(env, second_verdict):
    """§7: the first durable verdict IS the round's result. A later attempt that
    disagrees neither inserts nor overwrites."""
    assert submit().status_code == 201

    env.as_("tok-a2")
    second = submit(_body(verdict=second_verdict, comment="second attempt"))

    assert second.status_code == 409, second.text
    payload = second.json()
    assert payload["code"] == "review_round_already_registered"
    assert payload["registered"] is False
    rows = env.db.reviews()
    assert len(rows) == 1
    # Not overwritten either: the row still says what the first attempt said.
    assert (rows[0]["verdict"], rows[0]["comment"]) == ("issues", "review comment")
    assert payload["review_id"] == rows[0]["id"]
    assert payload["verdict"] == "issues"
    assert len(env.db.claims()) == 1


def test_tc2_attempt_no_is_not_a_round_boundary(env):
    """§3.1 / §10: a different attempt_no is a provider launch inside one round."""
    assert submit().status_code == 201

    env.runs[RUN_A] = _run_record(RUN_A, attempt_no=2)
    env.as_("tok-a2")

    assert submit().status_code == 409
    assert len(env.db.reviews()) == 1


def test_tc2_a_provider_fallback_is_not_a_round_boundary_either(env):
    """§4: requested != actual is a fallback INSIDE the round, not a new round."""
    assert submit().status_code == 201

    env.runs[RUN_A] = _run_record(RUN_A, provider_id="aip_opus",
                                  provider={"id": "aip_opus", "name": "Opus"})
    env.as_("tok-a2")

    assert submit().status_code == 409
    assert len(env.db.reviews()) == 1


def test_the_duplicate_answer_speaks_the_requested_locale(env):
    assert submit().status_code == 201
    env.as_("tok-a2")

    ko = submit(_body(), headers={"x-locale": "ko"})
    assert ko.status_code == 409
    assert ko.json()["error_message"] == inbox_routes._REVIEW_ROUND_DUPLICATE_MESSAGES["ko"]

    en = submit(_body(), headers={"x-locale": "en"})
    assert en.json()["error_message"] == inbox_routes._REVIEW_ROUND_DUPLICATE_MESSAGES["en"]
    ja = submit(_body(), headers={"x-locale": "ja"})
    assert ja.json()["error_message"] == inbox_routes._REVIEW_ROUND_DUPLICATE_MESSAGES["ja"]


# ── TC-4: two tokens that both pass the pre-check still produce one row ────────────

def test_tc4_the_barrier_and_not_the_precheck_is_what_stops_the_race(env, monkeypatch):
    """§4: "SELECT then INSERT" alone leaves a window. Here BOTH submissions see an
    unclaimed round (the pre-check is answered None once per request), so the only thing
    left between them is the claim INSERT itself."""
    assert submit().status_code == 201
    env.as_("tok-a2")

    real_get = db_rounds.get
    seen = {"n": 0}

    def _blind_first(*args, **kwargs):
        # The pre-check reads first; the post-rollback classification reads second.
        seen["n"] += 1
        return None if seen["n"] == 1 else real_get(*args, **kwargs)

    monkeypatch.setattr(db_rounds, "get", _blind_first)
    # The other half of the barrier - the already-durable verdict read, which covers the
    # deployment boundary - is blinded for this whole request, so the ONLY thing that can
    # stop the second registration here is the claim INSERT itself.
    monkeypatch.setattr(db_rounds, "existing_verdict", lambda *_a, **_k: None)

    second = submit(_body(verdict="pass"))

    assert seen["n"] >= 2, "the claim INSERT should have raised and been classified"
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "review_round_already_registered"
    assert len(env.db.reviews()) == 1
    assert len(env.db.claims()) == 1


# ── the deployment boundary: a verdict that predates its claim ───────────────────────
#
# The claim table is created empty, so on the day this ships every round that already
# holds a verdict is unclaimed. A review token issued before the deploy (or a late
# submission on one) addresses exactly such a round, and a barrier that only ever read
# the claim table would find that round free and let a SECOND durable verdict in - the
# invariant broken at the boundary this change exists to close. Two things answer it:
# migration 113 backfills the identities that already exist, and both the pre-check and
# the registration transaction read document_reviews as well as the claim table.


def _seed_unclaimed_verdict(
    db,
    *,
    review_run_id=RUN_A,
    revision_no: int = 1,
    doc_id: str = DOC_ID,
    verdict: str = "issues",
    comment: str = "the verdict this round already registered",
    reviewed_at: str = "2026-09-17T08:37:00+09:00",
) -> None:
    """One durable verdict that carries a run id and NO claim beside it."""
    db.conn.execute(
        "INSERT INTO document_reviews (doc_id, revision_no, reviewer_id, verdict, findings, "
        "comment, reviewed_at, created_at, updated_at, review_run_id, attempt_no) "
        "VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, 1)",
        (doc_id, revision_no, USER, verdict, comment, reviewed_at, reviewed_at,
         reviewed_at, review_run_id),
    )
    db.conn.commit()












# ── TC-5: an explicit rerun is a different round on the same revision ──────────────



# ── TC-7 (write half): a review with no run id is outside the barrier ──────────────



# ── the root cause: a rework-first loop's REVIEW hop owns what it writes ───────────





# ── the loop half: TC-3 and TC-6, driven through the real gate ─────────────────────

@pytest.fixture
def loop_world(tmp_path, monkeypatch):
    made = []

    def _make(*, run_id="aiv_0583_loop", review_count=2, failure_restart_max_attempts=0):
        conn = _connect_loop_db(tmp_path, f"{run_id}.db", LOOP_MIGRATIONS_107)
        made.append(conn)
        world = LoopWorld(conn, monkeypatch, run_id=run_id, review_count=review_count)
        world.store._execute(
            "UPDATE ai_invoke_document_review_loops SET failure_restart_max_attempts = ? "
            "WHERE run_id = ?", [failure_restart_max_attempts, run_id])
        return world

    yield _make
    for conn in made:
        conn.close()


def _run_payload(world) -> dict:
    return {
        "run_id": world.run_id, "group_id": LOOP_GROUP, "doc_ref": LOOP_DOC,
        "attempt_no": 1, "outcome": "complete", "issued_to": LOOP_OWNER,
        "document_review_loop": dict(world.loop()),
    }










# ── TC-7 (read half): legacy duplicate history is still safe to read ───────────────
