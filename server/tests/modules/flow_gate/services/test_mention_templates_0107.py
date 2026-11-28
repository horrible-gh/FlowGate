"""Short migration checks; no deployment activation and no real credentials."""
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modules.flow_gate.services import mention_service, terminal_prompt as terminal
from modules.flow_gate.services.help import loader
from modules.flow_gate.services.prompt_blocks import mention, mention_transition
from modules.flow_gate.services.prompt_blocks.compose import compose
from modules.flow_gate.services.prompt_blocks.matrix import MentionFacts, select_mention, transition_leaves


@pytest.mark.parametrize("mode", mention.MODES)
@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_disabled_is_byte_identical(mode, locale):
    kind = "chat" if mode.startswith("chat.") else "first_write"
    legacy = compose(["opaque {doc_id}", "資料"], kind=kind, locale=locale)
    result = mention.render_mention(legacy, facts=MentionFacts(mode, kind), values={}, context={}, locale=locale)
    assert result.text == legacy
    assert result.fallback_reason == "switch_disabled"

@pytest.mark.parametrize("mode", mention.MODES)
@pytest.mark.parametrize("locale", ("ko", "en", "ja"))
def test_all_assets_ready(mode, locale):
    asset, applied, reasons = loader.load_mention(mode, locale)
    assert asset is not None and applied == locale and not reasons


@pytest.mark.parametrize("mode", mention.MODES)
@pytest.mark.parametrize("locale", ("ko", "en", "ja"))
def test_asset_contract(mode, locale):
    asset, applied, reasons = loader.load_mention(mode, locale)
    assert applied == locale and not reasons
    body = asset["body"]
    assert body.startswith("{{task_block}}\n")
    assert body.count("{{task_block}}") == 1
    assert body.endswith("{{materials}}")
    spec = loader.definition("mention/" + mode)
    routes = loader.read_json("routes")
    values = {name: "value-" + name for name in spec["required"]}
    values["base_url"] = "https://example.invalid/api/v1"
    values["materials"] = "{{token}} 原文"
    text = mention.render_body(body, spec, values, routes)
    assert text.startswith(values["task_block"] + "\n")
    assert text.endswith(values["materials"])
    assert values["doc_id"] in text
    assert "GET https://example.invalid/api/v1/help" in text
    for name in spec["required"]:
        with pytest.raises(loader.HelpContentError, match="missing_values:" + name):
            mention.render_body(body, spec, {k: v for k, v in values.items() if k != name}, routes)


@pytest.mark.parametrize("mode,tool_kind", (("card.read", "read"), ("card.edit", "read_write")))
def test_card_builder_provider_materials_and_locale_fallback(monkeypatch, mode, tool_kind):
    from modules.flow_gate.db import groups
    from modules.flow_gate.services import encoding_probe, tr_self_check_quota
    from modules.flow_gate.services.help import visibility

    context = {"tool_kind": tool_kind, "group_id": "flowgate.v02test.0107"}
    monkeypatch.setattr(visibility, "resolve_context", lambda token, locale, base: context)
    monkeypatch.setattr(visibility, "decide_visibility", lambda *args: SimpleNamespace(visible=False))
    monkeypatch.setattr(groups, "get_by_id", lambda *args: {"title": "Flow"})
    monkeypatch.setattr(encoding_probe, "probe_for", lambda token_id: "probe-" + token_id)
    monkeypatch.setattr(tr_self_check_quota, "token_notice", lambda token, lang: "check-" + lang)
    monkeypatch.setattr(mention_service, "_first_card_names", lambda *args: "")
    read_json = loader.read_json
    rollout = {}
    monkeypatch.setattr(loader, "read_json", lambda name, root=None:
                        rollout if name == "mention_rollout" else read_json(name, root))
    token = {"token_id": "test-id", "action_scope": "edit", "doc_ref": "doc-123"}
    legacy = compose("legacy body", kind="rework", locale="en")

    def build_input():
        return mention_service._card_mention_input(
            token, "test-token", "https://example.invalid/api/v1", "en", "rework", "TR",
            "doc-123", "TR", "Original document", "edit", ["ref-456"], 0,
            "rejected", "rejection_id=test-rejection", None, False, False, None,
            {"reason": "original {{token}} reason"}, None, [],
        )

    assert build_input() is None
    rollout[mode] = {"enabled": True}
    data = build_input()
    assert data["context"] is context
    assert data["facts"].mode == mode
    assert data["values"]["expected_revision"] == 0
    assert data["values"]["probe"] == "probe-test-id"
    def select(facts, ctx, locale):
        assert ctx is context
        return (*select_mention(facts), "ko")
    monkeypatch.setattr(mention, "help_selection", select)
    assert mention.render_mention(legacy, **data, locale="en").text == legacy
    first, more = select_mention(data["facts"])
    rollout[mode]["verification"] = {
        "mode": mode, "token_mode": mode, "checked_at": "now", "passed": True,
        "items": {name: {"visible": True, "response": True, "context": True}
                  for name in set(first + more + transition_leaves(mode))},
    }
    result = mention.render_mention(legacy, **data, locale="en")
    assert result.fallback_reason is None
    assert result.locale == "ko" and result.language_reasons
    _, task = terminal.split(result.text)
    decorated = mention_service.add_card_materials(task, "Start-time {{workspace}} material")
    prompt = terminal.finalize(mention.provider_prompt(decorated, "provider-workspace"))
    assert prompt.startswith("## 지금 할 일\n")
    assert "provider-workspace" in prompt
    assert "doc-123" in prompt and "ref-456" in prompt
    assert ("check-ko" in prompt) == (mode == "card.edit")
    assert prompt.endswith("Start-time {{workspace}} material\n\n## 반려 사유\n---\noriginal {{token}} reason")


