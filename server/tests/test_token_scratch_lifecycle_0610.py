"""0610 token scratch ownership, rollback, and retention regressions."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.flow_gate.services import token_scratch, token_service
from modules.flow_gate.settings import scratch_retention
from modules.flow_gate.services import ai_invoke_service as ai

PROJECT = "project-0610"
OLD = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def default_scratch_ttl(monkeypatch):
    monkeypatch.setattr(scratch_retention, "effective_retention", lambda: timedelta(days=7))


@pytest.fixture
def owned(monkeypatch, tmp_path):
    monkeypatch.setattr(token_scratch.storage_paths, "get_storage_root", lambda: tmp_path)
    monkeypatch.setattr(token_service, "get_storage_root", lambda: tmp_path)
    monkeypatch.setattr(token_scratch.db_projects, "get_by_id", lambda _: {"project_name": "Project"})
    monkeypatch.setattr(token_scratch.storage_paths, "resolve_storage_dir", lambda value, _: Path(value))
    rows = {}
    leases = {}
    monkeypatch.setattr(token_scratch.db_tokens, "get_by_id", lambda token_id: rows.get(token_id))
    monkeypatch.setattr(token_scratch.db_leases, "get_by_token_id", lambda token_id: leases.get(token_id))
    with ai._runs_lock:
        old_runs = dict(ai._runs)
        ai._runs.clear()
    yield tmp_path, rows, leases
    with ai._runs_lock:
        ai._runs.clear()
        ai._runs.update(old_runs)


def _scratch(root, number, *, old=True):
    token_id = f"tok_20261009_{number:06d}"
    path = root / "work" / "Project" / token_id
    token_scratch.create(PROJECT, token_id, path)
    if old:
        manifest_path = path / token_scratch.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = OLD
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return token_id, path


def _row(token_id, path, *, consumed_at=None, revoked_at=None, expires_at=FUTURE):
    return {
        "token_id": token_id, "project": PROJECT, "scratch_dir": str(path),
        "consumed_at": consumed_at, "revoked_at": revoked_at,
        "expires_at": expires_at,
    }


def test_active_token_and_active_ai_references_are_preserved(owned):
    root, rows, leases = owned
    active_id, active = _scratch(root, 1)
    rows[active_id] = _row(active_id, active)
    run_id, run_path = _scratch(root, 2)
    rows[run_id] = _row(run_id, run_path, consumed_at=OLD)
    lease_id, lease_path = _scratch(root, 3)
    rows[lease_id] = _row(lease_id, lease_path, revoked_at=OLD)
    leases[lease_id] = {"token_id": lease_id}
    with ai._runs_lock:
        ai._runs["aiv_20261009_000001"] = {
            "status": "running", "project_id": PROJECT,
            "token_scratch_dir": str(run_path),
        }

    assert token_scratch.sweep(PROJECT) == 0
    assert active.is_dir() and run_path.is_dir() and lease_path.is_dir()


def test_terminal_expired_and_manifest_proven_orphan_retention(owned):
    root, rows, _ = owned
    consumed_id, consumed = _scratch(root, 4)
    rows[consumed_id] = _row(consumed_id, consumed, consumed_at=OLD)
    expired_id, expired = _scratch(root, 5)
    rows[expired_id] = _row(expired_id, expired, expires_at=OLD)
    _, orphan = _scratch(root, 6)
    young_id, young = _scratch(root, 7, old=False)
    rows[young_id] = _row(
        young_id, young, consumed_at=datetime.now(timezone.utc).isoformat()
    )

    assert token_scratch.sweep(PROJECT) == 3
    assert not consumed.exists() and not expired.exists() and not orphan.exists()
    assert young.exists()


def test_relative_tokens_scratch_dir_resolves_to_owned_path(owned, monkeypatch):
    root, rows, _ = owned
    token_id, path = _scratch(root, 13)
    rows[token_id] = _row(token_id, path, consumed_at=OLD)
    rows[token_id]["scratch_dir"] = str(path.relative_to(root))
    monkeypatch.setattr(
        token_scratch.storage_paths, "resolve_storage_dir",
        lambda value, _: root / value,
    )
    assert token_scratch.sweep(PROJECT) == 1
    assert not path.exists()


def test_legacy_path_mismatch_and_symlink_fail_closed(owned, tmp_path):
    root, rows, _ = owned
    legacy = root / "work" / "Project" / "tok_20261009_000008"
    legacy.mkdir(parents=True)
    mismatch_id, mismatch = _scratch(root, 9)
    rows[mismatch_id] = _row(mismatch_id, mismatch, consumed_at=OLD)
    rows[mismatch_id]["scratch_dir"] = str(root / "elsewhere")
    forged_id, forged = _scratch(root, 10)
    manifest_path = forged / token_scratch.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scratch_path"] = str(root / "elsewhere")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "outside"
    target.mkdir()
    link = root / "work" / "Project" / "tok_20261009_000011"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        link = None

    assert token_scratch.sweep(PROJECT) == 0
    assert legacy.exists() and mismatch.exists() and forged.exists()
    assert target.exists()
    if link is not None:
        assert link.is_symlink()


def test_reparse_guard_blocks_candidate(owned, monkeypatch):
    root, rows, _ = owned
    token_id, path = _scratch(root, 12)
    rows[token_id] = _row(token_id, path, consumed_at=OLD)
    original = token_scratch._unsafe_link
    monkeypatch.setattr(token_scratch, "_unsafe_link", lambda candidate: candidate == path or original(candidate))
    assert token_scratch.sweep(PROJECT) == 0
    assert path.exists()


class _Store:
    @contextmanager
    def transaction(self):
        yield


def test_successful_issue_records_manifest_and_db_scratch_path(owned, monkeypatch):
    root, _, _ = owned
    token_id = "tok_20261009_000098"
    captured = {}
    monkeypatch.setattr(token_service, "_next_token_id", lambda: token_id)
    monkeypatch.setattr(token_service, "_active_pepper", lambda: ("v1", "pepper"))
    monkeypatch.setattr(token_service, "to_storage_relative", lambda path, _: str(path))
    monkeypatch.setattr(token_service, "get_store", lambda: _Store())
    monkeypatch.setattr(token_scratch, "sweep_on_issue", lambda _: None)
    monkeypatch.setattr(token_service.db_tokens, "create", lambda data: captured.update(data) or {})
    monkeypatch.setattr(token_service.db_events, "create", lambda _: None)

    issued = token_service.issue(PROJECT, None, "new", None, "user")
    path = Path(issued["scratch_dir"])
    manifest = json.loads((path / token_scratch.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert path == root / "work" / "Project" / token_id
    assert captured["scratch_dir"] == str(path)
    assert manifest["project_id"] == PROJECT
    assert manifest["token_id"] == token_id
    assert manifest["scratch_path"] == str(path)


@pytest.mark.parametrize("scope,fail_at", [
    ("new", "insert"), ("new", "event"), ("chat", "claim"),
])
def test_issue_failure_removes_its_new_scratch(owned, monkeypatch, scope, fail_at):
    root, _, _ = owned
    token_id = "tok_20261009_000099"
    monkeypatch.setattr(token_service, "_next_token_id", lambda: token_id)
    monkeypatch.setattr(token_service, "_active_pepper", lambda: ("v1", "pepper"))
    monkeypatch.setattr(token_service, "to_storage_relative", lambda path, _: str(path))
    monkeypatch.setattr(token_service, "get_store", lambda: _Store())
    monkeypatch.setattr(token_scratch, "sweep_on_issue", lambda _: None)
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", lambda *_: (None, True))
    monkeypatch.setattr(token_service.db_tokens, "create", lambda _: (_ for _ in ()).throw(RuntimeError("insert")) if fail_at == "insert" else {})
    monkeypatch.setattr(token_service.db_events, "create", lambda _: (_ for _ in ()).throw(RuntimeError("event")) if fail_at == "event" else None)
    if fail_at == "claim":
        from modules.flow_gate.db import user_chat_source_access
        monkeypatch.setattr(user_chat_source_access, "claim", lambda *_: (_ for _ in ()).throw(RuntimeError("claim")))

    with pytest.raises(RuntimeError, match=fail_at):
        token_service.issue(PROJECT, None, scope, None, "user")
    assert not (root / "work" / "Project" / token_id).exists()


def test_startup_sweeps_after_lease_recovery(monkeypatch):
    import startup
    calls = []
    for name in ("configure_console_encoding", "record_deployment", "preload_singletons",
                 "recover_ai_invoke_leases", "sweep_ai_run_scratches", "sweep_token_scratches",
                 "recover_git_sessions", "encrypt_ai_provider_keys"):
        monkeypatch.setattr(startup, name, lambda name=name: calls.append(name))
    startup.run_all()
    assert calls.index("recover_ai_invoke_leases") < calls.index("sweep_token_scratches")


def test_startup_sweep_reclaims_old_manifest_orphan(owned, monkeypatch):
    root, _, _ = owned
    _, orphan = _scratch(root, 100)
    monkeypatch.setattr(token_scratch.db_projects, "list_projects", lambda: [{"project_id": PROJECT}])
    assert token_scratch.startup_sweep() == 1
    assert not orphan.exists()
