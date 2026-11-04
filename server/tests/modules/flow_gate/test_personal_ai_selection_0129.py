"""flowgate.v02.0129 CH0012 -- a user's own remote-CLI AI is selectable and runnable by its owner.

Covers the server half of "AI 등록 -> Agent 지정 -> 채팅/작업에서 선택 -> 실행":
- the selection list (chat composer, card settings, start dialogs read ai-invoke/providers ->
  admission.list_runtime_providers) offers the caller's runnable own AI after the system AI;
- only a remote CLI AI with a target Agent is runnable (local CLI / API own AI stay out);
- another user's AI is never listed, never selectable and never runnable (approved (B));
- the workspace allowlist exemption applies to the owner only (approved (A));
- system AI and the unpinned fallback chain are unchanged;
- AI groups list/assign the caller's own AI and show another user's as unavailable.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.flow_gate.db import ai_providers  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.settings import ai_settings_service as svc  # noqa: E402
from modules.flow_gate.settings import workspace_service  # noqa: E402

TS = "2026-11-04T00:00:00Z"


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)

    def _execute(self, sql, params=None):
        return self.db.execute(sql, params or [])

    def _fetch_one(self, sql, params=None):
        cur = self.db.execute(sql, params or [])
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    def _fetch_all(self, sql, params=None):
        cur = self.db.execute(sql, params or [])
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]


def _user(store, uid):
    store._execute(
        "INSERT INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,"
        "created_at,updated_at) VALUES(?,?,?,?,1,0,0,?,?)", [uid, uid, uid + "@example.invalid", "x", TS, TS])


def _provider(store, pid, *, owner=None, enabled=1, exec_type="cli", location="local", agent=None, order=0):
    store._execute(
        "INSERT INTO ai_providers(provider_id,project_id,name,exec_type,kind,enabled,cli_command,sort_order,"
        "created_at,updated_at,owner_scope,owner_user_id,exec_location,target_agent_id) "
        "VALUES(?,NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, "name-" + pid, exec_type, "codex" if exec_type == "cli" else "openai", enabled,
         "codex exec -" if exec_type == "cli" else None, order, TS, TS,
         "user" if owner else "system", owner, location, agent])


@pytest.fixture
def world(migrated_sqlite_db, monkeypatch):
    store = Store(str(migrated_sqlite_db("personal_ai_selection_0129.sqlite")))
    for module in (db_connection, ai_providers):
        monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(svc, "_require_project", lambda project_id: None)
    monkeypatch.setattr(svc._user_default_db, "get", lambda user_id: None)
    monkeypatch.setattr(ai_providers, "get_system_default_provider_id", lambda: None)
    monkeypatch.setattr(workspace_service, "allowed_resource_ids", lambda project_id, kind: None)
    for uid in ("alice", "bob"):
        _user(store, uid)
    _provider(store, "sys_a", order=0)
    _provider(store, "sys_off", enabled=0, order=1)
    _provider(store, "alice_remote", owner="alice", location="remote", agent="agt_alice")
    _provider(store, "alice_off", owner="alice", enabled=0, location="remote", agent="agt_alice", order=1)
    _provider(store, "alice_local", owner="alice", location="local", order=2)
    _provider(store, "alice_noagent", owner="alice", location="remote", agent=None, order=3)
    _provider(store, "alice_api", owner="alice", exec_type="api", location="local", order=4)
    _provider(store, "bob_remote", owner="bob", location="remote", agent="agt_bob")
    return store


def _ids(result):
    return [p["id"] for p in result["providers"]]


def _listed(user_id):
    from modules.flow_gate.services.ai_invoke import admission
    return admission.list_runtime_providers("prj", user_id)


def test_owner_sees_runnable_own_ai_after_system_ai(world):
    assert _ids(_listed("alice")) == ["sys_a", "alice_remote"]
    assert _ids(_listed("bob")) == ["sys_a", "bob_remote"]
    assert [p["id"] for p in svc.personal_selectable("alice")] == ["alice_remote"]
    assert svc.personal_selectable(None) == []


def test_the_owners_default_may_be_their_own_ai(world, monkeypatch):
    monkeypatch.setattr(svc._user_default_db, "get", lambda user_id: "alice_remote")
    assert _listed("alice")["default_provider_id"] == "alice_remote"
    # bob cannot default to alice's AI: the system default rule applies.
    assert _listed("bob")["default_provider_id"] == "sys_a"


def test_system_only_views_are_unchanged(world):
    # Every existing caller (fallback chains, name lookups) keeps the system AI only.
    assert _ids(svc.resolve_effective("prj")) == ["sys_a"]
    assert _ids(svc.resolve_effective("prj", "alice")) == ["sys_a"]
    assert _ids(svc.resolve_global("alice")) == ["sys_a"]


def test_runtime_provider_list_never_carries_the_command_or_the_agent(world):
    listed = _listed("alice")
    assert set(listed["providers"][1]) == {"id", "name", "exec_type", "kind", "capabilities"}


def test_allowlist_exemption_applies_to_the_owner_only(world, monkeypatch):
    monkeypatch.setattr(workspace_service, "allowed_resource_ids", lambda project_id, kind: set())
    assert _ids(_listed("alice")) == ["alice_remote"]
    assert _ids(_listed("bob")) == ["bob_remote"]
    assert _ids(svc.resolve_effective("prj")) == []


def test_explicit_selection_is_owner_only(world):
    assert svc.selectable_provider("prj", "alice_remote", "alice")["id"] == "alice_remote"
    assert svc.selectable_provider("prj", "alice_remote", "bob") is None
    assert svc.selectable_provider("prj", "alice_remote", None) is None
    assert svc.selectable_provider("prj", "sys_a", "bob")["id"] == "sys_a"
    for pid in ("alice_off", "alice_local", "alice_noagent", "alice_api", "sys_off"):
        assert svc.selectable_provider("prj", pid, "alice") is None
    assert svc.provider_selectable_any_owner("prj", "alice_remote") is True
    assert svc.provider_selectable_any_owner("prj", "alice_local") is False


def test_run_fence_returns_a_user_row_to_its_owner_only(world):
    assert ai_providers.get_runnable_row("alice_remote", "alice")["provider_id"] == "alice_remote"
    assert ai_providers.get_runnable_row("alice_remote", "bob") is None
    assert ai_providers.get_runnable_row("alice_remote", None) is None
    assert ai_providers.get_runnable_row("sys_a", None)["provider_id"] == "sys_a"
    # The fallback/secret paths still never read a user row.
    assert ai_providers.get_system_row("alice_remote") is None


@pytest.mark.parametrize("row,allowed,cause", [
    # A runnable own AI (remote CLI on an Agent) can be a card's role AI; the allowlist governs shared AI.
    ({"enabled": True, "owner_scope": "user", "exec_type": "cli", "exec_location": "remote",
      "target_agent_id": "agt"}, None, None),
    ({"enabled": True, "owner_scope": "user", "exec_type": "cli", "exec_location": "remote",
      "target_agent_id": "agt"}, set(), None),
    # Everything else keeps its cause.
    ({"enabled": True, "owner_scope": "user", "exec_type": "cli", "exec_location": "local",
      "target_agent_id": None}, None, "personal_not_runnable"),
    ({"enabled": True, "owner_scope": "user", "exec_type": "cli", "exec_location": "remote",
      "target_agent_id": None}, set(), "not_allowed_in_project"),
    ({"enabled": False, "owner_scope": "user", "exec_type": "cli", "exec_location": "remote",
      "target_agent_id": "agt"}, None, "inactive"),
])
def test_card_role_availability_of_an_own_ai(monkeypatch, row, allowed, cause):
    from flowgate.ai_roles import availability
    monkeypatch.setattr(availability, "lookup", lambda provider_id: row)
    monkeypatch.setattr(availability.workspace_service, "allowed_resource_ids", lambda *a: allowed)
    assert availability.check_availability("X", "card", project_id="p") == cause


def test_ai_group_offers_and_shows_own_ai_per_viewer(world):
    from modules.flow_gate.services import ai_group_service as groups
    assert set(groups._available("alice")) == {"sys_a", "alice_remote"}
    assert set(groups._available()) == {"sys_a"}
    with groups.viewer("alice"):
        assert set(groups._available()) == {"sys_a", "alice_remote"}
        assert groups._connection("alice_remote", groups._available(), {})["status"] == "available"
    with groups.viewer("bob"):
        # Another user's AI in a shared group: listed by name, unavailable, never "broken".
        shown = groups._connection("alice_remote", groups._available(), {})
        assert (shown["status"], shown["provider_name"]) == ("unavailable", "name-alice_remote")
    # A deleted own AI is broken for everyone, as a deleted system AI is.
    world._execute("UPDATE ai_providers SET deleted_at=? WHERE provider_id='alice_remote'", [TS])
    with groups.viewer("alice"):
        assert groups._connection("alice_remote", groups._available(), {})["status"] == "broken"
