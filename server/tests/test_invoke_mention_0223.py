"""Group 0223 — parallel [AI 호출] beside every [멘트복사].

Covers the server-side ports of the client-only mention builders
(invoke_mention_service) and the ai-invoke route's wire-scope → token-scope
mapping. The builders must stay byte-identical to their client counterparts
(useFlowGateToken.ts / mentionMessages.ts / i18n templates) — these tests pin
the exact text shapes so a drift on either side goes RED.
"""
from __future__ import annotations

import json

import pytest

from modules.flow_gate.api.v1.ai_invoke_routes import _ALLOWED_SCOPES, _TOKEN_SCOPE
from modules.flow_gate.services import invoke_mention_service as ims


def _submission_block(text: str) -> str:
    """Return the append instruction, whose based_on_seq placeholder is pseudo-JSON."""
    return text[text.index("Submit: POST"):]


class TestScopeMap:
    def test_all_wire_scopes_allowed(self):
        for scope in ("new", "edit", "workflow_decide", "chat", "rework", "review",
                      "vr_correction", "next_step_message", "design_handoff"):
            assert scope in _ALLOWED_SCOPES

    def test_extra_scopes_map_to_inbox_honoured_token_scopes(self):
        # Chat has its own append-only worker endpoints; other extra scopes still
        # ride the inbox-honoured new/edit grants.
        assert _TOKEN_SCOPE["chat"] == "chat"
        assert _TOKEN_SCOPE["rework"] == "edit"
        assert _TOKEN_SCOPE["vr_correction"] == "edit"
        assert _TOKEN_SCOPE["next_step_message"] == "new"
        assert _TOKEN_SCOPE["design_handoff"] == "new"
        assert "review" not in _TOKEN_SCOPE  # issue_builder path (request_review)


class TestConversationMention:
    TOKEN_ID = "tok_20260729_000071"

    @pytest.fixture(autouse=True)
    def _short_conversation(self, monkeypatch):
        """0362 T0012: the builder now reads the head to size the recent-turn window.

        These cases pin text shapes and run without a database. A head of 0 is the
        short conversation of P0009 시나리오 12 — nothing folds, and the mention is the
        one this file already describes. The two cases with a provider cursor set their
        own head, because a cursor beyond the end of the conversation is not a state
        the mention should be asked to render.
        """
        monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 0)

    @staticmethod
    def _build(**overrides):
        args = {
            "doc_id": "p.default.0001.0008-CH",
            "project": "p",
            "module": "default",
            "group_name": "p.default.0001",
            "raw_token": "RAW",
            "token_id": TestConversationMention.TOKEN_ID,
            "api_base_url": "http://h:1/api/v1",
        }
        args.update(overrides)
        return ims.build_conversation_mention(**args)

    def test_matches_turn_append_contract(self):
        text = self._build()
        lines = text.split("\n")
        assert lines[0] == "## Conversation"
        assert "Conversation document: p.default.0001.0008-CH" in lines
        assert (
            "GET http://h:1/api/v1/conversation/p.default.0001.0008-CH/turns"
            "?after_seq=0&include_head=1"
        ) in lines
        assert "Submit: POST http://h:1/api/v1/conversation/p.default.0001.0008-CH/turn" in lines
        submit = _submission_block(text)
        assert f'"idempotency_key": "{self.TOKEN_ID}"' in submit
        assert '"based_on_seq": <the head_seq you got from your last read>' in submit
        assert '"display_name": "<your model name>"' in submit
        assert "Reading does not consume this token." in text
        assert text.index("Submit: POST") < text.index("## Document search and lookup rules")

    def test_old_full_body_contract_is_absent(self):
        text = self._build()
        assert "/document?doc_id=" not in text
        assert "COMPLETE body" not in text
        assert "## 🤖 AI(" not in text
        assert '"action": "edit"' not in text
        assert "/inbox" not in text

    def test_absolute_host_survives_into_the_mention(self):
        text = self._build(
            doc_id="d",
            api_base_url="http://192.168.0.9:8088/flowgate/api/v1",
        )
        assert (
            "GET http://192.168.0.9:8088/flowgate/api/v1/conversation/d/turns"
            "?after_seq=0&include_head=1"
        ) in text
        assert "POST http://192.168.0.9:8088/flowgate/api/v1/conversation/d/turn" in text

    def test_unpinned_mention_starts_at_zero_without_reading_a_provider_cursor(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            ims.conversation_turns,
            "get_last_read_seq",
            lambda *args: called.append(args) or 99,
        )
        text = self._build(provider_id=None)
        assert "?after_seq=0&include_head=1" in text
        assert called == []

    def test_pinned_provider_cursor_is_baked_into_after_seq(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ims.conversation_turns,
            "get_last_read_seq",
            lambda doc_id, participant_key: calls.append((doc_id, participant_key)) or 13,
        )
        # A provider caught up with a 13-turn conversation: the default range cannot
        # move it, so the cursor is still what the mention advertises.
        monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 13)
        text = self._build(provider="Claude Opus", provider_id="cx_claude_opus")
        assert "?after_seq=13&include_head=1" in text
        assert calls == [("p.default.0001.0008-CH", "provider:cx_claude_opus")]

    def test_no_provider_asks_the_worker_to_fill_or_omit_display_name(self):
        text = self._build()
        assert '"display_name": "<your model name>"' in text
        assert "omit the field" in text
        assert "not used to determine identity" in text

    def test_known_provider_is_baked_into_display_name_verbatim(self, monkeypatch):
        monkeypatch.setattr(ims.conversation_turns, "get_last_read_seq", lambda *args: 0)
        text = self._build(provider="Claude Opus", provider_id="cx_claude_opus")
        assert '"display_name": "Claude Opus"' in text
        assert "pinned provider name" in text
        assert "<your model name>" not in text

    def test_provider_containing_a_paren_is_safe_in_json(self, monkeypatch):
        monkeypatch.setattr(ims.conversation_turns, "get_last_read_seq", lambda *args: 0)
        text = self._build(provider="we(ird)name", provider_id="weird")
        assert '"display_name": "we(ird)name"' in text
        assert "## 🤖 AI(" not in text

