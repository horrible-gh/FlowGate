"""Project-list N+1 removal (flowgate.default.0559 T0005 / B0001) — unit suite.

B0001 ("리팩터링 하다 만거같은데?") and 0559 NR0003 §7·§10 권고1: one
``GET /projects?status=active`` fired ``1 + 2N`` queries because
``_attach_project_modules()`` called ``db/projects.list_modules()`` once per
project (operations measurement: N=13 → 27 queries / 84.5ms).

Covers:
  - 쿼리 예산: the endpoint's query count stays flat (3) whether one project or
    many are active, measured the way test_screen_load_query_reduction_0282.py
    measures it — a mock SQLite connection that counts every statement it runs.
  - 청킹 경계: >900 ids are split into 900-sized chunks (SQLite's historical 999
    bind-variable limit) and the per-chunk results are merged.
  - 단건 호환: list_modules(project_id) keeps its signature/return shape and now
    delegates, so it must agree with the batch path row for row.
  - 정렬·제목 규칙: 'none'/"All" first, then alphabetical; project_modules.title
    wins when present, the label itself is the fallback.
  - `_project_modules_table_exists()` guard survives on the batch path.

Environment mirrors test_screen_load_query_reduction_0282.py: TESTING=1 and a
temporary SQLite built from the real sqlite migrations, patched into
connection.STORE.
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
    def __init__(self, db):
        self._db = db
        self._cur = None

    def execute(self, sql, params=None):
        self._db.record(sql, params)
        self._cur = self._db._conn.execute(sql, params or [])
        self._db._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    """SQLite connection that also keeps a log of every statement it runs.

    The query budget below is measured here, at the connection, so nothing a
    caller does above it (helper layering, caching, chunking) can hide a query.
    """

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.calls: list[tuple[str, list]] = []
        self.recording = False

    def record(self, sql, params=None) -> None:
        if self.recording:
            self.calls.append((sql, list(params or [])))

    @contextmanager
    def counting(self):
        """Record statements for the duration of the block, then stop."""
        self.calls = []
        self.recording = True
        try:
            yield self.calls
        finally:
            self.recording = False

    def execute(self, sql, params=None):
        self.record(sql, params)
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def raw_execute(self, sql, params=None):
        """Seed/fixture write that is never counted as product behaviour."""
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        self.record(sql, params)
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        self.record(sql, params)
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self)

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
    from modules.flow_gate.db import meta_cache

    meta_cache.clear_all()
    yield
    meta_cache.clear_all()


# Module/title rule fixtures ─────────────────────────────────────────────────
#
#   modprj   groups: 'none', 'server', 'alpha'
#            project_modules: server→"Server Side", zeta→"" , none→"Nope"
#   blankprj a single group whose module is '' (must normalise to 'none')
#   emptyprj a project with neither groups nor project_modules
#   ghostprj never created — a caller may still ask for it
#   __SYSTEM__ the special id the batch must NOT filter out (seeded by
#            migration 004_rbac.sql, so the fixture only adds its group)
P_MOD = "modprj"
P_BLANK = "blankprj"
P_EMPTY = "emptyprj"
P_GHOST = "ghostprj"
P_SYSTEM = "__SYSTEM__"


def _add_project_module(mock_db, project_id: str, name: str, title: str) -> None:
    mock_db.raw_execute(
        "INSERT INTO project_modules (module_id, project_id, name, title,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        [f"{project_id}:{name}", project_id, name, title],
    )


@pytest.fixture(scope="module")
def seed(tmp_db):
    from modules.flow_gate.db import groups, projects

    mock_db, _ = tmp_db

    for pid in (P_MOD, P_BLANK, P_EMPTY):
        projects.create({"project_id": pid, "project_name": pid.upper()})
    assert projects.get_by_id(P_SYSTEM) is not None  # 004_rbac.sql seed

    for seq, module in enumerate(["none", "server", "alpha"], start=1):
        groups.create({
            "group_id": f"{P_MOD}.{module}.{seq:04d}",
            "project_id": P_MOD,
            "module": module,
            "title": module,
        })
    _add_project_module(mock_db, P_MOD, "server", "Server Side")
    _add_project_module(mock_db, P_MOD, "zeta", "")
    _add_project_module(mock_db, P_MOD, "none", "Nope")

    groups.create({
        "group_id": f"{P_BLANK}.0001",
        "project_id": P_BLANK,
        "module": "",
        "title": "blank module",
    })
    groups.create({
        "group_id": f"{P_SYSTEM}.0001",
        "project_id": P_SYSTEM,
        "module": "sys",
        "title": "system",
    })
    yield


@pytest.fixture
def warm_pm_probe(seed):
    """Resolve the process-wide project_modules probe before anything is counted.

    `_project_modules_table_exists()` caches its positive answer for the process
    lifetime (0282), so leaving it cold would put a one-off probe into whichever
    measurement happened to run first.
    """
    from modules.flow_gate.db import projects
    from modules.flow_gate.db.connection import get_store

    assert projects._project_modules_table_exists(get_store()) is True
    yield


def _sql_of(calls, table: str) -> list[tuple[str, list]]:
    return [(sql, params) for sql, params in calls if f"FROM {table}" in sql]


# ── 쿼리 예산: /projects?status=active ───────────────────────────────────────

class TestProjectsEndpointQueryBudget:
    """One screen load's query count must not follow the project count."""

    def _call_endpoint(self):
        from modules.flow_gate.settings.routers import project_settings as mod

        return mod.list_projects_endpoint(status="active", user={"user_id": "tester"})

    def _seed_active_projects(self, tmp_db, count: int) -> list[str]:
        from modules.flow_gate.db import groups, projects

        mock_db, _ = tmp_db
        ids = []
        for i in range(count):
            pid = f"budget{i:02d}"
            ids.append(pid)
            if projects.get_by_id(pid) is None:
                projects.create({"project_id": pid, "project_name": pid})
                groups.create({
                    "group_id": f"{pid}.0001",
                    "project_id": pid,
                    "module": "server",
                    "title": pid,
                })
            mock_db.raw_execute(
                "UPDATE projects SET is_active = 1 WHERE project_id = ?", [pid]
            )
        return ids

    def _deactivate_all(self, tmp_db) -> None:
        mock_db, _ = tmp_db
        mock_db.raw_execute("UPDATE projects SET is_active = 0")

    def test_query_count_is_flat_across_project_counts(self, tmp_db, warm_pm_probe):
        from modules.flow_gate.db import meta_cache

        mock_db, _ = tmp_db
        self._deactivate_all(tmp_db)
        self._seed_active_projects(tmp_db, 8)

        # N = 1
        mock_db.raw_execute(
            "UPDATE projects SET is_active = 0 WHERE project_id != 'budget00'"
        )
        meta_cache.clear_all()
        with mock_db.counting() as calls_one:
            payload_one = self._call_endpoint()
        count_one = len(calls_one)

        # N = 8, same DB, same endpoint
        mock_db.raw_execute(
            "UPDATE projects SET is_active = 1 WHERE project_id LIKE 'budget%'"
        )
        meta_cache.clear_all()
        with mock_db.counting() as calls_many:
            payload_many = self._call_endpoint()
        count_many = len(calls_many)

        assert len(payload_one["projects"]) == 1
        assert len(payload_many["projects"]) == 8
        # The budget: projects list + one project_modules IN + one groups IN.
        assert count_one == 3, calls_one
        assert count_many == count_one, calls_many
        # Pre-fix shape was 1 + 2N — 17 statements for these eight projects.
        assert count_many < 1 + 2 * 8
        assert len(_sql_of(calls_many, "project_modules")) == 1
        assert len(_sql_of(calls_many, "groups")) == 1

        self._deactivate_all(tmp_db)

    def test_endpoint_payload_matches_the_per_project_path(self, tmp_db, warm_pm_probe):
        """Same rows as before: every project's modules equal list_modules()."""
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        self._deactivate_all(tmp_db)
        mock_db.raw_execute(
            "UPDATE projects SET is_active = 1 WHERE project_id IN (?, ?, ?)",
            [P_MOD, P_BLANK, P_EMPTY],
        )
        payload = self._call_endpoint()
        by_id = {p["project_id"]: p["modules"] for p in payload["projects"]}

        assert set(by_id) == {P_MOD, P_BLANK, P_EMPTY}
        for pid, modules in by_id.items():
            expected = [
                {"name": m["name"], "title": m.get("title") or m["name"]}
                for m in projects.list_modules(pid)
            ]
            assert modules == expected, pid
        assert [m["name"] for m in by_id[P_MOD]] == ["none", "alpha", "server", "zeta"]

        self._deactivate_all(tmp_db)

    def test_single_project_response_path_still_batches(self, tmp_db, warm_pm_probe):
        """_project_state_response() passes a 1-element list — still one pair."""
        from modules.flow_gate.settings.routers import project_settings as mod

        mock_db, _ = tmp_db
        with mock_db.counting() as calls:
            row = mod._project_state_response(P_MOD)

        assert [m["name"] for m in row["modules"]] == ["none", "alpha", "server", "zeta"]
        assert len(_sql_of(calls, "project_modules")) == 1
        assert len(_sql_of(calls, "groups")) == 1


