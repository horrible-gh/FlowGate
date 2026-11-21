from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modules.flow_gate.auth import auth_api
from modules.flow_gate.db import connection

@pytest.fixture
def client(monkeypatch):
    store = Mock()
    store._fetch_one.return_value = {"ok": 1}
    monkeypatch.setattr(connection, "get_store", lambda: store)
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/auth")
    app.dependency_overrides[auth_api.get_current_user] = lambda: {"user_id": "test"}
    return TestClient(app), store, app

def test_probe_reads_db_even_with_cached_identity(client):
    http, store, _ = client
    for _ in range(2):
        response = http.get("/auth/connection-check")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert response.headers["cache-control"] == "no-store"
    assert store._fetch_one.call_count == 2
    store._fetch_one.assert_called_with("SELECT 1 AS ok")

def test_probe_db_failure_is_unavailable(client):
    http, store, _ = client
    store._fetch_one.side_effect = RuntimeError("db unavailable")
    assert http.get("/auth/connection-check").status_code == 503

def test_probe_requires_authentication(client):
    http, store, app = client
    app.dependency_overrides.clear()
    assert http.get("/auth/connection-check").status_code in (401, 403)
    store._fetch_one.assert_not_called()
