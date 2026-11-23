"""AI-facing text names Flows and cards by number and name (v02test.0077, AI 안내문 card).

The user asked: "너희가 그룹번호를 부르는데 난 어딘지 몰라" -- so the chat AI is told to say ``F68(깃)`` and
``F77-12(card title)`` instead of ``0068`` or a document id, and the prompts that describe a Flow carry the
new display ids next to the stored ids (API calls still need those).
"""
from __future__ import annotations

import pytest

from modules.flow_gate.services import display_names, invoke_mention_service, mention_service
from modules.flow_gate.services import prompt_selection as selection
from modules.flow_gate.services.prompt_blocks.blocks import cards
from test_prompt_selection_0049 import hooks  # noqa: F401 -- fixture used by name

BASE = "http://h/flowgate/api/v1"


# ── pure wording ─────────────────────────────────────────────────────────────

def test_flow_name_is_number_and_name_together():
    assert display_names.flow_name("flowgate.v02test.0068", "깃") == "F68(깃)"
    assert display_names.flow_name("flowgate.v02test.0077", "  I\nD ") == "F77(I D)"   # one line
    assert display_names.flow_name("flowgate.v02test.10000", "x") == "F10000(x)"
    assert display_names.flow_name("flowgate.v02test.0077", None) == "F77"            # no name: number only
    assert display_names.flow_name("not-a-group", "x") is None


def test_card_name_uses_the_short_card_id_and_title():
    ref = {"card_no": 12, "local": "#12", "short": "F77-12", "module_ref": "v02test.F77-12",
           "full": "flowgate.v02test.F77-12"}
    assert display_names.card_name(ref, "사이드바") == "F77-12(사이드바)"
    assert display_names.card_name(ref, "") == "F77-12"
    assert display_names.card_name(None, "unnumbered") is None


def test_bound_flow_line_keeps_the_stored_id_and_adds_display_ids():
    line = display_names.bound_flow_line("flowgate.v02test.0068", "깃")
    assert line == "Bound flow_id: flowgate.v02test.0068 = F68(깃) (full id flowgate.v02test.F68)."
    assert display_names.bound_flow_line("odd", "x") == "Bound flow_id: odd."


def test_naming_rule_says_number_with_name_and_keeps_api_ids():
    rule = display_names.NAMING_RULE
    assert "F68(깃)" in rule and "F77-12(card title)" in rule
    assert "0068" in rule and "document id alone" in rule
    assert "node_key" in rule and "doc_id" in rule


# ── where the AI reads it ────────────────────────────────────────────────────

def _chat(monkeypatch, title):
    monkeypatch.setattr(invoke_mention_service.chat_settings_service, "resolve_chat_settings_safe", lambda user: {})
    monkeypatch.setattr(invoke_mention_service.chat_doc_settings_service, "context_window",
                        lambda *a, **kw: {"after_seq": 0, "folded": 0})
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)
    from modules.flow_gate.services import question_policy
    monkeypatch.setattr(question_policy, "text", lambda user: "")
    monkeypatch.setattr(selection, "chat_situation", lambda *a, **kw: selection.ChatSituation(False, False, False))
    monkeypatch.setattr(invoke_mention_service, "_group_title", lambda group_id: title)
    return invoke_mention_service.build_conversation_mention(
        doc_id="flowgate.v02test.0068.0002-CH", project="flowgate", module="v02test",
        group_name="flowgate.v02test.0068", raw_token="test-token", token_id="test-id", api_base_url=BASE,
        source_access="read", command_execution=True)


def test_chat_mention_names_the_bound_flow_and_carries_the_naming_rule(hooks, monkeypatch):
    text = _chat(monkeypatch, "깃")
    head = text.split("\n\n")[0]
    assert "Bound flow_id: flowgate.v02test.0068 = F68(깃) (full id flowgate.v02test.F68)." in head
    assert display_names.NAMING_RULE in head
    assert "Read cards/state help to discover node keys when needed." in head
    assert "(flow_label F77, flow_name F77(name))" in text            # flow list lookup line
    assert "(also takes F77 / F77-12; answers flow_ref, flow_name)" in text
    assert f"{BASE}/list/groups/<group_id>" in text                      # stored id still the API argument


def test_chat_mention_survives_an_unreadable_flow_name(hooks, monkeypatch):
    text = _chat(monkeypatch, None)
    assert "Bound flow_id: flowgate.v02test.0068 = F68 (full id flowgate.v02test.F68)." in text


def test_group_title_never_breaks_the_mention(monkeypatch):
    from modules.flow_gate.db import groups as db_groups

    def boom(group_id):
        raise RuntimeError("no store")

    monkeypatch.setattr(db_groups, "get_by_id", boom)
    assert invoke_mention_service._group_title("flowgate.v02test.0068") is None
    monkeypatch.setattr(db_groups, "get_by_id", lambda group_id: {"title": "깃"})
    assert invoke_mention_service._group_title("flowgate.v02test.0068") == "깃"


def test_graph_state_read_explains_display_ids_next_to_stored_ids():
    text = cards.graph_state_read(BASE)
    assert text.startswith("[Bound Graph state read]\n")
    assert text.endswith(cards.DISPLAY_IDS)
    for field in ("flow_name", "card_ref", "card_name", "F77-12(card title)", "node_key", "doc_id"):
        assert field in cards.DISPLAY_IDS


def test_cards_state_help_leaf_names_by_flow_and_card_name():
    from modules.flow_gate.services.help.leaves import leaf_content

    body = leaf_content("cards", "state", {"base_url": BASE})
    assert "flow_name" in body["note"] and "card_name" in body["note"]
    assert "F77-12(card title)" in body["note"] and "flow_id/node_key/doc_id" in body["note"]
