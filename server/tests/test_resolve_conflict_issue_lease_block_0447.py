"""flowgate.default.0447 T0005 — resolve_conflict token admission lease guard."""
from __future__ import annotations
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))
from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.api import token_routes  # noqa: E402

_OPEN_CONFLICTS = {"branch": "grp/0447", "base_branch": "main", "files": [{"path": "app/x.py", "conflict_count": 1, "content": "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n"}]}

def _token_client(monkeypatch, calls: dict[str, int]) -> TestClient:
    app = FastAPI()
    app.include_router(token_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
    monkeypatch.setattr(token_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(token_routes, "_resolve_group", lambda p, g: "flowgate.default.0447")
    monkeypatch.setattr(token_routes, "has_permission", lambda *a, **k: True)
    def issue(**kw):
        calls["issue"] += 1
        return {"raw_token": "RAW", "token_id": "tok_1", "expires_at": "2026-08-29T00:00:00+00:00", "scratch_dir": "/tmp/scratch", "group_id": "flowgate.default.0447"}
    def list_conflicts(*_a, **_kw):
        calls["list_conflicts"] += 1
        return dict(_OPEN_CONFLICTS)
    monkeypatch.setattr(token_routes.token_service, "issue", issue)
    monkeypatch.setattr(token_routes.git_service, "list_conflicts", list_conflicts)
    return TestClient(app, raise_server_exceptions=False)

def _issue_body(action_scope: str = "resolve_conflict"):
    body = {"project": "flowgate", "group": "0447", "action_scope": action_scope}
    if action_scope == "resolve_conflict":
        body["merge_id"] = 5
    else:
        body["doc_ref"] = "flowgate.default.0447.0001-B"
    return body

def test_active_lease_blocks_before_token_or_conflict_read(monkeypatch):
    calls = {"issue": 0, "list_conflicts": 0}
    client = _token_client(monkeypatch, calls)
    monkeypatch.setattr(token_routes.db_group_ai_leases, "get_active", lambda gid: {"run_id": "aiv_1", "action_scope": "new", "token_id": "tok_x"})
    resp = client.post("/api/v1/token/issue", json=_issue_body())
    assert resp.status_code == 403
    assert "flowgate.default.0447" in resp.json()["detail"]
    assert "aiv_1" in resp.json()["detail"]
    assert calls == {"issue": 0, "list_conflicts": 0}

def test_no_active_lease_keeps_resolve_conflict_issuance(monkeypatch):
    calls = {"issue": 0, "list_conflicts": 0}
    client = _token_client(monkeypatch, calls)
    monkeypatch.setattr(token_routes.db_group_ai_leases, "get_active", lambda gid: None)
    resp = client.post("/api/v1/token/issue", json=_issue_body())
    assert resp.status_code == 200
    assert resp.json()["mention"]
    assert calls == {"issue": 1, "list_conflicts": 1}

def test_active_resolve_conflict_lease_is_also_blocked(monkeypatch):
    calls = {"issue": 0, "list_conflicts": 0}
    client = _token_client(monkeypatch, calls)
    monkeypatch.setattr(token_routes.db_group_ai_leases, "get_active", lambda gid: {"run_id": "aiv_resolve", "action_scope": "resolve_conflict"})
    resp = client.post("/api/v1/token/issue", json=_issue_body())
    assert resp.status_code == 403
    assert "aiv_resolve" in resp.json()["detail"]
    assert calls == {"issue": 0, "list_conflicts": 0}

def test_active_lease_does_not_block_other_issue_scopes(monkeypatch):
    calls = {"issue": 0, "list_conflicts": 0}
    client = _token_client(monkeypatch, calls)
    monkeypatch.setattr(token_routes.db_group_ai_leases, "get_active", lambda gid: {"run_id": "aiv_1", "action_scope": "new"})
    monkeypatch.setattr(token_routes, "_build_mention_for_token", lambda **kw: None)
    resp = client.post("/api/v1/token/issue", json=_issue_body("new"))
    assert resp.status_code == 200
    assert calls["issue"] == 1