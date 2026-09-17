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
