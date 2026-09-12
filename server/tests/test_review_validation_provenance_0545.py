"""0545 T0014: successful dry-run validation provenance is a real server contract.

NR0013 finding 1: the worker read ``response["validated_at"]`` and
``response["validation"][...]`` from a shape only the worker test's own mock ever
produced -- the real ``POST /inbox action=review dry_run=true`` route never put those
keys in its response, so production diagnostics stayed all-``None``. This suite proves
the producer side directly through the real HTTP route (no ``_encoding_guard`` /
``_encoding_validation_result`` mock, no receipt-service mock): the actual dry-run
response, the actual ``review_dry_run_receipts`` audit row, and the actual worker
consumer (``worker._review_submission_diagnostic``) all have to agree, against a real
SQLite database built from this repo's migrations -- the same harness style as
``test_review_atomicity_0535.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

import pytest  # noqa: E402

from inbox_client import post_inbox  # noqa: E402

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import review_receipts as db_receipts  # noqa: E402
from modules.flow_gate.db.connection import to_now_iso_tz  # noqa: E402
from modules.flow_gate.services import review_receipt_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import worker  # noqa: E402

_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

PROJECT = "flowgate"
GROUP_ID = "flowgate.default.0545"
DOC_ID = "flowgate.default.0545.0014-T"
USER = "usr_review_0545_prov"
TOKEN_ID = "tok-review-0545-prov"

CORRUPT = "??? ?? ? 0082(??3) ?? ? ?? ??"
CLEAN = "정상적인 한글 검수 코멘트입니다"


class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class LiveSqliteDB:
    """Real sqlite3 connection wired to the FlowGateStore driver shape."""

    db_type = 1  # dialect.SQLITE

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self.conn)
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

    def receipt(self, receipt_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM review_dry_run_receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        assert row is not None, "receipt was not persisted"
        return dict(row)


def _build_db(path: str) -> LiveSqliteDB:
    db = LiveSqliteDB(path)
    for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            db.conn.executescript(migration.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    now = "2026-09-11T00:00:00+09:00"
    db.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (PROJECT, "FlowGate", now, now),
    )
    db.conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (USER, "reviewer", "reviewer-0545-prov@example.com", "hashed", now, now),
    )
    db.conn.execute(
        "INSERT INTO documents (doc_id, project_id, group_id, type_code, seq, title, "
        "revision_no, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (DOC_ID, PROJECT, None, "T", 1, "작업지시", 0, now, now),
    )
    db.conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
        "issued_to, created_at, expires_at, ai_run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TOKEN_ID, "hash-0545-prov", "p1", PROJECT, DOC_ID, "review", USER,
         now, "2036-09-11T00:00:00+00:00", None),
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
        "ai_run_id": None,
        "dry_run_count": 0,
        "expires_at": "2036-09-11T00:00:00+00:00",
    }
    rec.update(overrides)
    return rec


def _setup_env(monkeypatch, tmp_path, **token_overrides) -> dict:
    db = _build_db(str(tmp_path / "flowgate.db"))
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)

    token_rec = _token_rec(**token_overrides)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: dict(token_rec))
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 0, "title": "작업지시",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    return {"db": db, "token_rec": token_rec}


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _setup_env(monkeypatch, tmp_path)


def _dry_run_body(**overrides) -> dict:
    body = {
        "action": "review", "project": PROJECT, "doc_id": DOC_ID,
        "verdict": "pass", "findings": [], "comment": CLEAN, "dry_run": True,
    }
    body.update(overrides)
    return body


def _fingerprint(text: str) -> tuple[str, int]:
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


# ── AC-1: normal Unicode dry-run ─────────────────────────────────────────────────────

def test_ac1_normal_unicode_dry_run_has_a_real_validated_at_and_no_corruption(env):
    response = post_inbox(_dry_run_body())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validated_at"] is not None
    assert payload["validation"]["corruption_detected"] is False


# ── AC-2: fingerprint supplied and matches ───────────────────────────────────────────

def test_ac2_matching_fingerprint_is_true_in_both_response_and_receipt_audit(env):
    sha, chars = _fingerprint(CLEAN)
    response = post_inbox(_dry_run_body(body_sha256=sha, body_chars=chars))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["fingerprint_supplied"] is True
    assert payload["validation"]["fingerprint_matched"] is True

    audit = json.loads(env["db"].receipt(payload["receipt"])["encoding_provenance"])
    assert audit["fingerprint_supplied"] is True
    assert audit["fingerprint_matched"] is True


# ── AC-3: fingerprint not supplied ───────────────────────────────────────────────────

def test_ac3_missing_fingerprint_is_consistently_false(env):
    response = post_inbox(_dry_run_body())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["fingerprint_supplied"] is False
    assert payload["validation"]["fingerprint_matched"] is False

    audit = json.loads(env["db"].receipt(payload["receipt"])["encoding_provenance"])
    assert audit["fingerprint_supplied"] is False
    assert audit["fingerprint_matched"] is False


# ── AC-4: corruption + valid force ───────────────────────────────────────────────────

def test_ac4_corruption_with_valid_force_reports_both_true(env):
    response = post_inbox(_dry_run_body(
        comment=CORRUPT,
        force_encoding_reason="검토자가 원문의 물음표 표현을 확인했습니다",
    ))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["corruption_detected"] is True
    assert payload["validation"]["force_used"] is True


# ── AC-5: normal payload, force unused (and force reason present but not needed) ────

def test_ac5_clean_payload_without_force_reports_both_false(env):
    response = post_inbox(_dry_run_body())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["corruption_detected"] is False
    assert payload["validation"]["force_used"] is False


def test_ac5b_force_reason_present_but_unneeded_does_not_mark_force_used(env):
    """T0014 §7: a force reason on a CLEAN payload must not read as force_used=True --
    force_used means the bypass was actually needed, not merely that the field was
    filled in."""
    response = post_inbox(_dry_run_body(
        force_encoding_reason="이 코멘트는 사실 정상이라 강제가 필요 없습니다",
    ))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["corruption_detected"] is False
    assert payload["validation"]["force_used"] is False


# ── AC-6: response and durable audit agree exactly ───────────────────────────────────

def test_ac6_response_and_receipt_audit_carry_the_same_validation_facts(env):
    sha, chars = _fingerprint(CLEAN)
    response = post_inbox(_dry_run_body(body_sha256=sha, body_chars=chars))

    assert response.status_code == 200, response.text
    payload = response.json()
    audit = json.loads(env["db"].receipt(payload["receipt"])["encoding_provenance"])

    assert audit["validated_at"] == payload["validated_at"]
    for key in ("corruption_detected", "fingerprint_supplied", "fingerprint_matched", "force_used"):
        assert audit[key] == payload["validation"][key], key


# ── AC-7: the worker consumes the real response shape, not a hand-authored mock ─────

def test_ac7_worker_diagnostic_reads_real_provenance_from_the_real_response(env):
    response = post_inbox(_dry_run_body())

    assert response.status_code == 200, response.text
    payload = response.json()

    run = {"run_id": "aiv_0545_prov", "doc_ref": DOC_ID, "provider_id": "provider-test",
           "locale": "ko"}
    worker._review_submission_diagnostic(run, payload, submitted=False)

    submission = run["review_submission"]
    assert submission["validated_at"] is not None
    assert submission["corruption_detected"] is False
    assert submission["fingerprint_supplied"] is False
    assert submission["fingerprint_matched"] is False
    assert submission["force_used"] is False


# ── T0019 (NR0017 §2): receipt expiry timezone normalization ────────────────────────

def _real_submit_body(**overrides) -> dict:
    body = {
        "action": "review", "project": PROJECT, "doc_id": DOC_ID,
        "verdict": "pass", "findings": [], "comment": CLEAN,
    }
    body.update(overrides)
    return body


def test_t0019_token_with_two_hours_left_utc_format_dry_run_then_real_submit_is_201(
    monkeypatch, tmp_path,
):
    """NR0017 §2 reproduction: a token with two real hours left, stored the way
    token_service.issue() actually writes it (UTC, +00:00), must let its dry-run
    receipt carry through to a real submit -- not be judged already expired just
    because classify() compares it against now_iso()'s JST string."""
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    _setup_env(monkeypatch, tmp_path, expires_at=expires_at)

    dry = post_inbox(_dry_run_body())
    assert dry.status_code == 200, dry.text
    receipt = dry.json()["receipt"]

    real = post_inbox(_real_submit_body(receipt=receipt))
    assert real.status_code == 201, real.text


