"""flowgate.default.0501 T0008 (T3): ai_invoke_runtime module boundary.

Covers the completion conditions T0008 T3 §15 asks for on the NEW runtime-registry
access module:

  * the mutable registry (`_runs`/`_runs_lock`/`_group_resume_locks`) has exactly ONE
    owner -- `ai_invoke_service.py` -- and a test that resets it there (the existing,
    pervasive `monkeypatch.setattr(svc, "_runs", ...)` pattern) is immediately visible
    through every `ai_invoke_runtime` accessor (T3 §9's alias-divergence warning, made
    concrete: an earlier draft of this module copied `_runs` by reference at import
    time and silently broke `test_get_run_detail_finished_memory_has_dialog_fields`)
  * the module never imports worker/provider/chain execution detail (T3 §13-F)
  * the compatibility facade (`svc.get_run_record` etc.) still produces the same
    results as calling the new module directly
  * T0008 §13-B/C/D/E: `start_run`'s own admission/collision code, `cancel_run`'s
    primitive, and the `start_run` facade itself -- none of which were extracted --
    still behave exactly as before once `is_run_live`/`get_run_record`/
    `_active_run_for_group` are served by the new module instead of being inlined
    in ai_invoke_service.py
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
_RUNTIME_PATH = (
    _SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke_runtime.py"
)

_FORBIDDEN_IMPORT_SUFFIXES = (
    # flowgate.default.0501 T4 re-split ai_invoke_part2_worker.py into these three;
    # T5 re-split ai_invoke_part3_chain.py into chain/review; the guard must track
    # the current file names, not any earlier one.
    "ai_invoke_worker",
    "ai_invoke_provider_api",
    "ai_invoke_provider_cli",
    "ai_invoke_chain",
    "ai_invoke_review",
    "process_runner",
)

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

PY = sys.executable


class TestModuleOwnsNoExecutionDetail:
    def test_direct_import_succeeds_standalone(self):
        import importlib

        from modules.flow_gate.services import ai_invoke_runtime

        importlib.reload(ai_invoke_runtime)
        assert callable(ai_invoke_runtime.get_run_record)

    def test_source_never_imports_worker_provider_or_chain_modules(self):
        """AST guard for T0008 T3 §13-F: no execution-layer dependency leak."""
        tree = ast.parse(_RUNTIME_PATH.read_text(encoding="utf-8"), filename=str(_RUNTIME_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(module.endswith(suffix) for suffix in _FORBIDDEN_IMPORT_SUFFIXES)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        alias.name.endswith(suffix) for suffix in _FORBIDDEN_IMPORT_SUFFIXES
                    )
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else None
                assert name != "exec"

    def test_source_only_reaches_ai_invoke_service_via_a_lazy_function_local_import(self):
        """The one intentional exception to 'no reverse import' (T3 §9): a deferred
        import inside a function body, never at module top level -- see the module
        docstring for why the registry objects cannot be copied by reference."""
        tree = ast.parse(_RUNTIME_PATH.read_text(encoding="utf-8"), filename=str(_RUNTIME_PATH))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                assert node.module != "modules.flow_gate.services.ai_invoke_service"
                assert not (node.module or "").endswith(".ai_invoke_service")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.endswith("ai_invoke_service")


class TestRegistryHasOneOwner:
    """The registry is defined once, in ai_invoke_service.py. A test (or any other
    caller) that resets `svc._runs` by full reassignment -- the existing, pervasive
    pattern across ~30 other test files -- must be immediately visible through every
    ai_invoke_runtime accessor, with no copied/aliased dict left stale."""

    def test_full_reassignment_of_svc_runs_is_observed_by_get_run_record(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        run = {"run_id": "aiv_probe", "group_id": "g1", "status": "running"}
        monkeypatch.setattr(svc, "_runs", {"aiv_probe": run})
        assert runtime.get_run_record("aiv_probe") is run
        assert runtime.is_run_live("aiv_probe") is True

        monkeypatch.setattr(svc, "_runs", {})
        assert runtime.get_run_record("aiv_probe") is None
        assert runtime.is_run_live("aiv_probe") is False

    def test_full_reassignment_of_svc_runs_is_observed_by_active_run_for_group(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        run = {"run_id": "aiv_probe2", "group_id": "g2", "status": "running"}
        monkeypatch.setattr(svc, "_runs", {"aiv_probe2": run})
        assert runtime.active_run_for_group("g2") is run
        assert runtime.active_run_for_group("g_absent") is None

    def test_full_reassignment_of_svc_runs_is_observed_by_list_live_runs(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        run = {
            "run_id": "aiv_probe3", "group_id": "g3", "project_id": "p3",
            "doc_ref": "d", "mode": "single", "status": "running",
        }
        monkeypatch.setattr(svc, "_runs", {"aiv_probe3": run})
        items = runtime.list_live_runs(group_id="g3")
        assert [item["run_id"] for item in items] == ["aiv_probe3"]

    def test_full_reassignment_of_svc_group_resume_locks_is_observed(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        sentinel_locks = {}
        monkeypatch.setattr(svc, "_group_resume_locks", sentinel_locks)
        lock = runtime.group_resume_lock("g_new")
        assert sentinel_locks["g_new"] is lock


class TestFacadeMatchesRuntimeModule:
    """`svc.<name>` is a thin wrapper; both sides must return the identical result."""

    def test_get_run_record_and_is_run_live(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        run = {"run_id": "aiv_facade", "group_id": "gf", "status": "running"}
        monkeypatch.setattr(svc, "_runs", {"aiv_facade": run})
        assert svc.get_run_record("aiv_facade") == runtime.get_run_record("aiv_facade")
        assert svc.is_run_live("aiv_facade") == runtime.is_run_live("aiv_facade")
        assert svc._active_run_for_group("gf") == runtime.active_run_for_group("gf")

    def test_list_live_runs_and_run_list_item_live(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime
        from modules.flow_gate.services import ai_invoke_service as svc

        run = {
            "run_id": "aiv_facade2", "group_id": "gf2", "project_id": "pf2",
            "doc_ref": "d", "mode": "single", "status": "running",
            "provider": {"id": "aip_1", "name": "cli-1"},
        }
        monkeypatch.setattr(svc, "_runs", {"aiv_facade2": run})
        assert svc.list_live_runs(group_id="gf2") == runtime.list_live_runs(group_id="gf2")
        assert svc._run_list_item_live(run) == runtime.run_list_item_live(run)


# ── T0008 §13-B/C/D/E: admission / collision / cancel / start_run facade ───────
#
# None of the code these four sections exercise was extracted -- start_run's admission
# check, its RunIdCollision retry, and cancel_run all live exactly where they lived
# before T3. What changed underneath them is that `is_run_live`/`get_run_record`/
# `_active_run_for_group` (which `force_release_group_lease` and `cancel_run` call) are
# now thin wrappers over ai_invoke_runtime. These tests are the direct proof that the
# swap is invisible to that untouched code, not an inference from "the six read
# functions match" (TestFacadeMatchesRuntimeModule above) or from running unrelated
# suites and finding them green.

def _provider(name="cli-1", cmd=None, pid="aip_test01"):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd,
        "api_base_url": None, "api_model": None,
        "api_key_set": False, "api_key_hint": None,
    }


@pytest.fixture
def admission_env(monkeypatch, tmp_path):
    """Every collaborator a single-mode start_run touches, trimmed to that mode's
    needs (no workflow-sequence fixture -- single mode never reads it). Same shape as
    test_ai_invoke_0187.py's fake_env; duplicated locally rather than imported, matching
    how every other test_ai_invoke_*.py suite in this repo builds its own copy."""
    chain_holder = {"providers": [], "source": "system", "registered_count": 0}
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 0)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: None)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain_holder}
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kwargs: {
            "raw_token": "tok_raw_test", "token_id": "tok_20260902_000099",
            "expires_at": "2026-09-03T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        },
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root",
        lambda pid, branch, *, group_id: src_root,
    )
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return chain_holder


def _start(chain_holder, group_id, providers, provider_id=None, mention="## prompt\ndo the work\n"):
    chain_holder["providers"] = providers
    chain_holder["registered_count"] = len(providers)
    return svc.start_run(
        project_id="flowgate", module="default", group_id=group_id,
        doc_ref=f"{group_id}.0001-R", action_scope="new", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: mention, provider_id=provider_id,
    )


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


class TestAdmissionUnchanged:
    """T0008 §13-B: same-group concurrent admission keeps the existing 409 contract.
    start_run's check (`db_group_ai_leases.get_active`) is DB-level and never touched
    the in-memory registry T3 extracted -- this locks that fact down instead of leaving
    it as an assumption."""

    def test_second_concurrent_start_for_same_group_is_rejected(self, admission_env):
        from fastapi import HTTPException

        group_id = "flowgate.default.0501admB"
        cmd = f'"{PY}" -c "import time; time.sleep(30)"'
        first = _start(admission_env, group_id, [_provider(cmd=cmd)])
        try:
            with pytest.raises(HTTPException) as exc:
                _start(admission_env, group_id, [_provider(cmd=cmd)])
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "run_in_progress"
            assert exc.value.detail["run_id"] == first["run_id"]
        finally:
            svc.cancel_run(first["run_id"])
            _wait_finished(first["run_id"])


class TestRunIdCollisionUnchanged:
    """T0008 §13-C: run-id collision retry shape (ai_invoke_service.py §1741-1762) is
    start_run's own code, untouched by T3. One collision retries once with a freshly
    minted id; a second collision surfaces as a clean 409, never a raw DB error."""

    def test_single_collision_retries_once_with_a_fresh_id(self, admission_env, monkeypatch):
        group_id = "flowgate.default.0501admC1"
        other_group = "flowgate.default.0501admC1-other"
        svc.db_group_ai_leases.acquire(
            group_id=other_group, project_id="flowgate", run_id="aiv_20260903_900001",
            chain_id=None, action_scope="new", worker_identity="other_worker",
        )
        ids = iter(["aiv_20260903_900001", "aiv_20260903_900002"])
        monkeypatch.setattr(svc, "_next_run_id", lambda: next(ids))

        result = _start(admission_env, group_id, [_provider(cmd=f'"{PY}" -c "print(1)"')])

        assert result["run_id"] == "aiv_20260903_900002"
        _wait_finished(result["run_id"])

    def test_double_collision_surfaces_as_409(self, admission_env, monkeypatch):
        from fastapi import HTTPException

        group_id = "flowgate.default.0501admC2"
        other_group = "flowgate.default.0501admC2-other"
        svc.db_group_ai_leases.acquire(
            group_id=other_group, project_id="flowgate", run_id="aiv_20260903_900003",
            chain_id=None, action_scope="new", worker_identity="other_worker",
        )
        monkeypatch.setattr(svc, "_next_run_id", lambda: "aiv_20260903_900003")

        with pytest.raises(HTTPException) as exc:
            _start(admission_env, group_id, [_provider(cmd="echo hi")])
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "run_id_collision"


class TestCancelPrimitiveUnchanged:
    """T0008 §13-D: cancel_run (ai_invoke_chain.py since T5, unmodified in behavior by
    T3 or T5) reaches the
    active run through `get_run_record`, now served by ai_invoke_runtime. This locks
    that cancel_run's mutation lands on the SAME object the new module hands back --
    not a stale copy -- and that the finished-run idempotent branch is unaffected."""

    def test_cancel_marks_the_run_and_is_visible_through_the_new_module(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_runtime as runtime

        run = {
            "run_id": "aiv_cancel_probe", "group_id": "g_cancel", "status": "running",
            "cancel_event": threading.Event(), "proc": None,
        }
        monkeypatch.setattr(svc, "_runs", {"aiv_cancel_probe": run})

        out = svc.cancel_run("aiv_cancel_probe")

        assert out == {"ok": True, "run_id": "aiv_cancel_probe", "status": "cancelling"}
        assert run["status"] == "cancelling"
        assert run["cancel_event"].is_set() is True
        assert runtime.get_run_record("aiv_cancel_probe") is run

    def test_cancel_after_finish_is_idempotent(self, monkeypatch):
        run = {"run_id": "aiv_cancel_done", "group_id": "g_cancel2", "status": "finished"}
        monkeypatch.setattr(svc, "_runs", {"aiv_cancel_done": run})

        out = svc.cancel_run("aiv_cancel_done")

        assert out == {"ok": True, "run_id": "aiv_cancel_done", "status": "finished"}
        assert run["status"] == "finished"


class TestStartRunFacadeUnchanged:
    """T0008 §13-E: `ai_invoke_service.start_run(...)` is the external surface T3 must
    preserve untouched. This proves the facade's OWN result is reachable through the
    new runtime module end-to-end -- registered while running, gone once finished --
    which is a stronger claim than TestFacadeMatchesRuntimeModule above (that class only
    shows the six moved functions individually agree with svc.*)."""

    def test_start_run_is_visible_live_then_gone_once_finished(self, admission_env):
        from modules.flow_gate.services import ai_invoke_runtime as runtime

        group_id = "flowgate.default.0501admE"
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        result = _start(admission_env, group_id, [_provider(cmd=cmd)])

        live = runtime.get_run_record(result["run_id"])
        assert live is not None
        assert live["group_id"] == group_id
        assert runtime.active_run_for_group(group_id) is live
        assert runtime.is_run_live(result["run_id"]) is True

        _wait_finished(result["run_id"])

        assert runtime.active_run_for_group(group_id) is None
        assert runtime.is_run_live(result["run_id"]) is False
