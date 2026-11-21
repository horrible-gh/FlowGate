"""Independently selectable scenarios; shared fixtures remain in test_workspaces_settings_0020.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_workspaces_settings_0020 import (
    ADMIN,
    Path,
    _MIGRATIONS,
    _SEED,
    _SERVER_DIR,
    _SqliteStore,
    _build_db,
    _client,
    _rbac_client,
    _role,
    _user,
    contextmanager,
    datetime,
    os,
    pytest,
    sqlite3,
    store,
    sys,
    timedelta,
    timezone,
)

class TestRoutes:
    def test_workspace_routes(self, store):
        client = _client({**ADMIN, "username": "admin"})
        assert client.get("/api/v1/workspaces").json()["workspaces"][0]["workspace_id"] == "ws_default"
        created = client.post("/api/v1/workspaces", json={"name": "Data Platform"})
        assert created.status_code == 201
        wid = created.json()["workspace_id"]
        assert client.post("/api/v1/workspaces", json={"name": "data platform"}).json()["detail"]["code"] == "duplicate_name"
        cands = client.get(f"/api/v1/workspaces/{wid}/member-candidates", params={"q": "dev"}).json()["users"]
        assert [c["user_id"] for c in cands] == ["u_dev"]
        assert client.post(f"/api/v1/workspaces/{wid}/members", json={"user_id": "u_dev", "role": "admin"}).status_code == 201
        assert client.patch(f"/api/v1/workspaces/{wid}/members/u_dev", json={"role": "member"}).status_code == 200
        assert client.put(f"/api/v1/workspaces/{wid}/access/defaults", json={"member": "review"}).json()["access"]["defaults"]["member"] == "review"
        assert client.put(f"/api/v1/workspaces/{wid}/policy", json={"ai_mode": "allow", "project_create": "all"}).json()["policy"] == {
            "ai_mode": "allow", "allowlist": [], "project_create": "all"}
        assert client.get(f"/api/v1/workspaces/{wid}/policy/resources").status_code == 200
        assert client.patch(f"/api/v1/workspaces/{wid}", json={"name": "Data"}).json()["name"] == "Data"
        assert client.delete(f"/api/v1/workspaces/{wid}").status_code == 409
        assert client.delete(f"/api/v1/workspaces/{wid}/members/u_dev").status_code == 200
        assert client.delete(f"/api/v1/workspaces/{wid}").status_code == 200
        assert client.get("/api/v1/workspaces/ws_nope").status_code == 404

    def test_member_cannot_reach_system_routes(self, store):
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})
        assert client.get("/api/v1/system/users").status_code == 403
        assert client.get("/api/v1/system/security").status_code == 403
        assert client.post("/api/v1/workspaces", json={"name": "X"}).status_code == 403

    def test_system_user_and_security_routes(self, store):
        client = _client({**ADMIN, "username": "admin"})
        listed = client.get("/api/v1/system/users").json()
        assert listed["summary"]["total"] == 5 and listed["workspaces"][0]["workspace_id"] == "ws_default"
        created = client.post("/api/v1/system/users", json={"name": "Lee Hana", "email": "hana@x.io", "start": "invite"})
        assert created.status_code == 201
        uid = created.json()["user"]["user_id"]
        token = created.json()["invite_path"].split("=", 1)[1]
        assert client.get("/auth/invite", params={"token": token}).json()["username"] == "Lee Hana"
        assert client.post("/auth/invite/accept", json={"token": token, "password": "x"}).status_code == 400
        assert client.post("/auth/invite/accept", json={"token": token, "password": "Str0ng-Passw0rd!"}).status_code == 200
        assert client.get("/auth/invite", params={"token": token}).status_code == 404
        assert client.put(f"/api/v1/system/users/{uid}/admin", json={"is_admin": True}).json()["is_admin"] is True
        assert client.post(f"/api/v1/system/users/{uid}/disable").json()["state"] == "disabled"
        assert client.post(f"/api/v1/system/users/{uid}/enable").json()["state"] != "disabled"
        assert client.put("/api/v1/system/users/u_admin/admin", json={"is_admin": False}).status_code == 200  # u_admin2 + new admin remain
        sec = client.get("/api/v1/system/security").json()
        assert sec["policy"]["tfa_scope"] == "off" and len(sec["people"]) == 6
        bad = client.put("/api/v1/system/security", json={"lock_attempts": 4})
        assert bad.status_code == 422
        saved = client.put("/api/v1/system/security", json={"tfa_scope": "all", "tfa_grace_days": 0}).json()
        assert saved["policy"]["tfa_scope"] == "all" and saved["signed_out"] == 0
class TestLoginEnrolment:
    def test_due_user_must_enrol_then_verifies_with_the_enrolment_token(self, store, monkeypatch):
        import base64
        import pyotp
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec

        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", base64.b64encode(b"k" * 32).decode())
        store._execute("UPDATE users SET password=? WHERE user_id='u_dev'", [hash_password("TestP@ssw0rd123")])
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})

        ok = client.post("/auth/login", json={"username": "dev", "password": "TestP@ssw0rd123"}).json()
        assert "access_token" in ok  # policy off: no enrolment
        last = store._fetch_one("SELECT last_login_at FROM users WHERE user_id='u_dev'")["last_login_at"]
        assert last

        sec.update_policy({"tfa_scope": "all", "tfa_grace_days": 0, "recovery_codes": False})
        step = client.post("/auth/login", json={"username": "dev", "password": "TestP@ssw0rd123"}).json()
        assert step == {"totp_setup_required": True, "temp_token": step["temp_token"]}
        enrol = client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).json()
        assert enrol["backup_codes"] == [] and enrol["qr_uri"].startswith("otpauth://")
        code = pyotp.TOTP(enrol["secret"]).now()
        tokens = client.post("/auth/totp", json={"temp_token": enrol["temp_token"], "code": code}).json()
        assert "access_token" in tokens
        assert client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": "X"}).status_code == 403

    def test_interrupted_enrolment_saves_nothing_and_restarts_on_next_sign_in(self, store, monkeypatch):
        import base64
        import pyotp
        from modules.flow_gate.auth.backup_codes import count_unused
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec
        from modules.flow_gate.settings.system_user_service import get_user_view

        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", base64.b64encode(b"k" * 32).decode())
        store._execute("UPDATE users SET password=? WHERE user_id='u_dev'", [hash_password("TestP@ssw0rd123")])
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})
        sec.update_policy({"tfa_scope": "all", "tfa_grace_days": 0})
        login = {"username": "dev", "password": "TestP@ssw0rd123"}

        def saved_secret():
            return store._fetch_one("SELECT totp_secret FROM users WHERE user_id='u_dev'")["totp_secret"]

        def clear_failures():
            store._execute("UPDATE users SET totp_failed_count=0 WHERE user_id='u_dev'")

        # First attempt: the key and backup codes are shown, then the user leaves (closes the page,
        # presses Back or never receives the response) without confirming a code.
        step = client.post("/auth/login", json=login).json()
        first = client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).json()
        assert len(first["backup_codes"]) == 10 and first["temp_token"] != step["temp_token"]
        assert saved_secret() is None and count_unused("u_dev") == 0
        assert get_user_view("u_dev")["tfa_enabled"] is False
        assert next(p for p in sec.coverage() if p["user_id"] == "u_dev")["tfa_enabled"] is False
        # A backup code cannot stand in for the unconfirmed enrolment, and a wrong code saves nothing.
        backup = client.post("/auth/totp/backup", json={"temp_token": first["temp_token"], "backup_code": first["backup_codes"][0]})
        assert backup.status_code == 409 and backup.json()["detail"] == "totp_not_enrolled"
        assert client.post("/auth/totp", json={"temp_token": first["temp_token"], "code": "000000"}).status_code == 401
        assert saved_secret() is None
        clear_failures()

        # Next sign-in asks for enrolment again (not for a code) and issues a new key.
        again = client.post("/auth/login", json=login).json()
        assert again == {"totp_setup_required": True, "temp_token": again["temp_token"]}
        second = client.post("/auth/totp/enroll", json={"temp_token": again["temp_token"]}).json()
        assert second["secret"] != first["secret"]
        # The sign-in token carries no enrolment, and the abandoned key does not confirm the new one.
        assert client.post("/auth/totp", json={"temp_token": again["temp_token"], "code": pyotp.TOTP(second["secret"]).now()}).status_code == 401
        assert client.post("/auth/totp", json={"temp_token": second["temp_token"], "code": pyotp.TOTP(first["secret"]).now()}).status_code == 401
        assert saved_secret() is None
        clear_failures()

        tokens = client.post("/auth/totp", json={"temp_token": second["temp_token"], "code": pyotp.TOTP(second["secret"]).now()}).json()
        assert "access_token" in tokens
        assert saved_secret() and count_unused("u_dev") == 10
        assert get_user_view("u_dev")["tfa_enabled"] is True

        # Enrolled now: sign-in asks for the code, a new enrolment is refused, and only the backup
        # codes of the confirmed enrolment work.
        step = client.post("/auth/login", json=login).json()
        assert step == {"totp_required": True, "temp_token": step["temp_token"]}
        assert client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).status_code == 409
        assert client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": first["backup_codes"][0]}).status_code == 401
        assert "access_token" in client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": second["backup_codes"][0]}).json()

    def test_lockout_follows_the_policy(self, store):
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec

        store._execute("UPDATE users SET password=? WHERE user_id='u_view'", [hash_password("TestP@ssw0rd123")])
        sec.update_policy({"lock_attempts": 3, "lock_minutes": 5})
        client = _client({"user_id": "u_view", "is_admin": 0, "is_active": 1})
        for _ in range(3):
            assert client.post("/auth/login", json={"username": "viewer", "password": "bad"}).status_code == 400
        assert client.post("/auth/login", json={"username": "viewer", "password": "TestP@ssw0rd123"}).status_code == 423
        until = store._fetch_one("SELECT login_locked_until FROM users WHERE user_id='u_view'")["login_locked_until"]
        delta = datetime.fromisoformat(until) - datetime.now(timezone.utc)
        assert timedelta(minutes=3) < delta <= timedelta(minutes=5)

    def test_idle_session_is_signed_out_on_refresh(self, store):
        from modules.flow_gate.auth.auth_api import _session_idle
        from modules.flow_gate.settings import security_policy_service as sec

        sec.update_policy({"idle_timeout": "30m"})
        old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(timespec="seconds")
        fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assert _session_idle({"last_used_at": old}) is True
        assert _session_idle({"last_used_at": fresh}) is False
