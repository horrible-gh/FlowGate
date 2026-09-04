"""flowgate.default.0501 T4: ai_invoke worker / provider transport module boundary.

T4 split ai_invoke_part2_worker.py (flowgate.default.0497 T0009's part 2 of 3) into
three modules; T6 then moved the whole engine into the ai_invoke/ package (NR0003
§12), where those three are
three files along the actual module boundary:

  ai_invoke/worker.py         provider-neutral worker orchestration (loop, retry,
                               judging, finalize, stop-code records, FlowGate-tool
                               dispatch)
  ai_invoke/provider_api.py   HTTP/API transport (_call_openai / _call_anthropic /
                               _http_post_json and prompt/config shaping helpers)
  ai_invoke/provider_cli.py   subprocess/CLI transport (spawn, watchdog, exit codes)

This file covers the two conditions T4 itself named as the hard requirement:

  * the pre-existing, pervasive `monkeypatch.setattr(svc, "_call_openai"/"_call_anthropic",
    fake)` pattern must still be observed by the REAL worker path even though the
    call site (`_api_execute`, now in ai_invoke/worker.py) and the callee
    (`_call_openai`/`_call_anthropic`, now in ai_invoke/provider_api.py) live in
    different files. flowgate.default.0501 T5 replaced the exec()-assembled shared
    globals() dict with normal imports: `ai_invoke/worker.py` reaches
    `svc._call_openai`/`svc._call_anthropic` through `svc` (`ai_invoke_service`,
    imported once at its own top), so `monkeypatch.setattr(svc, ...)` still patches
    exactly the attribute every caller resolves through, the same seam
    the registry accessors (now ai_invoke/runtime.py) already relied on this pattern for.
  * the import-dependency guard: neither provider module may import
    chain.py, review.py, group lease DB access, or the runtime
    registry (ai_invoke/runtime.py) -- and ai_invoke/worker.py does not itself
    define lease-acquisition-policy or run-id-allocation logic (those stay in
    ai_invoke/admission.py / ai_invoke/runtime.py; worker orchestration only calls
    into them).
"""
from __future__ import annotations

import ast
import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"
# flowgate.default.0501 T6 moved the engine into the ai_invoke/ package (NR0003 §12);
# the three files T4 created kept their contents and lost the redundant prefix.
_PKG_DIR = _SERVICES_DIR / "ai_invoke"
_PROVIDER_API_PATH = _PKG_DIR / "provider_api.py"
_PROVIDER_CLI_PATH = _PKG_DIR / "provider_cli.py"
_WORKER_PATH = _PKG_DIR / "worker.py"

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402


def _api_run(**over):
    """A minimal run dict the worker's provider-chain walk can drive without a
    database -- same shape test_glm_diagnosis_paths_0505.py's `_api_run` uses for
    `_api_execute` directly, extended with the couple of fields
    `_execute_provider_chain` (one call frame further up, inside `_worker`'s own
    while-loop) also touches."""
    run = {
        "run_id": "aiv_boundary_test",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0501",
        "doc_ref": None,
        "action_scope": "new",
        "mode": "single",
        "docs_target": 1,
        "raw_token": "tok_raw",
        "token_id": "tok_20260902_000002",
        "cancel_event": threading.Event(),
        "started_mono": time.monotonic(),
        "timeout_sec": 3600,
        "api_base_url": "http://127.0.0.1:1/flowgate/api/v1",
        "module": "default",
        "attempt_no": 1,
        "fallback_history": [],
        "provider": None,
        "provider_id": None,
    }
    run.update(over)
    return run


def _openai_provider():
    return {"id": "aip_openai", "name": "OpenAI-compat", "exec_type": "api", "kind": "openai",
            "api_base_url": "https://api.example.invalid/v1", "api_model": "gpt-test"}


def _anthropic_provider():
    return {"id": "aip_anthropic", "name": "Anthropic-compat", "exec_type": "api", "kind": "claude",
            "api_base_url": "https://api.example.invalid", "api_model": "claude-test"}