# ── 청킹 경계: 900 bind-variable chunks ──────────────────────────────────────

class TestChunkBoundary:
    def test_over_900_ids_are_chunked_and_merged(self, tmp_db, warm_pm_probe):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        # Two real projects placed on opposite sides of the 900 boundary, padded
        # with ids that exist nowhere, so the merge has to cross chunks.
        pad = [f"padprj{i:04d}" for i in range(998)]
        ids = [P_MOD] + pad[:949] + [P_BLANK] + pad[949:]
        assert len(ids) == 1000 and ids.index(P_BLANK) == 950

        with mock_db.counting() as calls:
            result = projects.list_modules_bulk(ids)

        pm_calls = _sql_of(calls, "project_modules")
        group_calls = _sql_of(calls, "groups")
        assert [len(p) for _, p in pm_calls] == [900, 100]
        assert [len(p) for _, p in group_calls] == [900, 100]
        # SQLite's historical limit is 999 binds per statement.
        assert max(len(p) for _, p in calls) <= 900

        assert set(result) == set(ids)
        assert len(result) == 1000
        assert [m["name"] for m in result[P_MOD]] == ["none", "alpha", "server", "zeta"]
        assert result[P_BLANK] == [{"name": "none", "title": "All"}]
        assert all(result[pid] == [] for pid in pad)

    def test_exactly_900_ids_use_a_single_chunk(self, tmp_db, warm_pm_probe):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        ids = [P_MOD] + [f"edgeprj{i:04d}" for i in range(899)]
        with mock_db.counting() as calls:
            result = projects.list_modules_bulk(ids)

        assert len(_sql_of(calls, "project_modules")) == 1
        assert len(_sql_of(calls, "groups")) == 1
        assert len(result) == 900
        assert [m["name"] for m in result[P_MOD]] == ["none", "alpha", "server", "zeta"]

    def test_duplicates_are_collapsed_but_still_keyed(self, tmp_db, warm_pm_probe):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        with mock_db.counting() as calls:
            result = projects.list_modules_bulk([P_MOD, P_BLANK, P_MOD, P_BLANK])

        assert list(result) == [P_MOD, P_BLANK]
        assert [len(p) for _, p in _sql_of(calls, "groups")] == [2]

    def test_empty_input_short_circuits(self, tmp_db, warm_pm_probe):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        with mock_db.counting() as calls:
            assert projects.list_modules_bulk([]) == {}
        assert calls == []


