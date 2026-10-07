"""Real Source Bundle harness for Test Basis suites (flowgate.default.0682 T#1).

The Basis now captures the Group worktree into a Source Bundle and judges it with a Live
Probe, so these suites run the production capture, materializer, integrity check, Pin
SQL and execution copy over real git repositories. Only the edges are replaced: the
Group -> worktree lookup (a dict of temp repositories), the Bundle storage root (a temp
directory), the Group lock (``stub_group_lock``), and the documents/events tables (an
in-memory dict that a failed transaction rolls back together with the SQLite rows).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path

from group_lock_stub import stub_group_lock

_MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"
PROJECT = "flowgate"
CASES = [{"case_id": "TC-001", "execution_mode": "automated",
          "automation_ref": "tests/test_a.py::test_a", "test_assets": "tests/fixture.json"}]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path, value: str = "1") -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "tests" / "test_a.py").write_text("def test_a(): assert True\n")
    (root / "tests" / "fixture.json").write_text('{"v":1}\n')
    (root / "server" / "app.py").write_text(f"VALUE = {value}\n")
    git(root, "init")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    # Never captured: secret file names are excluded by the Bundle policy.
    (root / ".env").write_text("SECRET=1\n")
    return root


class BundleStore:
    """The store surface the db modules use, over one in-memory SQLite connection."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for name in ("122_source_bundles.sql", "123_source_bundle_cleanup_metrics.sql",
                     "139_source_bundle_pins.sql"):
            self.conn.executescript((_MIGRATIONS / name).read_text(encoding="utf-8"))
        self.docs: dict[str, dict] = {}
        self.events: list[tuple[str, str, dict]] = []
        self._depth = 0

    def _execute(self, sql, values=None):
        self.conn.execute(sql, list(values or []))

    def _execute_affected(self, sql, values=None):
        return self.conn.execute(sql, list(values or [])).rowcount

    def _fetch_one(self, sql, values=None):
        row = self.conn.execute(sql, list(values or [])).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, values=None):
        return [dict(row) for row in self.conn.execute(sql, list(values or [])).fetchall()]

    @contextmanager
    def transaction(self):
        if self._depth:
            yield self
            return
        self._depth = 1
        docs, events = copy.deepcopy(self.docs), list(self.events)
        self.conn.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self.conn.execute("ROLLBACK")
            self.docs, self.events = docs, events
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._depth = 0


class BasisEnv:
    def __init__(self, tmp_path: Path, monkeypatch):
        from modules.flow_gate.db import connection
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import events as db_events
        from modules.flow_gate.db import source_bundles as db_source_bundles
        from modules.flow_gate.services import source_bundle_materializer as materializer
        from modules.flow_gate.services import test_asset_service
        from modules.flow_gate.services import test_basis_service as basis
        from modules.flow_gate.services import test_run_service as runner

        self.tmp_path = tmp_path
        self.store = BundleStore()
        self.roots: dict[str, Path] = {}
        self.cases = copy.deepcopy(CASES)
        self.db = db_source_bundles
        monkeypatch.setattr(db_source_bundles, "get_store", lambda: self.store)
        monkeypatch.setattr(connection, "get_store", lambda: self.store)
        monkeypatch.setattr(test_asset_service, "get_store", lambda: self.store)

        def resolve_worktree(project_id, group_id):
            root = self.roots.get(group_id)
            if project_id != PROJECT or root is None or not root.is_dir():
                raise materializer.SourceBundleError("group_worktree_unavailable", "no worktree")
            return root.resolve()

        monkeypatch.setattr(materializer, "resolve_worktree", resolve_worktree)
        monkeypatch.setattr(materializer, "bundle_path",
                            lambda project_id, bundle_id: tmp_path / "bundles" / bundle_id)
        stub_group_lock(monkeypatch)
        monkeypatch.setattr(db_docs, "get_by_id",
                            lambda doc_id: copy.deepcopy(self.store.docs.get(doc_id)))

        def update(doc_id, fields):
            if doc_id not in self.store.docs:
                return None
            self.store.docs[doc_id].update(fields)
            return copy.deepcopy(self.store.docs[doc_id])

        monkeypatch.setattr(db_docs, "update", update)
        monkeypatch.setattr(db_events, "insert_event", lambda doc_id, event, note=None, **kw:
                            self.store.events.append((doc_id, event, json.loads(note or "{}"))))
        monkeypatch.setattr(basis, "_doc_cases", lambda doc: self.cases)
        monkeypatch.setattr(runner, "_scratch_dir", lambda doc, run_id: tmp_path / "runs" / run_id)
        basis._MEMO.clear()

    def repo(self, group_id: str, value: str = "1") -> Path:
        self.roots[group_id] = make_repo(self.tmp_path / group_id, value)
        return self.roots[group_id]

    def ts(self, group_id: str = "g1", number: int = 10) -> dict:
        doc = {"doc_id": f"flowgate.default.0682.{number:04d}-TS", "project_id": PROJECT,
               "group_id": group_id, "revision_no": 1, "doc_review_status": "approved",
               "meta": "{}"}
        self.store.docs[doc["doc_id"]] = doc
        return copy.deepcopy(doc)

    def approve(self, doc: dict) -> dict:
        """The approval transaction's Basis part: capture, store and pin."""
        from modules.flow_gate.services import test_basis_service as basis
        captured = basis.capture(doc, self.cases)
        with self.store.transaction():
            updated = self.store.docs[doc["doc_id"]]
            updated["meta"] = basis.metadata_with_basis(updated, captured)
            basis.pin(updated, captured)
        return captured

    def doc(self, doc_id: str) -> dict:
        return copy.deepcopy(self.store.docs[doc_id])

    def bundle_files(self, bundle_id: str) -> dict:
        manifest = json.loads((self.tmp_path / "bundles" / bundle_id / "manifest.json").read_text())
        return {entry["path"]: entry["sha256"] for entry in manifest["files"]}

    def remove_bundle_dir(self, bundle_id: str) -> None:
        import os
        import shutil
        import stat
        root = self.tmp_path / "bundles" / bundle_id

        def writable(func, path, _exc):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(path)
        for path in [root, *root.rglob("*")]:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        shutil.rmtree(root, onerror=writable)
