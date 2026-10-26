"""flowgate.v02.0076 T#2 — the server side of 내 설정 (D §2.1/§2.2, §5.2 V08/V09).

The 0076 settings screens reuse existing contracts only, so what is pinned here is that those
contracts answer the way the screens rely on: the real ``/auth`` router and the real
``/me/ui-settings`` router, mounted under the production prefixes, on a sqlite file built from
every migration. Nothing in the request path is mocked — no ``get_current_user`` override and
no permission seed.

V09 (현재 sid=s1, 다른 sid=s2): s1 cannot be DELETEd (409), s2 is DELETEd and its token stops
working; a wrong current password changes nothing; the right one ends the other sessions, the
old password stops signing in and the new one signs in; logout ends s1.
V08: 30→60 is stored and read back, 5 minutes is refused and changes nothing.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

import jwt
import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

OLD_PASSWORD = "OldP@ssw0rd123"
NEW_PASSWORD = "NewSecureP@ss123"
FIELD = "ai_finished_card_retention_minutes"


class _SqliteStore:
    """FlowGateStore's surface on one sqlite file, with a real transaction (as in test_auth)."""

    def __init__(self, db_path):
        self._db_path = db_path
        self._txn = None

    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self):
        if self._txn is not None:
            yield self
            return
        conn = self._conn()
        self._txn = conn
        try:
            yield self
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._txn = None
            conn.close()

    def with_transaction(self):
        return self.transaction()

    def _execute(self, sql, params=None):
        if self._txn is not None:
            self._txn.execute(sql, params or [])
            return
        with self._conn() as conn:
            conn.execute(sql, params or [])
            conn.commit()

    def _fetch_one(self, sql, params=None):
        if self._txn is not None:
            row = self._txn.execute(sql, params or []).fetchone()
        else:
            with self._conn() as conn:
                row = conn.execute(sql, params or []).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, sql, params=None):
        if self._txn is not None:
            rows = self._txn.execute(sql, params or []).fetchall()
        else:
            with self._conn() as conn:
                rows = conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]


@pytest.fixture
def client(migrated_sqlite_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import modules.flow_gate.db.connection as conn_mod
    from modules.flow_gate.api.v1.ui_settings_routes import router as ui_settings_router
    from modules.flow_gate.auth.auth_api import router as auth_router
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db.connection import now_iso

    store = _SqliteStore(migrated_sqlite_db("test_me_settings_0076.db"))
    monkeypatch.setattr(conn_mod, "STORE", store)
    store._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
        "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["usr_me0076", "me0076", "me0076@example.com", hash_password(OLD_PASSWORD), 1, 0, 0, now_iso(), now_iso()],
    )
    # The in-process auth caches belong to the process, not to this file's store.
    from modules.flow_gate.auth import auth_cache

    auth_cache.invalidate_everything()

    app = FastAPI()
    app.include_router(auth_router, prefix="/flowgate/auth")
    app.include_router(ui_settings_router, prefix="/flowgate/api/v1")
    return TestClient(app)


def _login(client, password=OLD_PASSWORD, agent="Chrome"):
    return client.post(
        "/flowgate/auth/login",
        json={"username": "me0076", "password": password},
        headers={"User-Agent": agent},
    )


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _sid(token):
    return jwt.decode(token, options={"verify_signature": False})["sid"]


