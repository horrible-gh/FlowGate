"""flowgate.v02gui.0012 0001-B item B — changing a Group identifier renames the module key everywhere.

Contract under test:
  - PATCH /projects/{pid}/modules/{name} changes title and/or identifier independently; "neither" is a no-op.
  - An identifier change renames the key itself (services.module_rename): group / document ids, module
    columns, id_counter, project_modules, storage paths, branch names, ids inside text and JSON, the files
    (directories, revision snapshot names, front matter, bodies). The old identifier is free afterwards.
  - An identifier another group uses is refused (409 module_identifier_taken) and the refusal names it.
  - Running work in the module refuses the rename (409 module_rename_busy); nothing changes.
  - Format / reserved identifiers are refused with a stable code.
  - flowgate.v02test.0040 T0005: every refusal is a warning, an unexpected failure an exception record and a
    success an info line with its duration and document count, all of them reaching logs/default.log; a Flow /
    document lock not free in time (one wait budget for all the locks together, not one per lock) is 409
    module_rename_busy (reason flow_lock_timeout / document_lock_timeout) with nothing changed; a module above the
    document limit is refused (409 module_rename_too_large) before any lock is taken.
  - flowgate.v02test.0040 T0007: an identifier an archived group holds is still refused (409 module_identifier_taken),
    with details.holder.archived / archived_at and a message saying the holder is archived; the archived group's own
    identifier change succeeds, keeps project_modules.archived_at / archived_by, and frees the identifier.

Lightweight: a small SQLite schema, a temp storage root, no app startup, no Git, no migrations run.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

P = "mi0012"
NOW = "2026-11-14T00:00:00+09:00"


class _Store:
    """The FlowGateStore surface the rename uses, on one SQLite connection."""

    dialect = 1  # SQLITE

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])

    def _execute_affected(self, sql, params=None):
        return self._conn.execute(sql, params or []).rowcount

    def table_exists(self, name):
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]).fetchone() is not None

    def column_exists(self, table, column):
        return any(r[1] == column for r in self._conn.execute(f"PRAGMA table_info({table})"))

    @contextmanager
    def transaction(self):
        """Like FlowGateStore.transaction: after_commit / after_rollback callbacks run after the frame."""
        from modules.flow_gate.db import connection
        tx = connection._tx_local
        if getattr(tx, "txn", None) is not None:
            yield self
            return
        tx.txn, tx.after_commit, tx.after_rollback = self._conn, [], []
        self._conn.execute("BEGIN")
        try:
            yield self
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            tx.txn = None
            callbacks, tx.after_rollback, tx.after_commit = list(tx.after_rollback), [], []
            for cb in reversed(callbacks):
                cb()
            raise
        tx.txn = None
        callbacks, tx.after_commit, tx.after_rollback = list(tx.after_commit), [], []
        for cb in callbacks:
            cb()


SCHEMA = """
CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_name TEXT);
CREATE TABLE project_modules (
    module_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, title TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (project_id, name));
CREATE TABLE groups (group_id TEXT PRIMARY KEY, project_id TEXT, module TEXT, parent_id TEXT REFERENCES groups(group_id),
                     title TEXT);
CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL UNIQUE, project_id TEXT,
                        module TEXT, group_id TEXT REFERENCES groups(group_id), file_path TEXT, meta TEXT);
CREATE TABLE events (event_id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(doc_id), note TEXT);
CREATE TABLE document_revisions (id INTEGER PRIMARY KEY, doc_id TEXT REFERENCES documents(doc_id), backup_path TEXT);
CREATE TABLE id_counter (project_id TEXT, module TEXT, group_seq TEXT, last_seq INTEGER,
                         PRIMARY KEY (project_id, module, group_seq));
CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, doc_id TEXT, body TEXT, body_hash TEXT);
CREATE TABLE group_git_state (group_id TEXT PRIMARY KEY, project_id TEXT, branch TEXT);
CREATE TABLE remote_tool_grant (grant_id TEXT PRIMARY KEY, project TEXT, module TEXT);
"""

G = f"{P}.v02gui.0001"
DOC = f"{G}.0001-R"
CH = f"{G}.0002-CH"
REL = f"documents/{P}/main/v02gui/0001"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO projects VALUES (?, ?)", [P, "MI"])
    c.execute("INSERT INTO projects VALUES ('other', 'Other')")
    for pid, name, title in ((P, "v02gui", "v0.2.chat"), (P, "server", "Server"), (P, "v02test", "v0.2.test"),
                             ("other", "v02gui", "Other GUI")):
        c.execute("INSERT INTO project_modules VALUES (?, ?, ?, ?, ?, ?)", [f"{pid}:{name}", pid, name, title, NOW, NOW])
    c.execute("INSERT INTO groups VALUES (?, ?, 'v02gui', NULL, 'Flow 1')", [G, P])
    c.execute("INSERT INTO groups VALUES (?, ?, 'v02test', ?, 'Child')", [f"{P}.v02test.0001", P, G])
    c.execute("INSERT INTO groups VALUES ('other.v02gui.0001', 'other', 'v02gui', NULL, 'Other')")
    c.execute("INSERT INTO groups VALUES (?, ?, 'legacy', NULL, 'Old')", [f"{P}.legacy.0001", P])
    c.execute("INSERT INTO documents (doc_id, project_id, module, group_id, file_path, meta) VALUES (?,?,?,?,?,?)",
              [DOC, P, "v02gui", G, f"{REL}/0001-R_requirement.md", f'{{"module": "v02gui", "src": "{CH}"}}'])
    c.execute("INSERT INTO documents (doc_id, project_id, module, group_id, file_path, meta) VALUES (?,?,?,?,?,?)",
              [CH, P, "v02gui", G, f"{REL}/0002-CH_document.md", None])
    c.execute("INSERT INTO documents (doc_id, project_id, module, group_id, file_path, meta) VALUES (?,?,?,?,?,?)",
              ["other.v02gui.0001.0001-R", "other", "v02gui", "other.v02gui.0001",
               "documents/other/main/v02gui/0001/0001-R_x.md", '{"module": "v02gui"}'])
    c.execute("INSERT INTO events VALUES (1, ?, ?)", [DOC, f"see x{P}.v02gui.0001 and {CH}"])
    c.execute("INSERT INTO document_revisions VALUES (1, ?, ?)", [DOC, f"{REL}/revisions/{DOC}.r1.md"])
    c.execute("INSERT INTO id_counter VALUES (?, 'v02gui', '0001', 2)", [P])
    c.execute("INSERT INTO id_counter VALUES ('other', 'v02gui', '0001', 1)")
    body = f"answer about {DOC}"
    c.execute("INSERT INTO conversation_turns VALUES (1, ?, ?, ?)", [CH, body, hashlib.sha256(body.encode()).hexdigest()])
    c.execute("INSERT INTO group_git_state VALUES (?, ?, ?)", [G, P, f"{P}_v02gui_0001"])
    c.execute("INSERT INTO remote_tool_grant VALUES ('g1', ?, 'v02gui')", [P])
    yield c
    c.close()


@pytest.fixture()
def storage(tmp_path):
    group_dir = tmp_path / REL
    (group_dir / "revisions").mkdir(parents=True)
    (group_dir / "0002-CH").mkdir()
    front = f"---\nproject: {P}\nmodule: v02gui\ngroup: 0001\ngroup_id: {G}\ntype: R\ndoc_number: 0001-R\n---\n"
    (group_dir / "0001-R_requirement.md").write_text(front + f"see {CH}\nmodule: v02gui stays in prose\n", encoding="utf-8")
    (group_dir / "0002-CH_document.md").write_text(front.replace("0001-R", "0002-CH"), encoding="utf-8")
    (group_dir / "revisions" / f"{DOC}.r1.md").write_text(front + "old body\n", encoding="utf-8")
    attachment = f"raw {DOC} bytes".encode()
    (group_dir / "0002-CH" / "note.md").write_bytes(attachment)
    other = tmp_path / "documents" / "other" / "main" / "v02gui" / "0001"
    other.mkdir(parents=True)
    (other / "0001-R_x.md").write_text("module: v02gui\n", encoding="utf-8")
    return tmp_path


@contextmanager
def _service(conn, storage, busy=(), gate=None):
    """rename_module with the store, storage root, running-work probe and Git replaced; the gate runs the
    body in one transaction like write_gate.module_rename (or ``gate`` when given)."""
    store = _Store(conn)

    def one_transaction(op, group_ids, docs, call, **kw):
        assert op == "module_rename"
        with store.transaction():
            return call()

    gate = gate or one_transaction

    with patch("modules.flow_gate.services.module_rename.get_store", return_value=store), \
         patch("modules.flow_gate.services.module_rename.busy_reasons", return_value=list(busy)), \
         patch("modules.flow_gate.services.module_rename._git_plan",
               side_effect=lambda pid, rw, groups: (None, [
                   __import__("modules.flow_gate.services.module_rename", fromlist=["_BranchMove"])._BranchMove(
                       G, f"{P}_v02gui_0001", f"{P}_{rw.new}_0001", None, None)])), \
         patch("modules.flow_gate.storage.paths.get_storage_root", return_value=storage), \
         patch("modules.flow_gate.graph_workflow.services.card_document_ports.write_gate", side_effect=gate):
        yield store


def _rename(conn, storage, new="v02chat", busy=(), gate=None):
    from modules.flow_gate.services import module_rename
    with _service(conn, storage, busy, gate):
        return module_rename.rename_module(P, "v02gui", new, actor="t")


# ── the text rule ──────────────────────────────────────────────────────────────────────────────

def test_rewriter_is_anchored():
    from modules.flow_gate.db.module_rename import KeyRewriter
    rw = KeyRewriter(P, "v02gui", "v02chat", P, {f"{P}_v02gui_0001": f"{P}_v02chat_0001"})
    assert rw.text(f"{G}.0004-CH") == f"{P}.v02chat.0001.0004-CH"
    assert rw.text(f"x{P}.v02gui.0001") == f"x{P}.v02gui.0001"          # another project's id
    assert rw.text(f"{P}.v02gui2.0001") == f"{P}.v02gui2.0001"          # a longer module name
    assert rw.text(f"{P}.v02gui.abc") == f"{P}.v02gui.abc"              # not an id
    assert rw.text(f"module:{P}:v02gui") == f"module:{P}:v02chat"
    assert rw.text(f"module:{P}:v02gui2") == f"module:{P}:v02gui2"
    assert rw.text(f"C:\\s\\documents\\{P}\\main\\v02gui\\0001\\a.md") == f"C:\\s\\documents\\{P}\\main\\v02chat\\0001\\a.md"
    assert rw.text(f"documents/other/main/v02gui/0001/a.md") == "documents/other/main/v02gui/0001/a.md"
    assert rw.text(f"/srv/src/MI/{P}_v02gui_0001/x") == f"/srv/src/MI/{P}_v02chat_0001/x"
    assert rw.text(f"{P}_v02gui_00012") == f"{P}_v02gui_00012"
    # A bare module field is rewritten only in a text that is evidently about this project.
    assert rw.text('{"module": "v02gui"}') == '{"module": "v02gui"}'
    assert rw.text(f'{{"project_id": "{P}", "module": "v02gui"}}') == f'{{"project_id": "{P}", "module": "v02chat"}}'
    assert rw.text(f"---\nmodule: v02gui\ngroup_id: {G}\n---\n") == f"---\nmodule: v02chat\ngroup_id: {P}.v02chat.0001\n---\n"


# ── the service ────────────────────────────────────────────────────────────────────────────────

def test_rename_moves_every_key(conn, storage):
    result = _rename(conn, storage)
    new_g = f"{P}.v02chat.0001"
    assert result["groups"] == {G: new_g}
    q = lambda sql, *a: [tuple(r) for r in conn.execute(sql, a).fetchall()]  # noqa: E731
    assert q("SELECT group_id, module FROM groups WHERE project_id = ? AND title = 'Flow 1'", P) == [(new_g, "v02chat")]
    assert q("SELECT parent_id FROM groups WHERE title = 'Child'") == [(new_g,)]
    assert q("SELECT doc_id, module, group_id, file_path FROM documents WHERE project_id = ? ORDER BY id", P) == [
        (f"{new_g}.0001-R", "v02chat", new_g, f"documents/{P}/main/v02chat/0001/0001-R_requirement.md"),
        (f"{new_g}.0002-CH", "v02chat", new_g, f"documents/{P}/main/v02chat/0001/0002-CH_document.md")]
    assert q("SELECT meta FROM documents WHERE id = 1") == [(f'{{"module": "v02chat", "src": "{new_g}.0002-CH"}}',)]
    assert q("SELECT doc_id, note FROM events") == [(f"{new_g}.0001-R", f"see x{P}.v02gui.0001 and {new_g}.0002-CH")]
    assert q("SELECT backup_path FROM document_revisions") == [
        (f"documents/{P}/main/v02chat/0001/revisions/{new_g}.0001-R.r1.md",)]
    assert q("SELECT project_id, module, last_seq FROM id_counter ORDER BY project_id") == [
        (P, "v02chat", 2), ("other", "v02gui", 1)]
    assert q("SELECT module_id, name, title FROM project_modules WHERE project_id = ? AND title = 'v0.2.chat'", P) == [
        (f"{P}:v02chat", "v02chat", "v0.2.chat")]
    body, digest = q("SELECT body, body_hash FROM conversation_turns")[0]
    assert body == f"answer about {new_g}.0001-R" and digest == hashlib.sha256(body.encode()).hexdigest()
    assert q("SELECT branch FROM group_git_state") == [(f"{P}_v02chat_0001",)]
    assert q("SELECT module FROM remote_tool_grant") == [("v02chat",)]
    # Another project's module of the same name is untouched.
    assert q("SELECT doc_id, module, meta FROM documents WHERE project_id = 'other'") == [
        ("other.v02gui.0001.0001-R", "v02gui", '{"module": "v02gui"}')]
    assert q("PRAGMA foreign_key_check") == []
    # Nothing in the project still carries the old key.
    assert q("SELECT COUNT(*) FROM groups WHERE project_id = ? AND module = 'v02gui'", P) == [(0,)]


def test_rename_moves_files(conn, storage):
    _rename(conn, storage)
    new_dir = storage / f"documents/{P}/main/v02chat/0001"
    assert not (storage / REL).exists()
    body = (new_dir / "0001-R_requirement.md").read_text(encoding="utf-8")
    assert f"module: v02chat\n" in body and f"group_id: {P}.v02chat.0001\n" in body
    assert f"see {P}.v02chat.0001.0002-CH" in body
    assert "module: v02gui stays in prose" in body  # prose is not a front matter field
    snapshot = new_dir / "revisions" / f"{P}.v02chat.0001.0001-R.r1.md"
    assert snapshot.is_file() and f"group_id: {P}.v02chat.0001" in snapshot.read_text(encoding="utf-8")
    # An attachment is a user's file: moved, never edited.
    assert (new_dir / "0002-CH" / "note.md").read_bytes() == f"raw {DOC} bytes".encode()
    assert (storage / "documents/other/main/v02gui/0001/0001-R_x.md").read_text(encoding="utf-8") == "module: v02gui\n"


def test_old_identifier_is_free_after_rename(conn, storage):
    _rename(conn, storage)
    # The swap the user wanted: v0.2.test takes the identifier v0.2.chat gave up.
    from modules.flow_gate.services import module_rename
    with _service(conn, storage):
        result = module_rename.rename_module(P, "v02test", "v02gui", actor="t")
    assert result["groups"] == {f"{P}.v02test.0001": f"{P}.v02gui.0001"}
    assert [tuple(r) for r in conn.execute("SELECT group_id, module, parent_id FROM groups WHERE title = 'Child'")] == [
        (f"{P}.v02gui.0001", "v02gui", f"{P}.v02chat.0001")]


def test_taken_identifier_names_the_group_and_changes_nothing(conn, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename(conn, storage, new="v02test")
    assert exc.value.code == "module_identifier_taken" and exc.value.status == 409
    assert exc.value.details["holder"]["title"] == "v0.2.test"
    assert "v0.2.test" in exc.value.message
    assert conn.execute("SELECT module FROM groups WHERE group_id = ?", [G]).fetchone()[0] == "v02gui"
    assert (storage / REL / "0001-R_requirement.md").is_file()


def test_busy_module_is_refused_and_nothing_changes(conn, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename(conn, storage, busy=[{"group_id": G, "reason": "flow_run_active"}])
    assert exc.value.code == "module_rename_busy" and exc.value.details["busy"][0]["group_id"] == G
    assert conn.execute("SELECT COUNT(*) FROM groups WHERE module = 'v02chat'").fetchone()[0] == 0
    assert not (storage / f"documents/{P}/main/v02chat").exists()


def test_failure_after_files_rolls_everything_back(conn, storage):
    from modules.flow_gate.services import module_rename
    with _service(conn, storage), \
         patch("modules.flow_gate.services.module_rename.rewrite_database", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            module_rename.rename_module(P, "v02gui", "v02chat", actor="t")
    assert not (storage / f"documents/{P}/main/v02chat").exists()
    assert (storage / REL / "0001-R_requirement.md").is_file()
    assert conn.execute("SELECT module FROM groups WHERE group_id = ?", [G]).fetchone()[0] == "v02gui"


def test_existing_target_directory_is_refused_untouched(conn, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    stray = storage / f"documents/{P}/main/v02chat"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename(conn, storage)
    assert exc.value.code == "module_rename_path_exists"
    assert (stray / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (storage / REL / "0001-R_requirement.md").is_file()


@pytest.mark.parametrize("value, status, code", [
    ("GUI", 422, "module_identifier_uppercase"),
    ("gui.2", 422, "module_identifier_invalid"),
    ("x" * 41, 422, "module_identifier_too_long"),
    ("none", 422, "module_identifier_reserved"),
])
def test_identifier_format(conn, storage, value, status, code):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename(conn, storage, new=value)
    assert (exc.value.status, exc.value.code) == (status, code)


# ── the route ──────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(conn):
    from modules.flow_gate.api.v1.module_routes import router
    app = FastAPI()
    app.include_router(router)
    with patch("modules.flow_gate.api.v1.module_routes.get_store", return_value=_Store(conn)), \
         patch("modules.flow_gate.api.v1.module_routes.verify_bearer", return_value={"issued_to": "t"}):
        yield TestClient(app)


def _patch(client, name, body):
    return client.patch(f"/api/v1/projects/{P}/modules/{name}", json=body)


def test_route_title_only_does_not_rename(client, conn):
    with patch("modules.flow_gate.services.module_rename.rename_module") as rename:
        r = _patch(client, "v02gui", {"title": "GUI", "name": "v02gui"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["name"], d["title"], d["title_changed"], d["identifier_changed"]) == ("v02gui", "GUI", True, False)
    rename.assert_not_called()
    assert conn.execute("SELECT title FROM project_modules WHERE module_id = ?", [f"{P}:v02gui"]).fetchone()[0] == "GUI"


def test_route_identifier_calls_the_rename(client):
    with patch("modules.flow_gate.services.module_rename.rename_module",
               return_value={"groups": {G: f"{P}.v02chat.0001"}}) as rename:
        r = _patch(client, "v02gui", {"name": "v02chat"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["name"], d["previous_name"], d["identifier_changed"], d["module_id"]) == (
        "v02chat", "v02gui", True, f"{P}:v02chat")
    assert d["renamed_groups"] == {G: f"{P}.v02chat.0001"}
    rename.assert_called_once_with(P, "v02gui", "v02chat", actor="t")


def test_route_reports_refusal_with_details(client):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    refusal = ModuleRenameRefused("module_identifier_taken", "Identifier 'v02test' is used by the group 'v0.2.test'.",
                                  details={"holder": {"title": "v0.2.test"}})
    with patch("modules.flow_gate.services.module_rename.rename_module", side_effect=refusal):
        r = _patch(client, "v02gui", {"title": "v0.2.chat2", "name": "v02test"})
    assert r.status_code == 409
    d = r.json()
    assert d["code"] == "module_identifier_taken" and d["details"]["holder"]["title"] == "v0.2.test"
    assert d["title_changed"] is True  # the title part was saved; the client must not resend it


def test_route_rejects_bad_format_before_renaming(client):
    with patch("modules.flow_gate.services.module_rename.rename_module") as rename:
        r = _patch(client, "v02gui", {"name": "v0.2.gui"})
    assert r.status_code == 422 and r.json()["code"] == "module_identifier_invalid"
    rename.assert_not_called()


def test_route_empty_and_unknown(client):
    assert _patch(client, "v02gui", {}).status_code == 400
    r = _patch(client, "nope", {"title": "x"})
    assert r.status_code == 404 and r.json()["code"] == "module_not_found"


def test_route_module_known_only_through_groups(client, conn):
    r = _patch(client, "legacy", {"title": "Legacy"})
    assert r.status_code == 200, r.text
    assert conn.execute("SELECT title FROM project_modules WHERE name = 'legacy'").fetchone()[0] == "Legacy"


# ── Git: a real repository, worktree move + branch rename, and the revert ───────────────────────

def _git(cwd, *args):
    import subprocess
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_git_worktree_and_branch_move_and_revert(tmp_path):
    import shutil as _shutil
    if _shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.services import module_rename as mr

    base = tmp_path / "src" / "MI" / "main"
    base.mkdir(parents=True)
    _git(base, "init", "-q", "-b", "main")
    (base / "a.txt").write_text("a", encoding="utf-8")
    _git(base, "add", "a.txt")
    _git(base, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    old_wt = tmp_path / "src" / "MI" / f"{P}_v02gui_0001"
    _git(base, "worktree", "add", "-q", "-b", f"{P}_v02gui_0001", str(old_wt))
    new_wt = tmp_path / "src" / "MI" / f"{P}_v02chat_0001"
    plans = [mr._BranchMove(G, f"{P}_v02gui_0001", f"{P}_v02chat_0001", old_wt, new_wt)]

    mr._check_git(base, plans)
    mr._apply_git(base, plans)
    assert not old_wt.exists() and (new_wt / "a.txt").is_file()
    assert _git(new_wt, "rev-parse", "--abbrev-ref", "HEAD") == f"{P}_v02chat_0001"
    assert _git(base, "branch", "--list", f"{P}_v02gui_0001") == ""

    # The same names again are refused: the branch and the worktree are there now.
    with pytest.raises(ModuleRenameRefused) as exc:
        mr._check_git(base, [mr._BranchMove(G, "x", f"{P}_v02chat_0001", old_wt, new_wt)])
    assert exc.value.code == "module_rename_branch_exists"

    mr._revert_git(base, plans)
    assert (old_wt / "a.txt").is_file() and not new_wt.exists()
    assert _git(old_wt, "rev-parse", "--abbrev-ref", "HEAD") == f"{P}_v02gui_0001"


# ── statement count: a rename must not cost one round trip per column or per value (0004-CH) ──────

def test_rewrite_statements_do_not_grow_with_values(conn):
    from modules.flow_gate.db.module_rename import KeyRewriter, defer_foreign_keys, rewrite_database
    for i in range(2, 402):
        conn.execute("INSERT INTO events VALUES (?, ?, ?)", [i, DOC, f"note {P}.v02gui.0001.{i:04d}-R"])
        body = f"turn {i} about {DOC}"
        conn.execute("INSERT INTO conversation_turns VALUES (?, ?, ?, ?)",
                     [i, CH, body, hashlib.sha256(body.encode()).hexdigest()])
    statements: list[str] = []

    class _Counting(_Store):
        def _fetch_all(self, sql, params=None):
            statements.append(sql)
            return super()._fetch_all(sql, params)

        def _execute(self, sql, params=None):
            statements.append(sql)
            return super()._execute(sql, params)

        def _execute_affected(self, sql, params=None):
            statements.append(sql)
            return super()._execute_affected(sql, params)

    store = _Counting(conn)
    rw = KeyRewriter(P, "v02gui", "v02chat", P, {f"{P}_v02gui_0001": f"{P}_v02chat_0001"})
    with store.transaction():
        defer_foreign_keys(store)
        statements.clear()
        result = rewrite_database(store, rw)

    q = lambda sql, *a: [tuple(r) for r in conn.execute(sql, a).fetchall()]  # noqa: E731
    new_g = f"{P}.v02chat.0001"
    notes = dict(q("SELECT event_id, note FROM events"))
    assert notes[1] == f"see x{P}.v02gui.0001 and {new_g}.0002-CH"
    assert all(notes[i] == f"note {new_g}.{i:04d}-R" for i in range(2, 402))
    assert q("SELECT DISTINCT doc_id FROM events") == [(f"{new_g}.0001-R",)]
    for doc_id, body, digest in q("SELECT doc_id, body, body_hash FROM conversation_turns"):
        assert doc_id == f"{new_g}.0002-CH" and f"{new_g}.0001-R" in body
        assert digest == hashlib.sha256(body.encode()).hexdigest()
    assert q("PRAGMA foreign_key_check") == []
    assert result["values"] >= 802
    # One schema read plus one search per table (10 tables), whatever the number of columns.
    assert sum(1 for s in statements if s.lstrip().upper().startswith("SELECT")) == 11
    # A changed column is written in chunks, not per value: 401 notes -> 3 statements, doc_id -> 1.
    assert sum(1 for s in statements if s.startswith('UPDATE "events"')) == 4
    assert sum(1 for s in statements if s.startswith('UPDATE "conversation_turns"')) == 4


def test_git_check_lists_refs_once(tmp_path):
    import shutil as _shutil
    if _shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.services import module_rename as mr

    base = tmp_path / "src" / "MI" / "main"
    base.mkdir(parents=True)
    _git(base, "init", "-q", "-b", "main")
    (base / "a.txt").write_text("a", encoding="utf-8")
    _git(base, "add", "a.txt")
    _git(base, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    _git(base, "update-ref", f"refs/remotes/origin/{P}_v02chat_0002", "HEAD")
    _git(base, "branch", f"{P}_v02chat_0003")
    plans = [mr._BranchMove(f"{P}.v02gui.{n:04d}", f"{P}_v02gui_{n:04d}", f"{P}_v02chat_{n:04d}", None, None)
             for n in (1, 2, 3)]
    calls: list[str] = []
    real = mr._git
    with patch.object(mr, "_git", side_effect=lambda b, args: calls.append(args[0]) or real(b, args)):
        with pytest.raises(ModuleRenameRefused) as exc:
            mr._check_git(base, plans)
    assert calls == ["for-each-ref"]
    assert exc.value.details["branches"] == [
        {"branch": f"{P}_v02chat_0002", "where": "origin"}, {"branch": f"{P}_v02chat_0003", "where": "local"}]


# ── v02test.0040 T0005: logs, lock timeout -> 409, too large refused before any lock ─────────────────

_MR_LOGGER = "modules.flow_gate.services.module_rename"
_FG_LOGGER = "modules.flow_gate.graph_workflow.services.flow_guard"


def _records(caplog, name, level):
    return [r for r in caplog.records if r.name == name and r.levelno == level]


def _unchanged(conn, storage):
    assert conn.execute("SELECT module FROM groups WHERE group_id = ?", [G]).fetchone()[0] == "v02gui"
    assert conn.execute("SELECT COUNT(*) FROM groups WHERE module = 'v02chat'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE module = 'v02chat'").fetchone()[0] == 0
    assert (storage / REL / "0001-R_requirement.md").is_file()
    assert not (storage / f"documents/{P}/main/v02chat").exists()


def test_refusal_is_logged_as_a_warning(conn, storage, caplog):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    caplog.set_level(logging.INFO)
    with pytest.raises(ModuleRenameRefused):
        _rename(conn, storage, busy=[{"group_id": G, "reason": "flow_run_active"}])
    [record] = _records(caplog, _MR_LOGGER, logging.WARNING)
    text = record.getMessage()
    assert text.startswith(f"module rename refused {P}: v02gui -> v02chat by t: module_rename_busy "), text
    assert G in text and "flow_run_active" in text
    assert not [r for r in _records(caplog, _MR_LOGGER, logging.INFO) if r.getMessage().startswith("module rename ")]


def test_taken_refusal_is_logged_with_its_holder(conn, storage, caplog):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    caplog.set_level(logging.INFO)
    with pytest.raises(ModuleRenameRefused):
        _rename(conn, storage, new="v02test")
    [record] = _records(caplog, _MR_LOGGER, logging.WARNING)
    assert "module_identifier_taken" in record.getMessage() and "v0.2.test" in record.getMessage()


def test_refusal_details_are_summarised():
    from modules.flow_gate.services.module_rename import _details_summary
    many = _details_summary({"busy": [{"group_id": f"g{i}", "reason": "ai_lease_active"} for i in range(40)]})
    assert '"busy_total": 40' in many and '"g4"' in many and '"g5"' not in many
    assert len(_details_summary({"paths": ["x" * 2000]})) < 530
    assert _details_summary({}) == "{}" and _details_summary(None) == "{}"


def test_unexpected_failure_is_logged_with_its_traceback_and_reraised(conn, storage, caplog):
    from modules.flow_gate.services import module_rename
    caplog.set_level(logging.INFO)
    with _service(conn, storage), \
         patch("modules.flow_gate.services.module_rename.rewrite_database", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            module_rename.rename_module(P, "v02gui", "v02chat", actor="t")
    [record] = [r for r in _records(caplog, _MR_LOGGER, logging.ERROR) if "module rename failed" in r.getMessage()]
    assert record.exc_info and record.exc_info[0] is RuntimeError
    assert f"{P}: v02gui -> v02chat by t after " in record.getMessage() and "(rolled back)" in record.getMessage()
    _unchanged(conn, storage)  # the rollback is unchanged


def test_success_log_carries_duration_and_document_count(conn, storage, caplog):
    caplog.set_level(logging.INFO)
    _rename(conn, storage)
    [record] = [r for r in _records(caplog, _MR_LOGGER, logging.INFO) if r.getMessage().startswith("module rename ")]
    text = record.getMessage()
    assert text.startswith(f"module rename {P}: v02gui -> v02chat by t (1 groups, 2 documents, "), text
    assert re.search(r", \d+ ms\)$", text), text


def test_rename_and_lock_timeout_lines_reach_the_app_file_log(conn, storage, tmp_path):
    """A plain module logger propagates to a handler-less root and never reached logs/default.log; both the rename's
    lines and the lock timeout are handed to LogAssist's file handlers (looked up per record)."""
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.graph_workflow.services import flow_guard
    app = logging.getLogger("LogAssist.log")
    handler = logging.FileHandler(tmp_path / "default.log", encoding="utf-8")
    app.addHandler(handler)
    try:
        with pytest.raises(ModuleRenameRefused):
            _rename(conn, storage, busy=[{"group_id": G, "reason": "flow_run_active"}])
        _rename(conn, storage)
        flow_guard.lock_timeout("t0005-flow", 0.01, "flow", "t0005")
    finally:
        app.removeHandler(handler)
        handler.close()
    text = (tmp_path / "default.log").read_text(encoding="utf-8")
    assert text.count(f"module rename refused {P}: v02gui -> v02chat by t: module_rename_busy") == 1, text
    assert text.count(f"module rename {P}: v02gui -> v02chat by t (1 groups, 2 documents,") == 1, text
    assert "flow lock timeout: flow lock t0005-flow not acquired in 0.01s (t0005, thread " in text, text


