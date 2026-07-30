"""0293 T0005 — one chat mention builder, and the provider slot's fill rule.

R0001 asked to show WHICH AI answered a CH turn. NR0004 found the chat mention existed
twice (Python + TypeScript) and that the invoke path builds its mention BEFORE the run
picks a provider. These tests pin the two invariants that came out of that:

  1. /token/issue's "chat" wire scope serves the CH mention, so the [멘트복사] path and
     the in-app AI 호출 path read the same builder.
  2. A provider name is only claimed when fallback is structurally impossible.

Group 0351 superseded HALF of invariant 1. "chat" used to be a mention selector layered
on an edit grant because the inbox honoured no such action and a literal chat token
would have been unusable. It now IS a grant: the append-only conversation endpoints
(POST/GET /api/v1/conversation/{doc_id}/turn[s]) accept action_scope="chat" and nothing
else, and an edit token is explicitly refused there. What survives unchanged is the part
the group actually cared about — one mention builder for both chat entrances.

Pure-unit (no HTTP harness): both invariants live in module-level tables and one helper.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

import pytest  # noqa: E402

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service  # noqa: E402

# token_routes reaches config → sqloader, which is absent from a bare dev checkout
# (the same reason test_t244_normalizations skips there). The mapping assertions below
# are worth keeping wherever the import DOES work, so skip rather than drop them.
try:
    from modules.flow_gate.api import token_routes
except ModuleNotFoundError:  # pragma: no cover — environment-dependent
    token_routes = None


@pytest.mark.skipif(token_routes is None, reason="token_routes import needs sqloader")
class TestChatWireScope:
    def test_token_issue_accepts_chat(self):
        assert "chat" in token_routes._WIRE_SCOPES

    def test_chat_now_mints_its_own_append_only_grant(self):
        # 0351: the conversation turn endpoints authorise on action_scope="chat", so the
        # token issued for a chat mention must carry that scope rather than borrowing an
        # edit grant. Minting "edit" here would 403 every worker that follows the mention.
        assert token_routes._WIRE_TOKEN_SCOPE["chat"] == "chat"

    def test_both_chat_entrances_mint_the_same_append_only_grant(self):
        # T3 completes the transition: both [멘트복사] and in-app [AI 호출] mint
        # the only scope accepted by the worker conversation endpoints.
        assert ai_invoke_routes._TOKEN_SCOPE["chat"] == "chat"
        assert ai_invoke_routes._TOKEN_SCOPE["chat"] == token_routes._WIRE_TOKEN_SCOPE["chat"]

    def test_other_scopes_pass_through_unmapped(self):
        for scope in ("new", "edit", "resolve_conflict"):
            assert token_routes._WIRE_TOKEN_SCOPE.get(scope, scope) == scope


class TestPinnedProviderName:
    def _chain(self, *providers):
        return patch.object(
            ai_invoke_service.ai_settings_service, "resolve_effective",
            return_value={"providers": list(providers)},
        )

    def test_explicit_pin_returns_that_providers_name(self):
        with self._chain({"id": "a", "name": "Claude"}, {"id": "b", "name": "Codex"}):
            assert ai_invoke_service.resolve_pinned_provider_name("p", "b") == "Codex"

    def test_unpinned_multi_provider_chain_claims_nothing(self):
        # start_run keeps the whole chain, so _worker may answer from any of them —
        # naming chain[0] here would print a guess as if the server had confirmed it.
        with self._chain({"id": "a", "name": "Claude"}, {"id": "b", "name": "Codex"}):
            assert ai_invoke_service.resolve_pinned_provider_name("p", None) is None

    def test_unpinned_single_provider_chain_is_still_unambiguous(self):
        with self._chain({"id": "a", "name": "Claude"}):
            assert ai_invoke_service.resolve_pinned_provider_name("p", None) == "Claude"

    def test_unknown_pin_and_empty_chain_claim_nothing(self):
        with self._chain({"id": "a", "name": "Claude"}):
            assert ai_invoke_service.resolve_pinned_provider_name("p", "zzz") is None
        with self._chain():
            assert ai_invoke_service.resolve_pinned_provider_name("p", None) is None

    def test_settings_failure_never_breaks_the_run(self):
        # A missing badge is a cosmetic loss; a 500 from a mention helper is not.
        with patch.object(ai_invoke_service.ai_settings_service, "resolve_effective",
                          side_effect=RuntimeError("db down")):
            assert ai_invoke_service.resolve_pinned_provider_name("p", "a") is None
