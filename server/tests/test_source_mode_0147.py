from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import mention_service
from modules.flow_gate.settings import source_mode_service
from modules.flow_gate.settings.routers import project_settings


def _mention_kwargs(**over):
    data = {
        "project": "flowgate",
        "module": "default",
        "group": "0147",
        "parent_type": "T",
        "parent_doc_number": "T0006",
        "parent_title": "작업지시 승인",
        "parent_doc_id": "T0006",
        "parent_canonical_doc_id": "flowgate.default.0147.0006-T",
        "head_type": "TR",
        "head_status": "pending",
        "scratch_dir": "",
        "raw_token": "RAW",
        "api_base_url": "http://h/flowgate/api/v1",
        "continuous": True,
    }
    data.update(over)
    return data


def _settings_client() -> TestClient:
    app = FastAPI()
    app.include_router(project_settings.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin", "is_admin": 1}
    return TestClient(app)


def test_mention_keeps_remote_crud_by_default(monkeypatch):
    monkeypatch.setattr(
        mention_service.source_mode_service,
        "include_remote_api_section",
        lambda project: True,
    )

    out = mention_service.build_mention(**_mention_kwargs())

    assert "## Remote project source CRUD" in out
    # 0349 TR-2: the section shrank to a help pointer, so the marker for "this step may
    # write" is the tool list, not a /remote/write example. tool_registry.DISPLAY_ORDER
    # since gained "diff"/"log" (merge-base patch/commit lookups), so the full read_write
    # kind now lists nine tools, not seven.
    assert "도구: read, grep, glob, stat, diff, log, show, write, patch, remove" in out


def test_mention_omits_remote_crud_in_local_mode(monkeypatch):
    monkeypatch.setattr(
        mention_service.source_mode_service,
        "include_remote_api_section",
        lambda project: False,
    )

    out = mention_service.build_mention(**_mention_kwargs())

    assert "## Remote project source CRUD" not in out
    assert "help/tools" not in out
    assert "## Artifact registration" in out


def test_source_mode_resolution_project_override_wins(monkeypatch):
    monkeypatch.setattr(source_mode_service._projects, "get_settings", lambda _pid: {"source_mode_override": "local"})
    monkeypatch.setattr(source_mode_service._system_settings, "get_value", lambda _key: "remote")

    assert source_mode_service.resolve_effective_mode("flowgate") == "local"
    assert source_mode_service.include_remote_api_section("flowgate") is False


def test_source_mode_resolution_falls_back_to_global_then_remote(monkeypatch):
    rows = iter([
        {"source_mode_override": "broken"},
        {},
    ])
    monkeypatch.setattr(source_mode_service._projects, "get_settings", lambda _pid: next(rows))
    monkeypatch.setattr(source_mode_service._system_settings, "get_value", lambda _key: "local")
    assert source_mode_service.resolve_effective_mode("flowgate") == "local"

    monkeypatch.setattr(source_mode_service._projects, "get_settings", lambda _pid: {})
    monkeypatch.setattr(source_mode_service._system_settings, "get_value", lambda _key: "broken")
    assert source_mode_service.resolve_effective_mode("flowgate") == "remote"


@pytest.mark.parametrize("value", ["", "cloud", None])
def test_set_global_mode_rejects_invalid(value):
    with pytest.raises(ValueError):
        source_mode_service.set_global_mode(value)  # type: ignore[arg-type]


def test_get_project_mode_rejects_missing_project(monkeypatch):
    monkeypatch.setattr(source_mode_service._projects, "get_by_id", lambda _pid: None)

    with pytest.raises(LookupError, match="project not found"):
        source_mode_service.get_project_mode("missing")


def test_set_project_mode_rejects_missing_project(monkeypatch):
    monkeypatch.setattr(source_mode_service._projects, "get_by_id", lambda _pid: None)

    with pytest.raises(LookupError, match="project not found"):
        source_mode_service.set_project_mode("missing", "local")


def test_set_project_mode_rejects_invalid_override(monkeypatch):
    monkeypatch.setattr(source_mode_service._projects, "get_by_id", lambda _pid: {"project_id": "flowgate"})

    with pytest.raises(ValueError, match="override must be one of"):
        source_mode_service.set_project_mode("flowgate", "cloud")


def test_project_mode_route_returns_not_found_envelope(monkeypatch):
    def _missing(_project_id):
        raise LookupError("project not found: missing")

    monkeypatch.setattr(project_settings.source_mode_service, "get_project_mode", _missing)

    response = _settings_client().get("/api/v1/settings/project/missing/mode")

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "error": {"code": "not_found", "message": "project not found: missing"},
    }


def test_project_mode_route_returns_invalid_request_envelope(monkeypatch):
    def _invalid(_project_id, _override):
        raise ValueError("override must be one of: local, remote, null")

    monkeypatch.setattr(project_settings.source_mode_service, "set_project_mode", _invalid)

    response = _settings_client().put(
        "/api/v1/settings/project/flowgate/mode",
        json={"override": "cloud"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "override must be one of: local, remote, null",
        },
    }