def test_flow_guard_timeouts_say_which_key_and_how_long(monkeypatch, caplog):
    from modules.flow_gate.graph_workflow.services import flow_guard
    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 0.05)
    monkeypatch.setattr(flow_guard, "in_transaction", lambda: False)
    caplog.set_level(logging.WARNING)
    cases = (
        ("flow", "t0005-guard-flow", flow_guard._flow_lock("t0005-guard-flow"), flow_guard.with_flow_guard),
        ("document", "t0005-guard-doc", flow_guard._named_lock(flow_guard._doc_locks, "t0005-guard-doc"),
         flow_guard.with_document_lock),
    )
    for kind, key, held, guarded in cases:
        assert held.acquire(timeout=1)
        try:
            with pytest.raises(flow_guard.FlowLockTimeout) as exc:
                guarded(key, lambda: pytest.fail("ran without the lock"))
        finally:
            held.release()
        assert (str(exc.value), exc.value.key, exc.value.kind, exc.value.wait_sec) == (key, key, kind, 0.05)
    messages = [r.getMessage() for r in _records(caplog, _FG_LOGGER, logging.WARNING)]
    assert any("flow lock timeout: flow lock t0005-guard-flow not acquired in 0.05s (with_flow_guard" in m
               for m in messages), messages
    assert any("flow lock timeout: document lock t0005-guard-doc not acquired in 0.05s (with_document_lock" in m
               for m in messages), messages


