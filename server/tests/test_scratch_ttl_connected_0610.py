"""Connected 0610 setting -> startup -> run/token cleanup -> DB purge path."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import startup
from modules.flow_gate.db import ai_invoke_runs as db_runs
from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.services import ai_invoke_service as ai
from modules.flow_gate.services import token_scratch
from modules.flow_gate.settings import system_settings_service

PROJECT = "project-0610-ttl"
OLD = datetime(2000, 1, 1, tzinfo=timezone.utc)
FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def connected(monkeypatch, tmp_path):
    settings = {}
    tokens = {}
    runs = {}
    events = []

    def get_setting(key):
        return settings.get(key)

    def set_setting(key, value, value_type="string", description=None, updated_by=None):
        row = {"setting_key": key, "setting_value": value, "value_type": value_type}
        settings[key] = row
        return row

    @contextmanager
    def transaction():
        yield

    class Store:
        pass

    store = Store()
    store.transaction = transaction
    monkeypatch.setattr(db_settings, "get", get_setting)
    monkeypatch.setattr(db_settings, "get_value", lambda key, default=None: settings[key]["setting_value"] if key in settings else default)
    monkeypatch.setattr(db_settings, "list_settings", lambda: list(settings.values()))
    monkeypatch.setattr(db_settings, "set_value", set_setting)
    monkeypatch.setattr(system_settings_service._connection, "get_store", lambda: store)

    monkeypatch.setattr(ai.db_projects, "get_by_id", lambda _: {"project_name": "Flowgate"})
    monkeypatch.setattr(ai.db_projects, "list_projects", lambda: [{"project_id": PROJECT}])
    monkeypatch.setattr(ai.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(ai.storage_paths, "resolve_storage_dir", lambda path, _: Path(path))
    monkeypatch.setattr(db_runs, "get", lambda run_id: runs.get(run_id))
    monkeypatch.setattr(db_leases, "get_by_run_id", lambda _: None)
    monkeypatch.setattr(db_leases, "get_by_token_id", lambda _: None)
    monkeypatch.setattr(token_scratch.db_tokens, "get_by_id", lambda token_id: tokens.get(token_id))
    monkeypatch.setattr(token_scratch.db_tokens, "list_expired_for_purge", lambda: [
        row for row in list(tokens.values()) if row["expires_at"] < (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    ])

    def delete_expired_one(token_id):
        events.append("purge")
        assert not Path(tokens[token_id]["scratch_dir"]).exists()
        tokens.pop(token_id)

    monkeypatch.setattr(token_scratch.db_tokens, "delete_expired_one", delete_expired_one)
    with ai._runs_lock:
        old_runs = dict(ai._runs)
        ai._runs.clear()
    for hook in ("configure_console_encoding", "record_deployment", "preload_singletons",
                 "recover_git_sessions", "encrypt_ai_provider_keys"):
        monkeypatch.setattr(startup, hook, lambda: None)
    monkeypatch.setattr(startup, "recover_ai_invoke_leases", lambda: events.append("recover"))
    real_run_sweep = startup.sweep_ai_run_scratches
    real_token_sweep = startup.sweep_token_scratches

    def run_sweep():
        events.append("run_sweep")
        real_run_sweep()

    def token_sweep():
        events.append("token_sweep")
        real_token_sweep()

    monkeypatch.setattr(startup, "sweep_ai_run_scratches", run_sweep)
    monkeypatch.setattr(startup, "sweep_token_scratches", token_sweep)
    yield tmp_path, settings, tokens, runs, events
    with ai._runs_lock:
        ai._runs.clear()
        ai._runs.update(old_runs)


def _run_row(run_id, finished):
    started = datetime.strptime(run_id[4:12], "%Y%m%d").replace(tzinfo=timezone.utc)
    return {"run_id": run_id, "project_id": PROJECT, "status": "finished",
            "started_at": started.isoformat(), "finished_at": finished.isoformat()}


def _run(run_id, completed_at, *, legacy_policy=False):
    path = ai._create_scratch(PROJECT, run_id)
    data_path = path / ai.SCRATCH_MANIFEST_NAME
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["completed_at"] = completed_at.isoformat() if completed_at else None
    if legacy_policy:
        data["policy"] = {"retention_days": 7, "delete_on_complete": True}
    data_path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _token(root, number, created, terminal=None, *, expired=False, legacy_policy=False):
    token_id = f"tok_20261009_{number:06d}"
    path = root / "work" / "Flowgate" / token_id
    token_scratch.create(PROJECT, token_id, path)
    data_path = path / token_scratch.MANIFEST_NAME
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["created_at"] = created.isoformat()
    if legacy_policy:
        data["policy"] = {"retention_days": 7}
    data_path.write_text(json.dumps(data), encoding="utf-8")
    return token_id, path, {"token_id": token_id, "project": PROJECT, "scratch_dir": str(path),
                            "consumed_at": terminal.isoformat() if terminal else None,
                            "revoked_at": None,
                            "expires_at": (OLD + timedelta(days=1) if expired else FUTURE).isoformat()}


def test_setting_change_reuses_legacy_manifests_and_startup_orders_purge(connected):
    root, settings, tokens, runs, events = connected
    now = datetime.now(timezone.utc)
    system_settings_service.set_values({"scratch_ttl_value": "7", "scratch_ttl_unit": "day"})

    recent_id = f"aiv_{(now - timedelta(days=1)):%Y%m%d}_000101"
    recent_run = _run(recent_id, now - timedelta(hours=2), legacy_policy=True)
    token_id, recent_token, row = _token(root, 1, now - timedelta(hours=3), now - timedelta(hours=2), legacy_policy=True)
    tokens[token_id] = row
    ai._cleanup_retained_scratches(PROJECT)
    token_scratch.sweep(PROJECT)
    assert recent_run.exists() and recent_token.exists()

    system_settings_service.set_values({"scratch_ttl_value": "30", "scratch_ttl_unit": "minute"})
    assert settings["scratch_ttl_value"]["setting_value"] == "30"
    assert settings["scratch_ttl_unit"]["setting_value"] == "minute"

    legacy_id = "aiv_20260822_000461"
    legacy = ai._project_scratch_root(PROJECT) / legacy_id
    legacy.mkdir(parents=True)
    old_epoch = datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp()
    os.utime(legacy, (old_epoch, old_epoch))
    runs[legacy_id] = _run_row(legacy_id, datetime(2026, 8, 23, tzinfo=timezone.utc))

    crash_start = now - timedelta(days=10)
    crash_id = f"aiv_{crash_start:%Y%m%d}_000102"
    crash = _run(crash_id, None, legacy_policy=True)
    crash_manifest = crash / ai.SCRATCH_MANIFEST_NAME
    crash_data = json.loads(crash_manifest.read_text(encoding="utf-8"))
    crash_data["created_at"] = crash_start.isoformat()
    crash_manifest.write_text(json.dumps(crash_data), encoding="utf-8")
    runs[crash_id] = _run_row(crash_id, now - timedelta(days=8))

    active_id = f"aiv_{crash_start:%Y%m%d}_000103"
    active = _run(active_id, now - timedelta(days=8))
    with ai._runs_lock:
        ai._runs[active_id] = {"run_id": active_id, "status": "running", "project_id": PROJECT}

    expired_id, expired, expired_row = _token(root, 2, OLD, expired=True, legacy_policy=True)
    tokens[expired_id] = expired_row
    live_id, live, live_row = _token(root, 3, OLD, expired=True)
    tokens[live_id] = live_row
    with ai._runs_lock:
        ai._runs["aiv_20261009_000201"] = {"status": "running", "project_id": PROJECT,
                                             "token_scratch_dir": str(live)}
    unknown = root / "work" / "Flowgate" / "tok_20261009_000004"
    unknown.mkdir()

    startup.run_all()
    assert events[:3] == ["recover", "run_sweep", "token_sweep"]
    assert "purge" in events and events.index("purge") > events.index("token_sweep")
    assert not recent_run.exists() and not legacy.exists() and not crash.exists()
    assert active.exists()
    assert not recent_token.exists() and not expired.exists()
    assert live.exists() and unknown.exists()
    assert expired_id not in tokens and live_id in tokens


def test_failed_filesystem_delete_keeps_expired_token_row(connected, monkeypatch):
    root, _, tokens, _, events = connected
    system_settings_service.set_values({"scratch_ttl_value": "1", "scratch_ttl_unit": "minute"})
    token_id, path, row = _token(root, 401, OLD, expired=True)
    tokens[token_id] = row
    original = token_scratch.shutil.rmtree

    def fail_for_token(candidate, *args, **kwargs):
        if Path(candidate) == path:
            raise OSError("simulated delete failure")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(token_scratch.shutil, "rmtree", fail_for_token)
    startup.run_all()
    assert path.exists() and token_id in tokens
    assert "purge" not in events


def test_one_minute_boundary_applies_to_both_gc_paths(connected):
    root, _, tokens, _, _ = connected
    now = datetime.now(timezone.utc)
    system_settings_service.set_values({"scratch_ttl_value": "1", "scratch_ttl_unit": "minute"})
    old_run = _run(f"aiv_{(now - timedelta(days=1)):%Y%m%d}_000301", now - timedelta(seconds=70))
    young_run = _run(f"aiv_{(now - timedelta(days=1)):%Y%m%d}_000302", now - timedelta(seconds=40))
    old_id, old_token, old_row = _token(root, 301, now - timedelta(hours=1), now - timedelta(seconds=70))
    young_id, young_token, young_row = _token(root, 302, now - timedelta(hours=1), now - timedelta(seconds=40))
    tokens[old_id] = old_row
    tokens[young_id] = young_row
    ai._cleanup_retained_scratches(PROJECT)
    token_scratch.sweep(PROJECT)
    assert not old_run.exists() and not old_token.exists()
    assert young_run.exists() and young_token.exists()