def test_optional_lines_zero_and_opaque_original():
    spec = {"required": ["doc_id"], "placeholders": {"doc_id": "neutral", "revision_no": "neutral", "materials": "original", "workspace": "system"}}
    text = mention.render_body("id={{doc_id}}\nrevision={{revision_no}}\nworkspace={{workspace}}\n{{materials}}", spec,
                               {"doc_id": "D", "revision_no": 0, "materials": "{{workspace}} 사용자 원문", "base_url": "base"}, {})
    assert text == "id=D\nrevision=0\n{{workspace}} 사용자 원문"
    with pytest.raises(loader.HelpContentError, match="missing_values"):
        mention.render_body("{{doc_id}}", spec, {"base_url": "base"}, {})


def test_new_selection_preserves_rework_and_priority():
    facts = MentionFacts("card.edit", "rework", "TR", attachment_request=True)
    first, more = select_mention(facts)
    assert first[:3] == ("notices", "submit/format", "submit/changed_files")
    assert {"attachments/read", "attachments/upload_request", "question/register", "question/precheck", "submit/self_check"} <= set(more)
    assert "conversation/commands" not in transition_leaves("chat.read")
    assert "conversation/commands" in transition_leaves("chat.execute")
    assert "cards/recovery" in transition_leaves("chat.edit_once")


@pytest.fixture
def ready(monkeypatch):
    mode = "chat.read"
    spec = loader.definition("mention/" + mode)
    routes = loader.read_json("routes")
    rows = {name: {"visible": True, "response": True, "context": True} for name in transition_leaves(mode)}
    state = {mode: {"enabled": True, "verification": {"mode": mode, "token_mode": mode, "checked_at": "now", "passed": True, "items": rows}}}
    asset = {"body": "{{task_block}}\nBearer {{token}}\n{{flow_name}}\n{{doc_id}}\n{{workspace}}\n{{route:help.index}}\n{{help_first}}\n{{help_more}}\n{{materials}}"}
    def read(name, root=None):
        return state if name == "mention_rollout" else routes if name == "routes" else asset
    monkeypatch.setattr(loader, "read_json", read)
    monkeypatch.setattr(loader, "load_mention", lambda mode, loc, root=None: (asset, loc, []))
    monkeypatch.setattr(loader, "definition", lambda name, root=None: spec)
    monkeypatch.setattr(mention, "help_selection", lambda facts, context, locale: (*select_mention(facts), locale))
    values = {"token": "test-token", "base_url": "https://example.invalid/api/v1", "flow_id": "flow", "flow_name": "F1(원문)", "doc_id": "CH", "probe": "probe", "after_seq": 0, "idempotency_key": "key"}
    def build(locale="en", localized=None):
        legacy = compose("legacy unchanged", kind="chat", locale=locale)
        return mention.render_mention(legacy, facts=MentionFacts(mode), values=values, context={}, locale=locale,
                                      localized_values=localized if localized is not None else {"en": {"materials": "{{token}} 原文"}, "ko": {"materials": "原文"}})
    return build, state, asset


def test_render_provider_and_api_task_at_top(ready):
    build, _, _ = ready
    result = build()
    assert result.fallback_reason is None
    assert result.locale == "en"
    prompt = mention.provider_prompt(result.text, "workspace-path")
    delivered = terminal.finalize(prompt, locale="ja")
    assert delivered.startswith("## What to do now\n")
    assert "workspace-path" in delivered
    assert "{{token}} 原文" in delivered
    assert "flowgate-terminal-task" not in delivered
    assert terminal.finalize(delivered).startswith("## What to do now\n")
    messages = [{"role": "user", "content": mention.provider_prompt(result.text)}, {"role": "user", "content": "conversation/hints"}]
    task = terminal.finalize_messages(messages, locale="ja")
    assert messages[0]["content"].startswith("## What to do now\n")
    assert messages[1]["content"] == "conversation/hints"
    assert terminal.finalize_nudge("retry", task).startswith("retry\n\n## What to do now")
    assert result.values["after_seq"] == 0


