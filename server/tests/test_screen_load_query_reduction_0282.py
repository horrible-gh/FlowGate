"""Screen-load SQL reduction (flowgate.default.0282 NR0003) — unit suite.

Covers:
  - 발견 1: _groups_root_wf_done / _group_ac_doc_ids batch helpers return
    exactly what the per-group probes (_group_root_wf_done / _group_ac_doc_id)
    return, so project_git_status's loop-of-queries → one-IN-query swap cannot
    change any slot's transition or any pending row's [open] target.
  - 발견 2: db/meta_cache TTL cache for projects.get_by_id /
    git_integration.get_config — off by default under TESTING, on with an
    explicit FLOWGATE_META_CACHE_TTL, invalidated by every write path, and
    copy-out so caller-side mutation cannot poison the cache.

Environment mirrors test_git_integration_0115.py: TESTING=1 and a temporary
SQLite built from the real sqlite migrations, patched into connection.STORE.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

import sys

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


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

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(autouse=True)
def clean_meta_cache():
    """Every test starts and ends with an empty metadata cache."""
    from modules.flow_gate.db import meta_cache

    meta_cache.clear_all()
    yield
    meta_cache.clear_all()


@pytest.fixture(scope="module")
def seed(tmp_db):
    from modules.flow_gate.db import documents, groups, projects

    projects.create({"project_id": "qredprj", "project_name": "QRedProj"})

    def _group(group_id: str, doc_types: list[str], wf_done: set[str]) -> None:
        groups.create({
            "group_id": group_id,
            "project_id": "qredprj",
            "module": "default",
            "title": group_id,
        })
        for seq, doc_type in enumerate(doc_types, start=1):
            doc_id = f"{group_id}.{seq:04d}-{doc_type}"
            documents.create({
                "doc_id": doc_id,
                "project_id": "qredprj",
                "module": "default",
                "group_id": group_id,
                "type_code": doc_type,
                "seq": seq,
                "title": doc_type,
            })
            if doc_id in wf_done or doc_type in wf_done:
                documents.update(doc_id, {"doc_review_status": "wf_done"})

    # g1: R root final-approved, two ACs (newest must win the [open] target)
    _group("qredprj.default.0001", ["R", "AC", "AC"], wf_done={"R"})
    # g2: B root final-approved, no AC yet
    _group("qredprj.default.0002", ["B", "TR"], wf_done={"B"})
    # g3: root not final-approved (approved ≠ wf_done for the R/B probe)
    _group("qredprj.default.0003", ["R", "AC"], wf_done=set())
    yield


G1 = "qredprj.default.0001"
G2 = "qredprj.default.0002"
G3 = "qredprj.default.0003"
G_MISSING = "qredprj.default.9999"


# ── 발견 1: batch helpers ≡ per-group probes ─────────────────────────────────

class TestBatchWfDone:
    def test_matches_single_probe_per_group(self, seed):
        from modules.flow_gate.services import git_service as svc

        ids = [G1, G2, G3, G_MISSING]
        batch = svc._groups_root_wf_done(ids)
        single = {gid for gid in ids if svc._group_root_wf_done(gid)}
        assert batch == single == {G1, G2}

    def test_empty_input_short_circuits(self):
        from modules.flow_gate.services import git_service as svc

        assert svc._groups_root_wf_done([]) == set()


class TestBatchAcDocIds:
    def test_matches_single_probe_and_picks_newest(self, seed):
        from modules.flow_gate.services import git_service as svc

        ids = [G1, G2, G3, G_MISSING]
        batch = svc._group_ac_doc_ids(ids)
        single = {gid: svc._group_ac_doc_id(gid) for gid in ids}
        assert batch == {gid: doc for gid, doc in single.items() if doc is not None}
        # Two ACs in g1 — ORDER BY doc_id DESC and MAX(doc_id) must agree.
        assert batch[G1] == f"{G1}.0003-AC"
        assert G2 not in batch and G_MISSING not in batch

    def test_empty_input_short_circuits(self):
        from modules.flow_gate.services import git_service as svc

        assert svc._group_ac_doc_ids([]) == {}


# ── 발견 2: metadata TTL cache ───────────────────────────────────────────────

class TestMetaCachePolicy:
    def test_disabled_by_default_under_testing(self):
        from modules.flow_gate.db import meta_cache

        assert os.environ.get("TESTING") == "1"
        assert os.environ.get("FLOWGATE_META_CACHE_TTL") is None
        assert meta_cache._configured_ttl() == 0.0

    def test_env_override_enables(self, monkeypatch):
        from modules.flow_gate.db import meta_cache

        monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "300")
        assert meta_cache._configured_ttl() == 300.0
        monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "0")
        assert meta_cache._configured_ttl() == 0.0


class TestProjectCache:
    def test_cached_read_and_write_invalidation(self, seed, monkeypatch, tmp_db):
        from modules.flow_gate.db import projects

        monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "300")
        mock_db, _ = tmp_db

        assert projects.get_by_id("qredprj")["project_name"] == "QRedProj"
        # A raw write the cache cannot see → the cached row is (deliberately) stale.
        mock_db.execute(
            "UPDATE projects SET project_name = 'RawRename' WHERE project_id = 'qredprj'"
        )
        assert projects.get_by_id("qredprj")["project_name"] == "QRedProj"
        # The real write path invalidates → immediately fresh.
        projects.update("qredprj", {"project_name": "ApiRename"})
        assert projects.get_by_id("qredprj")["project_name"] == "ApiRename"

    def test_copy_out_prevents_cache_poisoning(self, seed, monkeypatch):
        from modules.flow_gate.db import projects

        monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "300")
        row = projects.get_by_id("qredprj")
        row["project_name"] = "mutated-by-caller"
        assert projects.get_by_id("qredprj")["project_name"] != "mutated-by-caller"

    def test_disabled_ttl_reads_through(self, seed, tmp_db):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        before = projects.get_by_id("qredprj")["project_name"]
        mock_db.execute(
            "UPDATE projects SET project_name = 'ReadThrough' WHERE project_id = 'qredprj'"
        )
        assert projects.get_by_id("qredprj")["project_name"] == "ReadThrough"
        mock_db.execute(
            "UPDATE projects SET project_name = ? WHERE project_id = 'qredprj'",
            [before],
        )


class TestGitConfigCache:
    def test_upsert_and_delete_invalidate(self, seed, monkeypatch):
        from modules.flow_gate.db import git_integration as db_git

        monkeypatch.setenv("FLOWGATE_META_CACHE_TTL", "300")
        assert db_git.get_config("qredprj") is None
        cfg = db_git.upsert_config("qredprj", {
            "repo_url": "https://example.invalid/r.git",
            "provider": "generic",
            "secret_enc": None,
            "enabled": True,
        })
        assert cfg["repo_url"] == "https://example.invalid/r.git"
        # The upsert invalidated the cached None → cached read sees the row.
        assert db_git.get_config("qredprj")["enabled"] == 1
        # Second upsert must see the existing row (never a stale None → INSERT).
        db_git.upsert_config("qredprj", {
            "repo_url": "https://example.invalid/r2.git",
            "provider": "generic",
            "secret_enc": None,
            "enabled": False,
        })
        assert db_git.get_config("qredprj")["repo_url"] == "https://example.invalid/r2.git"
        assert db_git.delete_config("qredprj") is True
        assert db_git.get_config("qredprj") is None
