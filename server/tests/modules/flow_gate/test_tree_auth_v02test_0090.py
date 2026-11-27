"""Project tree authentication at the HTTP boundary, without a running server/DB."""
import itertools
from unittest.mock import Mock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import tree_routes
from modules.flow_gate.services import auth_outbound
from modules.flow_gate.auth import jwt_service, session_store, token_store


@pytest.fixture
def env(monkeypatch):
    state = {"allow": True, "active": True, "session": True, "checks": []}

    def worker(raw):
        if raw == "worker":
            return {"issued_to": "reader", "project": "p", "group_id": "p.m.1"}
        raise HTTPException(401, "invalid token")

    def decode(raw):
        if raw == "invalid":
            raise jwt.InvalidTokenError()
        if raw == "expired":
            raise jwt.ExpiredSignatureError()
        return {"sub": "reader", "type": "access", "sid": "session", "jti": "jti"}

    def permission(user, project, permission):
        state["checks"].append((user, project, permission))
        return state["allow"] and project == "p"

    monkeypatch.setattr(auth_outbound.token_service, "verify", worker)
    monkeypatch.setattr(jwt_service, "decode_token", decode)
    monkeypatch.setattr(token_store, "is_blacklisted", lambda jti: False)
    monkeypatch.setattr(session_store, "is_session_active", lambda sid: state["session"])
    monkeypatch.setattr(auth_outbound, "_load_active_user", lambda uid: {"is_active": state["active"]})
    monkeypatch.setattr(auth_outbound, "has_permission", permission)
    monkeypatch.setattr(tree_routes, "has_permission", permission)
    nodes = [{"id": "p.m.1", "node_type": "group", "label": "Private flow"}]
    state["nodes"] = nodes
    state["file"] = Mock(return_value={"nodes": [{"path": "private.txt"}]})
    state["raw"] = Mock(return_value=nodes)
    state["group"] = Mock(return_value={"nodes": nodes})
    state["hidden"] = Mock(return_value={"nodes": nodes})
    state["summary"] = Mock(return_value={"total_documents": 0, "working_groups": 0, "type_distribution": []})
    monkeypatch.setattr(tree_routes.process_service, "get_file_tree", state["file"])
    monkeypatch.setattr(tree_routes, "_get_raw_tree_nodes", state["raw"])
    monkeypatch.setattr(tree_routes.process_service, "get_group_tree", state["group"])
    monkeypatch.setattr(tree_routes.process_service, "get_hidden_group_tree", state["hidden"])
    monkeypatch.setattr(tree_routes.process_service, "get_group_tree_overview_summary", state["summary"])
    app = FastAPI()
    app.include_router(tree_routes.router)
    with TestClient(app) as client:
        state["client"] = client
        yield state


def read(env, route, token="user", project="p", **params):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    return env["client"].get(f"/api/v1/projects/{project}/{route}/tree", headers=headers, params=params)


def assert_no_data_read(env):
    for name in ("file", "raw", "group", "hidden", "summary"):
        env[name].assert_not_called()


@pytest.mark.parametrize("route", ["files", "groups"])
@pytest.mark.parametrize("token", [None, "invalid", "expired"])
def test_missing_invalid_expired(env, route, token):
    assert read(env, route, token).status_code == 401
    assert_no_data_read(env)
    assert env["checks"] == []


@pytest.mark.parametrize("route", ["files", "groups"])
@pytest.mark.parametrize("state", ["active", "session"])
def test_disabled_account_or_revoked_session(env, route, state):
    env[state] = False
    assert read(env, route).status_code == 401
    assert_no_data_read(env)
    assert env["checks"] == []


@pytest.mark.parametrize("route", ["files", "groups"])
@pytest.mark.parametrize("project", ["p", "foreign-workspace"])
def test_permission_denied_before_any_tree_or_inflight_read(env, route, project):
    env["allow"] = False
    response = read(env, route, project=project, include_terminal=False, include_archived=True, include_summary=True, force=True)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    expected = "project.document.read" if route == "files" else "project.group.read"
    assert env["checks"] == [("reader", project, expected)]
    assert_no_data_read(env)


@pytest.mark.parametrize("route", ["files", "groups"])
@pytest.mark.parametrize("project", ["p", "other"])
def test_group_bound_worker_cannot_read_project_wide_tree(env, route, project):
    response = read(env, route, token="worker", project=project)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert_no_data_read(env)


def test_authorized_files_preserve_envelope_and_branch(env):
    response = read(env, "files", branch="feature")
    assert response.status_code == 200
    assert response.json() == {"data": {"nodes": [{"path": "private.txt"}]}}
    env["file"].assert_called_once_with("p")
    assert env["checks"] == [("reader", "p", "project.document.read")]


@pytest.mark.parametrize("terminal,archived,summary,force", list(itertools.product([False, True], repeat=4)))
def test_authorized_groups_preserve_query_branches(env, terminal, archived, summary, force):
    response = read(env, "groups", include_terminal=terminal, include_archived=archived, include_summary=summary, force=force)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["nodes"] == env["nodes"]
    assert set(data) == ({"nodes", "overview_summary"} if summary else {"nodes"})
    assert env["checks"] == [("reader", "p", "project.group.read")]
    if terminal and not archived:
        env["raw"].assert_called_once_with("p", force=force)
    elif terminal:
        env["group"].assert_called_once_with("p", include_archived=True)
    elif archived:
        env["hidden"].assert_called_once_with("p", include_archived=True)
    else:
        env["hidden"].assert_called_once_with("p")


@pytest.mark.parametrize("route", ["files", "groups"])
def test_permission_rechecked_after_prior_success(env, route):
    assert read(env, route).status_code == 200
    for name in ("file", "raw", "group", "hidden", "summary"):
        env[name].reset_mock()
    env["allow"] = False
    assert read(env, route).status_code == 403
    assert_no_data_read(env)
