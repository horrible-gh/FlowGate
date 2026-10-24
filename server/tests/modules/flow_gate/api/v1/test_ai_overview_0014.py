"""AI Overview composes Provider, Evaluation, and Group authoritative reads."""
from modules.flow_gate.api.v1 import ai_overview_routes as route


def _status(lifecycle=None, *, stale=False):
    return {
        "current": {"lifecycle": lifecycle} if lifecycle else None,
        "latest_history": {"is_stale": stale} if stale else None,
    }


def test_counts_pending_separately_from_running(monkeypatch):
    states = {
        "p1": _status("pending"),
        "p2": _status("running"),
        "p3": _status("failed"),
        "p4": _status(stale=True),
    }
    monkeypatch.setattr(route.ai_evaluation_service, "current", lambda project, provider: states[provider])
    assert route._evaluation("project", list(states)) == {
        "providers_with_current_result": 3, "running": 1, "failed": 1, "stale": 1,
    }


def test_invalid_group_counts_group_once(monkeypatch):
    role = lambda available: {"is_available": available}
    monkeypatch.setattr(route.ai_group_service, "list_groups", lambda project: {"items": [
        {"work_executor": role(True), "reviewer": role(False), "reworker": role(False)},
        {"work_executor": role(True), "reviewer": None, "reworker": None},
    ]})
    assert route._groups("project") == {"total": 2, "invalid": 1}


def test_partial_failure_keeps_other_sections(monkeypatch):
    monkeypatch.setattr(route.projects, "get_by_id", lambda project: {"id": project})
    monkeypatch.setattr(route, "_providers", lambda project: ({"mode": "custom", "total": 1,
        "enabled": 1, "default_provider_id": "p1"}, ["p1"]))
    monkeypatch.setattr(route, "_evaluation", lambda project, ids: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(route, "_groups", lambda project: {"total": 2, "invalid": 0})
    result = route.ai_overview("project", user={})
    assert result["providers"]["total"] == 1
    assert result["evaluation"] is None
    assert result["ai_groups"] == {"total": 2, "invalid": 0}
    assert result["errors"] == [{"section": "evaluation", "code": "overview_section_failed"}]


def test_missing_project_is_whole_request_failure(monkeypatch):
    monkeypatch.setattr(route.projects, "get_by_id", lambda project: None)
    response = route.ai_overview("missing", user={})
    assert response.status_code == 404
