"""F86 R3 corpus. Legacy values are frozen, never depend on Unidecode."""
import pytest
from modules.flow_gate.utils.slug import romanize_to_slug

# input, Unidecode 1.x baseline, AnyAscii 0.3.3 expectation
CORPUS = [
    ("안녕하세요", "annyeonghaseyo", "annyeonghaseyo"),
    ("한국어 프로젝트", "hangugeo-peurojegteu", "hangugeo-peulojegteu"),
    ("日本語", "ri-ben-yu", "ribenyu"),
    ("東京", "dong-jing", "dongjing"),
    ("こんにちは", "konnichiha", "konnichiha"),
    ("Hello World", "hello-world", "hello-world"),
    ("FlowGate 한국어 2026", "flowgate-hangugeo-2026", "flowgate-hangugeo-2026"),
    ("Café déjà vu", "cafe-deja-vu", "cafe-deja-vu"),
    ("Hello___World -- Test", "hello_world-test", "hello_world-test"),
]

@pytest.mark.parametrize("text,legacy,current", CORPUS)
def test_corpus(text, legacy, current):
    assert romanize_to_slug(text) == current

@pytest.mark.parametrize("text", ["", "---", "a/b", "a\\b", "a\tb", "__SYSTEM__", "a" * 51])
def test_invalid(text):
    with pytest.raises(ValueError):
        romanize_to_slug(text)


@pytest.mark.parametrize("text,legacy,current", [row for row in CORPUS if row[1] != row[2]])
@pytest.mark.parametrize("active", [0, 1])
def test_changed_suggestion_collision_and_explicit_suffix(monkeypatch, text, legacy, current, active):
    from fastapi import HTTPException
    from modules.flow_gate.settings.routers import project_settings as api
    from modules.flow_gate.settings import workspace_service
    from unittest.mock import Mock

    # Store boundary fake, exercising the real create handler and conflict rules.
    rows = {
        legacy: {"project_id": legacy, "project_name": "Saved legacy name", "is_active": 1},
        current: {"project_id": current, "project_name": "Occupied suggestion", "is_active": active},
    }
    before = {key: dict(value) for key, value in rows.items()}
    monkeypatch.setattr(workspace_service, "resolve_create_workspace", lambda *a: "ws_test")
    monkeypatch.setattr(workspace_service, "on_project_created", Mock())
    monkeypatch.setattr(api.projects_db, "get_by_name", lambda name: None)
    monkeypatch.setattr(api.projects_db, "get_by_id", rows.get)
    def create(data):
        assert data["project_id"] not in rows
        rows[data["project_id"]] = dict(data)
        return rows[data["project_id"]]
    writer = Mock(side_effect=create)
    monkeypatch.setattr(api.projects_db, "create", writer)
    suggested = romanize_to_slug(text)
    with pytest.raises(HTTPException) as exc:
        api.create_project_endpoint(api.ProjectCreate(project_name=text, project_id=suggested), user={})
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == ("project_id_conflict" if active else "project_archived_conflict")
    writer.assert_not_called()
    assert rows == before
    result = api.create_project_endpoint(
        api.ProjectCreate(project_name=text, project_id=suggested + "-2"), user={})
    assert result["project_id"] == current + "-2"
    assert {key: rows[key] for key in before} == before


def test_existing_legacy_id_is_not_regenerated(monkeypatch):
    from modules.flow_gate.settings.routers import project_settings as api
    row = {"project_id": "ri-ben-yu", "project_name": "日本語", "is_active": 1}
    monkeypatch.setattr(api.projects_db, "get_by_id", lambda key: dict(row) if key == row["project_id"] else None)
    monkeypatch.setattr(api.projects_db, "list_modules_bulk", lambda ids: {})
    assert api._project_state_response("ri-ben-yu")["project_id"] == "ri-ben-yu"