class TestConversationMentionLookupBlock:
    """0334 R0001 — the chat mention must carry the read-only lookup APIs.

    Without them a chat worker asked about the source had nothing to call, guessed
    at a local directory and reported the guess as fact. The scopes were always
    there; the text was not.

    0392 B0001/NR0003 settled on chat=none: the chat token's action_scope is "chat",
    and back then tool_registry.kind_for_step demoted every "chat" to "none" (same
    judge /help/tools uses), so the "Remote project source CRUD" block was always
    absent for a chat mention, remote mode or not.

    0431 T0004/NR0003 reverses that policy: kind_for_step now returns "read" for
    "chat" at the same early branch as "review"/"workflow_decide", so a chat token
    really does resolve to ["read", "grep"] and the CRUD block advertises exactly
    the four read-only tools (read/grep/glob/stat) in remote mode. Local mode still
    drops the block entirely — that gate is untouched.
    """

    @staticmethod
    def _mention(monkeypatch, *, remote_mode: bool = True) -> str:
        from modules.flow_gate.services import mention_service

        monkeypatch.setattr(
            mention_service, "_include_remote_source_crud", lambda project: remote_mode
        )
        # 0362 T0012: no database here either — see TestConversationMention._short_conversation.
        monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 0)
        return ims.build_conversation_mention(
            doc_id="p.default.0001.0008-CH",
            project="p",
            module="default",
            group_name="p.default.0001",
            raw_token="RAW",
            token_id="tok_20260729_000071",
            api_base_url="http://h:1/api/v1",
        )

    def test_source_crud_block_offers_read_tools_to_chat_in_remote_mode(self, monkeypatch):
        # 0431 T0004/NR0003: kind_for_step("chat", "CH") now returns ("read", None), so
        # the CRUD block that other document types get in remote mode appears here too --
        # this must match what /help/tools actually grants a chat token now
        # (read/grep/glob/stat), never write/patch/remove.
        text = self._mention(monkeypatch, remote_mode=True)
        assert "Remote project source CRUD" in text
        block = text[text.index("Remote project source CRUD"):]
        assert "help/tools" in block
        assert "Tools: read, grep, glob, stat" in block
        assert "write" not in block
        assert "patch" not in block
        assert "remove" not in block

    def test_document_search_is_pinned_to_the_token_project(self, monkeypatch):
        text = self._mention(monkeypatch)
        assert "GET http://h:1/api/v1/search/documents?q=<keyword>&project=p" in text
        assert "GET http://h:1/api/v1/search/documents/content?q=<keyword>&project=p" in text
        assert "GET http://h:1/api/v1/list/groups/p.default.0001/documents?limit=5" in text

    def test_guards_against_guessing_local_paths(self, monkeypatch):
        text = self._mention(monkeypatch)
        assert "guess local absolute paths" in text
        # A stale legacy column, not the resolved root — naming it would reintroduce
        # exactly the wrong-directory failure this block exists to stop.
        assert "/source-path" not in text

    def test_warns_that_submitting_consumes_the_token(self, monkeypatch):
        text = self._mention(monkeypatch)
        assert "consumes this token" in text

    def test_local_source_mode_drops_source_apis_but_keeps_document_search(self, monkeypatch):
        text = self._mention(monkeypatch, remote_mode=False)
        assert "help/tools" not in text
        assert "Remote project source CRUD" not in text
        assert "source root" not in text
        assert "GET http://h:1/api/v1/search/documents?q=<keyword>&project=p" in text
        assert "consumes this token" in text

    @pytest.mark.parametrize("remote_mode", [True, False])
    def test_submission_json_remains_the_tail_of_the_mention(self, monkeypatch, remote_mode):
        # The new turn submission contract comes first; lookup guidance closes the mention
        # with the warning that submission consumes the token. 0431 T0004/NR0003: chat now
        # gets the CRUD block in remote mode
        # (source_crud_block_offers_read_tools_to_chat_in_remote_mode), so help/tools appears
        # there too -- but search/documents stays the anchor because it is the one lookup
        # line present in BOTH modes; local mode still drops the CRUD block entirely.
        text = self._mention(monkeypatch, remote_mode=remote_mode)
        assert text.index("Submit: POST") < text.index("search/documents")
        assert '"idempotency_key": "tok_20260729_000071"' in _submission_block(text)
        assert text.rstrip().endswith(
            "Submitting your turn consumes this token, so finish all reading and searching first."
        )


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


