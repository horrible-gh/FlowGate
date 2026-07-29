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


def _submitted_body(text: str) -> dict:
    """The inbox JSON the worker is told to POST.

    0334: the mention now carries API examples that are themselves JSON, so the
    first '{' in the text is no longer the submission body. The submission block is
    still the tail of the mention by construction, so anchor on it.
    """
    return json.loads(text[text.index("{", text.index("Submit: POST")):])


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
        # Read, source/document lookup, and submit each carry their own credential line.
        assert lines.count("Authorization: Bearer RAW") == 4
        assert "Submit: POST http://h:1/api/v1/inbox" in lines
        body = _submitted_body(text)
        assert body["action"] == "edit"
        assert body["edit_reason"] == "worker_self"
        assert body["module"] == "default"
        assert body["doc_id"] == "p.default.0001.0008-CH"

    def test_module_omitted_when_none(self):
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://h:1/api/v1",
        )
        body = _submitted_body(text)
        assert "module" not in body

    def test_absolute_host_survives_into_the_mention(self):
        # Guard inherited from client/tests/main/buildConversationMention.host.spec.ts,
        # deleted with the TS builder in 0293. Group 0103 B0001 was "the chat copy mention
        # shows no host anywhere" — a host-less URL is unusable to a worker on another
        # machine, and this is now the only place that property is pinned.
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://192.168.0.9:8088/flowgate/api/v1",
        )
        assert "GET http://192.168.0.9:8088/flowgate/api/v1/document?doc_id=d" in text
        assert "POST http://192.168.0.9:8088/flowgate/api/v1/inbox" in text

    def test_no_provider_asks_the_worker_to_fill_the_slot(self):
        # Copy path: the server cannot know who the user will paste this to.
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://h:1/api/v1",
        )
        assert "## 🤖 AI(<your model name>) · <ISO-8601 timestamp>" in text
        # "I don't know" must be an accepted answer, not a forced guess.
        assert "drop the parentheses entirely" in text

    def test_known_provider_is_baked_in_verbatim(self):
        # Invoke path with a pinned provider: nothing for the worker to decide.
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://h:1/api/v1",
            provider="Claude Opus",
        )
        assert "## 🤖 AI(Claude Opus) · <ISO-8601 timestamp>" in text
        assert "<your model name>" not in text

    def test_provider_containing_a_paren_falls_back_to_self_report(self):
        # ")" would break HEADER_RE, so such a name is never baked in.
        text = ims.build_conversation_mention(
            doc_id="d", project="p", module=None, group_name="g",
            raw_token="RAW", api_base_url="http://h:1/api/v1",
            provider="we(ird)name",
        )
        assert "## 🤖 AI(<your model name>) · <ISO-8601 timestamp>" in text


class TestConversationMentionLookupBlock:
    """0334 R0001 — the chat mention must carry the read-only lookup APIs.

    Without them a chat worker asked about the source had nothing to call, guessed
    at a local directory and reported the guess as fact. The scopes were always
    there; the text was not.
    """

    @staticmethod
    def _mention(monkeypatch, *, remote_mode: bool = True) -> str:
        from modules.flow_gate.services import mention_service

        monkeypatch.setattr(
            mention_service, "_include_remote_source_crud", lambda project: remote_mode
        )
        return ims.build_conversation_mention(
            doc_id="p.default.0001.0008-CH",
            project="p",
            module="default",
            group_name="p.default.0001",
            raw_token="RAW",
            api_base_url="http://h:1/api/v1",
        )

    def test_source_search_endpoints_are_offered(self, monkeypatch):
        # 0349 TR-2: the tools are named and their usage is one help call away, instead of
        # one request-format block per tool inlined here.
        text = self._mention(monkeypatch)
        assert "도구: read, grep, glob" in text
        assert "GET http://h:1/api/v1/help/tools" in text
        assert "http://h:1/api/v1/help/tools/{name}" in text

    def test_source_block_stays_read_only(self, monkeypatch):
        # The chat token resolves to ["read", "grep"]; advertising a mutating call
        # would hand the worker an instruction its grant refuses.
        text = self._mention(monkeypatch)
        tool_section = text.split("## Remote project source CRUD")[1].split("\n\n## ")[0]
        assert "write" not in tool_section
        assert "remove" not in tool_section

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
        # The lookup block goes BEFORE the submit block, so "search, then submit" is
        # both the reading order and the required order (a submit consumes the token).
        text = self._mention(monkeypatch, remote_mode=remote_mode)
        assert text.index("help/tools" if remote_mode else "search/documents") < text.index("Submit: POST")
        assert _submitted_body(text)["edit_reason"] == "worker_self"
        assert text.rstrip().endswith("}")


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
