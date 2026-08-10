"""T057: pytest tests for the auth module.

Environment: TESTING=1 (unit tests without DB + TestClient integration tests)
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Set TESTING=1 (prevent DB initialization)
os.environ["TESTING"] = "1"

# ── Test SECRET_KEY ─────────────────────────────────────────────────────────
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")


# ─────────────────────────────────────────────────────────────────────────────
# Schema paths
# ─────────────────────────────────────────────────────────────────────────────
_SERVER_DIR = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(_SERVER_DIR))


# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_path(migrated_sqlite_db):
    """The auth schema, built from every migration (0394 T0004, NR0003 §13-6).

    This used to run migrations 001, 002 and 005 and stop — a hand-picked subset from
    when those were the only ones that touched auth. It went stale silently: 067a added
    the `auth_sessions` table on 2026-07-17, the login path started writing to it, and
    seven cases here began failing with `no such table` against a schema no product
    install has ever had. Every new auth table would have cost the same day again.
    """
    return migrated_sqlite_db("test_auth.db")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Password tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPassword:
    def test_hash_and_verify(self):
        from modules.flow_gate.auth.password import hash_password, verify_password
        plain = "MyP@ssw0rd123!"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed)

    def test_wrong_password_fails(self):
        from modules.flow_gate.auth.password import hash_password, verify_password
        hashed = hash_password("CorrectP@ss123")
        assert not verify_password("WrongPass", hashed)

    def test_pbkdf2_compat(self):
        """Existing pbkdf2_sha256 hashes should also verify."""
        from passlib.context import CryptContext
        from modules.flow_gate.auth.password import verify_password

        old_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
        old_hash = old_ctx.hash("OldP@ssw0rd123")
        assert verify_password("OldP@ssw0rd123", old_hash)

    def test_policy_valid(self):
        from modules.flow_gate.auth.password import validate_password
        assert validate_password("MyP@ssw0rd123!") == []

    def test_policy_too_short(self):
        from modules.flow_gate.auth.password import validate_password
        errors = validate_password("Short1!")
        assert any("12 characters" in e for e in errors)

    def test_policy_only_letters(self):
        from modules.flow_gate.auth.password import validate_password
        errors = validate_password("onlylowercaseletters")
        assert any("3 categories" in e for e in errors)

    def test_policy_three_types_enough(self):
        from modules.flow_gate.auth.password import validate_password
        # upper/lower/digits -> 3 categories (passes without special chars)
        assert validate_password("LowerUPPER1234567") == []

    def test_policy_violations_list(self):
        from modules.flow_gate.auth.password import validate_password
        errors = validate_password("abc")
        assert len(errors) == 2  # too short + fewer than 3 categories


# ─────────────────────────────────────────────────────────────────────────────
# 2. JWT tests
# ─────────────────────────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode_access_token(self):
        from modules.flow_gate.auth.jwt_service import create_access_token, decode_token
        token, jti = create_access_token("usr_001", "alice", ["role_admin"])
        payload = decode_token(token)
        assert payload["sub"] == "usr_001"
        assert payload["username"] == "alice"
        assert payload["roles"] == ["role_admin"]
        assert payload["type"] == "access"
        assert payload["jti"] == jti

    def test_create_and_decode_refresh_token(self):
        from modules.flow_gate.auth.jwt_service import create_refresh_token, decode_token
        token, jti, exp = create_refresh_token("usr_001")
        payload = decode_token(token)
        assert payload["sub"] == "usr_001"
        assert payload["type"] == "refresh"
        assert payload["jti"] == jti

    def test_create_and_decode_temp_token(self):
        from modules.flow_gate.auth.jwt_service import create_temp_token, decode_token
        token, jti = create_temp_token("usr_002")
        payload = decode_token(token)
        assert payload["sub"] == "usr_002"
        assert payload["type"] == "temp"
        assert payload["totp_pending"] is True

    def test_expired_token_raises(self):
        import jwt as pyjwt
        from modules.flow_gate.auth.jwt_service import create_access_token, decode_token
        token, _ = create_access_token(
            "usr_001", "alice", [],
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_token(token)

    def test_invalid_token_raises(self):
        import jwt as pyjwt
        from modules.flow_gate.auth.jwt_service import decode_token
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token("not.a.valid.token")

    def test_decode_no_verify_exp(self):
        from modules.flow_gate.auth.jwt_service import (
            create_access_token,
            decode_token_no_verify_exp,
        )
        token, jti = create_access_token(
            "usr_001", "alice", [],
            expires_delta=timedelta(seconds=-10),
        )
        payload = decode_token_no_verify_exp(token)
        assert payload["sub"] == "usr_001"

    def test_access_token_lifetime_follows_config_settings(self, monkeypatch):
        """group 0021 / NR0003 item 3: the access-token lifetime must come from
        config.settings.ACCESS_TOKEN_EXPIRE_MINUTES, not a hardcoded constant — the
        regression was that the operator's .env override was silently ignored."""
        import types
        from modules.flow_gate.auth import jwt_service

        fake_config = types.ModuleType("config")
        fake_config.settings = types.SimpleNamespace(ACCESS_TOKEN_EXPIRE_MINUTES=99)
        monkeypatch.setitem(sys.modules, "config", fake_config)

        assert jwt_service.get_access_token_expire_minutes() == 99

        token, _ = jwt_service.create_access_token("usr_x", "x", [])
        payload = jwt_service.decode_token(token)
        span_min = (payload["exp"] - payload["iat"]) / 60
        assert 98 <= span_min <= 100

    def test_access_token_lifetime_env_fallback(self, monkeypatch):
        """When config.settings is unavailable, fall back to the
        ACCESS_TOKEN_EXPIRE_MINUTES environment variable."""
        import types
        from modules.flow_gate.auth import jwt_service

        # config module present but without `settings` → import raises → env fallback.
        broken = types.ModuleType("config")
        monkeypatch.setitem(sys.modules, "config", broken)
        monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "7")

        assert jwt_service.get_access_token_expire_minutes() == 7


