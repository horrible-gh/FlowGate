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

class TestSystemUsers:




    def test_resend_invite_and_resets(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Park Jiwon", "jiwon@x.io", "invite", False)
        uid = created["user"]["user_id"]
        again = su.resend_invite(ADMIN, uid)
        assert again["invite_path"] != created["invite_path"]
        assert su.find_invited(created["invite_path"].split("=", 1)[1]) is None
        with pytest.raises(su.SystemUserError):
            su.resend_invite(ADMIN, "u_dev")
        store._execute("UPDATE users SET totp_secret='enc' WHERE user_id='u_dev'")
        assert su.reset_tfa(ADMIN, "u_dev")["tfa_enabled"] is False
        assert su.reset_password(ADMIN, "u_dev")["temp_password"]
class TestSecurityPolicy:
    def test_defaults_keep_the_previous_behaviour(self, store):
        from modules.flow_gate.auth.password import validate_password
        from modules.flow_gate.settings import security_policy_service as sec

        policy = sec.get_policy(use_cache=False)
        assert policy["tfa_scope"] == "off" and policy["password_min_length"] == 12
        assert sec.lock_rule() == (5, 15)
        assert any("12 characters" in e for e in validate_password("Abcdef1!x"))

    def test_update_validates_and_applies(self, store):
        from modules.flow_gate.auth.password import validate_password
        from modules.flow_gate.settings import security_policy_service as sec

        with pytest.raises(sec.SecurityPolicyError):
            sec.update_policy({"lock_attempts": 4})
        with pytest.raises(sec.SecurityPolicyError):
            sec.update_policy({"nope": 1})
        sec.update_policy({"password_min_length": 8, "lock_attempts": 3, "lock_minutes": 60,
                           "idle_timeout": "30m", "recovery_codes": False})
        assert validate_password("Abcdef1!x") == []
        assert sec.lock_rule() == (3, 60)
        assert sec.idle_seconds() == 1800 and sec.recovery_codes_enabled() is False

    def test_tfa_enrolment_waits_for_the_grace_period(self, store):
        from modules.flow_gate.settings import security_policy_service as sec

        sec.update_policy({"tfa_scope": "admin", "tfa_grace_days": 7})
        admin = {"is_admin": 1, "created_at": "2026-01-01T00:00:00+00:00"}
        member = {"is_admin": 0, "created_at": "2026-01-01T00:00:00+00:00"}
        assert sec.tfa_required(admin) and not sec.tfa_required(member)
        assert sec.tfa_enrollment_due(admin) is False  # policy changed just now: 7 days left
        sec.update_policy({"tfa_grace_days": 0})
        assert sec.tfa_enrollment_due(admin) is True
        assert sec.tfa_enrollment_due({**admin, "totp_secret": "x"}) is False
        assert sec.tfa_enrollment_due(member) is False

    def test_widening_scope_signs_out_newly_covered_users(self, store):
        from modules.flow_gate.auth.session_store import create_session, get_session
        from modules.flow_gate.settings import security_policy_service as sec

        sid_dev, sid_admin = create_session("u_dev"), create_session("u_admin")
        result = sec.update_policy({"tfa_scope": "admin"})
        assert result["signed_out"] == 0  # logout_on_tighten is off by default
        result = sec.update_policy({"logout_on_tighten": True, "tfa_scope": "all"})
        assert result["signed_out"] == 3  # u_dev, u_view, u_free — admins were already covered
        assert get_session(sid_dev)["revoked_at"] is not None
        assert get_session(sid_admin)["revoked_at"] is None

    def test_coverage_lists_active_people(self, store):
        from modules.flow_gate.settings import security_policy_service as sec

        store._execute("UPDATE users SET is_active=0 WHERE user_id='u_free'")
        people = sec.coverage()
        assert {p["user_id"] for p in people} == {"u_admin", "u_admin2", "u_dev", "u_view"}
        assert people[0]["is_admin"] is True
