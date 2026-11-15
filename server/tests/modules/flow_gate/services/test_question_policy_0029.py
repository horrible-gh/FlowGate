import pytest

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("policy", ["User policy", ""])
def test_question_help_policy_and_search(monkeypatch, locale, policy):
    from modules.flow_gate.services import help_catalog, question_policy
    seen = []
    monkeypatch.setattr(question_policy, "text", lambda user, group, **kw: seen.append((user, group, kw)) or policy)
    ctx = {"token_rec": {"issued_to": "u"}, "group_id": "g", "action_scope": "edit"}
    result = help_catalog.build_question_content(locale, ctx)
    assert seen == [("u", "g", {"card": True})]
    assert bool(result.get("question_policy")) is bool(policy)
    assert result["before_asking"]
    assert result["search"]["url"] == "/flowgate/api/v1/q/{doc_id}/search?q=<keyword>&limit=10"


def test_chat_question_exposes_responder_error():
    from flowgate.card_document_edit.chat_questions import _item_view
    result = _item_view({"id": 1, "responder_state": "failed", "responder_error_code": "no_enabled_provider"}, False)
    assert result["responder_error_code"] == "no_enabled_provider"
    assert _item_view({"id": 2}, False)["responder_error_code"] is None
