"""Verify the card, tool and question help assets through the shared loader."""
import pytest

from modules.flow_gate.services import question_policy, tool_registry
from modules.flow_gate.services.help import loader, render
from modules.flow_gate.services.help.tree import GUIDE_TYPES


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_cards_area_has_complete_assets(locale):
    names = {
        name
        for name, spec in loader.manifest()["items"].items()
        if spec["owner"] == 3 and spec["kind"] != "area" and not spec.get("dynamic")
    }
    names.update(f"source_tools/{name}" for name in tool_registry.DISPLAY_ORDER)
    names.update(f"authoring_guide/{code}" for code in GUIDE_TYPES)
    for name in sorted(names):
        item, resolved, fallback = loader.load_item(name, locale)
        assert item and resolved == locale and not fallback, name


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_user_policy_appears_only_in_its_guide(locale, monkeypatch):
    original = "사용자가 적은 방침 {{keep_original}}"
    monkeypatch.setattr(question_policy, "text", lambda *_args, **_kwargs: original)
    ctx = {
        "principal_kind": "worker",
        "locale": locale,
        "action_scope": "edit",
        "base_url": "/flowgate/api/v1",
    }
    items, unavailable = render.build_bulk(["notices", "question/policy"], ctx)
    assert not unavailable
    notices, policy = items
    assert "question/policy" in str(notices["content"]["body"])
    assert original not in str(notices["content"])
    assert policy["content"]["body"]["original"] == original
    assert policy["original_fields"] == ["/body/original"]
    assert all(item["language"]["resolved_locale"] == locale for item in items)


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_submission_help_preserves_test_context_and_worker_routes(locale, monkeypatch):
    from modules.flow_gate.services import test_command_service

    command = "python3 -m pytest {{literal}}"
    monkeypatch.setattr(test_command_service, "build_verified_commands_block", lambda project: command)
    monkeypatch.setattr(test_command_service, "current_os", lambda: "posix")
    monkeypatch.setattr(test_command_service, "current_shell", lambda: "/bin/sh")
    base = "/flowgate/api/v1"
    ctx = {
        "principal_kind": "worker",
        "locale": locale,
        "action_scope": "edit",
        "doc_type": "TS",
        "project": "flowgate",
        "base_url": base,
    }
    commands = render.build_item("submit/test_commands", ctx)
    response = commands["content"]["response"]
    assert response["original"] == command
    assert commands["original_fields"] == ["/response/original"]
    assert response["host_os"] == "posix"
    assert response["shell"] == "/bin/sh"
    assert response["engine_recipe_route"] == {
        "method": "GET", "url": base + "/test-commands/help",
    }
    assert response["engine_recipe_details"]

    workflow = render.build_item("submit/format", ctx)["content"]["request"]["workflow"]
    assert workflow["decision"] == {"method": "POST", "url": base + "/workflow/decide"}
    assert workflow["sequence_update"] == {"method": "PATCH", "url": base + "/workflow/sequence"}


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_question_registration_route_matches_legacy_contract(locale):
    from modules.flow_gate.services import help_catalog

    ctx = {
        "principal_kind": "worker",
        "locale": locale,
        "action_scope": "edit",
        "base_url": "/flowgate/api/v1",
    }
    current = render.build_item("question/register", ctx)["content"]["request"]["route"]
    legacy = help_catalog.build_question_content(locale)["example"]
    assert current == {key: legacy[key] for key in ("method", "url")}


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize(("name", "legacy_name"), [
    ("cards/place", "chat_control"),
    ("cards/recovery", "chat_recovery"),
    ("cards/questions", "chat_questions"),
])
def test_card_help_primary_route_matches_legacy_contract(locale, name, legacy_name):
    from modules.flow_gate.services import chat_help_content

    ctx = {
        "principal_kind": "worker",
        "locale": locale,
        "action_scope": "chat",
        "base_url": "/flowgate/api/v1",
    }
    route = render.build_item(name, ctx)["content"]["request"]["route"]
    sections = chat_help_content.content(legacy_name, ctx)["sections"]
    assert any(f'{route["method"]} {route["url"]}' in section for section in sections)