# ─────────────────────────────────────────────────────────────────────────────
# 3. TOTP service tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def totp_key_env(monkeypatch):
    """Set a 32-byte AES key for tests."""
    key = base64.b64encode(b"A" * 32).decode()
    monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", key)


class TestTOTP:
    def test_encrypt_decrypt(self, totp_key_env):
        from modules.flow_gate.auth.totp_service import encrypt_totp_secret, decrypt_totp_secret
        secret = "JBSWY3DPEHPK3PXP"
        encrypted = encrypt_totp_secret(secret)
        assert encrypted != secret
        decrypted = decrypt_totp_secret(encrypted)
        assert decrypted == secret

    def test_encrypt_different_each_time(self, totp_key_env):
        """A different nonce each time should yield different ciphertext for the same plaintext."""
        from modules.flow_gate.auth.totp_service import encrypt_totp_secret
        s = "JBSWY3DPEHPK3PXP"
        assert encrypt_totp_secret(s) != encrypt_totp_secret(s)

    def test_verify_valid_code(self, totp_key_env):
        import pyotp
        from modules.flow_gate.auth.totp_service import (
            encrypt_totp_secret,
            verify_totp_code,
        )
        secret = pyotp.random_base32()
        encrypted = encrypt_totp_secret(secret)
        code = pyotp.TOTP(secret).now()
        assert verify_totp_code(encrypted, code)

    def test_verify_invalid_code(self, totp_key_env):
        import pyotp
        from modules.flow_gate.auth.totp_service import (
            encrypt_totp_secret,
            verify_totp_code,
        )
        secret = pyotp.random_base32()
        encrypted = encrypt_totp_secret(secret)
        assert not verify_totp_code(encrypted, "000000")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLOWGATE_TOTP_ENCRYPT_KEY", raising=False)
        from modules.flow_gate.auth.totp_service import encrypt_totp_secret
        with pytest.raises(RuntimeError, match="FLOWGATE_TOTP_ENCRYPT_KEY"):
            encrypt_totp_secret("TESTSECRET")

    def test_key_rotation_fallback(self, monkeypatch):
        """Decrypt a value encrypted with the previous key using the PREV key."""
        key_a = base64.b64encode(b"A" * 32).decode()
        key_b = base64.b64encode(b"B" * 32).decode()

        # encrypt with key_a
        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", key_a)
        from modules.flow_gate.auth.totp_service import encrypt_totp_secret, decrypt_totp_secret
        encrypted = encrypt_totp_secret("MYSECRET")

        # switch to key_b and set key_a as PREV
        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", key_b)
        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY_PREV", key_a)
        assert decrypt_totp_secret(encrypted) == "MYSECRET"

    def test_provisioning_uri(self, totp_key_env):
        from modules.flow_gate.auth.totp_service import (
            generate_totp_secret,
            get_totp_provisioning_uri,
        )
        secret = generate_totp_secret()
        uri = get_totp_provisioning_uri(secret, "user@example.com")
        assert "otpauth://totp/" in uri
        assert "FlowGate" in uri


