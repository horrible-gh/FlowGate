"""flowgate.v02gui.0028: card AIs check the user's question policy and the past Q&A before they register a Q, and the
chat sees why an automatic answer failed.

Q624 / Q625 (0003-NR): the asking card raised questions the policy says the AI decides itself, and asked again for
something already refused, because the policy reached it only through the ``notices`` help item and the past Q&A
search only reached the answering AI. Every Q-registration guide (clarification, continuous, review phase) and the
``question`` help item now carry the same check-before-asking steps with the past Q&A search address; the chat
question list carries ``responder_error_code``.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_document_edit_0127 import env, F, D  # noqa: F401,E402
from test_card_document_chat_adapter_0127 import chat  # noqa: F401,E402
from test_chat_card_questions_v02gui_0010 import api, ask, q  # noqa: F401,E402
from flowgate.card_document_edit import chat_questions  # noqa: E402
from modules.flow_gate.db import question_items as db_question_items  # noqa: E402

BASE = "http://h/flowgate/api/v1"
ANCHOR = "flowgate.v02.0001.0002-T"
SEARCH = f"GET {BASE}/q/{ANCHOR}/search?q=<keyword>&limit=10"
HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")


# ── mention guides ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_clarification_guide_checks_policy_and_past_answers_before_the_post(locale):
    from modules.flow_gate.services import mention_service
    body = mention_service._clarification_guide_body(BASE, ANCHOR, "raw", locale)
    assert SEARCH in body
    assert body.index(f"POST {BASE}/q/{ANCHOR}/questions") < body.index(SEARCH)
    assert "notices" in body
    if locale != "ko":
        assert not HANGUL.search(body)


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_clarification_guide_leaves_register_time_details_to_the_help_leaves(locale):
    """flowgate.v02test.0061 card 7: most cards never register a Q, so the ordinary guide keeps the policy line and
    the past Q&A search address, and what is needed only while registering (reuse / 409 handling, list format,
    transport rule) is read from the help leaves the guide names. Nothing is lost: the leaves carry it."""
    import json
    from modules.flow_gate.services import ai_text_guard, help_catalog, mention_service
    body = mention_service._clarification_guide_body(BASE, ANCHOR, "raw", locale)
    rule = ai_text_guard.transport_rule(locale)
    for moved in ("existing_answer_requires_reason", "next_cursor", "rejected_answer_id", "<answer_id>", rule):
        assert moved not in body
    assert f"GET {BASE}/help/items/question/register" in body
    assert f"GET {BASE}/help/items/question/precheck" in body
    content = help_catalog.build_question_content(locale)
    assert content["transport"] == rule
    precheck = json.dumps(content["registration_precheck"])
    for kept in ("reused_answer", "existing_answer_requires_reason", "next_cursor", "rejected_answer_id",
                 "reuse_rejection_reason", "answer_preview", "/search/{answer_id}"):
        assert kept in precheck
    assert "options" in content["note"]                              # the Q&A panel sentence moved here
    # the full text stays for the guides whose task is to register a Q
    assert rule in mention_service._question_precheck(BASE, ANCHOR, locale)


def test_question_leaves_show_what_left_the_guide(monkeypatch):
    from modules.flow_gate.services import ai_text_guard, question_policy
    from modules.flow_gate.services.help import leaves
    monkeypatch.setattr(question_policy, "text", lambda *a, **k: "")
    ctx = {"base_url": "/flowgate/api/v1", "locale": "ko", "project": "flowgate", "group_id": "g1",
           "action_scope": "new", "token_rec": {"issued_to": "u1"}}
    register = leaves.leaf_content("question", "register", ctx)
    assert register["transport"] == ai_text_guard.transport_rule("ko")
    assert {"note", "options_requires", "example"} <= set(register)
    precheck = leaves.leaf_content("question", "precheck", ctx)
    assert {"before_asking", "registration_precheck", "search"} <= set(precheck)


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_review_phase_guide_carries_the_same_check(locale):
    from modules.flow_gate.services import mention_service
    body = mention_service._continuous_review_guide_body(BASE, ANCHOR, "raw", locale)
    assert SEARCH in body and "notices" in body


def test_precheck_copy_names_the_same_three_steps_in_every_locale():
    from modules.flow_gate.services import mention_service
    for locale in ("ko", "ja", "en"):
        text = mention_service._question_precheck(BASE, ANCHOR, locale)
        assert text.count("\n") >= 3 and "1)" in text and "2)" in text and "3)" in text
        assert SEARCH in text


# ── question help item ───────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_question_help_names_the_steps_and_the_search(locale):
    from modules.flow_gate.services import help_catalog
    content = help_catalog.build_question_content(locale)
    assert content["search"] == {"method": "GET", "url": "/flowgate/api/v1/q/{doc_id}/search?q=<keyword>&limit=10",
                                 "note": content["search"]["note"]}
    assert "notices" in content["before_asking"] and "search" in content["before_asking"]
    assert content["example"]["url"] == "/flowgate/api/v1/q/{doc_id}/questions"       # unchanged
    if locale != "ko":
        assert not HANGUL.search(content["before_asking"] + content["search"]["note"])


def test_question_item_carries_the_users_policy(monkeypatch):
    from modules.flow_gate.services import help_catalog, question_policy
    seen = {}

    def text(issued_to=None, group_id=None, *, card=False):
        seen.update(issued_to=issued_to, group_id=group_id, card=card)
        return "- 되돌릴 수 있는 작업은 모두 AI가 알아서 진행한다"

    monkeypatch.setattr(question_policy, "text", text)
    ctx = {"base_url": "/flowgate/api/v1", "locale": "ko", "project": "flowgate", "group_id": "g1",
           "action_scope": "new", "token_rec": {"issued_to": "u1"}}
    content = help_catalog._content_question(ctx)
    assert "되돌릴 수 있는 작업" in content["policy"] and "[사용자 질의 방침]" in content["policy"]
    assert seen == {"issued_to": "u1", "group_id": "g1", "card": True}
    assert content["answers"]["field"] == "answers"


def test_question_item_without_policy_or_with_a_lookup_failure_still_answers(monkeypatch):
    from modules.flow_gate.services import help_catalog, question_policy
    ctx = {"base_url": "/flowgate/api/v1", "locale": "en", "project": "flowgate", "group_id": "g1",
           "action_scope": "chat", "token_rec": {"issued_to": "u1"}}
    monkeypatch.setattr(question_policy, "text", lambda *a, **k: "")
    assert "policy" not in help_catalog._content_question(ctx)

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(question_policy, "text", boom)
    content = help_catalog._content_question(ctx)
    assert "policy" not in content and content["before_asking"]


# ── chat question list: why the automatic answer failed ──────────────────────

def test_chat_list_shows_the_responder_error_code(q):
    items = ask(D, ["Which DB?", "Which port?"])
    assert db_question_items.fail_responder(items[0]["id"], "no_enabled_provider", "internal detail")
    status, body = api(q, chat_questions.LIST_TOOL, {})
    assert status == 200, body
    first, second = body["documents"][0]["questions"]
    assert (first["responder_state"], first["responder_error_code"]) == ("failed", "no_enabled_provider")
    assert "internal detail" not in str(body)                       # the stored message never leaves the server
    assert "responder_error_code" not in second


def test_list_description_and_cli_mention_tell_the_chat_to_report_the_cause():
    assert "responder_error_code" in chat_questions.LIST_DESCRIPTION
    assert "responder_error_code" in chat_questions.cli_mention_section(BASE, "raw")