def _real_gate(op, group_ids, docs, call, **kw):
    """The real write_gate.module_rename (in-process locks, then one transaction)."""
    from flowgate.card_document_edit import write_gate
    assert op == "module_rename"
    return write_gate.module_rename(group_ids, docs, call, **kw)


def test_flow_lock_timeout_is_409_busy_and_changes_nothing(conn, storage, monkeypatch, caplog):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.graph_workflow.services import flow_guard
    second = f"{P}.v02gui.0002"  # sorts after G: G's lock is taken first, then this one times out
    conn.execute("INSERT INTO groups VALUES (?, ?, 'v02gui', NULL, 'Flow 2')", [second, P])
    monkeypatch.setenv("FLOWGATE_MODULE_RENAME_LOCK_WAIT_SEC", "0.05")
    caplog.set_level(logging.INFO)
    held = flow_guard._flow_lock(second)
    assert held.acquire(timeout=1)
    try:
        with patch.object(flow_guard, "document_guard_key", side_effect=lambda d, i: ("flow", d["group_id"])), \
             patch.object(flow_guard.layout_store, "ensure_guard_row") as ensure:
            with pytest.raises(ModuleRenameRefused) as exc:
                _rename(conn, storage, gate=_real_gate)
    finally:
        held.release()
    assert (exc.value.status, exc.value.code) == (409, "module_rename_busy")
    assert exc.value.details == {"busy": [{"group_id": second, "reason": "flow_lock_timeout", "wait_sec": 0.05}]}
    assert isinstance(exc.value.__cause__, flow_guard.FlowLockTimeout)
    ensure.assert_called_once_with(G)
    # The lock taken before the timeout was released.
    released = flow_guard._flow_lock(G)
    assert released.acquire(blocking=False)
    released.release()
    _unchanged(conn, storage)
    assert any(f"flow lock timeout: flow lock {second} not acquired in 0.05s (module_rename" in r.getMessage()
               for r in _records(caplog, _FG_LOGGER, logging.WARNING))
    [refused] = _records(caplog, _MR_LOGGER, logging.WARNING)
    assert "module_rename_busy" in refused.getMessage() and "flow_lock_timeout" in refused.getMessage()
    assert not _records(caplog, _MR_LOGGER, logging.ERROR)  # a refusal, not a failure


