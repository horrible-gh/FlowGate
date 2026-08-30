from __future__ import annotations

import subprocess

import pytest

from modules.flow_gate.services import remote_tool_service as svc


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def diverged_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    (root / "old.txt").write_text("old\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base history")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-b", "target")
    (root / "shared.txt").write_text("base\ntarget-only\n", encoding="utf-8")
    (root / "target.txt").write_text("target file\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "target intent")
    target = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-b", "worker", base)
    (root / "shared.txt").write_text("base\nhead-only\n", encoding="utf-8")
    (root / "head.txt").write_text("head file\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "head intent")
    _git(root, "update-ref", "refs/remotes/origin/main", target)
    return root, base, target


def test_diff_and_log_contain_only_target_side_since_merge_base(diverged_repo):
    root, base, target = diverged_repo
    diff, _ = svc._exec_diff({}, root)
    log, _ = svc._exec_log({}, root)
    assert diff["merge_base"] == base
    assert diff["target_ref"] == "origin/main"
    assert "target-only" in diff["patch"] and "target.txt" in diff["patch"]
    assert "head-only" not in diff["patch"] and "head.txt" not in diff["patch"]
    assert [c["subject"] for c in log["commits"]] == ["target intent"]
    assert log["commits"][0]["sha"] == target
    assert "base history" not in str(log) and "head intent" not in str(log)


def test_optional_path_scopes_both_operations(diverged_repo):
    root, _, _ = diverged_repo
    diff, _ = svc._exec_diff({"path": "target.txt"}, root)
    log, _ = svc._exec_log({"path": "target.txt"}, root)
    assert "target.txt" in diff["patch"] and "shared.txt" not in diff["patch"]
    assert [c["subject"] for c in log["commits"]] == ["target intent"]


@pytest.mark.parametrize("op,body", [
    ("diff", {"target_ref": "--all"}),
    ("log", {"target_ref": "HEAD..main"}),
    ("log", {"max_count": 0}),
    ("log", {"max_count": True}),
])
def test_invalid_ref_and_max_count_are_rejected(op, body):
    with pytest.raises(svc._OpError) as exc:
        svc._validate_required(op, body)
    assert exc.value.status == 422


def test_path_escape_is_rejected_by_existing_jail():
    with pytest.raises(svc._OpError) as exc:
        svc._validate_paths("diff", {"path": "../secret"})
    assert exc.value.status == 422


def test_missing_ref_and_output_cap_are_explicit(diverged_repo, monkeypatch):
    root, _, _ = diverged_repo
    with pytest.raises(svc._OpError) as exc:
        svc._exec_log({"target_ref": "origin/missing"}, root)
    assert exc.value.status == 503
    monkeypatch.setattr(svc, "_MAX_DIFF_BYTES", 20)
    result, _ = svc._exec_diff({}, root)
    assert result["truncated"] is True
    assert result["returned_bytes"] <= 20


def test_log_max_count_reports_truncation(diverged_repo):
    root, _, target = diverged_repo
    (root / "target2.txt").write_text("second\n", encoding="utf-8")
    _git(root, "checkout", "target")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "second target intent")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    _git(root, "checkout", "worker")
    result, _ = svc._exec_log({"max_count": 1}, root)
    assert result["total"] == 1 and result["truncated"] is True
    assert result["commits"][0]["subject"] == "second target intent"


@pytest.mark.parametrize("operation", ["diff", "log"])
def test_http_read_scope_200_and_operation_is_logged(diverged_repo, monkeypatch, operation):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    root, base, _ = diverged_repo
    grant = {"grant_id": "g-read", "project": "p", "module": "default"}
    recorded = []
    monkeypatch.setattr(svc, "_authenticate", lambda token: grant if token == "ok" else None)
    monkeypatch.setattr(svc.db_grants, "get_scopes", lambda grant_id: {"read"})
    monkeypatch.setattr(svc, "_resolve_src_root", lambda _grant, _op: root)
    monkeypatch.setattr(svc.db_oplog, "insert", lambda **row: recorded.append(row))
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        f"/api/v1/remote/{operation}", json={}, headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 200
    assert response.json()["merge_base"] == base
    assert len(recorded) == 1
    assert recorded[0]["op"] == operation and recorded[0]["result"] == "success"


@pytest.mark.parametrize("operation", ["diff", "log"])
def test_http_without_read_scope_is_403(monkeypatch, operation):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    monkeypatch.setattr(svc, "_authenticate", lambda token: {"grant_id": "g-none"})
    monkeypatch.setattr(svc.db_grants, "get_scopes", lambda grant_id: set())
    monkeypatch.setattr(svc.db_oplog, "insert", lambda **row: None)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        f"/api/v1/remote/{operation}", json={}, headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("op,body", [
    ("read", {"path": "sample.txt", "encoding": "not-a-real-codec"}),
    ("write", {"path": "sample.txt", "content": "x", "encoding": "not-a-real-codec"}),
])
def test_unknown_encoding_is_rejected_as_422(op, body):
    with pytest.raises(svc._OpError) as exc:
        svc._validate_required(op, body)
    assert exc.value.status == 422
