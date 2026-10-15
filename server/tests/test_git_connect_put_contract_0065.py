"""flowgate.v02.0065 TR0011 — the `Git 연결` save against the real settings service.

The client_gui connect window (DesignerGitPanel) saves with exactly the six fields T0010 §1
allows: repo_url, provider, username, secret, base_branch and enabled=true. This runs that
body through the route model (`GitConfigPut.model_dump(exclude_unset=True)`, as
`put_git_config` does) into `save_config`, over settings stored earlier with Git off, and
records which stored values survive.

translate_url, author_* , tr_scope_stage and the secret (sent as null) follow the service's
"omitted = keep" protocol. `default_finalize_action` does not: the service stores
`body.get("default_finalize_action") or "wait"`, so a connect over settings whose default
finalize action is `merge` / `push` turns it into `wait`. That is the server contract as it
stands — this task changes no server code — and the test pins it so the effect is visible;
whether the action must be kept is registered as a Q on TR0011.
"""
from __future__ import annotations

import base64
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.flow_gate.api.v1.git_routes import GitConfigPut

PROJECT = "connect0065"
REPO = "https://example.com/team/repo.git"
# A made-up value: only checks that the stored token is kept, never shown.
STORED_TOKEN = "tok-0065-stored"


class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    @property
    def cursor(self):
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _DB:
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
        txn = _Txn(self._conn)
        try:
            yield txn
        except BaseException:
            self._conn.rollback()
            raise
        self._conn.commit()

    def close(self):
        self._conn.close()


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import projects

    # The same schema build as test_git_integration_0115 (a migration that does not replay
    # cleanly on sqlite is skipped there too).
    db = _DB(str(tmp_path / "connect0065.db"))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except sqlite3.Error:
            pass
    db._conn.commit()

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = db
            self._sq = None

    monkeypatch.setattr(conn_mod, "STORE", _Store())
    projects.create({"project_id": PROJECT, "project_name": "Connect0065"})
    yield db
    db.close()


def _stored_with_git_off(action: str) -> None:
    from modules.flow_gate.services import git_service as svc

    svc.save_config(PROJECT, {
        "repo_url": REPO,
        "provider": "github",
        "username": "bot",
        "secret": STORED_TOKEN,
        "base_branch": "main",
        "default_finalize_action": action,
        "enabled": False,
        "translate_url": "http://translate.example.com",
        "author_name": "Shin",
        "author_email": "shin@example.com",
        "tr_scope_stage": "enforce",
    })


def _connect_body() -> dict:
    # What client_gui/src/features/git/api.ts saveGitConfig sends (no token typed → null).
    return GitConfigPut(**{
        "repo_url": REPO,
        "provider": "github",
        "username": "bot",
        "secret": None,
        "base_branch": "main",
        "enabled": True,
    }).model_dump(exclude_unset=True)


def test_the_route_passes_only_the_six_fields_on():
    assert set(_connect_body()) == {"repo_url", "provider", "username", "secret", "base_branch", "enabled"}


@pytest.mark.parametrize("action", ["merge", "push", "wait"])
def test_connect_keeps_the_omitted_settings_but_stores_wait_as_finalize_action(store, action):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    _stored_with_git_off(action)
    before = db_git.get_config(PROJECT)
    assert before["default_finalize_action"] == action
    assert not before["enabled"]

    config = svc.save_config(PROJECT, _connect_body())["config"]
    after = db_git.get_config(PROJECT)

    assert config["enabled"] is True
    # Kept by the "omitted = keep" protocol.
    assert after["secret_enc"] == before["secret_enc"]
    assert config["has_secret"] is True
    assert config["translate_url"] == "http://translate.example.com"
    assert (config["author_name"], config["author_email"]) == ("Shin", "shin@example.com")
    assert config["tr_scope_stage"] == "enforce"
    # Not kept: the service's own default replaces an omitted finalize action.
    assert after["default_finalize_action"] == "wait"
    assert config["default_finalize_action"] == "wait"
    assert STORED_TOKEN not in str(config)
