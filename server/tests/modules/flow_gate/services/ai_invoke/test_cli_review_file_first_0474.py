"""0474: provider-aware CLI review file-first prompt contract."""
from __future__ import annotations

import re

import pytest

from modules.flow_gate.services.ai_invoke import worker


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_cli_review_prompt_requires_utf8_scratch_doc_path(locale):
    prompt = worker._provider_prompt(
        {"action_scope": "review", "continuation_locale": locale},
        {"exec_type": "cli", "kind": "claude"},
        "BASE",
    )
    assert prompt.startswith("BASE")
    clause = prompt[len("BASE"):]
    for required in ("UTF-8", "FLOWGATE_SCRATCH", "{SCRATCH}", "doc_path",
                     "verdict", "findings", "comment", "dry-run"):
        assert required in clause
    assert "absolute" in clause or "절대 경로" in clause
    assert "MUST NOT" in clause or "넣지 마십시오" in clause
    assert "command-line JSON" in clause


@pytest.mark.parametrize("kind", ["claude", "copilot", "custom"])
def test_every_cli_review_provider_gets_file_first_without_display_name_matching(kind):
    prompt = worker._provider_prompt(
        {"action_scope": "review", "continuation_locale": "en"},
        {"exec_type": "cli", "kind": kind, "name": "unrelated display label"},
        "BASE",
    )
    assert "mandatory contract" in prompt
    assert "doc_path" in prompt


def test_api_review_keeps_structured_registration_prompt_unchanged():
    base = "API STRUCTURED REGISTER"
    assert worker._provider_prompt(
        {"action_scope": "review"}, {"exec_type": "api", "kind": "claude"}, base
    ) == base


@pytest.mark.parametrize("scope", ["new", "edit"])
def test_general_cli_prompt_does_not_receive_review_only_contract(scope):
    base = "GENERAL"
    assert worker._provider_prompt(
        {"action_scope": scope}, {"exec_type": "cli", "kind": "claude"}, base
    ) == base


def test_en_and_ja_contract_copy_has_no_hangul():
    hangul = re.compile(r"[가-힣]")
    assert not hangul.search(worker._CLI_REVIEW_FILE_FIRST["en"])
    assert not hangul.search(worker._CLI_REVIEW_FILE_FIRST["ja"])
    assert hangul.search(worker._CLI_REVIEW_FILE_FIRST["ko"])


# 0108: admission scope stays fixed while the review loop changes its live hop.
@pytest.mark.parametrize("scope,hop,review", [
    ("review", "rework", False),
    ("edit", "review", True),
    ("review", "review", True),
    ("edit", "rework", False),
    ("review", None, True),
    ("edit", None, False),
    ("review", "work", True),
    ("edit", "work", False),
])
@pytest.mark.parametrize("exec_type", ["cli", "api"])
@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_current_hop_selects_review_contract(scope, hop, review, exec_type, locale):
    run = {"action_scope": scope, "continuation_locale": locale}
    if hop is not None:
        run["hop_kind"] = hop
    before = dict(run)
    # The document submission instructions must survive byte-for-byte in edit hops.
    base = "Submit the entire Markdown document as action=edit using doc_path.\n# 본문 日本語 🚀\n"
    prompt = worker._provider_prompt(run, {"exec_type": exec_type}, base)
    clause = worker._CLI_REVIEW_FILE_FIRST[locale]
    expected = base.rstrip() + "\n\n" + clause if review and exec_type == "cli" else base
    assert prompt == expected
    assert prompt.count(clause) == int(review and exec_type == "cli")
    assert run == before


@pytest.mark.parametrize("scope,hops", [
    ("review", ["review", "rework", "review"]),
    ("edit", ["rework", "review"]),
])
@pytest.mark.parametrize("transports", [("cli", "cli"), ("api", "cli"), ("cli", "api")])
def test_provider_dispatch_uses_current_hop_on_fallback_and_retry(monkeypatch, scope, hops, transports):
    import threading
    from types import SimpleNamespace

    captured = []
    switches = []

    def execute(provider, prompt, run):
        captured.append((run["hop_kind"], provider["exec_type"], provider["id"], prompt))
        if provider["id"] == "first":
            return "spawn_failed", "simulated startup failure"
        return "started_ok", None

    service = SimpleNamespace(
        _cli_execute=execute, _api_execute=execute,
        _broadcast=lambda *args: switches.append(args),
    )
    monkeypatch.setattr(worker, "_svc", lambda: service)
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *args: None)
    run = {
        "run_id": "0108-prompt", "group_id": "flowgate.v02.0108",
        "action_scope": scope, "continuation_locale": "en",
        "cancel_event": threading.Event(), "fallback_history": [],
    }
    chain = [
        {"id": "first", "name": "First", "exec_type": transports[0]},
        {"id": "fallback", "name": "Fallback", "exec_type": transports[1]},
    ]
    clause = worker._CLI_REVIEW_FILE_FIRST["en"]
    for hop in hops:
        run["hop_kind"] = hop
        base = "Review verdict/findings/comment via doc_path." if hop == "review" else (
            "Submit the entire Markdown document as action=edit using doc_path.\n# 수정 日本語 🚀\n"
        )
        # A fresh attempt and a same-hop retry must each decorate the original prompt once.
        for _attempt in range(2):
            start = len(captured)
            assert worker._execute_provider_chain(run, chain, base) is True
            attempts = captured[start:]
            assert len(attempts) == 2
            assert [item[2] for item in attempts] == ["first", "fallback"]
            for actual_hop, transport, _provider_id, actual_prompt in attempts:
                assert actual_hop == hop
                needs_clause = hop == "review" and transport == "cli"
                expected = base.rstrip() + "\n\n" + clause if needs_clause else base
                assert actual_prompt == expected
                assert actual_prompt.count(clause) == int(needs_clause)
            assert run["action_scope"] == scope
    assert len(switches) == len(hops) * 2