def test_fallback_language_and_gates(ready, monkeypatch):
    build, state, asset = ready
    result = build(localized={"ko": {"materials": "원문"}})
    assert result.locale == "ko" and result.language_reasons
    assert terminal.finalize(result.text).startswith("## 지금 할 일\n")
    state["chat.read"]["verification"]["token_mode"] = "chat.edit"
    assert build().fallback_reason == "verification_missing_or_failed"
    state["chat.read"]["verification"]["token_mode"] = "chat.read"
    asset["status"] = "pending"
    assert build().fallback_reason.startswith("template_pending")
    asset.pop("status")
    def fail(*args):
        raise loader.HelpContentError("unavailable_help:documents/list")
    monkeypatch.setattr(mention, "help_selection", fail)
    result = build()
    assert result.fallback_reason == "unavailable_help:documents/list"
    assert "legacy unchanged" in result.text


def test_transition_rejects_context_and_missing_mode_coverage(monkeypatch):
    requirements = {name: ["rule"] for name in transition_leaves("card.edit")}
    payload = {"ok": True, "content": {"rule": "rule", "url": "/q/{doc_id}/questions"}}
    monkeypatch.setattr(mention_transition, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    record = mention_transition.verify_transition("card.edit", "card.edit", "https://example.invalid", "secret", requirements)
    assert not record["passed"]
    assert all(not row["context"] for row in record["items"].values())
    assert "secret" not in json.dumps(record)
    payload["content"]["url"] = "/q/actual/questions"
    assert mention_transition.verify_transition("card.edit", "card.edit", "https://example.invalid", "secret", requirements)["passed"]
    assert mention_transition.verify_transition("card.edit", "card.edit", "base", "secret", {"notices": ["rule"]})["reason"] == "incomplete_mode_coverage"
    assert not mention_transition.verify_transition("card.read", "card.edit", "base", "secret", requirements)["passed"]


@pytest.mark.parametrize("optional", ["source_tools", "cards/ai_options"])
def test_unavailable_optional_name_is_omitted(monkeypatch, optional):
    from modules.flow_gate.services.help import render
    monkeypatch.setattr(render, "build_bulk", lambda names, ctx: ([], [{"name": optional}]))
    first, more, locale = mention.help_selection(MentionFacts("chat.read"), {}, "ko")
    assert optional not in first + more
    assert "notices" in first and "attachments/link" in more
    assert locale == "ko"


@pytest.mark.parametrize("required", ["notices", "attachments/link", "conversation/read"])
def test_unavailable_migrated_name_falls_back(monkeypatch, required):
    from modules.flow_gate.services.help import render
    monkeypatch.setattr(render, "build_bulk", lambda names, ctx: ([], [{"name": required}]))
    with pytest.raises(loader.HelpContentError, match="unavailable_help:" + required):
        mention.help_selection(MentionFacts("chat.read"), {}, "ko")


def test_render_checks_leaf_contract_and_omits_optional_bad_context(monkeypatch):
    from modules.flow_gate.services.help import render
    payload = {"name": "question", "child": "register", "content": {"url": "{node_key}"}}
    monkeypatch.setattr(render, "build_bulk", lambda names, ctx: ([payload], []))
    with pytest.raises(loader.HelpContentError, match="unresolved_help_context:question/register"):
        mention.help_selection(MentionFacts("card.edit", "rework", "TR"), {}, "ko")
    payload.update(name="cards", child="ai_options")
    first, more, _ = mention.help_selection(MentionFacts("chat.read"), {}, "ko")
    assert "cards/ai_options" not in first + more


def test_transition_decoded_values_and_manifest_context(monkeypatch):
    fragment = 'quoted "rule"\npath\\next'
    requirements = {name: [fragment] for name in transition_leaves("card.edit")}
    payload = {"ok": True, "content": {"nested": [{"text": fragment}]}}
    monkeypatch.setattr(mention_transition, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    def verify():
        return mention_transition.verify_transition("card.edit", "card.edit", "https://example.invalid", "secret", requirements)
    assert verify()["passed"]
    spec = loader.definition("question/register")
    for name, kind in spec["placeholders"].items():
        if kind == "original":
            continue
        for placeholder in ("{" + name + "}", "{{" + name + "}}"):
            payload["content"]["context"] = placeholder
            assert not verify()["items"]["question/register"]["context"]
    payload["content"] = {fragment: "unrelated text"}
    assert not verify()["items"]["question/register"]["response"]