def test_document_lock_timeout_names_the_document(conn, storage, monkeypatch):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.graph_workflow.services import flow_guard
    monkeypatch.setenv("FLOWGATE_MODULE_RENAME_LOCK_WAIT_SEC", "0.05")
    held = flow_guard._named_lock(flow_guard._doc_locks, CH)
    assert held.acquire(timeout=1)
    try:
        with patch.object(flow_guard, "document_guard_key",
                          side_effect=lambda d, i: ("document", i) if i == CH else ("flow", d["group_id"])), \
             patch.object(flow_guard.layout_store, "ensure_guard_row"):
            with pytest.raises(ModuleRenameRefused) as exc:
                _rename(conn, storage, gate=_real_gate)
    finally:
        held.release()
    assert (exc.value.status, exc.value.code) == (409, "module_rename_busy")
    assert exc.value.details == {
        "busy": [{"group_id": G, "doc_id": CH, "reason": "document_lock_timeout", "wait_sec": 0.05}]}
    released = flow_guard._flow_lock(G)
    assert released.acquire(blocking=False)
    released.release()
    _unchanged(conn, storage)


class _Clock:
    """write_gate._clock for the tests below: seconds pass only when a fake lock is waited for."""

    def __init__(self):
        self.now, self.waits, self.released = 100.0, [], []

    def __call__(self):
        return self.now


