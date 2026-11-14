"""flowgate.v02gui.0012 0001-B item B — changing a Group identifier renames the module key everywhere.

Contract under test:
  - PATCH /projects/{pid}/modules/{name} changes title and/or identifier independently; "neither" is a no-op.
  - An identifier change renames the key itself (services.module_rename): group / document ids, module
    columns, id_counter, project_modules, storage paths, branch names, ids inside text and JSON, the files
    (directories, revision snapshot names, front matter, bodies). The old identifier is free afterwards.
  - An identifier another group uses is refused (409 module_identifier_taken) and the refusal names it.
  - Running work in the module refuses the rename (409 module_rename_busy); nothing changes.
  - Format / reserved identifiers are refused with a stable code.

Lightweight: a small SQLite schema, a temp storage root, no app startup, no Git, no migrations run.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import threading
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
def _service(conn, storage, busy=()):
    """rename_module with the store, storage root, running-work probe and Git replaced; the gate runs the
    body in one transaction like write_gate.module_rename."""
    store = _Store(conn)

    def gate(op, group_ids, docs, call):
        assert op == "module_rename"
        with store.transaction():
            return call()

    with patch("modules.flow_gate.services.module_rename.get_store", return_value=store), \
         patch("modules.flow_gate.services.module_rename.busy_reasons", return_value=list(busy)), \
         patch("modules.flow_gate.services.module_rename._git_plan",
               side_effect=lambda pid, rw, groups: (None, [
                   __import__("modules.flow_gate.services.module_rename", fromlist=["_BranchMove"])._BranchMove(
                       G, f"{P}_v02gui_0001", f"{P}_{rw.new}_0001", None, None)])), \
         patch("modules.flow_gate.storage.paths.get_storage_root", return_value=storage), \
         patch("modules.flow_gate.graph_workflow.services.card_document_ports.write_gate", side_effect=gate):
        yield store


def _rename(conn, storage, new="v02chat", busy=()):
    from modules.flow_gate.services import module_rename
    with _service(conn, storage, busy):
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
