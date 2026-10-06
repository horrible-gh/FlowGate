"""T#2 Source Bundle transport and Scratch regression cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.flow_gate.services import source_bundle_access_service as access
from modules.flow_gate.services import api_server_tools as tools


def _fixture(monkeypatch, tmp_path):
    bundle = tmp_path / "sb_00000000000000000000000000000000"
    source = bundle / "source"
    source.mkdir(parents=True)
    (source / "example.py").write_bytes(b"value = 1\n")
    row = {"bundle_id": bundle.name, "project_id": "project", "group_id": "group",
           "status": "created", "source_revision": "abc", "source_dirty": False,
           "content_fingerprint": "fingerprint", "bundle_sha256": "sha",
           "exclusion_policy_version": "source-bundle-v1", "file_count": 1,
           "byte_size": 10, "created_at": "2026-09-27", "expires_at": "2026-09-28"}
    monkeypatch.setattr(access.db, "get", lambda bundle_id: row if bundle_id == bundle.name else None)
    monkeypatch.setattr(access.bundles, "_integrity", lambda _row: True)
    monkeypatch.setattr(access.materializer, "bundle_path", lambda *_: bundle)
    monkeypatch.setattr(access, "locator_roots", lambda *_: ())
    usages = []
    monkeypatch.setattr(access.db, "record_usage", lambda *args, **kwargs: usages.append((args, kwargs)))
    monkeypatch.setattr(access.db, "scratch_created", lambda *args, **kwargs: None)
    monkeypatch.setattr(access.db, "scratch_reused", lambda *args, **kwargs: True)
    run = {"project_id": "project", "group_id": "group", "run_id": "run-1",
           "token_id": "token-1", "source_root": str(tmp_path / "live")}
    return run, row, bundle, usages


def test_bundle_reads_do_not_rehash_live_worktree(monkeypatch, tmp_path):
    run, row, _bundle, usages = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(access.materializer, "inspect_source",
                        lambda *_: pytest.fail("historical read rehashed live worktree"))
    status, data = access.access(run, {"bundle_id": row["bundle_id"],
                                       "operation": "read", "path": "example.py"})
    assert status == 200 and data["content"] == "value = 1\n"
    assert data["bundle"]["current_worktree_claim"] is None
    assert usages[0][1]["detail"]["access_kind"] == "bundle_read"


def test_stale_claim_preserves_historical_read_and_blocks_current_claim(monkeypatch, tmp_path):
    run, row, _bundle, _usages = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(access, "_fresh", lambda _row: False)
    status, data = access.access(run, {"bundle_id": row["bundle_id"], "operation": "read",
                                       "path": "example.py", "claim_current_worktree": True})
    assert status == 409 and data["content"] == "value = 1\n"
    assert data["error"]["code"] == "bundle_stale_claim_blocked"


def test_scratch_reused_within_run_and_new_for_new_bundle(monkeypatch, tmp_path):
    run, row, bundle, _usages = _fixture(monkeypatch, tmp_path)
    first, reused = access._scratch(run, row)
    second, reused_again = access._scratch(run, row)
    assert first == second and not reused and reused_again
    (first / "source" / "generated.txt").write_text("generated")
    assert not (bundle / "source" / "generated.txt").exists()
    next_bundle = bundle.parent / "sb_11111111111111111111111111111111"
    (next_bundle / "source").mkdir(parents=True)
    (next_bundle / "source" / "example.py").write_text("value = 2")
    monkeypatch.setattr(access.materializer, "bundle_path",
                        lambda _project, bundle_id: bundle.parent / bundle_id)
    newer = dict(row, bundle_id=next_bundle.name)
    third, third_reused = access._scratch(run, newer)
    assert third != first and not third_reused


def test_run_test_keeps_registry_gate_and_uses_bundle_executor(monkeypatch):
    monkeypatch.setattr(tools.test_command_service, "list_for_view",
                        lambda _project: [{"command": "pytest -q", "verified_os": tools.test_command_service.current_os()}])
    observed = []
    # 0672 T0004: run_test answers only inside the TS/TSR preserved set (a TS edit run here).
    monkeypatch.setattr(tools.source_bundle_exposure, "for_doc_ref", lambda *_a, **_k: "run")
    monkeypatch.setattr(tools, "run_source_bundle",
                        lambda run, data, remain: (observed.append(data) or 200,
                                                   {"exit_code": 0, "duration_ms": 1,
                                                    "stdout": "ok", "stderr": "", "truncated": False,
                                                    "timed_out": False, "bundle": {"bundle_id": "sb_x"},
                                                    "scratch_reused": False}))
    status, payload = tools.run_test({"project_id": "project"}, {"command": "pytest -q"}, 9)
    assert status == 200 and payload["exit_code"] == 0
    assert observed == [{"task_kind": "test", "command": "pytest -q"}]
    with pytest.raises(tools.ToolError) as exc:
        tools.run_test({"project_id": "project"}, {"command": "unregistered"}, 9)
    assert exc.value.reason == "not_verified"


def test_promotion_guard_and_provider_catalog(monkeypatch, tmp_path):
    run, row, bundle, _usages = _fixture(monkeypatch, tmp_path)
    with pytest.raises(access.BundleAccessError) as exc:
        access.guard_promotion(run, "write_source_file",
                               {"path": "source-bundles/" + row["bundle_id"] + "/source/example.py"})
    assert exc.value.code == "bundle_promotion_blocked"
    assert "access_source_bundle" in tools.SCHEMAS
    assert "run_source_bundle" in tools.SCHEMAS
    assert "request_source_snapshot" not in tools.BUNDLE_NAMES


def test_execution_uses_scratch_and_redacts_host_paths(monkeypatch, tmp_path):
    run, row, bundle, usages = _fixture(monkeypatch, tmp_path)
    calls = []

    class Process:
        returncode = 0

        def communicate(self, timeout=None):
            return str(calls[-1]["cwd"]).encode(), b""

    def spawn(command, **kwargs):
        calls.append(kwargs)
        return Process()

    monkeypatch.setattr(access.subprocess, "Popen", spawn)
    for expected_reuse in (False, True):
        status, result = access.execute(run, {"bundle_id": row["bundle_id"],
                                              "task_kind": "test", "command": "pytest -q"}, 10)
        assert status == 200 and result["scratch_reused"] is expected_reuse
        assert str(bundle.parent) not in result["stdout"]
        assert Path(calls[-1]["cwd"]).name == "source"
        assert Path(calls[-1]["cwd"]) != Path(run["source_root"])
    assert len(calls) == 2
    assert all(item[1]["detail"]["access_kind"] == "scratch_execution" for item in usages)


def test_scratch_failure_never_falls_back_to_live_worktree(monkeypatch, tmp_path):
    run, row, _bundle, _usages = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(access, "_scratch", lambda *_: (_ for _ in ()).throw(
        access.BundleAccessError(409, "scratch_create_failed", "failed")))
    monkeypatch.setattr(access.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("live fallback executed"))
    with pytest.raises(access.BundleAccessError) as exc:
        access.execute(run, {"bundle_id": row["bundle_id"], "task_kind": "test",
                             "command": "pytest -q"}, 10)
    assert exc.value.code == "scratch_create_failed"


def test_cli_transport_calls_bundle_core(monkeypatch):
    from modules.flow_gate.api.v1 import source_bundle_routes as routes

    run = {"project_id": "project", "group_id": "group", "run_id": "run-1"}
    monkeypatch.setattr(routes, "_cli_context", lambda _request: ("token", {}, run))
    # 0672 T0004: the routes answer only inside the TS/TSR preserved set (a TS edit run here).
    monkeypatch.setattr(routes.exposure, "for_doc_ref", lambda *_a, **_k: routes.exposure.RUN)
    calls = []
    monkeypatch.setattr(routes.core, "access",
                        lambda _run, data: (calls.append(data) or 200, {"ok": True}))
    monkeypatch.setattr(routes.core, "execute",
                        lambda _run, data, remain: (calls.append(data) or 200, {"ok": True}))
    assert routes.ensure_bundle(object())["ok"]
    assert routes.access_bundle("sb_x", {"operation": "read", "path": "x"}, object())["ok"]
    assert routes.run_bundle("sb_x", {"task_kind": "test", "command": "x"}, object())["ok"]
    assert calls == [
        {"operation": "status"},
        {"operation": "read", "path": "x", "bundle_id": "sb_x"},
        {"task_kind": "test", "command": "x", "bundle_id": "sb_x"},
    ]