class TestCallOpenaiAnthropicPatchCrossesTheFileBoundary:
    """The hard compatibility requirement T4 named explicitly: a test that patches
    `svc._call_openai` / `svc._call_anthropic` (defined in ai_invoke/provider_api.py)
    must still see the patch take effect when the REAL worker path (`_execute_provider_chain`,
    defined in ai_invoke/worker.py -- the function `_worker`'s own while-loop calls
    every attempt) drives an API provider through to that call."""

    def test_execute_provider_chain_uses_the_patched_call_openai(self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")
        calls = {"n": 0}

        def fake_call_openai(*args, **kwargs):
            calls["n"] += 1
            tool_name = args[5]
            return "ok", {"id": "tc1", "name": tool_name,
                          "input": {"doc_type": "standard", "title": "t", "content": "c"}}, \
                   {"role": "assistant", "content": "ok", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", fake_call_openai)
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: (201, {"doc_id": "d1"}))

        run = _api_run()
        started_ok = svc._execute_provider_chain(run, [_openai_provider()], "prompt")

        assert started_ok is True
        assert calls["n"] >= 1, (
            "svc._execute_provider_chain (ai_invoke/worker.py) did not reach the "
            "monkeypatched svc._call_openai (ai_invoke/provider_api.py) -- the "
            "cross-file global lookup regressed"
        )

    def test_execute_provider_chain_uses_the_patched_call_anthropic(self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")
        calls = {"n": 0}

        def fake_call_anthropic(*args, **kwargs):
            calls["n"] += 1
            tool_name = args[5]
            return "ok", [{"id": "tc1", "name": tool_name,
                           "input": {"doc_type": "standard", "title": "t", "content": "c"}}], \
                   {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}

        monkeypatch.setattr(svc, "_call_anthropic", fake_call_anthropic)
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: (201, {"doc_id": "d1"}))

        run = _api_run()
        started_ok = svc._execute_provider_chain(run, [_anthropic_provider()], "prompt")

        assert started_ok is True
        assert calls["n"] >= 1, (
            "svc._execute_provider_chain (ai_invoke/worker.py) did not reach the "
            "monkeypatched svc._call_anthropic (ai_invoke/provider_api.py) -- the "
            "cross-file global lookup regressed"
        )

    def test_assembled_module_still_exposes_both_names(self):
        # T4's explicit end-state requirement: whichever mechanism is used, the
        # ASSEMBLED ai_invoke_service module must still carry every symbol test code
        # reaches via svc.<name>, especially these two.
        assert callable(svc._call_openai)
        assert callable(svc._call_anthropic)


class TestProviderFallbackPreservesTokenIdentity:
    """0496 T0004 §7/§11-B: a provider chain fallback within ONE hop must not touch the
    run's token identity. `_execute_provider_chain` (ai_invoke/worker.py:229-285) never
    reissues between chain entries -- reissue only happens one full hop later, in the
    OUTER no-output retry loop via `_prepare_retry_token` (ai_invoke/worker.py:568-640).
    Each provider entry's `_api_execute` re-reads `current_token = run["raw_token"]`
    fresh at its own top (worker.py:850), so this test answers T0004 §7's six questions
    for real instead of asserting them: (1) same run_id -- same `run` dict object,
    never rebuilt; (2)/(3) same token_id/raw_token -- read straight off `run` again,
    never mutated by the failed first attempt; (4) no reissue happens before the
    fallback launches; (5) the second provider's local `current_token` IS
    re-initialized (from the same unchanged `run["raw_token"]`), not carried over as a
    stale Python local; (6) the first provider's spawn failure records a
    `fallback_history` entry and nothing else -- no revoke/consume of the token."""

    def test_second_provider_receives_the_same_raw_token_after_the_first_fails_to_start(
        self, monkeypatch,
    ):
        def fake_get_provider_secret(_scope, provider_id):
            # aip_openai never gets a key -> _api_execute returns before ever reading
            # run["raw_token"] (worker.py:826-839), so the FIRST attempt touches no
            # token at all.
            return None if provider_id == "aip_openai" else "sk-test"

        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", fake_get_provider_secret)

        def fake_call_anthropic(*args, **kwargs):
            return "ok", [{"id": "tc1", "name": "register_document",
                           "input": {"doc_type": "standard", "title": "t", "content": "c"}}], \
                   {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}

        monkeypatch.setattr(svc, "_call_anthropic", fake_call_anthropic)

        seen_tokens = []

        def fake_inbox_register(run, token, _tool_input):
            seen_tokens.append(token)
            return 201, {"doc_id": "d1"}

        monkeypatch.setattr(svc, "_inbox_register", fake_inbox_register)

        run = _api_run(raw_token="tok_original_raw", token_id="tok_original_id")
        started_ok = svc._execute_provider_chain(
            run, [_openai_provider(), _anthropic_provider()], "prompt",
        )

        assert started_ok is True
        # The first provider's failure is filed as a fallback event, never as a token
        # event (no revoke/consume/reissue side channel exists here to check for).
        assert len(run["fallback_history"]) == 1
        assert run["fallback_history"][0]["provider_id"] == "aip_openai"
        assert run["fallback_history"][0]["reason"] == "spawn_failed"
        # The second provider's self-HTTP used the run's ORIGINAL token, unchanged.
        assert seen_tokens == ["tok_original_raw"]
        assert run["raw_token"] == "tok_original_raw"
        assert run["token_id"] == "tok_original_id"
        # Landed on the provider actually being run this attempt.
        assert run["provider_id"] == "aip_anthropic"


class TestImportDependencyGuard:
    """T4's recommended AST guard: neither new provider module may reach into
    chain/review/rework/lease-DB/runtime-registry territory, and the worker module
    does not own lease-acquisition policy or run-id allocation itself."""

    _FORBIDDEN_SUFFIXES = (
        "group_ai_leases",
    )
    # Inside the package these are relative imports, so the guard matches the MODULE
    # NAME: `from . import chain` carries no dotted path and a suffix check would
    # silently stop guarding (NR0003 §28).
    _FORBIDDEN_PACKAGE_MODULES = {"chain", "review", "admission", "diagnostics", "facade"}
    # `runtime` is no longer forbidden outright -- T6 made it the home of the engine's
    # PARAMETERS as well as the registry, and a transport legitimately reads
    # ANTHROPIC_VERSION / OUTPUT_TAIL_BYTES from it. What a transport still may not touch
    # is the registry itself, so the guard moved from the module to those names.
    _FORBIDDEN_RUNTIME_NAMES = {
        "get_run_record", "is_run_live", "active_run_for_group", "list_live_runs",
        "run_list_item_live", "group_resume_lock", "_runs", "_runs_lock",
        "_group_resume_locks", "_auto_resume",
    }

    def _assert_no_forbidden_imports(self, path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    reached = ({a.name for a in node.names} if not module
                               else {module.split(".")[0]})
                    assert not (reached & self._FORBIDDEN_PACKAGE_MODULES), (
                        f"{path.name} imports forbidden package module(s) {reached}"
                    )
                    if module.split(".")[0] == "runtime":
                        taken = {a.name for a in node.names}
                        assert not (taken & self._FORBIDDEN_RUNTIME_NAMES), (
                            f"{path.name} reaches the run registry: "
                            f"{taken & self._FORBIDDEN_RUNTIME_NAMES}"
                        )
                assert not any(module.endswith(s) for s in self._FORBIDDEN_SUFFIXES), (
                    f"{path.name} imports forbidden module {module!r}"
                )
                for alias in node.names:
                    assert not any(alias.name.endswith(s) for s in self._FORBIDDEN_SUFFIXES), (
                        f"{path.name} imports forbidden name {alias.name!r} from {module!r}"
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.endswith(s) for s in self._FORBIDDEN_SUFFIXES), (
                        f"{path.name} imports forbidden module {alias.name!r}"
                    )

    def test_provider_api_module_has_no_forbidden_imports(self):
        self._assert_no_forbidden_imports(_PROVIDER_API_PATH)

    def test_provider_cli_module_has_no_forbidden_imports(self):
        self._assert_no_forbidden_imports(_PROVIDER_CLI_PATH)

    def test_worker_module_does_not_define_lease_acquisition_or_run_id_allocation(self):
        """T3 (flowgate.default.0501 T0008) put run-id allocation and lease
        acquisition/admission outside the worker; T6 gave both an explicit home --
        `ai_invoke/runtime.py` for the run-id counter, `ai_invoke/admission.py` for
        start_run and the lease. Neither may drift back into the worker."""
        tree = ast.parse(_WORKER_PATH.read_text(encoding="utf-8"), filename=str(_WORKER_PATH))
        top_level_names = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        forbidden_names = {"_next_run_id", "start_run", "_acquire_group_lease"}
        offenders = top_level_names & forbidden_names
        assert not offenders, f"ai_invoke/worker.py should not define {offenders}"

    def test_provider_modules_do_not_call_exec_or_eval(self):
        """Neither provider module should itself call exec/eval, which would be an
        unaudited layer of code assembly inside a module that is supposed to be pure
        transport."""
        for path in (_PROVIDER_API_PATH, _PROVIDER_CLI_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in ("exec", "eval"), f"{path.name} calls {node.func.id}()"


class TestFileSplitIsComplete:
    """The old single file is gone and the assembled module still loads every
    function T4's own plan named for each of the three new files."""

    def test_ai_invoke_part2_worker_no_longer_exists(self):
        assert not (_SERVICES_DIR / "ai_invoke_part2_worker.py").exists()
        assert not (_SERVICES_DIR / "ai_invoke_worker.py").exists()

    def test_the_three_new_files_exist(self):
        for path in (_PROVIDER_API_PATH, _PROVIDER_CLI_PATH, _WORKER_PATH):
            assert path.is_file(), path

    @pytest.mark.parametrize("name", [
        # worker orchestration
        "_worker", "_execute_provider_chain", "_classify_end_reason", "_retry_eligible",
        "_judge_hop", "_finalize_run", "_resolve_stop_code", "finished_payload",
        # provider-neutral FlowGate-tool dispatch (stays with worker orchestration)
        "_inbox_register", "_workflow_decide", "_resolve_conflict", "_api_execute",
        # provider API transport
        "_call_openai", "_call_anthropic", "_http_post_json", "_resolve_agent_api_base",
        # provider CLI transport
        "_cli_execute", "_resolve_cli_launch", "_start_progress_watchdog",
        "_recover_cli_last_message",
    ])
    def test_symbol_still_reachable_on_the_assembled_module(self, name):
        assert hasattr(svc, name), f"svc.{name} missing after the T4 file split"


class TestReissueBoundaryPreservesTokenIdentity:
    """0496 T0006 §3.1: TR0005 (rev1, approved) traced the reissue boundary in
    `_prepare_retry_token` (worker.py:560-640) by READING the code -- "token_id and
    raw_token are updated together, in the same function, with nothing else able to
    interleave" -- but never pinned that claim with a test that actually EXECUTES it.
    This class closes that gap: a reusable token must come back untouched; a reissued
    token must land BOTH `run["token_id"]` and `run["raw_token"]` pointing at the SAME
    new grant the instant `_prepare_retry_token` returns (never a half-updated state
    where one identifier is new and the other is stale); and the next provider's own
    entry point (`_api_execute` via `_execute_provider_chain`, which re-reads
    `current_token = run["raw_token"]` fresh at worker.py:850) must itself observe the
    new raw_token when it opens its first mediated self-HTTP call, not just the `run`
    dict in isolation.
    """

    def _run(self, **over):
        run = {
            "run_id": "aiv_reissue_boundary",
            # None (like _api_run()'s default) keeps `_api_execute` on the doc_ref-less
            # compatibility tool-selection branch (worker.py:917) instead of
            # `api_server_tools.definitions_for_run`, which would hit a real DB this
            # test never sets up -- this class is about token identity, not tool
            # routing.
            "doc_ref": None,
            "token_id": "tok_original_id",
            "raw_token": "tok_original_raw",
            "mention": "## original prompt\n",
            "issue_builder": None,
            # None keeps _prepare_retry_token off the group-lease DB update path
            # (worker.py: "if run.get('group_id'): db_group_ai_leases.update_token(...)")
            # -- this class is about token identity, not lease-scope bookkeeping.
            "group_id": None,
        }
        run.update(over)
        return run

    def test_reusable_token_is_returned_untouched(self, monkeypatch):
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": None, "revoked_at": None,
            "expires_at": "2999-01-01T00:00:00+00:00",
        })
        run = self._run()

        prepared = svc._prepare_retry_token(run)

        assert prepared == {"mention": "## original prompt\n", "token_id": "tok_original_id",
                            "token_id_before": "tok_original_id", "reissued": False}
        assert run["token_id"] == "tok_original_id"
        assert run["raw_token"] == "tok_original_raw"

    def test_reissue_lands_token_id_and_raw_token_on_the_same_new_grant(self, monkeypatch):
        # Consumed -> not reusable -> forces the issue_builder reissue path.
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": "2026-07-31T00:00:00+00:00",
            "revoked_at": None, "expires_at": "2999-01-01T00:00:00+00:00",
        })

        def _issue(ai_run_id=None):
            return {"raw_token": "tok_new_raw", "token_id": "tok_new_id",
                    "mention": "## fresh prompt\n"}

        run = self._run(issue_builder=_issue)

        prepared = svc._prepare_retry_token(run)

        assert prepared["reissued"] is True
        assert prepared["token_id_before"] == "tok_original_id"
        assert prepared["token_id"] == "tok_new_id"
        # The instant _prepare_retry_token returns, BOTH identifiers on `run` already
        # point at the SAME new grant -- never one updated ahead of the other.
        assert run["token_id"] == "tok_new_id"
        assert run["raw_token"] == "tok_new_raw"
        assert run["token_id"] != "tok_original_id"
        assert run["raw_token"] != "tok_original_raw"

    def test_next_provider_entry_point_reads_the_reissued_raw_token(self, monkeypatch):
        """Same capture pattern as `TestProviderFallbackPreservesTokenIdentity`: patch
        `_inbox_register` and record what token the mediated self-HTTP call actually
        carried, so this answers "did the reissued raw_token really reach the wire?"
        by execution instead of by reading `current_token = run["raw_token"]` and
        trusting it."""
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": "2026-07-31T00:00:00+00:00",
            "revoked_at": None, "expires_at": "2999-01-01T00:00:00+00:00",
        })

        def _issue(ai_run_id=None):
            return {"raw_token": "tok_new_raw", "token_id": "tok_new_id",
                    "mention": "## fresh prompt\n"}

        run = self._run(issue_builder=_issue)
        prepared = svc._prepare_retry_token(run)
        assert prepared["reissued"] is True
        assert run["raw_token"] == "tok_new_raw"

        # Round out the run to what _execute_provider_chain/_api_execute touch, the
        # same fields _api_run() seeds for TestProviderFallbackPreservesTokenIdentity.
        run.update({
            "project_id": "flowgate", "group_id": "flowgate.default.0501",
            "action_scope": "new", "mode": "single", "docs_target": 1,
            "cancel_event": threading.Event(), "started_mono": time.monotonic(),
            "timeout_sec": 3600, "api_base_url": "http://127.0.0.1:1/flowgate/api/v1",
            "module": "default", "attempt_no": 2, "fallback_history": [],
            "provider": None, "provider_id": None,
        })

        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret",
                            lambda scope, pid: "sk-test")

        def fake_call_anthropic(*args, **kwargs):
            return "ok", [{"id": "tc1", "name": "register_document",
                           "input": {"doc_type": "standard", "title": "t", "content": "c"}}], \
                   {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}

        monkeypatch.setattr(svc, "_call_anthropic", fake_call_anthropic)

        seen_tokens = []

        def fake_inbox_register(run, token, _tool_input):
            seen_tokens.append(token)
            return 201, {"doc_id": "d1"}

        monkeypatch.setattr(svc, "_inbox_register", fake_inbox_register)

        started_ok = svc._execute_provider_chain(
            run, [_anthropic_provider()], prepared["mention"],
        )

        assert started_ok is True
        # The provider entry point's own current_token = run["raw_token"] read the
        # REISSUED value, never the pre-reissue one.
        assert seen_tokens == ["tok_new_raw"]
        assert run["raw_token"] == "tok_new_raw"
        assert run["token_id"] == "tok_new_id"
