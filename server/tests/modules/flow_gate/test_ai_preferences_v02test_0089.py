"""Personal AI preferences: HTTP contract, persistence, ownership and runtime boundaries."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from modules.flow_gate.db import user_ai_preferences as repo
from modules.flow_gate.settings import ai_execution_policy_service as policy
from modules.flow_gate.settings import ai_settings_service, ai_title_provider
from modules.flow_gate.settings.routers import ai_settings as routes
from modules.flow_gate.auth.middleware import get_current_user

REAL_SELECTOR = ai_settings_service.selectable_provider


@pytest.fixture
def prefs(monkeypatch):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
    connection.executemany("INSERT INTO users VALUES (?)", [("alice",), ("bob",)])
    sql = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite/213_user_ai_preferences.sql"
    connection.executescript(sql.read_text())
    connection.executescript(sql.read_text())  # migration is repeatable

    class Store:
        def table_exists(self, table):
            return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name = ?", [table]).fetchone())

        def _fetch_one(self, sql, args):
            row = connection.execute(sql, args).fetchone()
            return dict(row) if row else None

        def _execute(self, sql, args):
            connection.execute(sql, args)

    monkeypatch.setattr(repo, "get_store", lambda: Store())
    ceiling = {"value": "3"}
    monkeypatch.setattr(policy._system_settings, "get_value", lambda key: ceiling["value"])
    monkeypatch.setattr(ai_title_provider, "value", lambda: "system-mini")
    # Real ownership/runnability validation is exercised separately below.
    monkeypatch.setattr(ai_settings_service, "selectable_provider", lambda project, provider, user:
                        ({"id": provider, "exec_type": "cli", "exec_location": "remote"} if provider == user + "-ai"
                         else {"id": provider, "exec_type": "api"} if provider == "system-mini" else None))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    caller = {"user_id": "alice"}
    app.dependency_overrides[get_current_user] = lambda: caller.copy()
    with TestClient(app) as client:
        yield client, caller, ceiling, connection
    connection.close()


def test_defaults_patch_merge_reset_and_user_isolation(prefs):
    client, caller, _, _ = prefs
    url = "/api/v1/me/ai-preferences"
    assert client.get(url).json() == {
        "title_provider": "auto", "title_provider_effective": "system-mini", "repeat_count_max": None,
        "repeat_count_max_effective": 3, "repeat_count_system_max": 3, "repeat_count_min": 1}
    assert client.patch(url, json={"title_provider": "alice-ai", "repeat_count_max": 2}).status_code == 200
    result = client.patch(url, json={"title_provider": "executor"}).json()
    assert result["repeat_count_max"] == 2
    assert result["title_provider_effective"] == "executor"
    assert client.patch(url, json={}).json() == result
    caller["user_id"] = "bob"
    assert client.get(url).json()["repeat_count_max"] is None
    caller["user_id"] = "alice"
    assert client.get(url).json() == result
    assert client.patch(url, json={"repeat_count_max": None}).json()["title_provider"] == "executor"
    assert client.patch(url, json={"title_provider": "auto"}).json()["repeat_count_max_effective"] == 3


@pytest.mark.parametrize("value", [0, -1, 4, True, 1.5, "2", [], {}])
def test_repeat_errors_are_exact_and_atomic(prefs, value):
    client, _, _, _ = prefs
    response = client.patch("/api/v1/me/ai-preferences", json={"title_provider": "off", "repeat_count_max": value})
    assert response.status_code == 422
    assert response.json() == {"code": "repeat_count_out_of_range", "max": 3}
    assert repo.get("alice")["title_provider"] == "auto"


@pytest.mark.parametrize("value", [None, "", " off", "bob-ai", "disabled", 4, [], {}])
def test_title_errors(prefs, value):
    client, _, _, _ = prefs
    response = client.patch("/api/v1/me/ai-preferences", json={"title_provider": value})
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_title_provider"}


def test_requires_authentication(prefs):
    client, _, _, _ = prefs
    def denied():
        raise HTTPException(401)
    client.app.dependency_overrides[get_current_user] = denied
    for method in (client.get, client.patch):
        assert method("/api/v1/me/ai-preferences").status_code == 401


def test_system_lowering_clamps_without_rewriting_and_null_restores(prefs):
    client, _, ceiling, _ = prefs
    client.patch("/api/v1/me/ai-preferences", json={"repeat_count_max": 3})
    ceiling["value"] = "1"
    result = client.get("/api/v1/me/ai-preferences").json()
    assert result["repeat_count_max"] == 3
    assert result["repeat_count_max_effective"] == 1
    assert result["repeat_count_system_max"] == 1
    ceiling["value"] = "5"
    assert policy.get_user_repeat_count_max("alice") == 3
    assert policy.get_repeat_count_max() == 5
    client.patch("/api/v1/me/ai-preferences", json={"repeat_count_max": None})
    assert policy.get_user_repeat_count_max("alice") == 5


def test_personal_choices_do_not_demote_stored_gate_values(prefs):
    from modules.flow_gate.services.ai_invoke import review, admission, runtime
    from modules.flow_gate.services import workflow_decision_service
    repo.patch("alice", {"repeat_count_max": 1})
    assert policy.execution_policy_payload("alice")["repeat_count_max"] == 1
    assert runtime.restart_max_attempts_choices("alice") == (-1, 0, 1)
    with pytest.raises(ValueError):
        workflow_decision_service.normalize_continuation_review_count_overrides({"1": 2}, user_id="alice")
    assert workflow_decision_service.normalize_continuation_review_count_overrides({"1": 2}) == {"1": 2}
    assert admission._resolve_restart_max_attempts(3) == 4
    assert review.normalize_review_count(3) == 3
    assert policy.repeat_count_choices(allow_zero=True) == (-1, 0, 1, 2, 3)


@pytest.mark.parametrize("choice,expected", [("off", None), ("executor", "alice-ai"),
                                             ("alice-ai", "alice-ai"), ("auto", "system-mini")])
def test_real_naming_uses_personal_selection(prefs, monkeypatch, choice, expected):
    from flowgate.conversation import legacy_adapter as naming
    repo.patch("alice", {"title_provider": choice})
    calls = []
    from flowgate.conversation import title_remote
    monkeypatch.setattr(title_remote, "complete", lambda project, provider, prompt, user, timeout:
                        (calls.append(provider["id"]) or "a title", None))
    monkeypatch.setattr(naming, "complete_prompt", lambda project, provider, prompt, **kw:
                        (calls.append(provider) or "a title", None))
    result = naming.complete_naming("p", "alice-ai", "prompt", "alice", timeout=10, max_tokens=20)
    assert calls == ([] if expected is None else [expected])
    assert result == ((None, "title_disabled") if expected is None else ("a title", None))
    assert ai_title_provider.selected("executor", user_id="bob") == "system-mini"


def test_api_uses_real_provider_ownership_and_enabled_checks(prefs, monkeypatch):
    client, _, _, _ = prefs
    monkeypatch.setattr(ai_settings_service, "selectable_provider", REAL_SELECTOR)
    monkeypatch.setattr(ai_settings_service, "resolve_global", lambda: {"providers": [{"id": "system-mini"}]})
    base = {"owner_scope": "user", "enabled": True, "exec_type": "cli",
            "exec_location": "remote", "target_agent_id": "agent"}
    rows = {"alice-ai": {**base, "owner_user_id": "alice"},
            "bob-ai": {**base, "owner_user_id": "bob"},
            "disabled": {**base, "owner_user_id": "alice", "enabled": False},
            "local": {**base, "owner_user_id": "alice", "exec_location": "local"}}
    monkeypatch.setattr(ai_settings_service._ai_db, "get_user_row", rows.get)
    monkeypatch.setattr(ai_settings_service, "_chain_entry", lambda row: row)
    for provider, status in [("system-mini", 200), ("alice-ai", 200), ("bob-ai", 422), ("disabled", 422), ("local", 422)]:
        assert client.patch("/api/v1/me/ai-preferences", json={"title_provider": provider}).status_code == status


def test_actual_start_route_validates_requester_ceiling(prefs, monkeypatch):
    from modules.flow_gate.api.v1 import ai_invoke_routes
    repo.patch("alice", {"repeat_count_max": 1})
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda request: {"issued_to": "alice", "_is_user_jwt": True})
    for value, rejected in [(1, False), (2, True)]:
        body = ai_invoke_routes.AiInvokeStartRequest(
            project="flowgate", module="v02test", group="0089", action_scope="__boundary_probe__",
            doc_ref="flowgate.v02test.0089.0003-T", mode="single", continuation_restart_max_attempts=value)
        response = ai_invoke_routes.start_ai_invoke(body, object())
        assert response.status_code == 422
        locations = {error["loc"] for error in json.loads(response.body)["errors"]}
        assert ("continuation_restart_max_attempts" in locations) is rejected


def test_missing_migration_is_defaults_and_unavailable_saved_title_never_dispatches(prefs, monkeypatch):
    _, _, _, connection = prefs
    from flowgate.conversation import legacy_adapter as naming
    repo.patch("alice", {"title_provider": "deleted-ai"})
    monkeypatch.setattr(naming, "complete_prompt", lambda *a, **kw: pytest.fail("unavailable provider dispatched"))
    assert naming.complete_naming("p", "alice-ai", "prompt", "alice", timeout=1, max_tokens=1) == (None, "provider_unavailable")
    connection.execute("DROP TABLE user_ai_preferences")
    assert repo.get("alice") == {"title_provider": "auto", "repeat_count_max": None}