# ─────────────────────────────────────────────────────────────────────────────
# 4. Backup code tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBackupCodes:
    def test_generate_format(self):
        from modules.flow_gate.auth.backup_codes import generate_codes
        codes = generate_codes()
        assert len(codes) == 10
        for code in codes:
            parts = code.split("-")
            assert len(parts) == 3
            assert all(len(p) == 4 for p in parts)

    def test_generate_unique(self):
        from modules.flow_gate.auth.backup_codes import generate_codes
        codes = generate_codes()
        assert len(set(codes)) == 10

    def test_store_and_verify(self):
        from modules.flow_gate.auth.backup_codes import (
            generate_codes,
            store_codes,
            verify_backup_code,
        )
        codes = generate_codes()
        uid = "test_user_bc"

        # mock DB module
        stored: list[dict] = []

        def mock_delete_all(u):
            stored.clear()

        def mock_create(data):
            stored.append({"user_id": data["user_id"], "code": data["code"], "used_at": None})

        def mock_list(u):
            return stored

        def mock_mark_used(u, code_hash):
            for row in stored:
                if row["code"] == code_hash and not row["used_at"]:
                    row["used_at"] = "2026-01-01T00:00:00"
                    break

        with patch("modules.flow_gate.auth.backup_codes._db") as mock_db:
            mock_db.delete_all = mock_delete_all
            mock_db.create = mock_create
            mock_db.list_by_user = mock_list
            mock_db.mark_used = mock_mark_used

            store_codes(uid, codes)
            assert len(stored) == 10

            # verify the first code
            assert verify_backup_code(uid, codes[0])
            # a used code cannot be verified again
            assert not verify_backup_code(uid, codes[0])

    def test_invalid_code_rejected(self):
        from modules.flow_gate.auth.backup_codes import (
            generate_codes,
            store_codes,
            verify_backup_code,
        )
        codes = generate_codes()
        uid = "test_user_bc2"
        stored: list[dict] = []

        def mock_delete_all(u): stored.clear()
        def mock_create(data): stored.append({"user_id": data["user_id"], "code": data["code"], "used_at": None})
        def mock_list(u): return stored
        def mock_mark_used(u, c): pass

        with patch("modules.flow_gate.auth.backup_codes._db") as mock_db:
            mock_db.delete_all = mock_delete_all
            mock_db.create = mock_create
            mock_db.list_by_user = mock_list
            mock_db.mark_used = mock_mark_used

            store_codes(uid, codes)
            assert not verify_backup_code(uid, "XXXX-XXXX-XXXX")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Lock counter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLockCounter:
    """Unit tests for _increment_totp_fail and _check_totp_lock."""

    def _make_user(self, failed=0, locked_until=None) -> dict:
        return {
            "user_id": "usr_lock",
            "username": "lock_user",
            "totp_failed_count": failed,
            "totp_locked_until": locked_until,
        }

    def test_not_locked_when_count_below_max(self):
        from modules.flow_gate.auth.auth_api import _check_totp_lock
        user = self._make_user(failed=4)  # 4 attempts, not locked yet
        _check_totp_lock(user)  # should not raise

    def test_locked_raises_423(self):
        from modules.flow_gate.auth.auth_api import _check_totp_lock
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        user = self._make_user(locked_until=future)
        with pytest.raises(Exception) as exc_info:
            _check_totp_lock(user)
        assert exc_info.value.status_code == 423

    def test_expired_lock_passes(self):
        from modules.flow_gate.auth.auth_api import _check_totp_lock
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        user = self._make_user(locked_until=past)
        _check_totp_lock(user)  # lock expired -> no exception

    def test_increment_sets_lock_on_max(self):
        from modules.flow_gate.auth.auth_api import _increment_totp_fail

        updates_received: list[dict] = []

        with patch("modules.flow_gate.auth.auth_api.db_users") as mock_users:
            mock_users.update = lambda uid, upd: updates_received.append(upd)
            user = self._make_user(failed=4)  # 4 -> 5 -> locked
            _increment_totp_fail(user)

        assert updates_received[0]["totp_failed_count"] == 5
        assert "totp_locked_until" in updates_received[0]

    def test_increment_no_lock_below_max(self):
        from modules.flow_gate.auth.auth_api import _increment_totp_fail

        updates_received: list[dict] = []

        with patch("modules.flow_gate.auth.auth_api.db_users") as mock_users:
            mock_users.update = lambda uid, upd: updates_received.append(upd)
            user = self._make_user(failed=2)
            _increment_totp_fail(user)

        assert updates_received[0]["totp_failed_count"] == 3
        assert "totp_locked_until" not in updates_received[0]


