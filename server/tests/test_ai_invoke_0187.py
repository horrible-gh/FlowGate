"""flowgate.default.0187 T: AI invoke engine (D0004 / P0005 / L0006).

Covers the termination-pattern measurement harness L0006 §4.3 demands —
① normal exit ② forced tree-kill ③ provider error — asserting the §4.1/§4.3
classifications against real subprocess behavior, with ⓐ document-reach and
ⓑ message-receipt recorded as independent columns in every case. Plus the
document-reach oracle outcomes (§2.6), per-kind last-message recovery (§2.3),
timeout classification, and the admission guards (§5).

Real subprocesses are used for the CLI adapter (sys.executable one-liners);
DB / settings / token layers are monkeypatched — no database required.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

PY = sys.executable


def _provider(name="cli-1", kind="claude", cmd=None, exec_type="cli", pid="aip_test01", **kw):
    p = {
        "id": pid,
        "name": name,
        "exec_type": exec_type,
        "kind": kind,
        "enabled": True,
        "cli_command": cmd,
        "api_base_url": "http://127.0.0.1:1" if exec_type == "api" else None,
        "api_model": "test-model" if exec_type == "api" else None,
        "api_key_set": False,
        "api_key_hint": None,
    }
    p.update(kw)
    return p


class FakeWfseq:
    """Mutable stand-in for db.workflow_sequences (0226 NR0003 §5-1: docs_target is
    derived from the decided sequence's items, never a group-doc-seq subtraction)."""

    def __init__(self):
        self.sequence: dict | None = {"id": 1}
        # Default decided sequence: N/NR realized, T/TR/TS/TSR pending — the pending
        # worker items (TR, TS, TSR) are what a continuous run can still produce.
        self.items: list[dict] = [
            {"item_seq": 1, "type": "N", "result_doc_id": "d-0002-N"},
            {"item_seq": 2, "type": "NR", "result_doc_id": "d-0003-NR"},
            {"item_seq": 3, "type": "T", "result_doc_id": None},
            {"item_seq": 4, "type": "TR", "result_doc_id": None},
            {"item_seq": 5, "type": "TS", "result_doc_id": None},
            {"item_seq": 6, "type": "TSR", "result_doc_id": None},
        ]

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return list(self.items)


class FakeDocs:
    """Mutable stand-in for db.documents limited to what the oracle reads."""

    def __init__(self, baseline_seq=4):
        self.max_seq = baseline_seq
        self.docs: list[dict] = []

    def get_group_max_seq(self, group_id):
        return self.max_seq

    def get_documents_by_group_id(self, group_id):
        return list(self.docs)

    def get_by_id(self, doc_id):
        return {"doc_id": doc_id, "branch": "main"}

    def register(self, seq, doc_type="TR", status="open"):
        self.max_seq = max(self.max_seq, seq)
        self.docs.append({
            "doc_id": f"flowgate.default.0187.{seq:04d}-{doc_type}",
            "seq": seq,
            "status": status,
        })


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """Patch every collaborator start_run touches; return the mutable doc store."""
    docs = FakeDocs()
    wfseq = FakeWfseq()
    # registered_count = rows before the enabled filter (0292 T0003): it is what lets
    # admission tell "nothing registered" from "everything switched off".
    chain_holder = {"providers": [], "source": "system", "registered_count": 0}

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", docs.get_group_max_seq)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", docs.get_documents_by_group_id)
    monkeypatch.setattr(svc.db_docs, "get_by_id", docs.get_by_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, **chain_holder},
    )
    monkeypatch.setattr(
        svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None
    )
    issues: list[dict] = []

    def _issue(**kwargs):
        issues.append(dict(kwargs))
        return {
            "raw_token": "tok_raw_test", "token_id": "tok_20260710_000001",
            "expires_at": "2026-07-11T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        }

    monkeypatch.setattr(svc.token_service, "issue", _issue)
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(
        svc.storage_paths, "get_storage_root",
        lambda *a, **kw: tmp_path / "storage",
    )
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    # Keyword-only group_id pins the group-worktree routing plumb (0187 rev2):
    # dropping the kwarg from the service call would TypeError every test here.
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root",
        lambda pid, branch, *, group_id: src_root,
    )
    monkeypatch.setattr(
        svc.storage_paths, "to_storage_relative",
        lambda path, project=None: str(path),
    )
    # Isolate the registry per test.
    monkeypatch.setattr(svc, "_runs", {})

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        svc, "_broadcast", lambda run, event_type, payload: events.append((event_type, payload))
    )

    return {
        "docs": docs, "wfseq": wfseq, "chain": chain_holder, "events": events,
        "issues": issues, "tmp": tmp_path,
    }


