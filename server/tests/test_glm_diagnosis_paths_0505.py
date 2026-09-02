"""flowgate.default.0505 TR0023: GLM 기동 실패(getaddrinfo) 진단 경로 고정 테스트.

TR0023 concluded that two differently-labelled failures sharing one UI shape are two
different code paths, both behaving as designed:
  * "기동 실패 — <urlopen error [Errno 11001] getaddrinfo failed>" (spawn_failed):
    _api_execute's first-turn transport-exception branch returns
    ("spawn_failed", str(exc)[:500]), _execute_provider_chain appends it to
    fallback_history, and a single-provider chain ends the hop with
    end_reason="all_providers_failed" — which _retry_eligible never re-opens.
  * "결과물 없음 — worker stopped: turn limit exhausted" (no_output):
    the turn loop ran to max_turns (docs_target=1 x API_MAX_TURNS_PER_DOC=4) without
    register_document, and _no_output_detail names that diagnosis verbatim.

No source change was made in TR0023 — this file only pins those behaviors so a future
edit that silently moves either diagnosis fails here first.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GETADDRINFO_DETAIL = "<urlopen error [Errno 11001] getaddrinfo failed>"


def _api_run(**over):
    """A minimal run dict _api_execute can drive without any database."""
    run = {
        "run_id": "aiv_test",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0505",
        "doc_ref": None,
        "action_scope": "new",
        "mode": "single",
        "docs_target": 1,
        "raw_token": "tok_raw",
        "token_id": "tok_20260902_000001",
        "cancel_event": threading.Event(),
        "started_mono": time.monotonic(),
        "timeout_sec": 3600,
        "api_base_url": "http://127.0.0.1:1/flowgate/api/v1",
        "module": "default",
    }
    run.update(over)
    return run


def _glm_provider():
    return {
        "id": "aip_glm", "name": "GLM", "exec_type": "api", "kind": "openai",
        "api_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_model": "glm-4.6",
    }


# ── 1. spawn_failed: the first-turn transport exception branch ───────────────

class TestSpawnFailedFirstTurn:
    def test_first_turn_transport_error_returns_spawn_failed_with_urlopen_text(
            self, monkeypatch):
        """A DNS failure on turn 1 surfaces as ("spawn_failed", str(exc)[:500]) —
        exactly the string T0022/ko.ts renders as "기동 실패 — <urlopen error ...>"."""

        def _boom(*a, **kw):
            raise Exception(GETADDRINFO_DETAIL)

        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")
        monkeypatch.setattr(svc, "_call_openai", _boom)

        run = _api_run()
        classification, detail = svc._api_execute(_glm_provider(), "prompt", run)

        assert (classification, detail) == ("spawn_failed", GETADDRINFO_DETAIL)
        assert run["api_turns_used"] == 1                 # a first turn is still consumed
        assert run["model_last_http_status"] == 0         # never reached HTTP
        # Not a turn-limit case: the transport failure is mutually exclusive with it.
        assert run.get("turn_limit_exhausted") is not True

    def test_transport_error_is_recorded_as_model_transport_error_in_the_trace(
            self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")

        def _boom(*a, **kw):
            raise Exception(GETADDRINFO_DETAIL)

        monkeypatch.setattr(svc, "_call_openai", _boom)
        run = _api_run()
        svc._api_execute(_glm_provider(), "prompt", run)

        trace = run["api_turn_trace"]
        assert len(trace) == 1
        assert trace[0]["model_status"] == 0
        assert trace[0]["disposition"] == "model_transport_error"


# ── 2. all_providers_failed: a single-provider chain ends the hop ────────────

def _judged_run(**over):
    run = {
        "mode": "continuous", "cancel_event": threading.Event(), "end_reason": "exited",
        "pause_requested": False, "completion_oracle": None, "action_scope": "new",
        "docs_target": 1, "docs_reached": 0, "attempts_used": 1, "group_id": "g",
        "started_mono": time.monotonic(), "timeout_sec": 3600, "outcome": "none",
        "fallback_history": [], "provider": {"name": "GLM", "exec_type": "api"},
    }
    run.update(over)
    return run


class TestSingleProviderChainExhaustion:
    def test_one_provider_spawn_failure_leaves_one_history_row_and_blocks_retry(self):
        run = _judged_run()
        chain = [_glm_provider()]
        prompts = []

        def _fake(provider, prompt, run):
            prompts.append(prompt)
            return "spawn_failed", GETADDRINFO_DETAIL

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(svc, "_api_execute", _fake)
            started_ok = svc._execute_provider_chain(run, chain, "prompt")

        assert started_ok is False
        assert prompts == ["prompt"]                     # one provider, one try
        history = run["fallback_history"]
        assert len(history) == 1                         # the "폴백 이력: 1건" entry
        assert (history[0]["reason"], history[0]["detail"]) == (
            "spawn_failed", GETADDRINFO_DETAIL)

        svc._classify_end_reason(run, started_ok)
        assert run["end_reason"] == "all_providers_failed"
        # The walk's own verdict is final: no automatic retry is opened for it.
        assert svc._retry_eligible(run) is False


# ── 3. turn_limit_exhausted: the no_output diagnosis path ────────────────────

class TestTurnLimitExhausted:
    def test_four_turn_budget_is_exhausted_without_registering_and_named_verbatim(
            self, monkeypatch):
        """docs_target=1 gives max(API_MAX_TURNS_PER_DOC, 1*4) = 4 model round trips.
        With the model answering but never calling the completion tool, the loop runs
        out of turns, sets turn_limit_exhausted, and _no_output_detail returns the
        exact sentence the UI shows."""
        monkeypatch.setattr(svc, "API_MAX_TOOL_NUDGES", 99)  # never trip the nudge break
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")
        calls = {"n": 0}

        def _replying_model(*a, **kw):
            calls["n"] += 1
            return (None, None, {"role": "assistant", "content": "thinking..."})

        monkeypatch.setattr(svc, "_call_openai", _replying_model)

        run = _api_run()
        classification, detail = svc._api_execute(_glm_provider(), "prompt", run)

        assert classification == "started_ok"            # the model itself never failed
        assert calls["n"] == svc.API_MAX_TURNS_PER_DOC == 4
        assert run["api_turns_used"] == 4
        assert run["turn_limit_exhausted"] is True
        assert run.get("model_call_failed", False) is False

        # _api_execute alone never sets run["provider"] — only its caller
        # _execute_provider_chain does (ai_invoke_part2_worker.py:223) — and
        # _no_output_detail branches on provider.exec_type=="api", so a direct
        # _api_execute call must supply it itself before checking the detail sentence.
        run["provider"] = {"exec_type": "api"}
        from modules.flow_gate.services.ai_invoke_worker import _no_output_detail
        assert _no_output_detail(run) == "worker stopped: turn limit exhausted"

    def test_transport_failure_after_turn_one_does_not_set_turn_limit(self, monkeypatch):
        """A transport error on a later turn breaks out with model_call_failed=True,
        which is mutually exclusive with turn_limit_exhausted."""
        monkeypatch.setattr(svc, "API_MAX_TOOL_NUDGES", 99)
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")
        state = {"calls": 0}

        def _flaky(*a, **kw):
            state["calls"] += 1
            if state["calls"] >= 3:
                raise Exception(GETADDRINFO_DETAIL)
            return (None, None, {"role": "assistant", "content": "thinking..."})

        monkeypatch.setattr(svc, "_call_openai", _flaky)
        run = _api_run()
        classification, _detail = svc._api_execute(_glm_provider(), "prompt", run)

        assert classification == "started_ok"            # post-first-turn errors still started
        assert state["calls"] == 3
        assert run.get("turn_limit_exhausted") is not True


# ── 4. the two labels come from different i18n keys ──────────────────────────

class TestUiLabels:
    def test_spawn_failed_and_no_output_are_different_labels(self):
        ko_path = _SERVER_DIR.parent / "client" / "shared" / "i18n" / "ko.ts"
        text = ko_path.read_text(encoding="utf-8")
        assert "reason_spawn_failed: '기동 실패'" in text
        assert "reason_no_output: '결과물 없음'" in text

    def test_turn_limit_sentence_matches_the_i18n_backed_detail(self):
        from modules.flow_gate.services.ai_invoke_worker import _no_output_detail
        run = _judged_run(provider={"name": "GLM", "exec_type": "api"},
                          turn_limit_exhausted=True, exit_code=None)
        detail = _no_output_detail(run)
        assert detail == "worker stopped: turn limit exhausted"
        # ...and it is NOT the spawn_failed sentence.
        assert "urlopen" not in detail
