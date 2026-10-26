"""flowgate.default.0542 T0008: bounded same-provider API startup retry."""
from __future__ import annotations

import socket
import urllib.error

import pytest

from test_glm_diagnosis_paths_0505 import _api_run, _glm_provider
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import worker as worker_module


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(
        worker_module, "API_STARTUP_TRANSPORT_BACKOFF_SEC", 0,
    )
    monkeypatch.setattr(
        svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "sk-test",
    )


def _dns_error():
    return urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))


def test_first_turn_dns_failure_retries_same_provider_then_starts(monkeypatch):
    calls = []

    def _flaky(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            raise _dns_error()
        run["cancel_event"].set()
        return None, None, {"role": "assistant", "content": "started"}

    monkeypatch.setattr(svc, "_call_openai", _flaky)
    run = _api_run()
    classification, detail = svc._api_execute(_glm_provider(), "prompt", run)

    assert (classification, detail) == ("started_ok", None)
    assert calls == [
        "https://open.bigmodel.cn/api/paas/v4",
        "https://open.bigmodel.cn/api/paas/v4",
    ]
    assert run["startup_retry_count"] == 1
    assert run["startup_retry_history"][0]["provider_id"] == "aip_glm"
    assert [entry["startup_result"] for entry in run["api_turn_trace"]] == [
        "retrying", "started",
    ]


def test_transient_startup_retry_is_bounded_and_preserves_spawn_failed(monkeypatch):
    calls = {"count": 0}

    def _always_fails(*args, **kwargs):
        calls["count"] += 1
        raise _dns_error()

    monkeypatch.setattr(svc, "_call_openai", _always_fails)
    run = _api_run()
    classification, detail = svc._api_execute(_glm_provider(), "prompt", run)

    assert classification == "spawn_failed"
    assert "getaddrinfo failed" in detail
    assert calls["count"] == 1 + worker_module.API_STARTUP_TRANSPORT_MAX_RETRIES
    assert run["startup_retry_count"] == 2
    assert len(run["startup_retry_history"]) == 2
    assert all(item["provider_id"] == "aip_glm" for item in run["startup_retry_history"])


def test_retry_recomputes_timeout_from_current_hop_budget(monkeypatch):
    timeouts = []
    remaining = iter([10.0, 9.0, 1.25, 1.0])

    def _flaky(*args, **kwargs):
        timeouts.append(args[4])
        if len(timeouts) == 1:
            raise _dns_error()
        run["cancel_event"].set()
        return None, None, {"role": "assistant", "content": "started"}

    monkeypatch.setattr(svc, "_remaining_sec", lambda run: next(remaining))
    monkeypatch.setattr(svc, "_call_openai", _flaky)
    run = _api_run()

    classification, detail = svc._api_execute(_glm_provider(), "prompt", run)

    assert (classification, detail) == ("started_ok", None)
    assert timeouts == [9.0, 1.0]
    assert timeouts[1] <= 1.0


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://example.invalid", 401, "Unauthorized", {}, None),
        ValueError("invalid response payload"),
    ],
)
def test_non_transient_startup_failures_are_not_retried(monkeypatch, error):
    calls = {"count": 0}

    def _fail(*args, **kwargs):
        calls["count"] += 1
        raise error

    monkeypatch.setattr(svc, "_call_openai", _fail)
    classification, _detail = svc._api_execute(_glm_provider(), "prompt", _api_run())

    assert classification in {"api_error", "spawn_failed"}
    assert calls["count"] == 1


def test_post_first_turn_transport_failure_is_not_startup_retried(monkeypatch):
    calls = {"count": 0}

    def _flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None, None, {"role": "assistant", "content": "thinking"}
        raise _dns_error()

    monkeypatch.setattr(svc, "API_MAX_TOOL_NUDGES", 99)
    monkeypatch.setattr(svc, "_call_openai", _flaky)
    run = _api_run()
    classification, _detail = svc._api_execute(_glm_provider(), "prompt", run)

    assert classification == "started_ok"
    assert calls["count"] == 2
    assert run.get("startup_retry_count", 0) == 0
    assert run.get("turn_limit_exhausted") is not True