def _start(fake_env, providers, mode="single", target=None, mention="## prompt\ndo the work\n", provider_id=None,
           registered_count=None):
    fake_env["chain"]["providers"] = providers
    # Default to "every registered provider is enabled" — the ordinary case. Tests that
    # care about disabled rows pass registered_count explicitly.
    fake_env["chain"]["registered_count"] = (
        len(providers) if registered_count is None else registered_count
    )
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0187",
        doc_ref="flowgate.default.0187.0001-R",
        action_scope="new",
        mode=mode,
        continuation_target_seq=target,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: mention,
        provider_id=provider_id,
    )


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_issued_token_is_bound_to_the_pinned_provider_and_run(fake_env):
    cmd = f'"{PY}" -c "import sys; sys.stdin.read(); print(\'ok\')"'
    provider = _provider(cmd=cmd, pid="cx_pinned")
    result = _start(fake_env, [provider], provider_id="cx_pinned")
    issued = fake_env["issues"][-1]

    assert issued["provider_id"] == "cx_pinned"
    assert issued["ai_run_id"] == result["run_id"]
    _wait_finished(result["run_id"])

# ── ① Normal exit (harness case 1) ───────────────────────────────────────────

class TestNormalExit:
    def test_exited_classification_and_columns(self, fake_env):
        cmd = f'"{PY}" -c "import sys; data=sys.stdin.read(); print(\'DONE \' + str(len(data)))"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        run = _wait_finished(res["run_id"])

        assert run["end_reason"] == "exited"          # §4.1 default branch
        assert run["exit_code"] == 0
        assert run["fallback_history"] == []          # started_ok on attempt 1
        # ⓐ document reach and ⓑ message receipt are independent columns:
        assert run["outcome"] == "none"               # no docs registered
        assert run["docs_reached"] == 0
        assert run["last_message_received"] is True   # dying message still captured
        assert "DONE" in run["last_message"]

    def test_stdin_injection_carries_full_prompt(self, fake_env):
        # The prompt is injected via stdin (never argv — cp932 truncation);
        # the child echoes the byte count so we can prove full delivery.
        mention = "M" * 5000
        cmd = f'"{PY}" -c "import sys; print(len(sys.stdin.read()))"'
        res = _start(fake_env, [_provider(cmd=cmd)], mention=mention)
        run = _wait_finished(res["run_id"])
        assert run["last_message"].strip() == "5000"

    def test_doc_reach_complete_with_normal_exit(self, fake_env):
        docs = fake_env["docs"]
        cmd = f'"{PY}" -c "import sys; sys.stdin.read(); print(\'ok\')"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        docs.register(5, "TR")  # the "AI" registered the target doc during the run
        run = _wait_finished(res["run_id"])
        assert run["outcome"] == "complete"
        assert run["docs_reached"] == 1
        assert run["reached_doc_ids"] == ["flowgate.default.0187.0005-TR"]
        # complete ⇒ scratch removed, not retained (§2.7)
        assert run["scratch_retained"] is None
        assert not Path(run["scratch_dir"]).exists()


# ── ② Forced tree-kill (harness case 2) ──────────────────────────────────────

