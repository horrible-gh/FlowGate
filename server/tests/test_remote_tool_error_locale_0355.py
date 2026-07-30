"""remote_tool_service — error envelope follows the worker's locale (0355 T0015).

_ERROR_MESSAGES (the 7 generic P0005 §6 status strings — 401/403/404/409/413/
422/503) used to be Korean-fixed regardless of the calling worker's requested
locale. A worker grant is lazily created from a document worker token
(`_worker_grant_from_flowgate_token`), and that token carries
`continuation_locale` (the unmanned chain's chosen locale, group 0099 B0001) —
the same field mention_service already reads. This suite seeds a worker token
with continuation_locale set to en/ja and asserts the generic error message
(status 404 here) is rendered in that locale, while a legacy non-worker grant
(no backing token) keeps the ko fallback unchanged.

Harness mirrors test_remote_tool_0003_T0012.py (TESTING=1, temp SQLite with all
migrations, pepper env, _MockDB injected into connection.STORE).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


PROJECT_ID = "testproj"
PROJECT_NAME = "testproj"


@pytest.fixture
def env(monkeypatch):
    # Avoid pytest's Windows 0700 temp-directory ACL trap. These tests only need
    # a stable readable source root; all writes/execution are mocked or target a miss.
    src = _SERVER_DIR

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for f in _migrations():
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    from modules.flow_gate.db import connection as conn_mod
    store = conn_mod.FlowGateStore()
    store._db = _MockDB(db_path)
    conn_mod.STORE = store

    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db.connection import now_iso
    store._execute(
        "INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
        "VALUES (?,?,1,?,?)",
        [PROJECT_ID, PROJECT_NAME, now_iso(), now_iso()],
    )
    store._execute(
        "INSERT INTO users(user_id,username,email,password,is_active,is_admin,created_at,updated_at) "
        "VALUES (?,?,?,?,1,0,?,?)",
        ["worker-user", "worker-user", "worker@example.test", "x", now_iso(), now_iso()],
    )
    db_projects.upsert_settings(PROJECT_ID, {"branch": "main"})

    class Ctx:
        def __init__(self):
            self.store = store
            self.src = src
            self.db_path = db_path

        def make_grant(self, scopes, *, grant_id="grant_test_1", raw_token="raw-remote-tool-token-abc123"):
            from modules.flow_gate.db import remote_tool_grants as db_grants
            from modules.flow_gate.services import token_service
            _pid, pepper = token_service._active_pepper()
            token_hash = token_service._hash_token(raw_token, pepper)
            db_grants.create(
                {
                    "grant_id": grant_id,
                    "token_hash": token_hash,
                    "project": PROJECT_ID,
                    "module": "default",
                    "status": "active",
                },
                scopes,
            )
            return raw_token

        def make_worker_token(self, token: str, *, token_id="tok_worker_1", locale=None):
            from modules.flow_gate.services import token_service
            _pid, pepper = token_service._active_pepper()
            token_hash = token_service._hash_token(token, pepper)
            now = datetime.now(timezone.utc)
            self.store._execute(
                "INSERT INTO tokens "
                "(token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, "
                "issued_to, created_at, expires_at, scratch_dir, continuation_locale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    token_id, token_hash, _pid, PROJECT_ID, None, None, "edit",
                    "worker-user", now.isoformat(timespec="seconds"),
                    (now + timedelta(hours=1)).isoformat(timespec="seconds"),
                    None, locale,
                ],
            )
            return token_id

    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_resolve_src_root", lambda _grant, _op: src)

    yield Ctx()

    store._db.close()
    conn_mod.STORE = None
    os.unlink(db_path)


def _call(operation, body, token):
    from modules.flow_gate.services import remote_tool_service
    return remote_tool_service.handle(operation, token, body)


@pytest.mark.parametrize(
    "locale, expected_snippet",
    [
        ("en", "could not be found"),
        ("ja", "見つかりません"),
        (None, "찾을 수 없습니다"),  # NULL continuation_locale -> ko fallback, unchanged behavior
        ("zh", "찾을 수 없습니다"),  # unsupported locale folds to ko (template_provision.normalize_locale)
    ],
)
def test_generic_404_message_follows_worker_locale(env, monkeypatch, locale, expected_snippet):
    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type", lambda _rec: "TR")
    worker_token = f"raw-worker-token-locale-{locale}"
    env.make_worker_token(worker_token, token_id=f"tok_{locale}", locale=locale)

    status, payload = _call("read", {"path": "app/missing.py"}, worker_token)

    assert status == 404
    assert payload["error"]["code"] == "not_found"
    assert expected_snippet in payload["error"]["message"]


def test_legacy_grant_without_backing_token_keeps_ko_fallback(env):
    raw_token = env.make_grant(["read"])

    status, payload = _call("read", {"path": "app/missing.py"}, raw_token)

    assert status == 404
    assert payload["error"]["message"] == "대상 경로를 찾을 수 없습니다."


def test_401_auth_failure_keeps_ko_default(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py"}, token=None)
    assert status == 401
    assert payload["error"]["message"] == "토큰이 없거나 유효하지 않습니다."


def _assert_no_korean_syllable(payload):
    if isinstance(payload, str):
        assert not any("가" <= char <= "힣" for char in payload), payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            _assert_no_korean_syllable(key)
            _assert_no_korean_syllable(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _assert_no_korean_syllable(value)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_all_seven_generic_statuses_have_zero_korean_via_worker_token(
    env, monkeypatch, locale
):
    """Exercise the real handle pipeline and locale lookup for every P0005 status."""
    from modules.flow_gate.services import remote_tool_service

    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type", lambda _rec: "TR")
    monkeypatch.setattr(remote_tool_service, "_resolve_src_root", lambda _grant, _op: env.src)
    # 401 is normally emitted before a locale-bearing grant exists. Injecting it at
    # the execution boundary verifies the same localized renderer through handle.
    monkeypatch.setattr(remote_tool_service, "_log", lambda *_args, **_kwargs: None)

    worker_token = f"raw-worker-all-statuses-{locale}"
    env.make_worker_token(worker_token, token_id=f"tok_all_statuses_{locale}", locale=locale)

    for expected_status in (401, 403, 404, 409, 413, 422, 503):
        def fail_execute(_op, _body, _root, status=expected_status):
            raise remote_tool_service._OpError(status)

        monkeypatch.setattr(remote_tool_service, "_execute", fail_execute)
        status, payload = _call("read", {"path": "app/main.py"}, worker_token)
        assert status == expected_status
        assert payload["error"]["code"] == remote_tool_service.ERROR_CODE_BY_STATUS[status]
        _assert_no_korean_syllable(payload)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_custom_errors_and_success_continuation_have_zero_korean(locale):
    from modules.flow_gate.services import remote_tool_service

    cases = [
        remote_tool_service._OpError(422, details={"reason": "no_op_edit"}),
        remote_tool_service._OpError(
            404, details={"reason": "no_match", "match_count": 0, "path": "a.py"}
        ),
        remote_tool_service._OpError(
            409,
            details={"reason": "multiple_matches", "match_count": 2, "path": "a.py"},
        ),
    ]
    for exc in cases:
        _assert_no_korean_syllable(remote_tool_service._op_error_message(exc, locale))
    _assert_no_korean_syllable(
        remote_tool_service._continuation({"report_doc_id": "TR0001"}, locale)
    )
    _assert_no_korean_syllable(remote_tool_service._continuation({}, locale))