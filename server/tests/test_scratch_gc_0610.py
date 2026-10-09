"""Connected run scratch GC cases from 0610 NR rev1 and T#1."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

import startup  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.settings import scratch_retention

PROJECT = "project-0610"
GROUP = "flowgate.default.0610"
OLD = "aiv_20260822_000461"


@pytest.fixture(autouse=True)
def default_scratch_ttl(monkeypatch):
    monkeypatch.setattr(scratch_retention, "effective_retention", lambda: timedelta(days=7))


@pytest.fixture
def scratch_state(monkeypatch, tmp_path):
    rows = {}
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _pid: {"project_id": PROJECT, "project_name": "flowgate"})
    monkeypatch.setattr(svc.db_projects, "list_projects", lambda: [{"project_id": PROJECT}])
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(db_runs, "get", lambda run_id: rows.get(run_id))
    monkeypatch.setattr(db_runs, "upsert", lambda row: rows.__setitem__(row["run_id"], dict(row)))
    monkeypatch.setattr(svc, "startup_recover_handoffs", lambda: None)
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()
    root = svc._project_scratch_root(PROJECT)
    root.mkdir(parents=True)
    yield root, rows
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()


def _row(run_id, *, project=PROJECT, finished=None, end_reason="orphaned_by_restart"):
    run_day = datetime.strptime(run_id[4:12], "%Y%m%d").replace(tzinfo=timezone.utc)
    return {
        "run_id": run_id, "project_id": project, "status": "finished",
        "started_at": run_day.isoformat(),
        "finished_at": (finished or run_day + timedelta(hours=1)).isoformat(),
        "end_reason": end_reason,
    }


def _legacy(root, run_id=OLD):
    child = root / run_id
    child.mkdir()
    (child / "worker-output.txt").write_text("retained")
    old = datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp()
    os.utime(child, (old, old))
    return child


def _crash(run_id, created):
    child = svc._create_scratch(PROJECT, run_id)
    manifest_path = child / svc.SCRATCH_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = created.isoformat()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return child


def _lease(run_id, group=GROUP):
    return db_leases.acquire(
        group_id=group, project_id=PROJECT, run_id=run_id, chain_id=run_id,
        action_scope="new", worker_identity="worker",
    )


def test_pre_manifest_legacy_requires_old_direct_child_and_durable_terminal(scratch_state):
    root, rows = scratch_state
    proven = _legacy(root)
    rows[OLD] = _row(OLD)
    ambiguous = _legacy(root, "aiv_20260823_000480")
    wrong_name = _legacy(root, "aiv_20260823_480")
    post_manifest = _legacy(root, "aiv_20260829_000001")
    rows[post_manifest.name] = _row(post_manifest.name)
    svc._cleanup_retained_scratches(PROJECT)
    assert not proven.exists()
    assert ambiguous.exists() and wrong_name.exists() and post_manifest.exists()


def test_legacy_conflicting_live_lease_project_or_recent_write_is_preserved(scratch_state):
    root, rows = scratch_state
    live = _legacy(root, OLD)
    lease_id = "aiv_20260822_000462"
    leased = _legacy(root, lease_id)
    wrong_id = "aiv_20260822_000463"
    wrong_project = _legacy(root, wrong_id)
    recent_id = "aiv_20260822_000464"
    recent = _legacy(root, recent_id)
    os.utime(recent, None)
    for child in (live, leased, wrong_project, recent):
        rows[child.name] = _row(child.name)
    rows[wrong_id]["project_id"] = "other-project"
    with svc._runs_lock:
        svc._runs[OLD] = {"run_id": OLD, "status": "running"}
    _lease(lease_id)
    svc._cleanup_retained_scratches(PROJECT)
    assert all(child.exists() for child in (live, leased, wrong_project, recent))


def test_legacy_symlink_and_manifest_lookalike_fail_closed(scratch_state, tmp_path):
    root, rows = scratch_state
    external = tmp_path / "outside"
    external.mkdir()
    sentinel = external / "keep"
    sentinel.write_text("safe")
    link_id = "aiv_20260822_000465"
    link = root / link_id
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    rows[link_id] = _row(link_id)
    invalid_id = "aiv_20260822_000466"
    invalid = _legacy(root, invalid_id)
    (invalid / svc.SCRATCH_MANIFEST_NAME).write_text("invalid json")
    rows[invalid_id] = _row(invalid_id)
    svc._cleanup_retained_scratches(PROJECT)
    assert link.is_symlink() and sentinel.read_text() == "safe" and invalid.exists()


def test_null_completion_requires_durable_terminal_and_grace(scratch_state):
    _, rows = scratch_state
    now = datetime.now(timezone.utc)
    started = now - timedelta(days=10)
    run_day = started.strftime("%Y%m%d")
    old_id = f"aiv_{run_day}_000101"
    old = _crash(old_id, started)
    rows[old_id] = _row(old_id, finished=now - timedelta(days=8))
    no_row = _crash(f"aiv_{run_day}_000102", started)
    young_id = f"aiv_{run_day}_000103"
    young = _crash(young_id, started)
    rows[young_id] = _row(young_id, finished=now - timedelta(days=6))
    svc._cleanup_retained_scratches(PROJECT)
    assert not old.exists()
    assert no_row.exists() and young.exists()


def test_null_completion_live_and_lease_are_preserved(scratch_state):
    _, rows = scratch_state
    now = datetime.now(timezone.utc)
    started = now - timedelta(days=10)
    day = started.strftime("%Y%m%d")
    live_id = f"aiv_{day}_000104"
    lease_id = f"aiv_{day}_000105"
    live = _crash(live_id, started)
    leased = _crash(lease_id, started)
    for run_id in (live_id, lease_id):
        rows[run_id] = _row(run_id, finished=now - timedelta(days=8))
    with svc._runs_lock:
        svc._runs[live_id] = {"run_id": live_id, "status": "running"}
    _lease(lease_id)
    svc._cleanup_retained_scratches(PROJECT)
    assert live.exists() and leased.exists()


def test_startup_bootstrap_sweeps_legacy_crash_and_retained_without_new_admission(
    scratch_state, monkeypatch,
):
    root, rows = scratch_state
    now = datetime.now(timezone.utc)
    legacy = _legacy(root)
    rows[OLD] = _row(OLD)
    started = now - timedelta(days=10)
    run_id = f"aiv_{started:%Y%m%d}_000106"
    crash = _crash(run_id, started)
    rows[run_id] = _row(run_id, finished=now - timedelta(days=8))
    retained_id = f"aiv_{started:%Y%m%d}_000107"
    retained = svc._create_scratch(PROJECT, retained_id)
    svc._mark_scratch_completed(PROJECT, retained_id, retained, (now - timedelta(days=8)).isoformat())
    young_id = f"aiv_{started:%Y%m%d}_000108"
    young = svc._create_scratch(PROJECT, young_id)
    svc._mark_scratch_completed(PROJECT, young_id, young, (now - timedelta(days=6)).isoformat())
    for hook in ("configure_console_encoding", "record_deployment", "preload_singletons",
                 "recover_git_sessions", "encrypt_ai_provider_keys"):
        monkeypatch.setattr(startup, hook, lambda: None)
    startup.run_all()
    assert not legacy.exists() and not crash.exists() and not retained.exists()
    assert young.exists()