def test_t0019_actually_expired_receipt_is_still_rejected(env):
    """The fix normalizes tz/format only -- an actually expired receipt must still be
    rejected. Exercises classify() directly (0545 T0019 §4): a token that is itself
    already expired is turned away by token_service.verify() (401) before classify()
    ever runs, so the "issued while valid, receipt now expired" path is fixed here at
    the receipt row instead."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    receipt_id = "receipt-t0019-expired"
    # token_rec here (like every other token_rec in this file) carries no group_id, so
    # the receipt's own group_id is NULL -- matching review_receipt_service.issue(),
    # which reads it from the token, not from the mocked document's group_id.
    row = db_receipts.create(
        receipt_id=receipt_id, token_id=TOKEN_ID, project_id=PROJECT, group_id=None,
        doc_id=DOC_ID, revision_no=0, payload_identity="irrelevant-identity",
        issued_at=to_now_iso_tz(past), expires_at=to_now_iso_tz(past),
        encoding_provenance=None,
    )
    assert row["expires_at"] is not None

    reason = review_receipt_service.classify(
        receipt_id, token_rec=env["token_rec"], project_id=PROJECT, group_id=None,
        doc_id=DOC_ID, revision_no=0, identity="irrelevant-identity",
    )
    assert reason == "receipt_expired"


# ── T0019: to_now_iso_tz unit tests ──────────────────────────────────────────────────

def test_to_now_iso_tz_treats_tz_naive_input_as_utc():
    naive = "2026-09-12T00:00:00"
    utc_form = "2026-09-12T00:00:00+00:00"
    assert to_now_iso_tz(naive) == to_now_iso_tz(utc_form)


def test_to_now_iso_tz_is_identity_on_already_normalized_jst_input():
    already_jst = "2026-09-12T09:00:00+09:00"
    assert to_now_iso_tz(already_jst) == already_jst
