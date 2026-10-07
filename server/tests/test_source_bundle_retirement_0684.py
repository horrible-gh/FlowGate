"""0684 T#4: Source Bundle final removal (D#1 §2-2, §7; 0672 NR0003 §7 stage 4).

What is pinned here:

* the Bundle service/access/cleanup/exposure/materializer modules, the CLI and overview
  routes and the DB module are gone, and nothing on the startup/shutdown/token/group path
  still calls into them;
* no worker is told about Source Bundle any more (API tool list -> 0492, CLI prompt/env,
  mention, help/notices -> 0372/0654);
* migration 140 drops the five Bundle tables and replaces the ``bundle`` lock holder kind
  with ``run_prepare`` in all three dialects, keeping a left-over lock row;
* the retired on-disk ``source-bundles/`` store is removed once at startup;
* the live-source permission judgment (``kind_for_step``) is exactly what 0672 pinned.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER))

from modules.flow_gate.services import mention_service, source_fingerprint, tool_registry  # noqa: E402
from modules.flow_gate.services import api_server_tools as tools  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_cli  # noqa: E402
from modules.flow_gate.services.git import lock_manager as lm  # noqa: E402

RETIRED_MODULES = (
    "modules.flow_gate.services.source_bundle_service",
    "modules.flow_gate.services.source_bundle_access_service",
    "modules.flow_gate.services.source_bundle_cleanup_service",
    "modules.flow_gate.services.source_bundle_exposure",
    "modules.flow_gate.services.source_bundle_materializer",
    "modules.flow_gate.api.v1.source_bundle_routes",
    "modules.flow_gate.db.source_bundles",
)
BUNDLE_TABLES = ("source_bundles", "source_bundle_builds", "source_bundle_usages",
                 "source_bundle_scratches", "source_bundle_pins")
MIGRATIONS = _SERVER / "sql" / "migrations"


# ── code ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", RETIRED_MODULES)
def test_bundle_modules_are_deleted(name):
    assert importlib.util.find_spec(name) is None


@pytest.mark.parametrize("relative", [
    "startup.py", "routers/main.py",
    "modules/flow_gate/services/token_service.py",
    "modules/flow_gate/workflow/pipeline_service.py",
    "modules/flow_gate/services/ai_invoke/terminal.py",
    "modules/flow_gate/services/ai_invoke/worker.py",
    "modules/flow_gate/services/ai_invoke/provider_cli.py",
    "modules/flow_gate/services/api_server_tools.py",
    "modules/flow_gate/services/help_catalog.py",
    "modules/flow_gate/services/mention_service.py",
])
def test_no_caller_reaches_into_a_bundle_module(relative):
    text = (_SERVER / relative).read_text(encoding="utf-8")
    for name in RETIRED_MODULES:
        assert name.rsplit(".", 1)[-1] not in text, (relative, name)
    assert "/source-bundles" not in text
    assert "FLOWGATE_BUNDLE_API" not in text


def test_startup_runs_the_disk_retirement_instead_of_the_bundle_sweeper():
    import startup
    assert not hasattr(startup, "start_source_bundle_cleanup")
    assert "remove_retired_source_bundle_storage()" in inspect.getsource(startup.run_all)


# ── advertisement: CLI prompt / env ──────────────────────────────────────────

class _Stop(BaseException):
    """Escapes _cli_execute right after the prompt is handed to the child."""


def _launch(monkeypatch, tmp_path, scope):
    captured = {}

    class Owner:
        active = False

        def __init__(self, *_a):
            pass

        def creationflags(self, flags):
            return flags

        def attach(self, _proc):
            pass

        def close(self):
            pass

    class Proc:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def communicate(_proc, _owner, input=None, timeout=None):
        captured["prompt"] = input.decode("utf-8")
        raise _Stop()

    monkeypatch.setattr(provider_cli, "_canonicalize_cli_prompt", lambda prompt, base: (prompt, "http://h/flowgate/api/v1"))
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda *_a: (
        {"effective_command": "agent", "agent_cwd": str(tmp_path), "spawn_cwd": str(tmp_path)}, "valid"))
    monkeypatch.setattr(provider_cli, "_audit_cli_launch", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_start_progress_watchdog", lambda *_a: (None, None))
    monkeypatch.setattr(provider_cli, "_stop_progress_watchdog", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_absolute_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(provider_cli.process_runner, "WindowsProcessOwner", Owner)
    monkeypatch.setattr(provider_cli.process_runner, "popen_kwargs", lambda _cwd, env: captured.update(env=dict(env)) or {})
    monkeypatch.setattr(provider_cli.process_runner, "communicate_with_cleanup", communicate)
    monkeypatch.setattr(provider_cli.process_runner, "kill_process_tree", lambda *_a: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: Proc())
    run = {"run_id": "r", "raw_token": "raw", "scratch_dir": str(tmp_path), "token_scratch_dir": str(tmp_path),
           "api_base_url": "http://h/flowgate/api/v1", "cancel_event": type("E", (), {"is_set": lambda self: False})(),
           "doc_ref": "d", "action_scope": scope}
    with pytest.raises(_Stop):
        provider_cli._cli_execute({"cli_command": "claude", "kind": "claude"}, "PROMPT", run)
    return captured


# The TS/TSR rows printed the Bundle CLI boundary and exported FLOWGATE_BUNDLE_API before.
@pytest.mark.parametrize("scope", ["new", "edit", "review", "chat", "test_run"])
def test_cli_prompt_and_env_carry_no_bundle(monkeypatch, tmp_path, scope):
    captured = _launch(monkeypatch, tmp_path, scope)
    assert captured["prompt"] == "PROMPT"
    assert not any(key.startswith("FLOWGATE_BUNDLE") for key in captured["env"])
    assert captured["env"]["FLOWGATE_TOKEN"] == "raw"


# ── advertisement: mention ───────────────────────────────────────────────────

def _mention(monkeypatch, scope, doc_type, head_type):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)
    return mention_service.build_mention(
        project="p", module="default", group="0684", parent_type=doc_type, parent_doc_number="0001",
        parent_title="t", parent_doc_id="p.default.0684.0001-" + doc_type, head_type=head_type,
        head_status="pending", scratch_dir="S", raw_token="RAW", api_base_url="http://h/flowgate/api/v1",
        action_scope=scope,
    )


# The first four rows printed "## Source Bundle policy" before (the 0672 preserved set).
@pytest.mark.parametrize("scope, doc_type, head_type", [
    ("new", "R", "TS"), ("new", "TR", "TS"), ("edit", "TS", "TS"), ("edit", "TSR", "TSR"),
    ("new", "R", "N"), ("new", "R", "T"), ("new", "T", "TR"), ("edit", "TR", "TR"),
])
def test_mention_never_mentions_source_bundle(monkeypatch, scope, doc_type, head_type):
    text = _mention(monkeypatch, scope, doc_type, head_type)
    assert "Source Bundle" not in text
    assert "/help/items/source_bundles" not in text
    assert "disposable Scratch" not in text


@pytest.mark.parametrize("scope, doc_type, head_type", [("new", "T", "TR"), ("edit", "TR", "TR")])
def test_tr_mention_keeps_the_self_check_section(monkeypatch, scope, doc_type, head_type):
    text = _mention(monkeypatch, scope, doc_type, head_type)
    assert "## TR test responsibility and Self-check" in text
    assert "(run_self_check / read_self_check)." in text


# ── permission (unchanged since 0672) ────────────────────────────────────────

@pytest.mark.parametrize("scope", ["new", "edit", "review", "workflow_decide", "chat", "resolve_conflict", "resolve_base_dirty", "test_run"])
@pytest.mark.parametrize("step_type", ["R", "N", "NR", "T", "TR", "TS", "TSR", "D", "CH", None])
def test_permission_judgment_is_untouched(scope, step_type):
    if scope in {"review", "workflow_decide", "chat", "resolve_conflict"}:
        expected = ("read", None)
    elif scope == "resolve_base_dirty":
        expected = ("read_write", None)
    elif scope not in {"new", "edit"}:
        expected = ("none", "token_scope_none")
    elif step_type in {"TR", "TSR", "TS"}:
        expected = ("read_write", None)
    else:
        expected = ("read", None)
    assert tool_registry.kind_for_step(scope, step_type) == expected


# ── promotion guard ──────────────────────────────────────────────────────────

def test_source_call_guard_keeps_only_the_legacy_snapshot_rule(monkeypatch):
    monkeypatch.setattr(tools.remote_tool_service, "handle", lambda op, token, body: (200, {"ok": True, "op": op}))
    run = {"project_id": "p", "group_id": "g", "doc_ref": "d", "action_scope": "edit"}
    # There is no Bundle store to promote from any more; the path is an ordinary path.
    assert tools.source_call(run, "live", "write_source_file",
                             {"path": "source-bundles/sb_" + "0" * 32 + "/source/app.py", "content": "x"}) \
        == (200, {"ok": True, "op": "write"})
    status, payload = tools.source_call(run, "live", "patch_source_file",
                                        {"path": "x/source-snapshots/snap_a1/source/app.py", "old_string": "a", "new_string": "b"})
    assert (status, payload["error"]["code"]) == (403, "snapshot_promotion_blocked")


# ── lock holder kind ─────────────────────────────────────────────────────────

def test_run_prepare_is_stored_as_itself_and_bundle_is_gone():
    assert "bundle" not in lm.STORED_HOLDER_KINDS
    assert lm.stored_holder_kind("run_prepare") == "run_prepare"
    assert "run_prepare" in lm._LONG_G_KINDS and "bundle" not in lm._LONG_G_KINDS
    assert "bundle_start" not in lm._WAIT_PARAMS
    assert lm.wait_budget("G", "run_prepare") == 30


def _holder_kinds(sql: str) -> set[str]:
    match = re.search(r"holder_kind IN \(([^)]*)\)", sql)
    assert match, sql[:200]
    return set(re.findall(r"'([a-z_0-9]+)'", match.group(1)))


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_migration_140_exists_in_every_dialect_with_the_code_holder_list(dialect):
    sql = (MIGRATIONS / dialect / "140_source_bundle_retirement.sql").read_text(encoding="utf-8")
    for table in BUNDLE_TABLES:
        assert f"DROP TABLE IF EXISTS {table};" in sql
    # children before the parent they reference
    assert sql.index("DROP TABLE IF EXISTS source_bundle_pins") < sql.index("DROP TABLE IF EXISTS source_bundles;")
    assert sql.index("DROP TABLE IF EXISTS source_bundle_usages") < sql.index("DROP TABLE IF EXISTS source_bundles;")
    assert _holder_kinds(sql) == set(lm.STORED_HOLDER_KINDS)
    assert "'run_prepare'" in sql


def _migrate_until(path: Path, last: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    for migration in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
        if migration.name > last:
            break
        conn.executescript(migration.read_text(encoding="utf-8"))
    return conn


def _tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_sqlite_migration_140_drops_bundle_tables_and_relabels_a_left_over_lock(tmp_path):
    conn = _migrate_until(tmp_path / "before.db", "139_source_bundle_pins.sql")
    assert set(BUNDLE_TABLES) <= _tables(conn)
    now = "2026-10-08T00:00:00+09:00"
    conn.executescript(f"""
        INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at)
            VALUES ('p140', 'P', 1, '{now}', '{now}');
        INSERT INTO server_instance (instance_id, node_key, pid, started_at, heartbeat_at, status)
            VALUES ('inst_{"a" * 32}', 'node', 1, '{now}', '{now}', 'alive');
        INSERT INTO resource_lock (lock_key, domain, project_id, group_id, holder_ctx_id, holder_kind,
            instance_id, lock_epoch, hold_class, acquired_at)
            VALUES ('G:p140:g', 'G', 'p140', 'g', 'req:1', 'bundle', 'inst_{"a" * 32}',
                    'le_{"0" * 24}', 'long', '{now}');
    """)
    conn.commit()
    conn.executescript((MIGRATIONS / "sqlite" / "140_source_bundle_retirement.sql").read_text(encoding="utf-8"))

    assert not (set(BUNDLE_TABLES) & _tables(conn))
    assert conn.execute("SELECT holder_kind, hold_class FROM resource_lock").fetchall() == [("run_prepare", "long")]
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='resource_lock'")}
    assert {"idx_resource_lock_instance", "idx_resource_lock_scope", "idx_resource_lock_sweep_long",
            "idx_resource_lock_sweep_short", "idx_resource_lock_job"} <= indexes
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE resource_lock SET holder_kind='bundle'")
    # the parent tables' cascades still reach the rebuilt table
    conn.execute("DELETE FROM projects WHERE project_id='p140'")
    assert conn.execute("SELECT COUNT(*) FROM resource_lock").fetchone() == (0,)
    conn.close()


def test_the_full_migration_chain_ends_without_bundle_tables(migrated_sqlite_db):
    conn = sqlite3.connect(migrated_sqlite_db("retirement_0684.db"))
    try:
        assert not (set(BUNDLE_TABLES) & _tables(conn))
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='resource_lock'").fetchone()[0]
        assert _holder_kinds(ddl) == set(lm.STORED_HOLDER_KINDS)
    finally:
        conn.close()


# ── one-time disk cleanup ────────────────────────────────────────────────────

def test_retired_storage_is_removed_once_and_nothing_else(tmp_path):
    import startup
    root_a, root_b, root_c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    scratch = root_a / "source-bundles" / "k" / "scratch" / ("f" * 64)
    scratch.mkdir(parents=True)
    (scratch / "x.py").write_text("x")
    (root_a / "source-bundles" / "k" / ("sb_" + "0" * 32)).mkdir()
    (root_a / "worktrees" / "keep").mkdir(parents=True)
    (root_b / "source-bundles").mkdir(parents=True)
    root_c.mkdir()

    removed = startup.remove_retired_source_bundle_storage([root_a, root_b, root_c])
    assert sorted(removed) == sorted([root_a / "source-bundles", root_b / "source-bundles"])
    assert not (root_a / "source-bundles").exists() and not (root_b / "source-bundles").exists()
    assert (root_a / "worktrees" / "keep").is_dir()
    assert startup.remove_retired_source_bundle_storage([root_a, root_b, root_c]) == []


def test_a_linked_store_is_not_followed(tmp_path):
    import startup
    outside = tmp_path / "outside"
    (outside / "important").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(outside, root / "source-bundles", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available")
    assert startup.remove_retired_source_bundle_storage([root]) == []
    assert (outside / "important").is_dir()


def _configure_roots(monkeypatch, tmp_path, *, env):
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.storage import paths as storage_paths
    overrides = {"p1": str(tmp_path / "p1"), "p2": str(tmp_path / "p2"), "p3": "  "}
    monkeypatch.setattr(db_projects, "list_projects",
                        lambda: [{"project_id": p} for p in ("p1", "p2", "p3", "p4")])
    monkeypatch.setattr(storage_paths, "_project_override_value",
                        lambda project_id: (overrides.get(project_id) or "").strip() or None)
    monkeypatch.setattr(storage_paths, "_system_storage_root_value", lambda: str(tmp_path / "system"))
    monkeypatch.setattr(storage_paths, "default_storage_root", lambda: tmp_path / "default")
    if env:
        monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "env"))
    else:
        monkeypatch.delenv("FLOWGATE_STORAGE_DIR", raising=False)


def test_storage_roots_cover_default_system_and_every_project(monkeypatch, tmp_path):
    import startup
    _configure_roots(monkeypatch, tmp_path, env=False)
    assert startup._storage_roots() == {
        tmp_path / "default", tmp_path / "system", tmp_path / "p1", tmp_path / "p2"}


def test_env_root_does_not_hide_the_other_configured_roots(monkeypatch, tmp_path):
    """FLOWGATE_STORAGE_DIR wins every get_storage_root() call, so the effective root alone
    would never reach a store left under the system/default root or a project override."""
    import startup
    from modules.flow_gate.storage import paths as storage_paths
    _configure_roots(monkeypatch, tmp_path, env=True)
    assert storage_paths.get_storage_root(None) == storage_paths.get_storage_root("p1") == tmp_path / "env"
    assert startup._storage_roots() == {
        tmp_path / "env", tmp_path / "default", tmp_path / "system", tmp_path / "p1", tmp_path / "p2"}

    for name in ("env", "default", "system", "p1", "p2"):
        (tmp_path / name / "source-bundles" / "k" / "scratch").mkdir(parents=True)
        (tmp_path / name / "keep").mkdir()
    removed = startup.remove_retired_source_bundle_storage()
    assert sorted(removed) == sorted(tmp_path / n / "source-bundles"
                                     for n in ("env", "default", "system", "p1", "p2"))
    for name in ("env", "default", "system", "p1", "p2"):
        assert not (tmp_path / name / "source-bundles").exists()
        assert (tmp_path / name / "keep").is_dir()
    assert startup.remove_retired_source_bundle_storage() == []


def test_storage_roots_survive_an_unreadable_project_list(monkeypatch, tmp_path):
    import startup
    from modules.flow_gate.db import projects as db_projects
    _configure_roots(monkeypatch, tmp_path, env=True)

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(db_projects, "list_projects", boom)
    assert startup._storage_roots() == {tmp_path / "env", tmp_path / "default", tmp_path / "system"}


# ── env ──────────────────────────────────────────────────────────────────────

def test_retired_bundle_limit_names_are_no_longer_read(monkeypatch, caplog):
    monkeypatch.delenv("FLOWGATE_SOURCE_MAX_FILES", raising=False)
    monkeypatch.setenv("FLOWGATE_BUNDLE_MAX_FILES", "7")
    with caplog.at_level("WARNING"):
        assert source_fingerprint._limit("FLOWGATE_SOURCE_MAX_FILES", 20000) == 20000
    assert "FLOWGATE_BUNDLE_MAX_FILES is no longer read" in caplog.text
    monkeypatch.setenv("FLOWGATE_SOURCE_MAX_FILES", "9")
    assert source_fingerprint._limit("FLOWGATE_SOURCE_MAX_FILES", 20000) == 9
