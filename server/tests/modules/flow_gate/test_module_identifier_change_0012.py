"""flowgate.v02gui.0012 0001-B item B — a Group (module) identifier changes without rewriting references.

Contract under test (0003-NR §9, query #604):
  - PATCH /projects/{pid}/modules/{name} changes title and/or identifier independently; "neither" is a no-op.
  - The permanent key (groups.module / documents.module / group_id / id_counter / branch input) never changes;
    only project_module_identifiers rows are written.
  - The old identifier keeps resolving to the same module and stays reserved for it.
  - Format / reserved / taken identifiers are refused with a stable code.
  - New documents of an existing Flow always use the group's permanent key, whatever module a request carried.

Lightweight: a minimal SQLite schema plus the real migration 187, no app startup.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))
_MIGRATION_187 = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "187_project_module_identifiers.sql"

P = "mi0012"
NOW = "2026-11-14T00:00:00+09:00"


class _Store:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])

    def table_exists(self, name):
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]
        ).fetchone() is not None

    @contextmanager
    def transaction(self):
        try:
            yield self
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_name TEXT);
        CREATE TABLE project_modules (
            module_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, title TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (project_id, name));
        CREATE TABLE groups (group_id TEXT PRIMARY KEY, project_id TEXT, module TEXT, title TEXT);
        """
    )
    c.executescript(_MIGRATION_187.read_text(encoding="utf-8"))
    c.execute("INSERT INTO projects VALUES (?, ?)", [P, "MI"])
    for name, title in (("v02gui", "v0.2.chat"), ("server", "Server")):
        c.execute(
            "INSERT INTO project_modules VALUES (?, ?, ?, ?, ?, ?)",
            [f"{P}:{name}", P, name, title, NOW, NOW],
        )
    c.execute("INSERT INTO groups VALUES (?, ?, ?, ?)", [f"{P}.v02gui.0001", P, "v02gui", "Flow 1"])
    # A module known only through groups.module (no project_modules row).
    c.execute("INSERT INTO groups VALUES (?, ?, ?, ?)", [f"{P}.legacy.0001", P, "legacy", "Old"])
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def client(conn):
    from modules.flow_gate.api.v1.module_routes import router
    from modules.flow_gate.db import module_identifiers

    module_identifiers._TABLE_SEEN = False
    store = _Store(conn)
    app = FastAPI()
    app.include_router(router)
    with patch("modules.flow_gate.api.v1.module_routes.get_store", return_value=store), \
         patch("modules.flow_gate.db.module_identifiers.get_store", return_value=store), \
         patch("modules.flow_gate.api.v1.module_routes.verify_bearer", return_value={"user_id": "t"}):
        yield TestClient(app)
    module_identifiers._TABLE_SEEN = False


def _patch(client, name, body):
    return client.patch(f"/api/v1/projects/{P}/modules/{name}", json=body)


def _keys_untouched(conn):
    rows = conn.execute("SELECT group_id, module FROM groups ORDER BY group_id").fetchall()
    assert [tuple(r) for r in rows] == [(f"{P}.legacy.0001", "legacy"), (f"{P}.v02gui.0001", "v02gui")]
    names = [r[0] for r in conn.execute("SELECT name FROM project_modules ORDER BY name")]
    assert "v02gui" in names