def test_v09_sessions_password_and_logout(client):
    first = _login(client, agent="Chrome")
    second = _login(client, agent="Firefox")
    assert first.status_code == 200 and second.status_code == 200
    a1, r1, a2 = first.json()["access_token"], first.json()["refresh_token"], second.json()["access_token"]
    s1, s2 = _sid(a1), _sid(a2)

    listed = client.get("/flowgate/auth/sessions", headers=_bearer(a1))
    assert listed.status_code == 200
    rows = {row["session_id"]: row["is_current"] for row in listed.json()["sessions"]}
    assert rows == {s1: True, s2: False}

    # The current session is never ended by DELETE.
    own = client.delete(f"/flowgate/auth/sessions/{s1}", headers=_bearer(a1))
    assert own.status_code == 409
    assert own.json()["detail"] == "current_session_use_logout"

    ended = client.delete(f"/flowgate/auth/sessions/{s2}", headers=_bearer(a1))
    assert ended.status_code == 200
    assert [row["session_id"] for row in client.get("/flowgate/auth/sessions", headers=_bearer(a1)).json()["sessions"]] == [s1]
    assert client.get("/flowgate/auth/me", headers=_bearer(a2)).status_code == 401
    again = client.delete(f"/flowgate/auth/sessions/{s2}", headers=_bearer(a1))
    assert again.status_code == 404

    third = _login(client, agent="Safari")
    a3 = third.json()["access_token"]
    s3 = _sid(a3)

    wrong = client.post(
        "/flowgate/auth/password/change",
        json={"current_password": "WrongP@ssw0rd1", "new_password": NEW_PASSWORD},
        headers=_bearer(a1),
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "current_password_incorrect"
    # Nothing changed: the old password still signs in, the other session still works.
    probe = _login(client, agent="Probe")
    assert probe.status_code == 200
    client.post("/flowgate/auth/logout", json={"refresh_token": probe.json()["refresh_token"]}, headers=_bearer(probe.json()["access_token"]))
    assert client.get("/flowgate/auth/me", headers=_bearer(a3)).status_code == 200

    weak = client.post(
        "/flowgate/auth/password/change",
        json={"current_password": OLD_PASSWORD, "new_password": "short"},
        headers=_bearer(a1),
    )
    assert weak.status_code == 400
    assert weak.json()["detail"]["code"] == "invalid_password_policy"
    assert weak.json()["detail"]["violations"]

    changed = client.post(
        "/flowgate/auth/password/change",
        json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        headers=_bearer(a1),
    )
    assert changed.status_code == 200
    assert changed.json()["first_login_required"] is False
    # Every other session is gone; this one stays.
    assert client.get("/flowgate/auth/me", headers=_bearer(a3)).status_code == 401
    remaining = [row["session_id"] for row in client.get("/flowgate/auth/sessions", headers=_bearer(a1)).json()["sessions"]]
    assert remaining == [s1]
    assert s3 not in remaining
    assert _login(client, password=OLD_PASSWORD).status_code == 400
    new_login = _login(client, password=NEW_PASSWORD)
    assert new_login.status_code == 200

    out = client.post("/flowgate/auth/logout", json={"refresh_token": r1}, headers=_bearer(a1))
    assert out.status_code == 200
    assert client.get("/flowgate/auth/me", headers=_bearer(a1)).status_code == 401
    assert client.post("/flowgate/auth/refresh", json={"refresh_token": r1}).status_code == 401


def test_v08_retention_saved_read_back_and_five_minutes_refused(client):
    token = _login(client).json()["access_token"]
    path = "/flowgate/api/v1/me/ui-settings"

    first = client.get(path, headers=_bearer(token))
    assert first.status_code == 200
    assert first.json()["domain"][FIELD] == [-1, 0, 30, 60, 120, 180, 360, 720, 1440]

    assert client.patch(path, json={FIELD: 30}, headers=_bearer(token)).status_code == 200
    saved = client.patch(path, json={FIELD: 60}, headers=_bearer(token))
    assert saved.status_code == 200
    assert saved.json()["settings"][FIELD] == 60
    assert client.get(path, headers=_bearer(token)).json()["settings"][FIELD] == 60

    refused = client.patch(path, json={FIELD: 5}, headers=_bearer(token))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_request"
    assert client.get(path, headers=_bearer(token)).json()["settings"][FIELD] == 60

    assert client.get(path).status_code == 401
    assert client.patch(path, json={FIELD: 120}).status_code == 401
    assert client.get(path, headers=_bearer(token)).json()["settings"][FIELD] == 60
