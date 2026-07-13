"""Group 0223 — parallel [AI 호출] beside every [멘트복사].

Covers the server-side ports of the client-only mention builders
(invoke_mention_service) and the ai-invoke route's wire-scope → token-scope
mapping. The builders must stay byte-identical to their client counterparts
(useFlowGateToken.ts / mentionMessages.ts / i18n templates) — these tests pin
the exact text shapes so a drift on either side goes RED.
"""
from __future__ import annotations

import json

from modules.flow_gate.api.v1.ai_invoke_routes import _ALLOWED_SCOPES, _TOKEN_SCOPE
from modules.flow_gate.services import invoke_mention_service as ims


class TestScopeMap:
    def test_all_wire_scopes_allowed(self):
        for scope in ("new", "edit", "workflow_decide", "chat", "rework", "review",
                      "vr_correction", "next_step_message", "design_handoff"):
            assert scope in _ALLOWED_SCOPES

    def test_extra_scopes_map_to_inbox_honoured_token_scopes(self):
        # The inbox only honours new/edit/review/workflow_decide grants; every
        # extra invoke scope must ride one of those.
        assert _TOKEN_SCOPE["chat"] == "edit"
        assert _TOKEN_SCOPE["rework"] == "edit"
        assert _TOKEN_SCOPE["vr_correction"] == "edit"
        assert _TOKEN_SCOPE["next_step_message"] == "new"
        assert _TOKEN_SCOPE["design_handoff"] == "new"
        assert "review" not in _TOKEN_SCOPE  # issue_builder path (request_review)


class TestConversationMention:
    def test_matches_client_shape(self):
        text = ims.build_conversation_mention(
            doc_id="p.default.0001.0008-CH",
            project="p",
            module="default",
            group_name="p.default.0001",
            raw_token="RAW",
            api_base_url="http://h:1/api/v1",
        )
        lines = text.split("\n")
        assert lines[0] == "## Conversation (대화)"
        assert "Conversation document: p.default.0001.0008-CH" in lines
        assert "Read the full conversation: GET http://h:1/api/v1/document?doc_id=p.default.0001.0008-CH" in lines
        assert lines.count("Authorization: Bearer RAW") == 2
        assert "Submit: POST http://h:1/api/v1/inbox" in lines
        body = json.loads(text[text.index("{"):])
        assert body["action"] == "edit"
        assert body["edit_reason"] == "worker_self"
        assert body["module"] == "default"
        assert body["doc_id"] == "p.default.0001.0008-CH"

    def test_module_omitted_when_none(self):
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://h:1/api/v1",
        )
        body = json.loads(text[text.index("{"):])
        assert "module" not in body


class TestRejectionSection:
    def test_empty_when_no_context(self):
        assert ims.build_rejection_section([], None) == ""

    def test_last_reason_only(self):
        text = ims.build_rejection_section(
            [{"rejected_at": "t1", "reason": "fix A"}], "fix A")
        assert "## Revision Request" in text
        assert "### Last rejection reason (apply first on rework)" in text
        assert "fix A" in text
        # A single rejection has no PRIOR entries → history block omitted
        # (client parity: the final history entry always duplicates `last`).
        assert "Prior rejection history" not in text

    def test_prior_history_listed_chronologically(self):
        text = ims.build_rejection_section(
            [{"rejected_at": "t1", "reason": "old"},
             {"rejected_at": "t2", "reason": "new"}], "new")
        assert "### Prior rejection history (1 item, chronological)" in text
        assert "1. [t1] old" in text
        assert text.count("new") == 1  # latest reason printed once, not twice


class TestMessagesSection:
    def test_prepends_localized_header(self):
        out = ims.prepend_messages_section("MENTION", ["hello"], "ko")
        assert out == "## 사용자 메세지\n---\nhello\n\nMENTION"

    def test_blank_messages_leave_mention_untouched(self):
        assert ims.prepend_messages_section("M", ["  ", ""], "en") == "M"

    def test_multiple_messages_joined_with_blank_line(self):
        out = ims.prepend_messages_section("M", ["a", "b"], "en")
        assert out.startswith("## User message\n---\na\n\nb")


class TestTemplates:
    def test_reject_context_locales(self):
        ko = ims.build_reject_context("D0001", "이유", "ko")
        assert ko == "## 반려\nD0001 가 반려되었습니다.\n사유: 이유"
        en = ims.build_reject_context("D0001", "why", "en")
        assert en.startswith("## Rejected")
        # Unknown locale folds to ko
        assert ims.build_reject_context("D", "r", "xx").startswith("## 반려")

    def test_design_handoff_batch_and_single(self):
        batch = ims.build_design_handoff_context(
            types=["D", "P"], mode="batch", doc_ref="p.g.0001-R", locale="ko")
        assert "D / P" in batch and "p.g.0001-R" in batch
        single = ims.build_design_handoff_context(
            types=["DB"], mode="single", doc_ref="p.g.0001-R", locale="en",
            first_label="Database Design")
        assert "Database Design(DB)" in single