def test_title_only(client, conn):
    r = _patch(client, "v02gui", {"title": "GUI"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["name"], d["module_key"], d["title"]) == ("v02gui", "v02gui", "GUI")
    assert d["title_changed"] is True and d["identifier_changed"] is False
    _keys_untouched(conn)


def test_identifier_only_keeps_title_and_keys(client, conn):
    from modules.flow_gate.db import module_identifiers as mi

    r = _patch(client, "v02gui", {"title": "v0.2.chat", "name": "gui"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["name"], d["module_key"], d["title"]) == ("gui", "v02gui", "v0.2.chat")
    assert d["title_changed"] is False and d["identifier_changed"] is True
    _keys_untouched(conn)
    # Both the new and the old identifier reach the same module.
    assert mi.resolve_module_key(P, "gui") == "v02gui"
    assert mi.resolve_module_key(P, "v02gui") == "v02gui"
    assert mi.exposed_identifier(P, "v02gui") == "gui"
    # The PATCH path accepts the new identifier too.
    r2 = _patch(client, "gui", {"title": "GUI"})
    assert r2.status_code == 200 and r2.json()["module_key"] == "v02gui"


def test_both_and_neither(client, conn):
    r = _patch(client, "v02gui", {"title": "GUI", "name": "gui"})
    assert r.status_code == 200
    assert r.json()["title_changed"] and r.json()["identifier_changed"]
    r = _patch(client, "gui", {"title": "GUI", "name": "gui"})
    assert r.status_code == 200
    assert not r.json()["title_changed"] and not r.json()["identifier_changed"]
    assert _patch(client, "gui", {}).status_code == 400


@pytest.mark.parametrize(
    "name,status,code",
    [
        ("GUI", 422, "module_identifier_uppercase"),
        ("gui.2", 422, "module_identifier_invalid"),
        ("x" * 41, 422, "module_identifier_too_long"),
        ("none", 422, "module_identifier_reserved"),
        ("server", 409, "module_identifier_taken"),   # another module's permanent key
        ("legacy", 409, "module_identifier_taken"),   # a key known only through groups.module
    ],
)
def test_refused_identifiers(client, conn, name, status, code):
    r = _patch(client, "v02gui", {"name": name})
    assert r.status_code == status, r.text
    assert r.json()["code"] == code
    assert conn.execute("SELECT COUNT(*) FROM project_module_identifiers").fetchone()[0] == 0


def test_retired_identifier_stays_reserved_and_back_to_key(client, conn):
    from modules.flow_gate.db import module_identifiers as mi

    assert _patch(client, "v02gui", {"name": "gui"}).status_code == 200
    assert _patch(client, "gui", {"name": "chat"}).status_code == 200
    assert mi.resolve_module_key(P, "gui") == "v02gui"          # retired, still resolves
    r = _patch(client, "server", {"name": "gui"})               # another module may not take it
    assert r.status_code == 409 and r.json()["code"] == "module_identifier_taken"
    assert _patch(client, "chat", {"name": "gui"}).status_code == 200   # the owner may take it back
    assert _patch(client, "gui", {"name": "v02gui"}).status_code == 200  # back to the key
    assert mi.exposed_identifier(P, "v02gui") == "v02gui"
    current = conn.execute("SELECT COUNT(*) FROM project_module_identifiers WHERE is_current = 1").fetchone()[0]
    assert current == 0
    # A new module cannot be created under a reserved identifier.
    r = client.post(f"/api/v1/projects/{P}/modules", json={"name": "chat"})
    assert r.status_code == 409


def test_groups_only_module_gets_a_row(client, conn):
    r = _patch(client, "legacy", {"name": "old-ui"})
    assert r.status_code == 200, r.text
    row = conn.execute("SELECT name, title FROM project_modules WHERE name = 'legacy'").fetchone()
    assert tuple(row) == ("legacy", "legacy")
    _keys_untouched(conn)


def test_document_module_is_the_group_key(client, conn):
    from modules.flow_gate.db import module_identifiers as mi

    assert _patch(client, "v02gui", {"name": "gui"}).status_code == 200
    group = {"group_id": f"{P}.v02gui.0001", "module": "v02gui"}
    # A request carrying the new identifier (or anything else) is numbered/stored under the key.
    assert mi.canonical_group_module(P, group, "gui") == "v02gui"
    assert mi.canonical_group_module(P, group, "server") == "v02gui"
    assert mi.canonical_group_module(P, group, None) == "v02gui"
    # Without a group row only the translation applies (new Flow creation path).
    assert mi.canonical_group_module(P, None, "gui") == "v02gui"
    assert mi.canonical_group_module(P, None, "brandnew") == "brandnew"
    assert mi.current_identifiers([P]) == {P: {"v02gui": "gui"}}