class TestForcedKill:
    def test_cancel_kills_and_classifies_cancelled(self, fake_env):
        cmd = f'"{PY}" -c "import sys, time; time.sleep(60)"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        # Give the worker a moment to spawn the child, then cancel.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (svc.get_run_record(res["run_id"]) or {}).get("proc") is None:
            time.sleep(0.05)
        out = svc.cancel_run(res["run_id"])
        assert out["status"] in ("cancelling", "finished")

        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "cancelled"       # §4.1 cancel branch
        assert run["outcome"] == "none"
        assert run["last_message_received"] is False  # killed ⇒ no dying message...
        # ...but a kill never falls back to another provider (§4.3):
        assert run["fallback_history"] == []
        # failed/cancelled run retains its scratch (§2.7)
        assert run["scratch_retained"] is not None
        assert Path(run["scratch_dir"]).exists()

    def test_cancel_after_finish_is_idempotent(self, fake_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        _wait_finished(res["run_id"])
        out = svc.cancel_run(res["run_id"])
        assert out == {"ok": True, "run_id": res["run_id"], "status": "finished"}

    def test_timeout_kills_and_classifies_timeout(self, fake_env, monkeypatch):
        monkeypatch.setattr(svc, "RUN_TIMEOUT_BASE_SEC", 1)
        monkeypatch.setattr(svc, "RUN_TIMEOUT_CAP_SEC", 1)
        cmd = f'"{PY}" -c "import sys, time; time.sleep(60)"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "timeout"         # §4.1 timeout branch
        assert run["exit_code"] is None
        assert run["last_message_received"] is False
        assert run["fallback_history"] == []          # timeout is not a startup failure


# ── ③ Provider error (harness case 3) ────────────────────────────────────────

class TestProviderError:
    def test_runtime_selection_moves_provider_to_chain_head(self, fake_env):
        first = _provider(name="first", cmd=f'"{PY}" -c "print(1)"', pid="aip_first")
        selected = _provider(name="selected", cmd=f'"{PY}" -c "print(2)"', pid="aip_selected")
        res = _start(fake_env, [first, selected], provider_id="aip_selected")
        run = _wait_finished(res["run_id"])
        assert res["provider"]["id"] == "aip_selected"
        assert run["provider_id"] == "aip_selected"

    def test_explicit_runtime_selection_never_falls_back(self, fake_env):
        good = _provider(name="good", cmd=f'"{PY}" -c "print(1)"', pid="aip_goodpin")
        selected = _provider(name="selected", cmd=f'"{PY}" -c "import sys; sys.exit(7)"',
                             pid="aip_selectedpin")
        res = _start(fake_env, [good, selected], provider_id="aip_selectedpin")
        run = _wait_finished(res["run_id"])

        assert res["provider"]["id"] == "aip_selectedpin"
        assert run["attempt_no"] == 1
        assert run["end_reason"] == "all_providers_failed"
        assert [item["provider_id"] for item in run["fallback_history"]] == ["aip_selectedpin"]
        assert not any(item["provider_id"] == "aip_goodpin" for item in run["fallback_history"])

    def test_unknown_runtime_selection_is_rejected(self, fake_env):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [_provider(cmd=f'"{PY}" -c "print(1)"')], provider_id="aip_missing")
        assert exc.value.status_code == 422

    def test_fast_fail_falls_back_to_next_provider(self, fake_env):
        bad = _provider(name="bad", cmd=f'"{PY}" -c "import sys; sys.exit(3)"', pid="aip_bad001")
        good = _provider(name="good", cmd=f'"{PY}" -c "import sys; sys.stdin.read(); print(\'rescued\')"',
                         pid="aip_good01")
        res = _start(fake_env, [bad, good])
        run = _wait_finished(res["run_id"])

        assert res["selected_provider_source"] == "project_default"
        assert res["fallback_allowed"] is True
        assert run["selected_provider_source"] == "project_default"
        assert run["fallback_allowed"] is True
        assert run["end_reason"] == "exited"
        assert run["provider_id"] == "aip_good01"
        assert run["attempt_no"] == 2
        assert len(run["fallback_history"]) == 1
        assert run["fallback_history"][0]["provider_id"] == "aip_bad001"
        assert run["fallback_history"][0]["reason"] == "fast_fail"   # §2.2 table
        assert run["last_message"] == "rescued"

    def test_all_providers_failed(self, fake_env):
        p1 = _provider(name="b1", cmd=f'"{PY}" -c "import sys; sys.exit(1)"', pid="aip_b1")
        p2 = _provider(name="b2", cmd=f'"{PY}" -c "import sys; sys.exit(2)"', pid="aip_b2")
        res = _start(fake_env, [p1, p2])
        run = _wait_finished(res["run_id"])

        assert run["end_reason"] == "all_providers_failed"           # §4.1
        assert run["provider_id"] is None
        assert run["outcome"] == "none"
        assert [f["reason"] for f in run["fallback_history"]] == ["fast_fail", "fast_fail"]
        assert run["last_message_received"] is False

    def test_api_key_missing_is_spawn_failed(self, fake_env):
        api = _provider(name="api-nokey", exec_type="api", kind="openai", pid="aip_api01")
        good = _provider(name="good", cmd=f'"{PY}" -c "import sys; sys.stdin.read(); print(\'x\')"',
                         pid="aip_good02")
        res = _start(fake_env, [api, good])
        run = _wait_finished(res["run_id"])
        assert res["selected_provider_source"] == "project_default"
        assert res["fallback_allowed"] is True
        assert run["selected_provider_source"] == "project_default"
        assert run["fallback_allowed"] is True
        assert run["fallback_history"][0]["reason"] == "spawn_failed"
        assert run["fallback_history"][0]["detail"] == "api_key_not_set"
        assert run["provider_id"] == "aip_good02"

    def test_slow_nonzero_exit_does_not_fall_back(self, fake_env, monkeypatch):
        # Past the fast-fail window a failure means the provider DID start work —
        # falling back would double-execute the task (D0004 §3-2).
        monkeypatch.setattr(svc, "FAST_FAIL_WINDOW_SEC", 0)
        bad_late = _provider(name="late", cmd=f'"{PY}" -c "import sys; sys.exit(9)"', pid="aip_late1")
        never = _provider(name="never", cmd=f'"{PY}" -c "print(1)"', pid="aip_never1")
        res = _start(fake_env, [bad_late, never])
        run = _wait_finished(res["run_id"])
        assert run["provider_id"] == "aip_late1"
        assert run["fallback_history"] == []
        assert run["end_reason"] == "exited"
        assert run["exit_code"] == 9
        assert run["outcome"] == "none"


# ── Last-message recovery per kind (§2.3) ────────────────────────────────────