class TestEditTurnEndAddendum:
    """Group 0446 T0005 (NR0003 §6-1 R1): rework (edit/single) mentions carry a
    turn-end / no-background-wait addendum that continuous mentions already had.

    NR0003 found that _CONTINUOUS_TEXT's "do NOT stop / no background wait" text
    never reached rework (action_scope=edit) mentions, because build_mention's
    continuous_mode = continuous and not is_edit excludes edit unconditionally. These
    cases pin: (a) the addendum is present on an edit/non-continuous mention in every
    locale, (b) the pre-existing Q-registration guidance survives alongside it, and
    (c) two control groups -- a continuous mention and a plain new/non-continuous
    mention -- do NOT carry the addendum, proving it is edit-only and that continuous
    mentions were not altered by this change.
    """

    @staticmethod
    def _build(monkeypatch, **overrides):
        from modules.flow_gate.services import mention_service

        monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: False)
        args = dict(
            project="p",
            module="default",
            group="0001",
            parent_type="TR",
            parent_doc_number="0005",
            parent_title="Rejected TR",
            parent_doc_id="p.default.0001.0005-TR",
            parent_revision_no=1,
            head_type="",
            head_status="pending",
            scratch_dir="/scratch",
            raw_token="RAW",
            api_base_url="http://h:1/api/v1",
        )
        args.update(overrides)
        return mention_service.build_mention(**args)

    @pytest.mark.parametrize("locale", ["ko", "ja", "en"])
    def test_edit_single_mention_carries_the_addendum(self, monkeypatch, locale):
        from modules.flow_gate.services import mention_service

        text = self._build(
            monkeypatch,
            action_scope="edit",
            edit_reason="rejected",
            continuous=False,
            locale=locale,
        )
        assert mention_service._EDIT_TURN_END_TEXT[locale] in text

    @pytest.mark.parametrize("locale", ["ko", "ja", "en"])
    def test_edit_single_mention_still_carries_q_registration_guidance(self, monkeypatch, locale):
        from modules.flow_gate.services import mention_service

        text = self._build(
            monkeypatch,
            action_scope="edit",
            edit_reason="rejected",
            continuous=False,
            locale=locale,
        )
        clarify = mention_service._CLARIFY_TEXT[locale]
        assert clarify["lead"] in text
        assert clarify["warn"] in text
        assert clarify["positive"] in text
        # _REMINDER_TEXT carries a single {post} placeholder -- check the fixed
        # prefix/suffix around it instead of formatting a second copy here.
        reminder_prefix, reminder_suffix = mention_service._REMINDER_TEXT[locale].split("{post}")
        assert reminder_prefix in text
        assert reminder_suffix in text

    @pytest.mark.parametrize("locale", ["ko", "ja", "en"])
    def test_continuous_mention_control_group_excludes_the_addendum(self, monkeypatch, locale):
        from modules.flow_gate.services import mention_service

        text = self._build(
            monkeypatch,
            action_scope="new",
            continuous=True,
            head_type="TR",
            locale=locale,
        )
        assert mention_service._EDIT_TURN_END_TEXT[locale] not in text
        # continuous mentions must be untouched by this change: they still carry
        # the pre-existing continuous block verbatim.
        assert mention_service._CONTINUOUS_TEXT[locale] in text

    @pytest.mark.parametrize("locale", ["ko", "ja", "en"])
    def test_plain_new_mention_control_group_excludes_the_addendum(self, monkeypatch, locale):
        from modules.flow_gate.services import mention_service

        text = self._build(
            monkeypatch,
            action_scope="new",
            continuous=False,
            head_type="TR",
            locale=locale,
        )
        assert mention_service._EDIT_TURN_END_TEXT[locale] not in text
