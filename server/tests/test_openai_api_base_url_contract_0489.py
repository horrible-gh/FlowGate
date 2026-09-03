"""Regression coverage for the OpenAI-compatible API base URL contract (0489 T0004)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modules.flow_gate.services import ai_invoke_service as invoke_service
from modules.flow_gate.settings import ai_settings_service


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepinfra.com/v1/openai", "https://api.deepinfra.com/v1/openai/chat/completions"),
        ("https://api.deepinfra.com/v1/openai/", "https://api.deepinfra.com/v1/openai/chat/completions"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        (ai_settings_service._DEFAULT_BASE_URLS["openai"], "https://api.openai.com/v1/chat/completions"),
    ],
)
def test_openai_call_joins_compatible_base_url_once(monkeypatch, base_url, expected):
    captured: list[tuple[str, dict, dict]] = []

    def fake_post(url, headers, body, _timeout):
        captured.append((url, headers, body))
        return {"choices": []}

    monkeypatch.setattr(invoke_service, "_http_post_json", fake_post)
    invoke_service._call_openai(
        base_url, "gpt-test", "test-key", [], 1, "submit_result", "", {},
    )

    assert captured == [(expected, {"Authorization": "Bearer test-key"}, {
        "model": "gpt-test", "messages": [], "tools": [{
            "type": "function", "function": {
                "name": "submit_result", "description": "", "parameters": {},
            },
        }],
    })]


def test_blank_openai_base_resolves_to_versioned_compatible_base():
    assert ai_settings_service._resolve_base_url({
        "exec_type": "api", "kind": "openai", "api_base_url": "",
    }) == "https://api.openai.com/v1"


def test_canonical_openai_base_migration_updates_only_exact_old_default():
    root = Path(__file__).resolve().parents[1]
    migrations = [
        root / "sql/migrations/sqlite/093_openai_api_base_url_contract.sql",
        root / "sql/migrations/mysql/093_openai_api_base_url_contract.sql",
        root / "sql/migrations/postgres/093_openai_api_base_url_contract.sql",
    ]
    sql_texts = [path.read_text(encoding="utf-8") for path in migrations]
    expected_update = """UPDATE ai_providers
SET api_base_url = 'https://api.openai.com/v1'
WHERE kind = 'openai'
  AND api_base_url = 'https://api.openai.com';"""
    assert all(expected_update in sql for sql in sql_texts)

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ai_providers (kind TEXT, api_base_url TEXT)")
    rows = [
        ("openai", "https://api.openai.com"),
        ("openai", "https://internal.example.com/openai"),
        ("claude", "https://api.openai.com"),
    ]
    conn.executemany("INSERT INTO ai_providers VALUES (?, ?)", rows)
    conn.executescript(sql_texts[0])
    assert conn.execute("SELECT kind, api_base_url FROM ai_providers").fetchall() == [
        ("openai", "https://api.openai.com/v1"),
        ("openai", "https://internal.example.com/openai"),
        ("claude", "https://api.openai.com"),
    ]