class _SlowLock:
    """A lock another writer frees ``frees_after`` seconds into the wait (on the fake clock)."""

    def __init__(self, clock, key, frees_after):
        self.clock, self.key, self.frees_after, self.held = clock, key, frees_after, False

    def acquire(self, blocking=True, timeout=-1):
        assert 0 <= timeout, (self.key, timeout)  # never unbounded (-1), never negative
        self.clock.waits.append((self.key, timeout))
        if self.frees_after <= timeout:
            self.clock.now += self.frees_after
            self.held = True
            return True
        self.clock.now += timeout
        return False

    def release(self):
        assert self.held, self.key
        self.held = False
        self.clock.released.append(self.key)


_G = [G] + [f"{P}.v02gui.{n:04d}" for n in range(2, 10)]


@pytest.mark.parametrize("flows, frees, loose, waits, timed_out", [
    # Nine Flow locks, each freed 4.9 s into its wait. Per-lock 5 s waits took all nine and held the first ones
    # 44.1 s (the review's 8 x 4.9 s + a timeout = 44.2 s); one 5 s budget gives up at the second lock.
    (_G, [4.9] * 9, False, [(_G[0], 5.0), (_G[1], 0.1)], _G[1]),
    # The deadline spent on the first lock: a free lock is still taken (wait 0), a held one is not waited for.
    (_G[:3], [5.0, 0.0, 4.9], False, [(_G[0], 5.0), (_G[1], 0.0), (_G[2], 0.0)], _G[2]),
    # A document lock (a document without a Flow) shares the same deadline as the Flow locks before it.
    (_G[:1], [4.9], True, [(_G[0], 5.0), (CH, 0.1)], CH),
], ids=["nine_flows_4_9s_each", "free_lock_after_deadline", "document_lock_same_deadline"])
def test_lock_waits_add_up_to_one_budget_not_one_per_lock(conn, storage, monkeypatch, flows, frees, loose, waits,
                                                         timed_out):
    """TR0006 review: lock_wait_sec bounds the waiting for all the rename's locks together, so the locks taken
    first are released (refusal 409) no later than lock_wait_sec after the first acquire began."""
    from flowgate.card_document_edit import write_gate
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.graph_workflow.services import flow_guard
    for gid in flows[1:]:
        conn.execute("INSERT INTO groups VALUES (?, ?, 'v02gui', NULL, 'Flow n')", [gid, P])
    monkeypatch.setenv("FLOWGATE_MODULE_RENAME_LOCK_WAIT_SEC", "5")
    clock = _Clock()
    monkeypatch.setattr(write_gate, "_clock", clock)
    locks = {key: _SlowLock(clock, key, after) for key, after in zip(flows, frees)}
    locks[CH] = _SlowLock(clock, CH, 4.9)
    guard_key = (lambda d, i: ("document", i) if i == CH else ("flow", d["group_id"])) if loose else \
        (lambda d, i: ("flow", d["group_id"]))
    with patch.object(flow_guard, "_flow_lock", side_effect=lambda key: locks[key]), \
         patch.object(flow_guard, "_named_lock", side_effect=lambda table, key: locks[key]), \
         patch.object(flow_guard, "document_guard_key", side_effect=guard_key), \
         patch.object(flow_guard.layout_store, "ensure_guard_row"):
        with pytest.raises(ModuleRenameRefused) as exc:
            _rename(conn, storage, gate=_real_gate)
    assert [k for k, _ in clock.waits] == [k for k, _ in waits]
    assert [w for _, w in clock.waits] == pytest.approx([w for _, w in waits])
    assert clock.now - 100.0 == pytest.approx(5.0)  # waited 5 s in all, then gave up
    taken = [k for k, _ in waits][:-1]
    assert clock.released == list(reversed(taken))  # every lock taken released, in reverse order
    assert not any(lock.held for lock in locks.values())
    assert (exc.value.status, exc.value.code) == (409, "module_rename_busy")
    [entry] = exc.value.details["busy"]
    assert (entry.get("doc_id") or entry["group_id"], entry["wait_sec"]) == (timed_out, 5.0)
    assert entry["reason"] == ("document_lock_timeout" if loose else "flow_lock_timeout")
    _unchanged(conn, storage)


