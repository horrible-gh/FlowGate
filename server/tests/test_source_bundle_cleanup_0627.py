"""T#4 cleanup ownership and run freshness regression tests."""
from __future__ import annotations

import json
import time

from modules.flow_gate.services import source_bundle_cleanup_service as cleanup
from modules.flow_gate.services import source_bundle_access_service as access
from modules.flow_gate.services import source_bundle_service as bundles


def test_scratch_cleanup_failure_does_not_change_bundle_lifecycle(monkeypatch, tmp_path):
    bundle_id = "sb_" + "1" * 32
    key = "2" * 64
    bundle = tmp_path / bundle_id
    scratch = tmp_path / "scratch" / key
    scratch.mkdir(parents=True)
    (scratch / "source").mkdir()
    row = {"bundle_id": bundle_id, "project_id": "p", "status": "created"}
    warnings = []
    monkeypatch.setattr(cleanup.db, "scratch_get",
                        lambda _key: {"scratch_key": key, "bundle_id": bundle_id, "status": "created"})
    monkeypatch.setattr(cleanup.db, "get", lambda _id: row)
    monkeypatch.setattr(cleanup.db, "scratch_cleanup_warning",
                        lambda _key, reason: warnings.append(reason))
    monkeypatch.setattr(cleanup.materializer, "bundle_path", lambda *_: bundle)
    monkeypatch.setattr(cleanup, "_safe_remove",
                        lambda _path: (_ for _ in ()).throw(OSError("locked")))
    assert cleanup.cleanup_scratch(key, trigger="run_finished") is False
    assert row["status"] == "created"
    assert warnings and "OSError" in warnings[0]


def test_scratch_and_bundle_cleanup_are_independent(monkeypatch, tmp_path):
    bundle_id = "sb_" + "3" * 32
    key = "4" * 64
    bundle = tmp_path / bundle_id
    (bundle / "source").mkdir(parents=True)
    scratch = tmp_path / "scratch" / key
    (scratch / "source").mkdir(parents=True)
    row = {"bundle_id": bundle_id, "project_id": "p", "status": "created"}
    deleted = []
    monkeypatch.setattr(cleanup.materializer, "bundle_path", lambda *_: bundle)
    monkeypatch.setattr(cleanup.db, "get", lambda _id: row)
    monkeypatch.setattr(cleanup.db, "scratch_get",
                        lambda _key: {"scratch_key": key, "bundle_id": bundle_id, "status": "created"})
    monkeypatch.setattr(cleanup.db, "scratch_deleted", lambda _key: deleted.append("scratch"))
    monkeypatch.setattr(cleanup.db, "bundle_cleanup_success", lambda _id: None)
    monkeypatch.setattr(cleanup.db, "deleted", lambda _id: deleted.append("bundle"))
    monkeypatch.setattr(cleanup.db, "is_pinned", lambda _id: False)
    assert cleanup.cleanup_scratch(key)
    assert not scratch.exists() and bundle.exists()
    assert cleanup.cleanup_bundle(bundle_id)
    assert not bundle.exists() and deleted == ["scratch", "bundle"]


def test_bundle_metrics_expose_reuse_without_copy():
    row = {"bundle_id": "sb_" + "5" * 32, "status": "created", "source_revision": "a",
           "source_dirty": False, "content_fingerprint": "fp", "bundle_sha256": "sha",
           "exclusion_policy_version": "source-bundle-v1", "file_count": 1,
           "byte_size": 10, "created_at": "2026-09-27", "expires_at": "2026-09-28",
           "metrics_json": json.dumps({"bundle_copy_duration_ms": 7,
                                       "bundle_build_duration_ms": 15})}
    reused = bundles._public(row, reused=True, fingerprint_ms=3)
    assert reused["metrics"]["bundle_reused"] is True
    assert reused["metrics"]["bundle_copy_duration_ms"] == 0
    assert reused["metrics"]["bundle_fingerprint_duration_ms"] == 3


def test_current_claim_rehashes_once_after_execution(monkeypatch, tmp_path):
    bundle_id = "sb_" + "6" * 32
    row = {"bundle_id": bundle_id, "project_id": "p", "group_id": "g", "status": "created",
           "source_revision": "a", "source_dirty": False, "content_fingerprint": "fp",
           "bundle_sha256": "sha", "exclusion_policy_version": "source-bundle-v1",
           "file_count": 1, "byte_size": 10, "created_at": "2026-09-27", "expires_at": "2026-09-28"}
    run = {"project_id": "p", "group_id": "g", "run_id": "r"}
    source = tmp_path / "scratch" / "source"
    source.mkdir(parents=True)
    monkeypatch.setattr(access, "_resolve", lambda *_: row)
    monkeypatch.setattr(access, "_scratch", lambda *_: (source.parent, True))
    monkeypatch.setattr(access, "_roots", lambda *_: ())
    monkeypatch.setattr(access, "_usage", lambda *_args, **_kwargs: None)
    checks = []
    monkeypatch.setattr(access, "_fresh", lambda _row: checks.append(1) or True)

    class Process:
        returncode = 0
        def communicate(self, timeout=None):
            return b"ok", b""

    monkeypatch.setattr(access.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    status, result = access.execute(run, {"bundle_id": bundle_id, "task_kind": "test",
                                          "command": "pytest -q", "claim_current_worktree": True}, 10)
    assert status == 200 and result["current_worktree_validation_allowed"] is True
    assert len(checks) == 1
    assert result["scratch_build_duration_ms"] == 0


def test_startup_recovers_interrupted_bundle_and_scratch(monkeypatch, tmp_path):
    bundle_id = "sb_" + "7" * 32
    key = "8" * 64
    bundle = tmp_path / bundle_id
    stage = tmp_path / ("." + bundle_id + ".building")
    stage.mkdir()
    scratch = tmp_path / "scratch" / key
    (scratch / "source").mkdir(parents=True)
    building = {"bundle_id": bundle_id, "project_id": "p", "group_id": "g", "status": "building"}
    scratch_row = {"scratch_key": key, "bundle_id": bundle_id, "status": "created"}
    recovered = []
    monkeypatch.setattr(cleanup.materializer, "bundle_path", lambda *_: bundle)
    monkeypatch.setattr(cleanup.db, "scratch_candidates", lambda **_kwargs: [scratch_row])
    monkeypatch.setattr(cleanup.db, "scratch_get", lambda _key: scratch_row)
    monkeypatch.setattr(cleanup.db, "get", lambda _id: building)
    monkeypatch.setattr(cleanup.db, "scratch_deleted", lambda _key: recovered.append("scratch"))
    monkeypatch.setattr(cleanup.db, "list_building", lambda: [building])
    monkeypatch.setattr(cleanup.db, "list_created", lambda **_kwargs: [])
    monkeypatch.setattr(cleanup.db, "slot", lambda *_: None)
    monkeypatch.setattr(cleanup.db, "failed",
                        lambda _id, _owner, code, _reason: recovered.append(code))
    result = cleanup.cleanup_orphans()
    assert result["scratch_deleted"] == 1 and result["builds_recovered"] == 1
    assert recovered == ["scratch", "orphan_build_recovered"]
    assert not scratch.exists() and not stage.exists()
