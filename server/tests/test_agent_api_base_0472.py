from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402


@pytest.fixture(autouse=True)
def _agent_base_unset(monkeypatch):
    monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", None)
    monkeypatch.setattr(settings, "FLOWGATE_PORT", 8089)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("http://flowgate.stg/flowgate/api/v1", "http://127.0.0.1:8089/flowgate/api/v1"),
        ("https://flowgate.stg/flowgate/api/v1/", "https://127.0.0.1:8089/flowgate/api/v1"),
        ("http://10.1.2.3:9010/x", "http://127.0.0.1:9010/x"),
        ("http://localhost:8088/x?q=1", "http://127.0.0.1:8088/x?q=1"),
        ("http://[::1]:8123/x", "http://127.0.0.1:8123/x"),
    ],
)
def test_resolver_fallback_matrix(operator, expected):
    assert svc._resolve_agent_api_base(operator) == expected


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("http://agent.internal", "http://agent.internal/flowgate/api/v1?q=1"),
        ("https://10.0.0.4:9443/", "https://10.0.0.4:9443/flowgate/api/v1?q=1"),
        ("http://[::1]:8089", "http://[::1]:8089/flowgate/api/v1?q=1"),
    ],
)
def test_valid_override_preserves_operator_path_and_query(monkeypatch, override, expected):
    monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", override)
    assert svc._resolve_agent_api_base("https://public/flowgate/api/v1/?q=1") == expected


@pytest.mark.parametrize(
    "override",
    ["", "   ", "agent.internal", "ftp://agent.internal", "http:///missing", "http://x:70000", "http://x/path", "http://u:p@x"],
)
def test_invalid_override_fails_fast(monkeypatch, override):
    monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", override)
    with pytest.raises(ValueError, match="FLOWGATE_AGENT_API_BASE"):
        svc._resolve_agent_api_base("http://public/flowgate/api/v1")


def test_cli_prompt_and_environment_base_share_canonical_value():
    operator = "http://flowgate.stg/flowgate/api/v1"
    prompt = " ".join(
        [
            f"GET {operator}/conversation/ch1",
            f"GET {operator}/help",
            f"GET {operator}/document/d1",
            f"POST {operator}/inbox",
            "document body mentions http://elsewhere/flowgate/api/v1 unchanged",
        ]
    )
    rewritten, exported = svc._canonicalize_cli_prompt(prompt, operator)
    canonical = "http://127.0.0.1:8089/flowgate/api/v1"
    assert exported == canonical
    assert rewritten.count(canonical) == 4
    assert "http://127.0.0.1/flowgate/api/v1" not in rewritten
    assert "http://elsewhere/flowgate/api/v1 unchanged" in rewritten


def test_api_provider_dispatch_does_not_resolve_cli_base(monkeypatch):
    called = []
    monkeypatch.setattr(svc, "_resolve_agent_api_base", lambda *_: called.append(True))
    monkeypatch.setattr(svc, "_api_execute", lambda *_: ("started_ok", None))
    run = {"cancel_event": type("E", (), {"is_set": lambda self: False})(), "fallback_history": [], "attempt_no": 1}
    assert svc._execute_provider_chain(run, [{"id": "api", "exec_type": "api"}], "prompt")
    assert called == []