# ─────────────────────────────────────────────────────────────────────────────
# 6. API integration tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def api_client(test_db_path):
    """TestClient with in-memory SQLite FlowGateStore override."""
    import sqlite3 as _sqlite3
    from contextlib import contextmanager

    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.auth_api import router as auth_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")

    # Replace FlowGateStore with a SQLite-file-backed store.
    #
    # 0394 T0004 (NR0003 §13-6): this double reproduces FlowGateStore's interface, and
    # like the frozen migration list above it fell behind the original. Session handling
    # (067a) writes through `store.transaction()`, which was never here, so logout /
    # refresh-rotation / reuse-detection / password-change all died on AttributeError as
    # soon as the schema was current enough to reach that code. `transaction()` is
    # implemented properly rather than stubbed out: one connection for the whole block,
    # committed at the end and rolled back on error, so a test can still tell an
    # all-or-nothing revoke from a half-finished one.
    class _SqliteStore:
        def __init__(self, db_path):
            self._db_path = db_path
            self._txn = None

        def _conn(self):
            conn = _sqlite3.connect(self._db_path)
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        @contextmanager
        def transaction(self):
            if self._txn is not None:   # already inside one: join it, like the real store
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
            if row is None:
                return None
            return dict(row)

        def _fetch_all(self, sql, params=None):
            if self._txn is not None:
                rows = self._txn.execute(sql, params or []).fetchall()
            else:
                with self._conn() as conn:
                    rows = conn.execute(sql, params or []).fetchall()
            return [dict(r) for r in rows]

    store = _SqliteStore(test_db_path)

    import modules.flow_gate.db.connection as conn_mod
    conn_mod.STORE = store  # type: ignore[assignment]

    # create test user
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db.connection import now_iso

    try:
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
            "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "usr_test01", "testuser", "test@example.com",
                hash_password("TestP@ssw0rd123"), 1, 0, 0, now_iso(), now_iso(),
            ],
        )
    except Exception:
        pass  # ignore if it already exists

    return TestClient(app)


class TestAuthAPI:
    def test_login_success_no_totp(self, api_client):
        resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_login_invalid_credentials(self, api_client):
        resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "WrongPassword",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_credentials"

    def test_login_unknown_user(self, api_client):
        resp = api_client.post("/auth/login", json={
            "username": "unknown",
            "password": "SomeP@ssw0rd123",
        })
        assert resp.status_code == 400

    def test_me_requires_auth(self, api_client):
        resp = api_client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_token(self, api_client):
        # call /me with the token after login
        login_resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        token = login_resp.json()["access_token"]
        resp = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"

    def test_logout(self, api_client):
        login_resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        token = login_resp.json()["access_token"]
        refresh = login_resp.json()["refresh_token"]

        logout_resp = api_client.post(
            "/auth/logout",
            json={"refresh_token": refresh},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_resp.status_code == 200

        # /me should be inaccessible after logout
        me_resp = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 401

    def test_refresh_token_rotation(self, api_client):
        login_resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        refresh = login_resp.json()["refresh_token"]

        ref_resp = api_client.post("/auth/refresh", json={"refresh_token": refresh})
        assert ref_resp.status_code == 200
        data = ref_resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # a new refresh_token must be issued
        assert data["refresh_token"] != refresh

    def test_refresh_reuse_detection(self, api_client):
        """Using the same refresh_token twice should return 401."""
        login_resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        refresh = login_resp.json()["refresh_token"]

        # first refresh
        api_client.post("/auth/refresh", json={"refresh_token": refresh})
        # second refresh (token already revoked)
        resp2 = api_client.post("/auth/refresh", json={"refresh_token": refresh})
        assert resp2.status_code == 401

    def test_password_change(self, api_client):
        import modules.flow_gate.db.connection as conn_mod
        from modules.flow_gate.db.connection import now_iso
        from modules.flow_gate.auth.password import hash_password

        # dedicated user for pw_change
        try:
            conn_mod.STORE._execute(
                "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
                "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    "usr_pwtest", "pwuser", "pwtest@example.com",
                    hash_password("OldP@ssw0rd123"), 1, 0, 0, now_iso(), now_iso(),
                ],
            )
        except Exception:
            pass

        login_resp = api_client.post("/auth/login", json={
            "username": "pwuser",
            "password": "OldP@ssw0rd123",
        })
        token = login_resp.json()["access_token"]

        resp = api_client.post(
            "/auth/password/change",
            json={
                "current_password": "OldP@ssw0rd123",
                "new_password": "NewSecureP@ss123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["first_login_required"] is False

    def test_password_change_policy_violation(self, api_client):
        login_resp = api_client.post("/auth/login", json={
            "username": "testuser",
            "password": "TestP@ssw0rd123",
        })
        token = login_resp.json()["access_token"]

        resp = api_client.post(
            "/auth/password/change",
            json={
                "current_password": "TestP@ssw0rd123",
                "new_password": "short",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
