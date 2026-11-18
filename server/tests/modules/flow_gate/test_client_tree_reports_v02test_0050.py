import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modules.flow_gate.api.v1.client_report_routes import router, get_current_user
from modules.flow_gate.services import server_logs
from test_server_logs_v02test_0050 import logs, fg_store

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "alice"}
    return TestClient(app)

def payload():
    return dict(id="tree-12345678", project_id="token=secret", screen="full_tree", status=504,
                elapsed_ms=10, occurred_at="2026-11-19T00:00:00Z", request_id="request-123")

def test_record_and_replay(client, logs):
    for _ in range(2):
        assert client.post("/api/v1/client-reports/tree-read-failed", json=payload()).status_code == 200
        assert server_logs.flush(10)
    rows = logs._fetch_all("SELECT * FROM server_logs WHERE source = ?", ["browser"])
    assert len(rows) == 1
    assert rows[0]["repeat_count"] == 1
    assert rows[0]["request_id"] == "request-123"
    assert "secret" not in rows[0]["extra_json"]
    assert "***" in rows[0]["extra_json"]
    assert json.loads(rows[0]["extra_json"])["user_id"] == "alice"

@pytest.mark.parametrize("field,value", [("id", "bad"), ("status", 200), ("screen", "url"), ("elapsed_ms", -1), ("request_id", "x"*65), ("occurred_at", "bad"), ("token", "secret")])
def test_invalid(client, field, value):
    body = payload(); body[field] = value
    assert client.post("/api/v1/client-reports/tree-read-failed", json=body).status_code == 422

def test_login_required():
    app = FastAPI(); app.include_router(router)
    assert TestClient(app).post("/api/v1/client-reports/tree-read-failed", json=payload()).status_code in (401,403)


def test_never_acknowledges_a_lost_queue_entry(client, logs, monkeypatch):
    monkeypatch.setattr(server_logs, "record", lambda *a, **kw: True)
    monkeypatch.setattr(server_logs, "flush", lambda *a, **kw: True)
    assert client.post("/api/v1/client-reports/tree-read-failed", json=payload()).status_code == 503


def test_queue_refusal(client, monkeypatch):
    monkeypatch.setattr(server_logs, "record", lambda *a, **kw: False)
    assert client.post("/api/v1/client-reports/tree-read-failed", json=payload()).status_code == 503


def test_request_id_credentials_are_redacted(client, logs):
    body = payload(); body["request_id"] = "Bearer secret"
    assert client.post("/api/v1/client-reports/tree-read-failed", json=body).status_code == 200
    rows = logs._fetch_all("SELECT * FROM server_logs WHERE source = ?", ["browser"])
    assert rows[0]["request_id"] == "Bearer ***"