def test_rename_passes_its_own_short_lock_budget(conn, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.graph_workflow.services.errors import FlowLockTimeout
    seen = {}

    def gate(op, group_ids, docs, call, **kw):
        seen.update(kw)
        raise FlowLockTimeout(G)  # the plain shape (no kind / wait) other raise sites still use

    with pytest.raises(ModuleRenameRefused) as exc:
        _rename(conn, storage, gate=gate)
    assert seen == {"lock_wait_sec": 5.0}
    assert exc.value.details == {"busy": [{"group_id": G, "reason": "flow_lock_timeout"}]}


@pytest.mark.parametrize("raw, wait", [(None, 5.0), ("2.5", 2.5), ("0", 5.0), ("-1", 5.0), ("abc", 5.0)])
def test_lock_wait_setting(monkeypatch, raw, wait):
    from modules.flow_gate.services import module_rename
    if raw is None:
        monkeypatch.delenv("FLOWGATE_MODULE_RENAME_LOCK_WAIT_SEC", raising=False)
    else:
        monkeypatch.setenv("FLOWGATE_MODULE_RENAME_LOCK_WAIT_SEC", raw)
    assert module_rename.lock_wait_sec() == wait


@pytest.mark.parametrize("raw, limit", [(None, 2000), ("500", 500), (" 3000 ", 3000), ("0", 0), ("abc", 2000)])
def test_document_limit_setting(monkeypatch, raw, limit):
    from modules.flow_gate.services import module_rename
    if raw is None:
        monkeypatch.delenv("FLOWGATE_MODULE_RENAME_MAX_DOCUMENTS", raising=False)
    else:
        monkeypatch.setenv("FLOWGATE_MODULE_RENAME_MAX_DOCUMENTS", raw)
    assert module_rename.max_documents() == limit


def test_too_large_module_is_refused_before_any_lock(conn, storage, monkeypatch, caplog):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    monkeypatch.setenv("FLOWGATE_MODULE_RENAME_MAX_DOCUMENTS", "1")
    gate = MagicMock(side_effect=AssertionError("write_gate must not be called"))
    caplog.set_level(logging.INFO)
    with pytest.raises(ModuleRenameRefused) as exc:
        # Busy as well: too large is answered first (waiting for the work to end would not help).
        _rename(conn, storage, gate=gate, busy=[{"group_id": G, "reason": "flow_run_active"}])
    assert (exc.value.status, exc.value.code) == (409, "module_rename_too_large")
    assert exc.value.details == {"documents": 2, "groups": 1, "limit": 1}
    gate.assert_not_called()  # no Flow lock, no path namespace
    _unchanged(conn, storage)
    [record] = _records(caplog, _MR_LOGGER, logging.WARNING)
    assert "module_rename_too_large" in record.getMessage() and '"limit": 1' in record.getMessage()


@pytest.mark.parametrize("limit", ["2", "0"])
def test_module_at_the_limit_or_without_limit_is_renamed(conn, storage, monkeypatch, limit):
    monkeypatch.setenv("FLOWGATE_MODULE_RENAME_MAX_DOCUMENTS", limit)
    assert _rename(conn, storage)["groups"] == {G: f"{P}.v02chat.0001"}


def test_route_lock_timeout_is_409_not_500(client, conn, storage):
    from modules.flow_gate.graph_workflow.services.errors import FlowLockTimeout

    def gate(op, group_ids, docs, call, **kw):
        raise FlowLockTimeout(G, kind="flow", wait_sec=5.0)

    with _service(conn, storage, gate=gate):
        r = _patch(client, "v02gui", {"name": "v02chat"})
    assert r.status_code == 409, r.text
    d = r.json()
    assert d["code"] == "module_rename_busy"
    assert d["details"]["busy"] == [{"group_id": G, "reason": "flow_lock_timeout", "wait_sec": 5.0}]
    assert d["title_changed"] is False


def test_route_too_large_keeps_the_saved_title(client, conn):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    refusal = ModuleRenameRefused("module_rename_too_large", "too many documents",
                                  details={"documents": 6049, "groups": 691, "limit": 2000})
    with patch("modules.flow_gate.services.module_rename.rename_module", side_effect=refusal):
        r = _patch(client, "v02gui", {"title": "v0.1 big", "name": "v01x"})
    assert r.status_code == 409, r.text
    d = r.json()
    assert d["code"] == "module_rename_too_large" and d["details"] == {"documents": 6049, "groups": 691, "limit": 2000}
    assert (d["title_changed"], d["title"]) == (True, "v0.1 big")
    assert conn.execute("SELECT title FROM project_modules WHERE module_id = ?", [f"{P}:v02gui"]).fetchone()[0] == "v0.1 big"


# ── v02test.0040 T0007: an archived group holds its identifier, says so, and can give it up ──────────────

ARCHIVED_AT = "2026-11-17T11:35:00+09:00"


@pytest.fixture()
def archived(conn):
    """v02gui (v0.2.chat) archived as on the operating server (11/17 11:35): migration 190's columns on
    project_modules and 170's on groups (its Flow archived too), filled the way module_routes /
    flow_archive write them."""
    for table in ("project_modules", "groups"):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN archived_at TEXT")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN archived_by TEXT")
    conn.execute("UPDATE project_modules SET archived_at = ?, archived_by = 'admin' WHERE project_id = ? AND name = 'v02gui'",
                 [ARCHIVED_AT, P])
    conn.execute("UPDATE groups SET archived_at = ?, archived_by = 'admin' WHERE group_id = ?", [ARCHIVED_AT, G])
    return conn


def _rename_from(conn, storage, old, new):
    from modules.flow_gate.services import module_rename
    with _service(conn, storage):
        return module_rename.rename_module(P, old, new, actor="t")


