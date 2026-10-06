"""Shared fixtures for the test_fg_conversation_* suites (flowgate.v02.0032 T#1).

Not a test module (no ``test_`` prefix): a real temp SQLite store with every migration
applied, seed rows, a live chat run record, a throwaway Git worktree and a controllable
fake process executor. Kept flat in tests/ like ``_korean_allowlist.py`` (D0006 §2.4).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

SERVER_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = SERVER_DIR / "sql" / "migrations" / "sqlite"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

PROJECT = "fgp"
GROUP = "fgp.v02.0001"
MODULE = "v02"
USER = "usr_fg"
OTHER_USER = "usr_other"
ADMIN = "usr_fg_admin"
CHAT_DOC = f"{GROUP}.0001-CH"
RUN_ID = "aiv_fg_run_1"


def apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


_TEMPLATE: dict[str, str] = {}


def _template_db() -> str:
    """Every migration applied once per process; each test gets its own copy."""
    path = _TEMPLATE.get("path")
    if path is None or not os.path.exists(path):
        path = os.path.join(tempfile.mkdtemp(prefix="fg-conv-db-"), "template.db")
        apply_migrations(path)
        _TEMPLATE["path"] = path
    return path


class _Txn:
    """sqloader-shaped transaction handle: ``execute`` returns the cursor (rowcount)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur is not None else None
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur is not None else []


class FgSqliteDB:
    """One connection per call (safe for the executor threads), CAS-capable transactions."""

    db_type = 1  # dialect.SQLITE

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute(self, sql, params=None):
        conn = self._c()
        try:
            conn.execute(sql, params or [])
            conn.commit()
        finally:
            conn.close()

    def fetch_one(self, sql, params=None):
        conn = self._c()
        try:
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def fetch_all(self, sql, params=None):
        conn = self._c()
        try:
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
        finally:
            conn.close()

    from contextlib import contextmanager as _cm

    @_cm
    def begin_transaction(self):
        conn = self._c()
        try:
            yield _Txn(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


@pytest.fixture()
def fg_store(tmp_path):
    """A FlowGateStore on a fresh SQLite file with every migration applied (137/138 included)."""
    from modules.flow_gate.db import connection as conn_mod

    db_path = str(tmp_path / "fg.db")
    shutil.copyfile(_template_db(), db_path)
    original = conn_mod.STORE
    store = conn_mod.FlowGateStore.__new__(conn_mod.FlowGateStore)
    store._db, store._sq = FgSqliteDB(db_path), None
    conn_mod.STORE = store
    try:
        yield store
    finally:
        conn_mod.STORE = original


def seed(store, *, project: str = PROJECT, group: str = GROUP, module: str = MODULE,
         chat_doc: Optional[str] = CHAT_DOC) -> None:
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db.connection import now_iso

    now = now_iso()
    for user_id, admin in ((USER, 0), (OTHER_USER, 0), (ADMIN, 1)):
        store._execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, password, is_active, is_admin, "
            "first_login_required, created_at, updated_at) VALUES (?, ?, ?, 'x', 1, ?, 0, ?, ?)",
            [user_id, user_id, f"{user_id}@example.test", admin, now, now],
        )
    if not db_projects.get_by_id(project):
        db_projects.create({"project_id": project, "project_name": project})
    if not db_groups.get_by_id(group):
        db_groups.create({"group_id": group, "project_id": project, "module": module,
                          "title": "conversation group", "status": "OPEN"})
    if chat_doc and not db_documents.get_by_id(chat_doc):
        db_documents.create({"doc_id": chat_doc, "project_id": project, "module": module,
                             "group_id": group, "type_code": "CH", "seq": 1, "title": "chat",
                             "status": "draft", "owner_id": USER})


def add_turn(doc_id: str, speaker: str, run_id: Optional[str] = None, body: str = "hello") -> int:
    from modules.flow_gate.db import conversation_turns
    from modules.flow_gate.db.connection import now_iso

    marker = f"{doc_id}:{speaker}:{run_id}:{time.monotonic_ns()}"
    digest = hashlib.sha256(marker.encode()).hexdigest()
    row = conversation_turns.insert_turn_with_next_seq(
        doc_id=doc_id, speaker=speaker, participant_key=f"{speaker}:{run_id or 'human'}",
        display_name=None, locale=None, body=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
        based_on_seq=0, source_run_id=run_id, idempotency_key=marker, idempotency_hash=digest,
        created_at=now_iso(),
    )
    return int(row["seq"])