class TestLastMessageRecovery:
    def test_codex_output_last_message_file(self, fake_env):
        # codex gets --output-last-message appended; argv[-1] is that file path.
        cmd = (
            f'"{PY}" -c "import sys; sys.stdin.read(); '
            f"open(sys.argv[-1], 'w').write('codex tail message')\""
        )
        res = _start(fake_env, [_provider(kind="codex", cmd=cmd)])
        run = _wait_finished(res["run_id"])
        assert run["last_message_received"] is True
        assert run["last_message"] == "codex tail message"

    def test_copilot_takes_last_stdout_block(self, fake_env):
        cmd = (
            f'"{PY}" -c "import sys; sys.stdin.read(); '
            f"print('first block'); print(); print('final block')\""
        )
        res = _start(fake_env, [_provider(kind="copilot", cmd=cmd)])
        run = _wait_finished(res["run_id"])
        assert run["last_message"] == "final block"

    def test_blank_output_means_not_received(self, fake_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        res = _start(fake_env, [_provider(kind="claude", cmd=cmd)])
        run = _wait_finished(res["run_id"])
        assert run["last_message_received"] is False
        assert run["last_message"] is None


# ── Registration diagnostics and no-tool nudges (group 0231) ─────────────────

def _registration_api_run() -> dict:
    return {
        "project_id": "flowgate", "chain_source": "system", "run_id": "aiv_register",
        "docs_target": 1, "raw_token": "doc-token", "action_scope": "new", "mode": "single",
        "cancel_event": threading.Event(), "started_mono": time.monotonic(), "timeout_sec": 30,
    }


class TestRegistrationDiagnostics:
    def test_first_valid_tool_call_ends_with_consistent_api_diagnostics(self, monkeypatch):
        handler_calls = []
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
        tool = {"id": "tc1", "name": "register_document", "input": {
            "doc_type": "TR", "title": "done", "content": "body",
        }}
        monkeypatch.setattr(svc, "_call_openai", lambda *_: (
            "registering", tool, {"role": "assistant", "content": "registering", "tool_calls": []},
        ))
        monkeypatch.setattr(svc, "_inbox_register", lambda *args: (
            handler_calls.append(args) or (201, {"ok": True, "doc_id": "d"})
        ))

        run = _registration_api_run()
        assert svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run) == ("started_ok", None)

        assert len(handler_calls) == 1
        # 0501 T0004 필수5: exit_code=None is the API contract even on a normal,
        # successful registration -- there is no subprocess to hand back a real code,
        # and this run dict is what the real turn loop produced, not a hand-built one.
        assert run["exit_code"] is None
        assert run["turn_limit_exhausted"] is False
        assert run["api_turns_used"] == 1
        assert run["model_http_calls"] == 1
        assert run["model_last_http_status"] == 200
        assert run["tool_calls_received"] == 1
        assert run["tool_calls_executed"] == 1
        assert run["api_turn_trace"] == [{
            "turn": 1, "model_status": 200, "response_text": True,
            "received": 1, "valid": 0, "dispatched": 1,
            "completion_selected": False, "register_attempted": True,
            "register_succeeded": True,
            "tools": [{"name": "register_document", "status": 201, "registration": True}],
            "disposition": "registered",
        }]

    def test_direct_tools_only_turn_reproduces_oracle_mismatch_trace(self, monkeypatch, fake_env):
        calls = 0
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
        monkeypatch.setattr(svc.api_server_tools, "definitions_for_run", lambda _run: [{
            "name": "run_test", "schema": {"type": "object"},
        }])
        monkeypatch.setattr(svc.api_server_tools, "run_test", lambda *_: (200, {"ok": True}))

        def model(*_args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("model closed after direct tool")
            tool = {"id": "tc-direct", "name": "run_test", "input": {}}
            return "worked", tool, {"role": "assistant", "content": "worked", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", model)
        monkeypatch.setattr(svc, "_inbox_register", lambda *_: pytest.fail("direct tool must not register"))
        run = _registration_api_run()
        run.update({
            "doc_ref": "flowgate.default.0187.0001-B", "group_id": "flowgate.default.0187",
            "baseline_seq": 4, "end_reason": "exited",
        })

        assert svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run) == ("started_ok", None)
        svc._judge_hop(run)

        first_turn = run["api_turn_trace"][0]
        assert first_turn["disposition"] == "direct_tools_only"
        assert first_turn["completion_selected"] is False
        assert first_turn["register_attempted"] is False
        assert first_turn["tools"] == [{"name": "run_test", "status": 200, "registration": False}]
        assert run["register_errors"] == []
        assert run["tool_call_misses"] == 0
        assert run["turn_limit_exhausted"] is False
        assert run["oracle_mismatch"] is True

    def test_missing_tool_is_nudged_twice_then_registration_succeeds(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")

        def model(*args):
            calls.append([dict(message) for message in args[3]])
            if len(calls) < 3:
                return "attached", None, {"role": "assistant", "content": "attached"}
            tool = {"id": "tc3", "name": "register_document", "input": {
                "doc_type": "TR", "title": "done", "content": "body",
            }}
            return "registering", tool, {"role": "assistant", "content": "registering", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", model)
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: (200, {"ok": True, "doc_id": "d"}))
        run = _registration_api_run()
        result = svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run)

        assert result == ("started_ok", None)
        assert len(calls) == 3
        assert run["tool_call_misses"] == 2
        assert "Call the `register_document` tool" in calls[1][-1]["content"]
        assert run["turn_limit_exhausted"] is False

    def test_registration_failures_are_recorded_and_turn_limit_is_visible(self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")

        def model(*args):
            tool = {"id": "tc", "name": "register_document", "input": {
                "doc_type": "TR", "title": "dup", "content": "same",
            }}
            return "done", tool, {"role": "assistant", "content": "done", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", model)
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: (409, {"code": "dup_body"}))
        run = _registration_api_run()
        svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run)

        assert len(run["register_errors"]) == svc.API_MAX_TURNS_PER_DOC
        assert run["register_errors"][0] == {"status": 409, "reason": "dup_body", "turn": 1}
        assert run["turn_limit_exhausted"] is True
        assert run["api_turns_used"] == svc.API_MAX_TURNS_PER_DOC
        assert run["model_http_calls"] == svc.API_MAX_TURNS_PER_DOC
        assert run["model_last_http_status"] == 200

    def test_model_failure_on_final_turn_is_not_turn_limit_exhaustion(self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
        monkeypatch.setattr(svc, "API_MAX_TURNS_PER_DOC", 2)
        attempts = 0

        def model(*_args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return "no tool", None, {"role": "assistant", "content": "no tool"}
            raise urllib.error.HTTPError("https://api.example", 503, "unavailable", None, None)

        monkeypatch.setattr(svc, "_call_openai", model)
        run = _registration_api_run()
        assert svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run) == ("started_ok", None)

        assert run["turn_limit_exhausted"] is False
        assert run["api_turns_used"] == 2
        assert run["model_http_calls"] == 2
        assert run["model_last_http_status"] == 503

    def test_first_model_transport_failure_records_attempted_turn(self, monkeypatch):
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
        monkeypatch.setattr(svc, "_call_openai", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
        run = _registration_api_run()

        assert svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run) == ("spawn_failed", "offline")
        assert run["turn_limit_exhausted"] is False
        assert run["api_turns_used"] == 1
        assert run["model_http_calls"] == 1
        assert run["model_last_http_status"] == 0

    def test_exit_code_none_reads_as_completed_not_failed_on_a_real_run(self, monkeypatch):
        """0501 T0004 필수5: test_no_output_detail_branching.py's
        test_api_provider_fallback_to_completed_on_exit_none proves the same text on a
        run dict it authors by hand ({"exit_code": None, ...}). This is the same contract
        pinned to a run dict svc._api_execute() itself produced -- no register_errors, no
        tool_call_misses, no turn_limit_exhausted, exit_code left at None -- which is
        exactly what happens when the hop's cancel flag trips before the first model
        turn: _api_execute breaks out of its loop immediately, without ever touching any
        of the other diagnostic fields, and still sets exit_code=None on its way out
        because there is no subprocess to hand one back. _no_output_detail must read
        that as a benign 'completed', not the CLI-style 'worker exited None ... failed'.
        """
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
        provider = _provider(exec_type="api", kind="openai")
        run = _registration_api_run()
        run["cancel_event"].set()  # tripped before the worker's first model turn
        # _worker sets this one line ahead of every _api_execute call (ai_invoke_part2_worker.py
        # line 70); _api_execute itself never touches run["provider"], so a caller that skips
        # _worker has to reproduce that exact assignment for _no_output_detail's exec_type
        # branch to see this as an API run at all.
        run["provider"] = svc._provider_brief(provider)

        assert svc._api_execute(provider, "prompt", run) == ("started_ok", None)
        assert run["exit_code"] is None
        assert run["register_errors"] == []
        assert run["tool_call_misses"] == 0
        assert run["turn_limit_exhausted"] is False

        detail = svc._no_output_detail(run)
        assert detail == "completed: no output to register"
        assert "worker exited" not in detail


# ── Workflow-decision API loop (group 0223) ──────────────────────────────────

def _workflow_api_run(mode: str, *, target_to_end: bool = False) -> dict:
    return {
        "project_id": "flowgate", "chain_source": "system", "run_id": "aiv_workflow",
        "docs_target": 0 if mode == "single" else 1, "raw_token": "decision-token",
        "action_scope": "workflow_decide", "mode": mode,
        "doc_ref": "flowgate.default.0187.0001-R",
        "cancel_event": threading.Event(), "started_mono": time.monotonic(),
        "timeout_sec": 30, "target_to_end": target_to_end, "baseline_seq": 4,
        # start_run always sets this; the to-end branch of the API loop reads it by key, so a
        # fixture without it fails on a KeyError that no production run can reach.
        "continuation_instruction_mode": None,
    }


class TestWorkflowDecisionApiLoop:
    def test_single_stops_after_decision(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")

        def model(*args):
            tool_name = args[5]
            calls.append(tool_name)
            return "decided", {
                "id": "tc1", "name": tool_name,
                "input": {"doc_class": "standard", "sequence": ["T"]},
            }, {"role": "assistant", "content": "decided", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", model)
        monkeypatch.setattr(svc, "_workflow_decide", lambda *a: (
            200, {"next_token": "doc-token", "next_mention": "next"}))
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: pytest.fail(
            "single workflow decision must not register a document"))
        result = svc._api_execute(
            _provider(exec_type="api", kind="openai"), "prompt", _workflow_api_run("single"))
        assert result[0] == "started_ok"
        assert calls == ["decide_workflow"]

    def test_continuous_switches_from_decision_to_registration(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")

        def model(*args):
            tool_name = args[5]
            calls.append(tool_name)
            payload = ({"doc_class": "standard", "sequence": ["T"]}
                       if tool_name == "decide_workflow"
                       else {"doc_type": "T", "title": "title", "content": "content"})
            return "worked", {"id": f"tc{len(calls)}", "name": tool_name, "input": payload}, {
                "role": "assistant", "content": "worked", "tool_calls": []}

        monkeypatch.setattr(svc, "_call_openai", model)
        monkeypatch.setattr(svc, "_workflow_decide", lambda *a: (200, {
            "next_token": "doc-token", "next_mention": "next", "continuation_target_seq": 2,
        }))
        monkeypatch.setattr(svc, "_inbox_register", lambda *a: (200, {"ok": True}))
        # 0226 NR0003 §5-1: the resolved to-end target (an item_seq) is now counted
        # against the decided sequence's worker items ([T, TR] ⇒ 1 doc, the TR).
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda d: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda s: [
            {"item_seq": 1, "type": "T", "result_doc_id": None},
            {"item_seq": 2, "type": "TR", "result_doc_id": None},
        ])
        run = _workflow_api_run("continuous", target_to_end=True)
        result = svc._api_execute(_provider(exec_type="api", kind="openai"), "prompt", run)
        assert result[0] == "started_ok"
        assert calls == ["decide_workflow", "register_document"]
        assert run["docs_target"] == 1

# ── Document-reach oracle (§2.6) ─────────────────────────────────────────────

class TestOracle:
    def _finished_run(self, fake_env, docs_target, registered_seqs, draft_seqs=()):
        docs = fake_env["docs"]
        for seq in registered_seqs:
            docs.register(seq)
        for seq in draft_seqs:
            docs.register(seq, status="draft")
        run = {
            "run_id": "aiv_test", "status": "running", "mode": "continuous",
            "project_id": "flowgate", "module": "default",
            "group_id": "flowgate.default.0187",
            "doc_ref": "flowgate.default.0187.0001-R",
            "docs_target": docs_target, "baseline_seq": 4,
            "timeout_sec": 60, "provider": None, "provider_id": "aip_x",
            "attempt_no": 1, "fallback_history": [],
            "started_at": "t", "started_mono": time.monotonic(),
            "cancel_event": threading.Event(), "proc": None,
            "timed_out": False, "end_reason": "exited", "exit_code": 0,
            "last_message": None, "last_message_received": False,
            "outcome": None, "docs_reached": 0, "reached_doc_ids": [],
            "source_dirty": None, "source_dirty_files": [],
            "scratch_dir": str(fake_env["tmp"] / "storage" / "scratch" / "testproj" / "aiv_test"),
            "scratch_retained": None, "duration_ms": None, "finished_at": None,
            "dirty_baseline": None, "source_root": None,
            "api_base_url": "", "chain_source": "system", "raw_token": "x",
        }
        Path(run["scratch_dir"]).mkdir(parents=True, exist_ok=True)
        svc._settle_and_judge(run)
        return run

    def test_complete(self, fake_env):
        run = self._finished_run(fake_env, 3, [5, 6, 7])
        assert (run["outcome"], run["docs_reached"]) == ("complete", 3)

    def test_partial(self, fake_env):
        run = self._finished_run(fake_env, 4, [5, 6])
        assert (run["outcome"], run["docs_reached"]) == ("partial", 2)
        assert run["scratch_retained"] is not None

    def test_none_default(self, fake_env):
        run = self._finished_run(fake_env, 1, [])
        assert (run["outcome"], run["docs_reached"]) == ("none", 0)
        assert run["oracle_mismatch"] is True

    def test_drafts_do_not_count(self, fake_env):
        run = self._finished_run(fake_env, 1, [], draft_seqs=[5])
        assert (run["outcome"], run["docs_reached"]) == ("none", 0)


# ── Admission guards (§2.1 / §5) ─────────────────────────────────────────────

class TestAdmission:
    def test_no_enabled_provider(self, fake_env):
        from fastapi import HTTPException
        # Providers exist, they are just all switched off.
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [], registered_count=2)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "no_enabled_provider"

    def test_no_provider_registered_is_its_own_code(self, fake_env):
        """0292 T0003: an install that skipped the provider seed must not be told to go
        toggle rows that do not exist — it is told what to run instead."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [], registered_count=0)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "no_provider_registered"
        assert "setup-ai" in exc.value.detail["message"]

    def test_run_in_progress(self, fake_env):
        from fastapi import HTTPException
        cmd = f'"{PY}" -c "import sys, time; time.sleep(30)"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        try:
            with pytest.raises(HTTPException) as exc:
                _start(fake_env, [_provider(cmd=cmd)])
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "run_in_progress"
            assert exc.value.detail["run_id"] == res["run_id"]
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_continuous_requires_decided_sequence(self, fake_env):
        # 0226 NR0003 §5-1: the target lives in the workflow item_seq space, so a
        # continuous run without a decided sequence has no coordinate system at all.
        from fastapi import HTTPException
        fake_env["wfseq"].sequence = None
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [_provider(cmd="echo hi")], mode="continuous", target=6)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "validation_failed"

    def test_continuous_target_below_head_is_rejected(self, fake_env):
        # No pending worker item at or below the target ⇒ nothing to run (replaces the
        # old group-doc-seq "must exceed baseline" guard in the item_seq space).
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [_provider(cmd="echo hi")], mode="continuous", target=2)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "validation_failed"

    def test_continuous_docs_target_counts_pending_worker_items(self, fake_env):
        # Fixture sequence: T(3)/TR(4)/TS(5)/TSR(6) pending — instruction head T is
        # auto-created server-side (draft, oracle-invisible), so target 6 ⇒ 3 worker docs.
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        res = _start(fake_env, [_provider(cmd=cmd)], mode="continuous", target=6)
        assert res["docs_target"] == 3
        _wait_finished(res["run_id"])

    def test_continuous_docs_target_survives_sparse_item_seq(self, fake_env):
        # 0226 B0001 ② reproduction: edit_workflow_pending renumbers the pending tail
        # past max_item_seq (items 18–21) while the group doc seq sits at 12. The old
        # subtraction targeted 21-12=9 docs for 4 actual steps ("0/9"); item_seq-space
        # counting yields the 3 worker docs (TR·TS·TSR — the T head auto-completes).
        fake_env["docs"].max_seq = 12
        fake_env["wfseq"].items = [
            {"item_seq": 18, "type": "T", "result_doc_id": None},
            {"item_seq": 19, "type": "TR", "result_doc_id": None},
            {"item_seq": 20, "type": "TS", "result_doc_id": None},
            {"item_seq": 21, "type": "TSR", "result_doc_id": None},
        ]
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        res = _start(fake_env, [_provider(cmd=cmd)], mode="continuous", target=21)
        assert res["docs_target"] == 3  # TR + TS + TSR (T auto-completes as draft)
        _wait_finished(res["run_id"])

    def test_mention_unavailable(self, fake_env):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _start(fake_env, [_provider(cmd="echo hi")], mention=None)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "mention_unavailable"

    def test_unknown_run_lookup(self, fake_env):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            svc.get_status("aiv_missing")
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "run_not_found"

    def test_active_status_is_group_scoped_and_redacted(self, fake_env):
        run = {
            "run_id": "aiv_active", "status": "running", "mode": "single",
            "group_id": "flowgate.default.0187",
            "doc_ref": "flowgate.default.0187.0001-R",
            "docs_target": 1, "baseline_seq": 4,
            "provider": {"id": "aip_test01", "name": "Codex"},
            "attempt_no": 1, "started_at": "2026-07-13T00:00:00+00:00",
            "started_mono": time.monotonic(),
            # These fields must never escape through the restore endpoint.
            "raw_token": "secret", "proc": object(),
        }
        svc._runs[run["run_id"]] = run

        active = svc.get_active_status("flowgate.default.0187")
        inactive = svc.get_active_status("flowgate.default.9999")

        assert active["active"] is True
        assert active["run_id"] == "aiv_active"
        assert active["doc_ref"] == "flowgate.default.0187.0001-R"
        assert "raw_token" not in active and "proc" not in active
        assert inactive == {
            "ok": True, "active": False, "group_id": "flowgate.default.9999",
        }


# ── SSE contract (P0005) ─────────────────────────────────────────────────────

class TestWorkerVisibility:
    """0406 T0022 작업 3 — 실행 중에도 "누가 이 홉을 수행하는가"를 답한다.

    NR0021 §11 이 확정한 현상은, auto_approved 에서 서버가 N/T 를 대신 작성·승인해
    AI 워커가 아예 붙지 않았고 그 사실이 어디에도 남지 않았다는 것이다. 끝난 뒤의
    기록만으로는 부족하다 — 시작 응답과 시작 이벤트가 먼저 답해야 실행 중 화면이
    "N/T 가 사라졌다"와 "TR 워커가 정상 실행됐다"를 구분해 그릴 수 있다.
    """

    def test_start_response_names_the_worker_and_the_mode_it_actually_used(self, fake_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read(); print(\'ok\')"'
        res = _start(fake_env, [_provider(cmd=cmd)], mode="continuous", target=6)
        _wait_finished(res["run_id"])

        for key in (
            "continuation_instruction_mode",
            "continuation_instruction_mode_requested",
            "continuation_instruction_mode_normalized",
            "continuation_instruction_mode_fallback_applied",
            "hop_item_seq",
            "worker_document_type",
            "auto_handled_item_seqs",
        ):
            assert key in res, key
        # _start 는 모드를 보내지 않는다 — 하위호환대로 auto_approved 로 접히되,
        # 접혔다는 사실이 응답에 적혀 있다(작업 2).
        assert res["continuation_instruction_mode"] == "auto_approved"
        assert res["continuation_instruction_mode_requested"] is None
        assert res["continuation_instruction_mode_normalized"] == "auto_approved"
        assert res["continuation_instruction_mode_fallback_applied"] is True

    def test_started_event_carries_the_same_worker_facts(self, fake_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read(); print(\'ok\')"'
        res = _start(fake_env, [_provider(cmd=cmd)], mode="continuous", target=6)
        _wait_finished(res["run_id"])

        started = next(p for name, p in fake_env["events"] if name == "ai_invoke_started")
        for key in ("hop_item_seq", "worker_document_type", "auto_handled_item_seqs"):
            assert key in started, key
        assert started["auto_handled_item_seqs"] == res["auto_handled_item_seqs"]


class TestEvents:
    def test_started_switched_finished_sequence(self, fake_env):
        bad = _provider(name="bad", cmd=f'"{PY}" -c "import sys; sys.exit(3)"', pid="aip_bad9")
        good = _provider(name="good", cmd=f'"{PY}" -c "import sys; sys.stdin.read(); print(\'m\')"',
                         pid="aip_good9")
        res = _start(fake_env, [bad, good])
        _wait_finished(res["run_id"])
        names = [name for name, _ in fake_env["events"]]
        assert names == [
            "ai_invoke_started",
            "ai_invoke_provider_switched",
            "ai_invoke_finished",
            "group_view_refresh",
        ]
        started = fake_env["events"][0][1]
        assert started["provider_id"] == "aip_bad9"
        assert started["provider_name"] == "bad"
        assert started["started_at"] == res["started_at"]
        switched = fake_env["events"][1][1]
        assert switched["from_provider_id"] == "aip_bad9"
        assert switched["to_provider_id"] == "aip_good9"
        assert switched["reason"] == "fast_fail"
        finished = fake_env["events"][2][1]
        for key in (
            "run_id", "group_id", "outcome", "docs_reached", "docs_target",
            "reached_doc_ids", "end_reason", "exit_code", "last_message_received",
            "last_message", "provider_id", "provider_name", "attempt_no", "fallback_history",
            "source_dirty", "duration_ms", "started_at",
        ):
            assert key in finished
        assert finished["provider_name"] == "good"
        assert finished["started_at"] == res["started_at"]


# ══════════════════════════════════════════════════════════════════════════════════════
# flowgate.default.0501 T0004 필수4 -- terminal run lifecycle: lease release
# ══════════════════════════════════════════════════════════════════════════════════════

class TestTerminalLeaseRelease0501:
    """The other half of TestEvents' finished-notification coverage above: a normal
    terminal finish must also release the group's AI lease, through the real
    _finalize_run path -- not the inspect.getsource() text guard in
    test_document_ai_running_guard_0378.py::test_hop_handoff_marks_releasing_before_successor_spawn,
    which never executes the code it greps for, and not the incidental fact that this
    same GROUP is reused, lease and all, by every other test in this file (nothing
    asserts on that; it would only ever fail as an unrelated 409 two tests later).
    """

    def test_normal_finish_releases_the_group_lease(self, fake_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read(); print(\'ok\')"'
        res = _start(fake_env, [_provider(cmd=cmd)])
        assert svc.db_group_ai_leases.get("flowgate.default.0187") is not None, (
            "setup check: the lease must actually be held while the run is live")

        run = _wait_finished(res["run_id"])

        assert run["status"] == "finished"
        assert svc.db_group_ai_leases.get("flowgate.default.0187") is None, (
            "a normal terminal finish must release the lease, or the next hop/run in "
            "this group is permanently blocked behind a dead run_id")
