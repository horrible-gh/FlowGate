"""engine_recipes (flowgate.default.0157) — db + service logic test.

Covers:
  - migration 057 seed: pytest + npm recipes present, active                    → DB §3
  - engine normalization / identity (trim + lower + whitespace-collapse)         → L §2-1
  - help list / single-engine / unregistered-engine empty list (not 404)         → P §help
  - CRUD: create / conflict 409 / suppressed-tombstone + revive / patch / immutable engine → L §2-2
  - engine detection: explicit tag, then ordered command scan                    → L §2-3
  - auto-learning from a passed run: create (origin=auto) / update / tombstone respected / setup join → L §2-4
  - failure classification (infra vs code) across the L §2-5 matrix              → L §2-5
  - consecutive-infra attempt counter derived from test_runs history             → L §2-6
  - TS-mention "Engine recipes" block always emitted with the help URL           → L §2-7
Environment: TESTING=1 with a temporary SQLite + the real queries.json (mirrors test_project_test_commands.py).
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
if _QUERIES_JSON.exists():
    raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    for section, entries in raw.items():
        if isinstance(entries, dict):
            for key, sql in entries.items():
                if isinstance(sql, str):
                    _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod
    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            if key in _QUERIES:
                return _QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


# ── migration seed (DB §3) ────────────────────────────────────────────────────

class TestSeed:
    def test_seed_recipes_present(self, patch_store):
        from modules.flow_gate.services import engine_recipe_service as svc
        engines = {r["engine"] for r in svc.list_help()}
        assert {"pytest", "npm"} <= engines
        pytest_recipe = svc.list_help("pytest")
        assert len(pytest_recipe) == 1
        assert "venv" in pytest_recipe[0]["setup"]
        assert pytest_recipe[0]["origin"] == "seed"


# ── normalization / identity (L §2-1) ─────────────────────────────────────────

class TestNormalize:
    def test_trim_lower_collapse(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        assert svc.normalize_engine("  PyTest  ") == "pytest"
        assert svc.normalize_engine("Go  Test") == "go test"


# ── help (P §help) ────────────────────────────────────────────────────────────

class TestHelp:
    def test_single_and_unregistered(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        assert svc.list_help("npm")[0]["engine"] == "npm"
        assert svc.list_help("cargo-not-seeded") == []     # empty list, never 404


# ── CRUD + tombstone (L §2-2) ─────────────────────────────────────────────────

class TestCrud:
    def test_create_conflict_revive_patch(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        created = svc.create("Deno", "Deno test", "deno cache main.ts", "deno test", "", "tok_x")
        assert created["engine"] == "deno"
        assert created["origin"] == "worker"
        assert "status" not in created                     # internal field hidden
        # duplicate active → 409
        with pytest.raises(svc.EngineRecipeConflictError):
            svc.create("deno", "dup", "x", "y", "", "tok_y")
        # suppress → tombstone, gone from help
        assert svc.suppress(created["id"], "tok_x") is True
        assert not any(r["engine"] == "deno" for r in svc.list_help())
        assert svc.suppress(created["id"], "tok_x") is False   # 404 signal
        # manual re-add revives the SAME row
        revived = svc.create("deno", "Deno again", "deno cache main.ts", "deno test", "", "tok_z")
        assert revived["id"] == created["id"]
        # patch active row; engine is immutable
        patched = svc.patch(revived["id"], {"notes": "needs deno on PATH"}, "tok_z")
        assert patched["notes"] == "needs deno on PATH"
        with pytest.raises(svc.EngineRecipeValidationError):
            svc.patch(revived["id"], {"engine": "denoland"}, "tok_z")
        assert svc.patch(999999, {"notes": "x"}, "tok_z") is None   # 404

    def test_create_empty_setup_422(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        with pytest.raises(svc.EngineRecipeValidationError):
            svc.create("elixir", "Elixir", "   ", "mix test", "", "tok_x")


# ── engine detection (L §2-3) ─────────────────────────────────────────────────

class TestDetect:
    def test_tag_takes_priority(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        doc = {"content": "some text\nengine: pytest\nmore"}
        assert svc.detect_engine(doc, [{"cmd": "npm test"}]) == "pytest"

    def test_command_scan_order(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        # pytest wins over an incidental npm helper step
        items = [{"cmd": "npm install"}, {"cmd": ".venv/bin/pytest -q"}]
        assert svc.detect_engine({}, items) == "pytest"
        assert svc.detect_engine({}, [{"cmd": "npx vitest run"}]) == "npm"
        assert svc.detect_engine({}, [{"cmd": "cargo test"}]) == "cargo"
        assert svc.detect_engine({}, [{"cmd": "echo hi"}]) is None


# ── auto-learning (L §2-4) ────────────────────────────────────────────────────

class TestReflect:
    def test_create_new_origin_auto(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        doc = {"content": "engine: gotest-new", "doc_id": "p.default.0001.0005-TS", "project_id": "p"}
        run = {"run_id": "trun_x_1"}
        items = [
            {"kind": "setup", "cmd": "go mod download"},
            {"kind": "case", "cmd": "go test ./...", "case_no": "TC-1"},
        ]
        svc.reflect_from_passed_run(doc, run, items)
        rec = svc.list_help("gotest-new")
        assert rec and rec[0]["origin"] == "auto"
        assert rec[0]["setup"] == "go mod download"
        assert rec[0]["run_example"] == "go test ./..."
        assert rec[0]["last_success_run_id"] == "trun_x_1"

    def test_update_existing_keeps_origin(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        base = svc.create("rspec", "RSpec", "bundle install", "bundle exec rspec", "", "tok_x")
        assert base["last_success_run_id"] is None
        doc = {"content": "", "doc_id": "p.default.0001.0006-TS", "project_id": "p"}
        run = {"run_id": "trun_x_2"}
        # detect via command scan won't hit rspec; use explicit tag
        doc["content"] = "engine: rspec"
        svc.reflect_from_passed_run(doc, run, [{"kind": "case", "cmd": "bundle exec rspec spec"}])
        rec = svc.list_help("rspec")[0]
        assert rec["origin"] == "worker"                   # origin preserved
        assert rec["last_success_run_id"] == "trun_x_2"
        assert rec["run_example"] == "bundle exec rspec spec"

    def test_tombstone_not_revived_by_reflect(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        made = svc.create("phpunit", "PHPUnit", "composer install", "phpunit", "", "tok_x")
        assert svc.suppress(made["id"], "tok_x") is True
        doc = {"content": "engine: phpunit", "doc_id": "p.default.0001.0007-TS", "project_id": "p"}
        svc.reflect_from_passed_run(doc, {"run_id": "trun_x_3"}, [{"kind": "case", "cmd": "phpunit"}])
        assert svc.list_help("phpunit") == []              # still suppressed


# ── failure classification (L §2-5) ───────────────────────────────────────────

class TestClassify:
    def test_infra_by_error_code(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        assert svc.classify_failure({"error": "setup_failed"}, []) == svc.INFRA
        assert svc.classify_failure({"error": "src_root_missing"}, []) == svc.INFRA

    def test_infra_by_log_and_exit127(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        items = [{"kind": "setup", "output_tail": "sh: 1: npm: not found", "exit_code": 127}]
        assert svc.classify_failure({"error": None, "case_passed": 0, "case_failed": 0}, items) == svc.INFRA

    def test_code_when_cases_ran_and_failed(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        run = {"error": None, "case_passed": 12, "case_failed": 3}
        items = [{"kind": "case", "output_tail": "AssertionError", "exit_code": 1}]
        assert svc.classify_failure(run, items) == svc.CODE

    def test_permission_denied_only_infra_in_setup(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        setup_fail = [{"kind": "setup", "output_tail": "Permission denied", "exit_code": 1}]
        assert svc.classify_failure({"case_passed": 0, "case_failed": 0}, setup_fail) == svc.INFRA
        # same string from a test case is a real failure, not infra
        case_fail = [{"kind": "case", "output_tail": "Permission denied", "exit_code": 1}]
        assert svc.classify_failure({"case_passed": 1, "case_failed": 1}, case_fail) == svc.CODE

    def test_nothing_ran_is_infra(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        assert svc.classify_failure({"error": None, "case_passed": 0, "case_failed": 0}, []) == svc.INFRA


# ── attempt counter (L §2-6) ──────────────────────────────────────────────────

class TestAttempts:
    def test_consecutive_infra_since_last_pass(self, monkeypatch):
        from modules.flow_gate.services import engine_recipe_service as svc
        from modules.flow_gate.db import test_runs as db_test_runs
        runs = [
            {"run_id": "r3", "status": "failed", "error": "setup_failed", "case_passed": 0, "case_failed": 0},
            {"run_id": "r2", "status": "failed", "error": "setup_failed", "case_passed": 0, "case_failed": 0},
            {"run_id": "r1", "status": "passed"},
            {"run_id": "r0", "status": "failed", "error": "setup_failed"},
        ]
        monkeypatch.setattr(db_test_runs, "list_by_doc", lambda doc_id: runs)
        monkeypatch.setattr(db_test_runs, "list_cases", lambda run_id: [])
        assert svc.count_infra_attempts("p.default.0001.0005-TS") == 2   # stops at the passed run

    def test_code_failure_breaks_the_streak(self, monkeypatch):
        from modules.flow_gate.services import engine_recipe_service as svc
        from modules.flow_gate.db import test_runs as db_test_runs
        runs = [
            {"run_id": "r2", "status": "failed", "error": "setup_failed", "case_passed": 0, "case_failed": 0},
            {"run_id": "r1", "status": "failed", "error": None, "case_passed": 5, "case_failed": 2},
        ]
        monkeypatch.setattr(db_test_runs, "list_by_doc", lambda doc_id: runs)
        monkeypatch.setattr(db_test_runs, "list_cases", lambda run_id: [])
        assert svc.count_infra_attempts("p.default.0001.0006-TS") == 1   # code failure breaks it


# ── TS-mention block (L §2-7) ─────────────────────────────────────────────────

class TestMentionBlock:
    def test_block_always_present_with_help_url(self):
        from modules.flow_gate.services import engine_recipe_service as svc
        block = svc.build_engine_recipes_block("http://host:8089/flowgate/api/v1")
        assert "http://host:8089/flowgate/api/v1/test-commands/help" in block
        assert "?engine=" in block
