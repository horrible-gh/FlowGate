"""Bounded regression checks for the file-first help migration."""
import json
import shutil
import pytest
from modules.flow_gate.services.help import loader, render, visibility
from modules.flow_gate.services.help.fill import fill

def ctx(locale="ko", scope="chat"):
    return {"principal_kind":"worker", "project":"flowgate", "group_id":"flowgate.v02test.0107",
            "doc_id":"flowgate.v02test.0107.0006-TR", "doc_type":"TR", "action_scope":scope,
            "tool_kind":"read_write", "source_mode":"remote", "reason":None, "locale":locale,
            "base_url":"/flowgate/api/v1", "registry":{"kind":"read_write", "tools":[], "notes":[]},
            "continuous":False, "token_rec":{}}

@pytest.fixture(autouse=True)
def policy(monkeypatch):
    from modules.flow_gate.services import question_policy
    monkeypatch.setattr(question_policy, "text", lambda *a, **k: "")

def test_manifest_and_all_assets():
    assert loader.validate_catalog()
    items = loader.manifest()["items"]
    assert len([name for name, spec in items.items() if "/" in name and not spec.get("dynamic") and not spec.get("retained")]) == 35

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_example_route_resolution(locale):
    result = render.build_child("cards", "state", ctx(locale))
    assert result["language"]["resolved_locale"] == locale
    assert result["content"]["request"]["canvas"] == "GET /flowgate/api/v2/ai/flows/flowgate.v02test.0107/canvas"
    assert "{{" not in json.dumps(result)

def test_whole_item_fallback(tmp_path):
    shutil.copytree(loader.ROOT, tmp_path / "help")
    root = tmp_path / "help"
    (root / "en/cards/state.json").unlink()
    item, resolved, reasons = loader.load_item("cards/state", "en", root)
    assert resolved == "ko" and item["locale"] == "ko" and reasons
    (root / "ko/cards/state.json").unlink()
    with pytest.raises(loader.HelpContentError, match="missing_korean"):
        loader.load_item("cards/state", "en", root)

def test_response_wide_fallback(tmp_path, monkeypatch):
    shutil.copytree(loader.ROOT, tmp_path / "help")
    monkeypatch.setattr(loader, "ROOT", tmp_path / "help")
    (loader.ROOT / "en/cards/state.json").unlink()
    items, unavailable = render.build_bulk(["cards/state", "attachments/link"], ctx("en"))
    assert not unavailable
    assert all(item["language"]["resolved_locale"] == "ko" for item in items)

def test_bad_language_and_unknown_reference():
    spec = loader.definition("cards/state")
    item, _, _ = loader.load_item("cards/state", "en")
    item["purpose"] = "한글 문장"
    with pytest.raises(loader.HelpContentError, match="mixed_language"):
        loader.validate(item, spec, "en", loader.read_json("routes"))
    item["purpose"] = "{{missing}}"
    with pytest.raises(loader.HelpContentError, match="unknown_placeholder"):
        loader.validate(item, spec, "en", loader.read_json("routes"))
    item["purpose"] = "{{route:missing}}"
    with pytest.raises(loader.HelpContentError, match="unknown_route"):
        loader.validate(item, spec, "en", loader.read_json("routes"))

def test_original_values_are_not_reinterpreted():
    value, originals = fill({"body":"{{policy}}"}, {"placeholders":{"policy":"original"}},
                            {"policy":"{{route:not_a_route}}"}, {}, "/api/v1")
    assert value == {"body":"{{route:not_a_route}}"}
    assert originals == ["/body"]

@pytest.mark.parametrize("context_value", [
    {"enabled": True, "text": "한글 {{route:unknown}}", "empty": None},
    ["pytest", {"ok": False}], True, False, None, 42,
])
def test_inline_context_uses_json_and_whole_field_preserves_type(context_value):
    spec = {"placeholders": {"question_policy": "original"}}
    result, originals = fill(
        {"whole": "{{question_policy}}", "inline": "value={{question_policy}}"},
        spec, {"question_policy": context_value}, {}, "/api/v1")
    assert result["whole"] == context_value
    assert type(result["whole"]) is type(context_value)
    assert json.loads(result["inline"].removeprefix("value=")) == context_value
    assert result["inline"] == "value=" + json.dumps(context_value, ensure_ascii=False)
    assert originals == ["/whole", "/inline"]


def test_inline_text_and_missing_context_are_single_pass():
    result, _ = fill("before {{test_commands}} after {{missing}}",
        {"placeholders": {"test_commands": "original", "missing": "context"}},
        {"test_commands": "{{route:unknown}}"}, {}, "/api/v1")
    assert result == "before {{route:unknown}} after {missing}"


def test_alias_visibility_and_removed_flat_names():
    for alias, canonical in loader.read_json("aliases").items():
        for scope in ("chat", "edit", "review"):
            assert visibility.decide_visibility(alias, ctx(scope=scope)) == visibility.decide_visibility(canonical, ctx(scope=scope))
    assert visibility.decide_visibility("chat_control", ctx()).reason == "unknown_item"
    assert visibility.decide_visibility("cards/state", ctx(scope="edit")).reason == "not_chat"

def test_retained_items_are_hidden_but_still_readable():
    children = render.enumerate_children("documents", ctx())
    assert not {"doc_type", "group_list"} & {row["name"] for row in children}
    assert visibility.decide_visibility("documents/doc_type", ctx()).visible
    assert visibility.decide_visibility("documents/group_list", ctx()).visible

def test_console_is_independent_of_area_packages(monkeypatch):
    from modules.flow_gate.services.help.legacy.compat import render as old
    user = dict(ctx(), principal_kind="user_session")
    assert render.build_index(user) == old.build_index(user)
    monkeypatch.setattr(render.context, "supplier", lambda owner: pytest.fail("area supplier imported"))
    assert render.build_item("document_access", user) == old.build_item("document_access", user)