def init_worktree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "fg@example.test")
    git("config", "user.name", "fg")
    git("config", "core.autocrlf", "false")
    (root / "keep.txt").write_text("one\ntwo\n", encoding="utf-8")
    (root / "gone.txt").write_text("bye\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return root.resolve()


def bind_worktree(monkeypatch, root: Optional[Path], reason: str = "worktree") -> None:
    """Point legacy_adapter's worktree lookup at ``root`` (None = no managed worktree)."""
    from flowgate.conversation import legacy_adapter

    if root is None:
        monkeypatch.setattr(legacy_adapter, "effective_src_root_ex",
                            lambda project_id, group_id: (None, "integration_off"))
    else:
        monkeypatch.setattr(legacy_adapter, "effective_src_root_ex",
                            lambda project_id, group_id: (Path(root), reason))


def make_run(*, run_id: str = RUN_ID, doc_id: str = CHAT_DOC, issued_to: str = USER,
             source_root: Optional[Path] = None, elapsed_sec: float = 0.0, register: bool = True,
             capability: bool = True, chat_start_seq: Optional[int] = 0) -> dict:
    """A live chat run record shaped like admission builds it (and, by default, registered
    in the AI invoke engine's process-local run table)."""
    from modules.flow_gate.services import ai_invoke_service as svc

    run = {
        "run_id": run_id, "status": "running", "mode": "single", "project_id": PROJECT,
        "group_id": GROUP, "doc_ref": doc_id, "action_scope": "chat", "issued_to": issued_to,
        "token_id": f"tok_{run_id}", "current_token_id": f"tok_{run_id}",
        "cancel_event": threading.Event(), "timeout_sec": 1800,
        "started_mono": time.monotonic() - float(elapsed_sec), "started_at": "2026-10-06T00:00:00+09:00",
        "finished_at": None, "source_root": str(source_root) if source_root else None,
        "stall_anchor_mono": None, "last_progress_mono": None, "last_progress_at": None,
        "last_progress_signal": None, "progress_observations": 0,
        "provider": {"name": "fg-provider"},
    }
    if capability:
        run["chat_command_capability"] = True
    run["chat_start_seq"] = chat_start_seq
    if register:
        with svc._runs_lock:
            svc._runs[run_id] = run
    return run


def unregister_run(run_id: str) -> None:
    from modules.flow_gate.services import ai_invoke_service as svc

    with svc._runs_lock:
        svc._runs.pop(run_id, None)


@pytest.fixture()
def clean_commands_state():
    """Reset the commands module's process-local tables around a test."""
    from flowgate.conversation import commands

    def reset():
        with commands._lock:
            commands._events.clear()
            commands._controls.clear()
            commands._cancel_requested.clear()
            commands._executions.clear()
            commands._closed_runs.clear()
            commands._launch_gates.clear()

    reset()
    yield
    reset()


@pytest.fixture()
def clean_runs():
    from modules.flow_gate.services import ai_invoke_service as svc

    before = set(svc._runs)
    yield
    with svc._runs_lock:
        for run_id in set(svc._runs) - before:
            svc._runs.pop(run_id, None)


@pytest.fixture()
def clean_hooks():
    from modules.flow_gate.services import chat_run_hooks

    chat_run_hooks.clear()
    yield chat_run_hooks
    chat_run_hooks.clear()


# ── a controllable fake process executor ─────────────────────────────────────

@dataclass
class FakeResult:
    exit_code: Optional[int]
    timed_out: bool
    cancelled: bool
    stdout_tail: str
    stderr_tail: str


class FakeControl:
    def __init__(self, owner: "FakeExecutor"):
        self.owner = owner
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def close(self):
        pass


class FakeExecutor:
    """Stands in for tr_self_check_executor: ``release`` finishes the process with
    ``exit_code``/``stdout``/``stderr``; ``cancel`` ends it as cancelled."""

    class OwnershipError(RuntimeError):
        pass

    def __init__(self, *, exit_code: int = 0, stdout: str = "ok\n", stderr: str = "",
                 auto_release: bool = True, timed_out: bool = False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.release = threading.Event()
        if auto_release:
            self.release.set()
        self.spawned: list[list[str]] = []
        self.spawned_event = threading.Event()

    def spawn(self, argv, cwd, env, timeout_seconds, run_id):
        self.spawned.append(list(argv))
        self.spawned_event.set()
        return FakeControl(self), {"process_owner_kind": "fake"}

    def wait(self, control, timeout_seconds, cancelled):
        while not (self.release.is_set() or control.cancelled.is_set() or cancelled.is_set()):
            time.sleep(0.01)
        if control.cancelled.is_set() or cancelled.is_set():
            return FakeResult(None, False, True, "", "")
        return FakeResult(self.exit_code, self.timed_out, False, self.stdout, self.stderr)


def use_executor(monkeypatch, executor) -> None:
    from flowgate.conversation import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "executor", lambda: executor)


def set_policy(monkeypatch, value: str) -> None:
    from flowgate.conversation import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "resolve_command_policy", lambda user_id: value)


def wait_until(predicate, timeout: float = 10.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


def tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="fg-conv-"))