def test_identifier_held_by_an_archived_group_is_refused_and_says_archived(archived, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename_from(archived, storage, "v02test", "v02gui")  # the user's v0.2.test -> v02gui
    assert (exc.value.status, exc.value.code) == (409, "module_identifier_taken")  # the client's mapping is kept
    holder = exc.value.details["holder"]
    assert (holder["kind"], holder["title"], holder["group_id"]) == ("group", "v0.2.chat", G)
    assert holder["archived"] is True and holder["archived_at"] == ARCHIVED_AT
    assert exc.value.message == ("Identifier 'v02gui' is used by the archived group 'v0.2.chat'. "
                                 "Change that group's identifier first to free it.")
    # Nothing moved: the archived group keeps its identifier (never overwritten nor renamed for the user).
    assert archived.execute("SELECT module FROM groups WHERE title = 'Child'").fetchone()[0] == "v02test"
    assert archived.execute("SELECT module FROM groups WHERE group_id = ?", [G]).fetchone()[0] == "v02gui"


def test_identifier_held_by_an_active_group_is_not_called_archived(archived, storage):
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    with pytest.raises(ModuleRenameRefused) as exc:
        _rename_from(archived, storage, "v02gui", "v02test")
    holder = exc.value.details["holder"]
    assert holder["archived"] is False and holder["archived_at"] is None
    assert exc.value.message == "Identifier 'v02test' is used by the group 'v0.2.test'."


def test_empty_archived_group_holder_says_archived(archived):
    from modules.flow_gate.db.module_rename import module_in_use
    archived.execute("INSERT INTO project_modules VALUES (?, ?, 'aigroup', 'AI group', ?, ?, ?, 'admin')",
                     [f"{P}:aigroup", P, NOW, NOW, ARCHIVED_AT])
    assert module_in_use(_Store(archived), P, "aigroup") == {
        "kind": "module", "module": "aigroup", "title": "AI group", "archived": True, "archived_at": ARCHIVED_AT}
    assert module_in_use(_Store(archived), P, "server")["archived"] is False
    assert module_in_use(_Store(archived), P, "free") is None


def test_holder_without_the_archive_column_reads_as_not_archived(conn):
    """A database before migration 190 (no project_modules.archived_at) still answers, as not archived."""
    from modules.flow_gate.db.module_rename import module_in_use
    holder = module_in_use(_Store(conn), P, "v02test")
    assert (holder["kind"], holder["title"], holder["archived"], holder["archived_at"]) == (
        "group", "v0.2.test", False, None)


def test_archived_group_is_renamed_and_stays_archived(archived, storage):
    result = _rename_from(archived, storage, "v02gui", "v02chat")
    new_g = f"{P}.v02chat.0001"
    assert result["groups"] == {G: new_g}
    q = lambda sql, *a: [tuple(r) for r in archived.execute(sql, a).fetchall()]  # noqa: E731
    # The archive state moves with the row (project_modules is updated in place, not re-inserted).
    assert q("SELECT module_id, name, title, archived_at, archived_by FROM project_modules"
             " WHERE project_id = ? AND name IN ('v02gui', 'v02chat')", P) == [
        (f"{P}:v02chat", "v02chat", "v0.2.chat", ARCHIVED_AT, "admin")]
    assert q("SELECT group_id, module, archived_at, archived_by FROM groups WHERE title = 'Flow 1'") == [
        (new_g, "v02chat", ARCHIVED_AT, "admin")]
    assert (storage / f"documents/{P}/main/v02chat/0001/0001-R_requirement.md").is_file()
    # Another project's v02gui and the other groups are untouched (and not archived).
    assert q("SELECT archived_at FROM project_modules WHERE project_id = 'other'") == [(None,)]
    assert q("SELECT name FROM project_modules WHERE project_id = ? AND archived_at IS NOT NULL", P) == [("v02chat",)]
    assert q("PRAGMA foreign_key_check") == []


def test_freeing_the_archived_identifier_lets_another_group_take_it(archived, storage):
    """The 11/17 case end to end: v02gui (archived v0.2.chat) -> v02chat, then v02test -> v02gui."""
    _rename_from(archived, storage, "v02gui", "v02chat")
    result = _rename_from(archived, storage, "v02test", "v02gui")
    assert result["groups"] == {f"{P}.v02test.0001": f"{P}.v02gui.0001"}
    q = lambda sql, *a: [tuple(r) for r in archived.execute(sql, a).fetchall()]  # noqa: E731
    assert q("SELECT name, title, archived_at FROM project_modules WHERE project_id = ? AND name IN"
             " ('v02gui', 'v02chat', 'v02test') ORDER BY name", P) == [
        ("v02chat", "v0.2.chat", ARCHIVED_AT), ("v02gui", "v0.2.test", None)]


def test_route_archived_holder_and_rename_of_the_archived_group(client, archived, storage):
    """Through PATCH: the refusal carries details.holder.archived; the archived group's identifier change is
    accepted (no archive check on the route) and frees the identifier for the next PATCH."""
    with _service(archived, storage):
        refused = _patch(client, "v02test", {"name": "v02gui"})
        assert refused.status_code == 409, refused.text
        d = refused.json()
        assert d["code"] == "module_identifier_taken"
        assert d["details"]["holder"]["archived"] is True and d["details"]["holder"]["archived_at"] == ARCHIVED_AT
        assert "archived group 'v0.2.chat'" in d["error_message"]
        freed = _patch(client, "v02gui", {"name": "v02chat"})
        assert freed.status_code == 200, freed.text
        assert (freed.json()["name"], freed.json()["renamed_groups"]) == ("v02chat", {G: f"{P}.v02chat.0001"})
        taken = _patch(client, "v02test", {"name": "v02gui"})
        assert taken.status_code == 200, taken.text
    assert archived.execute("SELECT archived_at FROM project_modules WHERE name = 'v02chat' AND project_id = ?",
                            [P]).fetchone()[0] == ARCHIVED_AT


def test_archive_state_alone_does_not_make_a_group_busy(archived, monkeypatch):
    """busy_reasons has no archive rule: an archived Flow's completed run (idle) is not work, a paused run still is
    (module archive is display only and does not stop runs)."""
    from modules.flow_gate.db import (
        ai_invoke_paused_chains, git_integration, group_ai_leases, test_runs, tr_self_check_runs,
    )
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import module_rename
    from modules.flow_gate.services.ai_invoke import chain
    archived.execute("CREATE TABLE operation_job (group_id TEXT, status TEXT)")
    archived.execute("CREATE TABLE chat_command_requests (group_id TEXT, status TEXT)")
    for target, name in ((group_ai_leases, "get_active"), (chain, "has_active_run"), (ai_invoke_paused_chains, "exists"),
                         (git_integration, "get_open_session_by_group"), (test_runs, "get_active_by_group"),
                         (tr_self_check_runs, "group_open_runs")):
        monkeypatch.setattr(target, name, lambda *a, **k: None)
    monkeypatch.setattr(module_rename, "get_store", lambda: _Store(archived))
    runs = {"status": "idle", "idle_reason": "completed"}
    monkeypatch.setattr(run_store, "active_run", lambda gid: dict(runs))
    assert module_rename.busy_reasons(P, [G]) == []
    runs["status"] = "paused"
    assert module_rename.busy_reasons(P, [G]) == [{"group_id": G, "reason": "flow_run_active", "status": "paused"}]