# ── 단건 호환 + 정렬·제목 규칙 ───────────────────────────────────────────────

class TestSingleCallCompatibility:
    @pytest.mark.parametrize("pid", [P_MOD, P_BLANK, P_EMPTY, P_GHOST])
    def test_single_call_equals_batch_entry(self, pid, warm_pm_probe):
        from modules.flow_gate.db import projects

        assert projects.list_modules(pid) == projects.list_modules_bulk([pid])[pid]

    def test_unknown_project_returns_empty_list(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        assert projects.list_modules(P_GHOST) == []
        assert projects.list_modules_bulk([P_GHOST]) == {P_GHOST: []}

    def test_return_shape_is_unchanged(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        modules = projects.list_modules(P_MOD)
        assert isinstance(modules, list)
        assert all(set(m) == {"name", "title"} for m in modules)


class TestOrderingAndTitleRules:
    def test_none_first_then_alphabetical(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        names = [m["name"] for m in projects.list_modules_bulk([P_MOD])[P_MOD]]
        assert names[0] == "none"
        assert names[1:] == sorted(names[1:])
        assert names == ["none", "alpha", "server", "zeta"]

    def test_none_is_titled_all_even_with_a_project_modules_title(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        titles = {m["name"]: m["title"] for m in projects.list_modules_bulk([P_MOD])[P_MOD]}
        # project_modules row for 'none' says "Nope"; "All" still wins (TR556).
        assert titles["none"] == "All"

    def test_project_modules_title_wins_and_label_is_the_fallback(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        titles = {m["name"]: m["title"] for m in projects.list_modules_bulk([P_MOD])[P_MOD]}
        assert titles["server"] == "Server Side"   # title present
        assert titles["zeta"] == "zeta"            # title empty → label
        assert titles["alpha"] == "alpha"          # groups-only label

    def test_blank_module_label_normalises_to_none(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        assert projects.list_modules_bulk([P_BLANK])[P_BLANK] == [
            {"name": "none", "title": "All"}
        ]

    def test_special_project_ids_are_not_filtered_out(self, warm_pm_probe):
        """__SYSTEM__ stays in the batch — discarding it is the client's job
        (client/src/main/stores/project.ts), not this query layer's."""
        from modules.flow_gate.db import projects

        batch = projects.list_modules_bulk([P_SYSTEM, P_MOD])
        assert P_SYSTEM in batch
        assert [m["name"] for m in batch[P_SYSTEM]] == ["sys"]
        assert batch[P_SYSTEM] == projects.list_modules(P_SYSTEM)

    def test_projects_never_borrow_each_others_modules(self, warm_pm_probe):
        from modules.flow_gate.db import projects

        batch = projects.list_modules_bulk([P_MOD, P_BLANK, P_EMPTY, P_GHOST])
        assert [m["name"] for m in batch[P_MOD]] == ["none", "alpha", "server", "zeta"]
        assert batch[P_BLANK] == [{"name": "none", "title": "All"}]
        assert batch[P_EMPTY] == []
        assert batch[P_GHOST] == []


# ── 마이그레이션 이전 DB 보호 ────────────────────────────────────────────────

class TestProjectModulesTableGuard:
    def test_missing_table_skips_the_project_modules_query(
        self, tmp_db, monkeypatch, warm_pm_probe
    ):
        from modules.flow_gate.db import projects

        mock_db, _ = tmp_db
        monkeypatch.setattr(
            projects, "_project_modules_table_exists", lambda store: False
        )
        with mock_db.counting() as calls:
            result = projects.list_modules_bulk([P_MOD, P_BLANK])

        assert _sql_of(calls, "project_modules") == []
        assert len(_sql_of(calls, "groups")) == 1
        # groups alone: 'zeta' only existed in project_modules, so it is gone,
        # and 'server'/'alpha' fall back to their own labels.
        assert result[P_MOD] == [
            {"name": "none", "title": "All"},
            {"name": "alpha", "title": "alpha"},
            {"name": "server", "title": "server"},
        ]
        assert result[P_BLANK] == [{"name": "none", "title": "All"}]

    def test_single_call_keeps_the_same_guard(self, tmp_db, monkeypatch, warm_pm_probe):
        from modules.flow_gate.db import projects

        monkeypatch.setattr(
            projects, "_project_modules_table_exists", lambda store: False
        )
        assert projects.list_modules(P_MOD) == [
            {"name": "none", "title": "All"},
            {"name": "alpha", "title": "alpha"},
            {"name": "server", "title": "server"},
        ]
