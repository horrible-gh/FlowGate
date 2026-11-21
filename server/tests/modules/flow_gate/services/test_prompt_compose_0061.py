"""Selection facts, help authorization parity and retained invocation contracts."""
import re
import pytest
from test_prompt_size_baseline_0061 import (HELP_PERSONAS, TOKEN, BASE, _help_client,
                                           CASES, env, hooks, mention_context)
from modules.flow_gate.services.prompt_blocks.compose import guidance, compose
from modules.flow_gate.services.prompt_blocks.matrix import Facts, select
from modules.flow_gate.services import terminal_prompt, encoding_probe
from modules.flow_gate.services.prompt_blocks.blocks.conversation import conversation_turn_brief


@pytest.mark.parametrize("persona,kind", [("chat_read", "chat"), ("chat_edit", "chat"),
    ("edit_TR", "edit"), ("review_TR", "review"), ("new_T", "new")])
def test_selected_links_open_for_call_token(monkeypatch, persona, kind):
    fields, step = HELP_PERSONAS[persona]
    token = dict(TOKEN, doc_ref="p.m.0001.0004-TR", **fields)
    client = _help_client(monkeypatch, token, step)
    text = guidance(BASE, "token", kind=kind, can_write=persona == "chat_edit",
                    unanswered=True, current_run=True)
    for leaf in re.findall(r"/help/items/([a-z_/]+)", text):
        response = client.get("/api/v1/help/items/" + leaf)
        assert response.status_code == 200, (persona, leaf, response.text)
        assert response.json().get("form") == "content", (persona, leaf)


def test_read_and_known_empty_states_drop_optional_blocks():
    leaves = select(Facts("chat", False, False, False))
    assert not set(leaves) & {"documents/edit", "cards/place", "cards/roles", "cards/run",
                              "cards/questions", "conversation/recovery"}
    unknown = select(Facts("chat"))
    assert "cards/questions" in unknown and "conversation/recovery" in unknown
    assert "documents/unreadable" not in select(Facts("failure_origin"))


def test_compose_preserves_values_policy_and_terminal_record():
    value = "token=private; idempotency_key=unique; policy=unchanged"
    text = compose([value], kind="edit", locale="ko")
    body, spec = terminal_prompt.split(text)
    assert body == value
    assert spec["kind"] == "edit"


def test_brief_keeps_probe_payload_and_preview():
    payload = ['{"idempotency_key":"unique","based_on_seq":7,"body":"reply"}']
    lines = conversation_turn_brief(api_base=BASE, doc_id="doc", raw_token="secret",
        token_id="invocation", payload_lines=payload, can_write=False, provider=None)
    assert payload[0] in lines
    assert encoding_probe.probe_for("invocation") in lines
    assert "dry_run" in "\n".join(lines)


@pytest.mark.parametrize("case_id", [c[0] for c in CASES if c[0].startswith(("P1.", "P2.", "P5.", "P6.", "P9."))])
def test_built_prompts_only_point_at_help_leaves(env, case_id):
    build = next(c[3] for c in CASES if c[0] == case_id)
    text = build(env)
    # The source tool detail is /help/tools/{name}, not a catalog to read first.
    assert not re.search(r"first action.*help/tools", text, re.I)
    assert "/help?detail=true" not in text
