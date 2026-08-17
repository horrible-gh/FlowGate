"""Project-control remote tool — pipeline tests (group 0003 R0001 / T0012).

Covers P0005 (message format) · L0006 (processing logic) · DB0007 (storage):
  ① auth 401 (not logged)  ② permission 403 (denied)  ③ path 422 (error)  ④ validity 422
  ⑤ execution read/write/grep/glob/remove + 404/409/413  ⑥ op_log  ⑦ completion mention
  §7 fail-fast priority (403 ▶ 422), result↔error_code consistency.

Harness mirrors test_qa_route_auth.py: TESTING=1, temp SQLite with ALL migrations
applied (picks up 041_remote_tool.sql), pepper env, _MockDB injected into
connection.STORE, FLOWGATE_STORAGE_DIR pointed at a temp source tree.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

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
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


PROJECT_ID = "testproj"
PROJECT_NAME = "testproj"
RAW_TOKEN = "raw-remote-tool-token-abc123"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Build an isolated DB + source tree and inject the store. Returns a context object."""
    # storage root → temp; source root = {storage}/src/{project_name}/main
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))
    src = storage_root / "src" / PROJECT_NAME / "main"
    (src / "app").mkdir(parents=True, exist_ok=True)
    (src / "docs").mkdir(parents=True, exist_ok=True)
    # newline="\n" to avoid Windows \r\n translation (keeps byte sizes deterministic)
    (src / "app" / "main.py").write_text("import sys\n# TODO: validate\nprint('hi')\n", encoding="utf-8", newline="\n")
    (src / "docs" / "readme.md").write_text("# readme\nTODO later\n", encoding="utf-8", newline="\n")

    # temp DB with all migrations
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for f in _migrations():
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    from modules.flow_gate.db import connection as conn_mod
    store = conn_mod.FlowGateStore()
    store._db = _MockDB(db_path)
    conn_mod.STORE = store

    # seed project + settings(branch=main)
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db.connection import now_iso
    store._execute(
        "INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
        "VALUES (?,?,1,?,?)",
        [PROJECT_ID, PROJECT_NAME, now_iso(), now_iso()],
    )
    store._execute(
        "INSERT INTO users(user_id,username,email,password,is_active,is_admin,created_at,updated_at) "
        "VALUES (?,?,?,?,1,0,?,?)",
        ["worker-user", "worker-user", "worker@example.test", "x", now_iso(), now_iso()],
    )
    db_projects.upsert_settings(PROJECT_ID, {"branch": "main"})

    class Ctx:
        def __init__(self):
            self.store = store
            self.src = src
            self.db_path = db_path

        def make_grant(self, scopes, *, report_doc_id=None, status="active", expires_at=None):
            from modules.flow_gate.db import remote_tool_grants as db_grants
            from modules.flow_gate.services import token_service
            _pid, pepper = token_service._active_pepper()
            token_hash = token_service._hash_token(RAW_TOKEN, pepper)
            db_grants.create(
                {
                    "grant_id": "grant_test_1",
                    "token_hash": token_hash,
                    "project": PROJECT_ID,
                    "module": "default",
                    "report_doc_id": report_doc_id,
                    "status": status,
                    "expires_at": expires_at,
                },
                scopes,
            )
            return "grant_test_1"

        def make_worker_token(self, token: str, *, token_id="tok_worker_1", action_scope="edit"):
            from datetime import datetime, timezone, timedelta
            from modules.flow_gate.services import token_service
            _pid, pepper = token_service._active_pepper()
            token_hash = token_service._hash_token(token, pepper)
            now = datetime.now(timezone.utc)
            self.store._execute(
                "INSERT INTO tokens "
                "(token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, "
                "issued_to, created_at, expires_at, scratch_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    token_id,
                    token_hash,
                    _pid,
                    PROJECT_ID,
                    None,
                    None,
                    action_scope,
                    "worker-user",
                    now.isoformat(timespec="seconds"),
                    (now + timedelta(hours=1)).isoformat(timespec="seconds"),
                    None,
                ],
            )
            return token_id

        def oplogs(self):
            return self.store._fetch_all(
                "SELECT * FROM remote_tool_op_log ORDER BY log_id", []
            )

    yield Ctx()

    store._db.close()
    conn_mod.STORE = None
    os.unlink(db_path)


def _call(operation, body, token=RAW_TOKEN):
    from modules.flow_gate.services import remote_tool_service
    return remote_tool_service.handle(operation, token, body)


# ── ① Authentication ────────────────────────────────────────────────────────────────────

def test_no_token_401_not_logged(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py"}, token=None)
    assert status == 401
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unauthorized"
    assert payload["op"] == "read"
    assert env.oplogs() == []  # subject not identified → not logged (L0006 §3.1)


def test_invalid_token_401(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py"}, token="wrong-token")
    assert status == 401
    assert env.oplogs() == []


def test_worker_token_lazily_gets_remote_grant(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("TR", False))
    worker_token = "raw-worker-token-0136"
    env.make_worker_token(worker_token, action_scope="edit")

    status, payload = _call(
        "write",
        {"path": "docs/from-worker.md", "content": "hello", "mode": "create"},
        token=worker_token,
    )

    assert status == 200
    assert payload["ok"] is True
    assert (env.src / "docs" / "from-worker.md").read_text(encoding="utf-8") == "hello"
    grant = env.store._fetch_one(
        "SELECT * FROM remote_tool_grant WHERE grant_id = ?",
        ["worker_tok_worker_1"],
    )
    assert grant is not None
    scopes = {
        row["scope"]
        for row in env.store._fetch_all(
            "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?",
            ["worker_tok_worker_1"],
        )
    }
    assert scopes == {"read", "write", "grep", "remove"}


def test_worker_remote_grant_expires_when_backing_token_consumed(env):
    worker_token = "raw-worker-token-consumed-0136"
    token_id = env.make_worker_token(worker_token, token_id="tok_worker_consumed", action_scope="edit")
    status, _ = _call("read", {"path": "app/main.py"}, token=worker_token)
    assert status == 200

    from modules.flow_gate.db import tokens as db_tokens
    db_tokens.consume(token_id)

    status, payload = _call("read", {"path": "app/main.py"}, token=worker_token)
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_next_step_worker_token_gets_crud_for_task_report_head(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("TR", False))
    worker_token = "raw-next-token-0136"
    env.make_worker_token(worker_token, token_id="tok_next_tr", action_scope="new")

    status, payload = _call(
        "write",
        {"path": "docs/tr-head.md", "content": "ok", "mode": "create"},
        token=worker_token,
    )

    assert status == 200
    assert payload["ok"] is True


def test_next_step_worker_token_is_read_only_for_investigation_head(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("NR", False))
    worker_token = "raw-next-token-readonly-0136"
    env.make_worker_token(worker_token, token_id="tok_next_nr", action_scope="new")

    status, payload = _call(
        "write",
        {"path": "docs/should-not-write.md", "content": "no", "mode": "create"},
        token=worker_token,
    )

    assert status == 403
    assert payload["error"]["code"] == "forbidden"


def test_edit_worker_token_is_read_only_for_non_task_head(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("CH", False))
    worker_token = "raw-edit-token-readonly-0179"
    env.make_worker_token(worker_token, token_id="tok_edit_ch", action_scope="edit")

    status, payload = _call(
        "write",
        {"path": "docs/should-not-write-edit.md", "content": "no", "mode": "create"},
        token=worker_token,
    )

    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert not (env.src / "docs" / "should-not-write-edit.md").exists()

    status, payload = _call("remove", {"path": "docs/readme.md"}, token=worker_token)
    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert (env.src / "docs" / "readme.md").exists()

    status, payload = _call("read", {"path": "app/main.py"}, token=worker_token)
    assert status == 200
    assert payload["ok"] is True

    scopes = {
        row["scope"]
        for row in env.store._fetch_all(
            "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?",
            ["worker_tok_edit_ch"],
        )
    }
    assert scopes == {"read", "grep"}


def test_existing_worker_grant_scopes_are_reconciled_for_non_task_edit(env, monkeypatch):
    from modules.flow_gate.db import remote_tool_grants as db_grants
    from modules.flow_gate.services import remote_tool_service, token_service

    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("CH", False))
    worker_token = "raw-existing-edit-token-0179"
    env.make_worker_token(worker_token, token_id="tok_existing_edit_ch", action_scope="edit")

    _pid, pepper = token_service._active_pepper()
    token_hash = token_service._hash_token(worker_token, pepper)
    db_grants.create(
        {
            "grant_id": "worker_tok_existing_edit_ch",
            "token_hash": token_hash,
            "project": PROJECT_ID,
            "module": "default",
            "status": "active",
        },
        ["read", "write", "grep", "remove"],
    )

    status, payload = _call(
        "write",
        {"path": "docs/stale-grant-write.md", "content": "no", "mode": "create"},
        token=worker_token,
    )

    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert not (env.src / "docs" / "stale-grant-write.md").exists()
    scopes = {
        row["scope"]
        for row in env.store._fetch_all(
            "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?",
            ["worker_tok_existing_edit_ch"],
        )
    }
    assert scopes == {"read", "grep"}

def test_revoked_grant_401(env):
    env.make_grant(["read"], status="revoked")
    status, _ = _call("read", {"path": "app/main.py"})
    assert status == 401


def test_expired_grant_401(env):
    env.make_grant(["read"], expires_at="2000-01-01T00:00:00+00:00")
    status, _ = _call("read", {"path": "app/main.py"})
    assert status == 401


# ── ② Permission scope ─────────────────────────────────────────────────────────────

def test_scope_denied_403_logged(env):
    env.make_grant(["write"])  # no read scope
    status, payload = _call("read", {"path": "app/main.py"})
    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    logs = env.oplogs()
    assert len(logs) == 1
    assert logs[0]["result"] == "denied"
    assert logs[0]["error_code"] == "forbidden"
    assert logs[0]["op"] == "read"


def test_glob_uses_grep_scope(env):
    env.make_grant(["grep"])
    status, payload = _call("glob", {"pattern": "**/*.py"})
    assert status == 200
    assert "app/main.py" in payload["paths"]


def test_glob_denied_without_grep_scope(env):
    env.make_grant(["read"])
    status, _ = _call("glob", {"pattern": "**/*.py"})
    assert status == 403


# ── ③ Path safety ─────────────────────────────────────────────────────────────

def test_path_traversal_422_logged_error(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "../secrets.txt"})
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"
    logs = env.oplogs()
    assert len(logs) == 1
    assert logs[0]["result"] == "error"
    assert logs[0]["error_code"] == "invalid_request"


def test_absolute_path_rejected_422(env):
    env.make_grant(["read"])
    status, _ = _call("read", {"path": "/etc/passwd"})
    assert status == 422


def test_grep_glob_pattern_traversal_422(env):
    env.make_grant(["grep"])
    status, _ = _call("grep", {"pattern": "x", "glob": "../*.py"})
    assert status == 422


# ── ④ Request validity ─────────────────────────────────────────────────────────────

def test_missing_required_path_422(env):
    env.make_grant(["read"])
    status, _ = _call("read", {})
    assert status == 422


def test_grep_missing_pattern_422(env):
    env.make_grant(["grep"])
    status, _ = _call("grep", {})
    assert status == 422


def test_write_bad_mode_422(env):
    env.make_grant(["write"])
    status, _ = _call("write", {"path": "x.txt", "content": "a", "mode": "bogus"})
    assert status == 422


def test_grep_invalid_regex_422(env):
    env.make_grant(["grep"])
    status, _ = _call("grep", {"pattern": "("})
    assert status == 422


def test_unknown_operation_422_not_logged(env):
    env.make_grant(["read"])
    status, payload = _call("destroy", {"path": "app/main.py"})
    assert status == 422
    assert env.oplogs() == []  # not representable in the op enum → not logged


# ── §7 fail-fast priority ──────────────────────────────────────────────────────

def test_scope_precedes_path_403_over_422(env):
    env.make_grant(["write"])  # no read scope + bad path too
    status, _ = _call("read", {"path": "../escape"})
    assert status == 403  # ② precedes ③


# ── ⑤ read ────────────────────────────────────────────────────────────────────

def test_read_success_no_continuation(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py"})
    assert status == 200
    assert payload["op"] == "read"
    assert "import sys" in payload["content"]
    assert payload["encoding"] == "utf-8"
    assert payload["truncated"] is False
    assert payload["size"] == len("import sys\n# TODO: validate\nprint('hi')\n".encode())
    assert "continuation" not in payload  # reads get no mention
    logs = env.oplogs()
    assert logs[0]["result"] == "success"
    assert logs[0]["error_code"] is None
    assert logs[0]["bytes_processed"] == payload["size"]


def test_read_not_found_404(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/missing.py"})
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    assert env.oplogs()[0]["result"] == "not_found"


def test_read_truncation(env):
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py", "max_bytes": 5})
    assert status == 200
    assert payload["truncated"] is True
    assert payload["content"] == "impor"
    assert payload["size"] > 5  # size = total bytes


# ── ⑤ 413 too_large (limit injection) ────────────────────────────────────────────────

def test_read_too_large_413(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    # max_bytes unset + file exceeds _MAX_READ_BYTES → 413 (unbounded-read guard)
    monkeypatch.setattr(remote_tool_service, "_MAX_READ_BYTES", 5)
    env.make_grant(["read"])
    status, payload = _call("read", {"path": "app/main.py"})
    assert status == 413
    assert payload["error"]["code"] == "too_large"
    log = env.oplogs()[0]
    assert log["result"] == "too_large" and log["error_code"] == "too_large"


def test_write_too_large_413(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service
    # encoded payload exceeds _MAX_WRITE_BYTES → 413
    monkeypatch.setattr(remote_tool_service, "_MAX_WRITE_BYTES", 3)
    env.make_grant(["write"])
    status, payload = _call("write", {"path": "docs/big.md", "content": "way too long"})
    assert status == 413
    assert payload["error"]["code"] == "too_large"
    assert env.oplogs()[0]["result"] == "too_large"
    assert not (env.src / "docs" / "big.md").exists()  # over the limit → not written


# ── ⑤ write (+ ⑦ mention) ────────────────────────────────────────────────────────

def test_write_create_success_with_continuation(env):
    env.make_grant(["write"], report_doc_id="flowgate.default.0003.0099-TR")
    status, payload = _call("write", {"path": "docs/new.md", "content": "hello", "mode": "create"})
    assert status == 200
    assert payload["created"] is True
    assert payload["bytes_written"] == 5
    cont = payload["continuation"]
    assert cont["next_action"] == "write_report"
    assert cont["report_doc_id"] == "flowgate.default.0003.0099-TR"
    assert (env.src / "docs" / "new.md").read_text(encoding="utf-8") == "hello"
    log = env.oplogs()[0]
    assert log["result"] == "success" and log["bytes_processed"] == 5


def test_write_create_conflict_409(env):
    env.make_grant(["write"])
    status, payload = _call("write", {"path": "app/main.py", "content": "x", "mode": "create"})
    assert status == 409
    assert payload["error"]["code"] == "conflict"
    assert env.oplogs()[0]["result"] == "conflict"


def test_write_overwrite_existing_created_false(env):
    env.make_grant(["write"])
    status, payload = _call("write", {"path": "app/main.py", "content": "new"})
    assert status == 200
    assert payload["created"] is False
    assert (env.src / "app" / "main.py").read_text(encoding="utf-8") == "new"


def test_write_append(env):
    env.make_grant(["write"])
    status, payload = _call("write", {"path": "docs/readme.md", "content": "X", "mode": "append"})
    assert status == 200
    assert payload["created"] is False
    assert (env.src / "docs" / "readme.md").read_text(encoding="utf-8").endswith("X")


def test_write_continuation_null_report_when_unset(env):
    env.make_grant(["write"])  # no report_doc_id
    status, payload = _call("write", {"path": "docs/x.md", "content": "y"})
    assert status == 200
    assert payload["continuation"]["report_doc_id"] is None


def test_write_unencodable_content_422_logged(env):
    # content cannot be represented in the requested narrow encoding (ascii) → a 422 envelope
    # rather than a bare 500, and it must be logged as error in the ⑥ history (guards against UnicodeEncodeError leak regression).
    env.make_grant(["write"])
    status, payload = _call(
        "write", {"path": "docs/x.md", "content": "한글", "encoding": "ascii"}
    )
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"
    log = env.oplogs()[0]
    assert log["result"] == "error" and log["error_code"] == "invalid_request"
    assert not (env.src / "docs" / "x.md").exists()  # failure → file not created


# ── ⑤ remove (+ ⑦ mention) ───────────────────────────────────────────────────────

def test_remove_success_with_continuation(env):
    env.make_grant(["remove"])
    status, payload = _call("remove", {"path": "docs/readme.md"})
    assert status == 200
    assert payload["removed"] is True
    assert payload["continuation"]["next_action"] == "write_report"
    assert not (env.src / "docs" / "readme.md").exists()
    assert env.oplogs()[0]["result"] == "success"


def test_remove_not_found_404(env):
    env.make_grant(["remove"])
    status, _ = _call("remove", {"path": "docs/nope.md"})
    assert status == 404
    assert env.oplogs()[0]["result"] == "not_found"


# ── ⑤ grep ────────────────────────────────────────────────────────────────────

def test_grep_matches(env):
    env.make_grant(["grep"])
    status, payload = _call("grep", {"pattern": "TODO"})
    assert status == 200
    files = {m["file"] for m in payload["matches"]}
    assert "app/main.py" in files and "docs/readme.md" in files
    assert payload["total"] == 2
    assert payload["truncated"] is False
    assert "continuation" not in payload


def test_grep_glob_filter_and_max_results(env):
    env.make_grant(["grep"])
    status, payload = _call("grep", {"pattern": "TODO", "glob": "**/*.py", "max_results": 1})
    assert status == 200
    assert all(m["file"].endswith(".py") for m in payload["matches"])
    assert len(payload["matches"]) == 1
    assert payload["total"] == 1


def test_grep_ignore_case(env):
    env.make_grant(["grep"])
    status, payload = _call("grep", {"pattern": "todo", "ignore_case": True})
    assert status == 200
    assert payload["total"] == 2


# ── ⑤ glob ────────────────────────────────────────────────────────────────────

def test_glob_matches(env):
    env.make_grant(["grep"])
    status, payload = _call("glob", {"pattern": "**/*.md"})
    assert status == 200
    assert payload["paths"] == ["docs/readme.md"]
    assert payload["total"] == 1


# ── ⑤ grep/glob `path` contract (B0001 / NR0003) ───────────────────────────────
# `path` is optional; omitting it defaults to the project source root. When present
# it must be a non-empty in-root relative string — an explicit empty string is
# rejected by the safety layer before the executor's root fallback ever runs, even
# though the executor itself would treat "" the same as omission.

@pytest.mark.parametrize("operation, extra", [
    ("grep", {"pattern": "TODO"}),
    ("glob", {"pattern": "**/*.py"}),
])
def test_path_omitted_defaults_to_root(env, operation, extra):
    env.make_grant(["grep"])
    status, _ = _call(operation, dict(extra))
    assert status == 200


@pytest.mark.parametrize("operation, extra", [
    ("grep", {"pattern": "TODO"}),
    ("glob", {"pattern": "**/*.py"}),
])
def test_path_empty_string_422(env, operation, extra):
    env.make_grant(["grep"])
    status, payload = _call(operation, {**extra, "path": ""})
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("operation, extra", [
    ("grep", {"pattern": "TODO"}),
    ("glob", {"pattern": "**/*.py"}),
])
def test_path_dot_alias_200(env, operation, extra):
    env.make_grant(["grep"])
    status, _ = _call(operation, {**extra, "path": "."})
    assert status == 200


def test_path_subdir_scopes_grep_results(env):
    env.make_grant(["grep"])
    status, payload = _call("grep", {"pattern": "TODO", "path": "app"})
    assert status == 200
    files = {m["file"] for m in payload["matches"]}
    assert files == {"app/main.py"}


def test_path_subdir_scopes_glob_results(env):
    env.make_grant(["grep"])
    status, payload = _call("glob", {"pattern": "**/*.md", "path": "docs"})
    assert status == 200
    assert payload["paths"] == ["docs/readme.md"]


@pytest.mark.parametrize("bad_path", ["/etc", "C:\\Windows", "..", 0])
@pytest.mark.parametrize("operation, extra", [
    ("grep", {"pattern": "TODO"}),
    ("glob", {"pattern": "**/*.py"}),
])
def test_path_invalid_values_422(env, operation, extra, bad_path):
    env.make_grant(["grep"])
    status, _ = _call(operation, {**extra, "path": bad_path})
    assert status == 422


# ── ⑤-b The worker mention's own examples must be executable (NR0003 §8-2) ─────
# B0001 was reported by a worker who copied the grep/glob request body straight out
# of the mention. Asserting only that the `path` key is gone (mention-side test) does
# not prove the copied body is accepted, so run the emitted JSON through the real
# handler. `step_type` covers both the mutating (T/TR) and read-only (N/NR) sections.

def _worker_example(step_type: str, operation: str) -> dict:
    """The request body a worker on this step is handed for `{operation}`.

    0349 TR-2: the mention no longer prints request bodies — it names the tools and points
    at GET /help/tools/{name}, which serves this example. So the copy path being tested is
    now help → worker, and the example is read from the same catalog that endpoint renders
    (tool_registry), for the same reason as before: whatever the worker is handed must be
    accepted verbatim by the live API, not merely look plausible.
    """
    import json as _json_mod
    from modules.flow_gate.services import tool_registry

    kind, _reason = tool_registry.kind_for_step("new", step_type)
    assert operation in tool_registry.tool_names(kind), (
        f"{operation} is not offered to a {step_type} step; the example would never be read"
    )
    detail = tool_registry.build_tool_detail(operation, "ko", "http://localhost:8089/flowgate/api/v1")
    # Round-trip through JSON: the worker copies serialized text, not a Python dict.
    return _json_mod.loads(_json_mod.dumps(detail["example_request"]["body"]))


@pytest.mark.parametrize("step_type", ["TR", "NR"])
@pytest.mark.parametrize("operation", ["grep", "glob"])
def test_help_example_body_is_accepted_verbatim(env, step_type, operation):
    env.make_grant(["grep"])
    body = _worker_example(step_type, operation)

    assert "path" not in body, f"{operation} example still sends a path key: {body!r}"
    status, payload = _call(operation, body)
    assert status == 200, f"copied {operation} example rejected: {payload!r}"


# ── Router wiring (HTTP surface) ──────────────────────────────────────────────────

def test_router_envelope_and_status(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    env.make_grant(["read"])
    app = FastAPI()
    app.include_router(router, prefix="/flowgate")
    client = TestClient(app)

    # success
    r = client.post(
        "/flowgate/api/v1/remote/read",
        json={"path": "app/main.py"},
        headers={"Authorization": f"Bearer {RAW_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["op"] == "read"

    # no auth header → 401
    r2 = client.post("/flowgate/api/v1/remote/read", json={"path": "app/main.py"})
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "unauthorized"


# ── Step-type resolution (0334 R0001 / NR0003 발견 7) ─────────────────────────

def _step_type_for(monkeypatch, *, doc_type, head_type):
    """Resolve a doc_ref's step type against a stubbed sequence + document store."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import remote_tool_service

    monkeypatch.setattr(
        db_documents, "get_by_id", lambda _d: {"type_code": doc_type} if doc_type else None)
    monkeypatch.setattr(
        db_wfseq, "get_sequence_for_member_doc",
        lambda _d: {"id": 1} if head_type else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _d: None)
    monkeypatch.setattr(
        db_wfseq, "get_effective_head", lambda _s: {"type": head_type} if head_type else None)
    return remote_tool_service._worker_token_step_type({"doc_ref": "d1"})


def test_chat_doc_keeps_its_own_type_over_the_sequence_head(monkeypatch):
    # A CH is auto-completed on creation, so the effective head is already the NEXT
    # slot. Following it would give a chat token source write/remove while its mention
    # promises read/search only — the worker acts on the mention it was handed.
    assert _step_type_for(monkeypatch, doc_type="CH", head_type="T") == "CH"

    from modules.flow_gate.services import remote_tool_service
    assert remote_tool_service._scopes_for_worker_token(
        {"action_scope": "edit", "doc_ref": "d1"}) == ["read", "grep"]


def test_non_chat_doc_still_follows_the_sequence_head(monkeypatch):
    assert _step_type_for(monkeypatch, doc_type="R", head_type="TR") == "TR"


def test_doc_type_is_the_fallback_when_there_is_no_sequence(monkeypatch):
    assert _step_type_for(monkeypatch, doc_type="TR", head_type=None) == "TR"


def test_unknown_doc_resolves_to_no_step_type(monkeypatch):
    assert _step_type_for(monkeypatch, doc_type=None, head_type=None) is None

# ── TR-1 TSR worker-token scope expansion (0349 T0008) ─────────────────────

@pytest.mark.parametrize("action_scope", ["new", "edit"])
def test_next_step_worker_token_gets_crud_for_tsr_head(monkeypatch, action_scope):
    from modules.flow_gate.services import remote_tool_service

    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("TSR", False))
    assert remote_tool_service._scopes_for_worker_token({
        "action_scope": action_scope,
        "doc_ref": "flowgate.default.0349.0001-R",
    }) == ["read", "write", "grep", "remove"]


def test_test_run_token_still_gets_no_remote_grant(monkeypatch):
    from modules.flow_gate.services import remote_tool_service

    def unexpected(_rec):
        raise AssertionError("test_run scope must not resolve or receive a source grant")
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", unexpected)
    assert remote_tool_service._scopes_for_worker_token({
        "action_scope": "test_run",
        "doc_ref": "flowgate.default.0349.0001-R",
    }) == []


# ── 0431 T0004/NR0003: chat=read policy end-to-end ──────────────────────────

def test_existing_chat_worker_grant_with_empty_scopes_is_reconciled_to_read_grep(env):
    """A chat worker grant persisted under the old chat=none policy has no scope
    rows at all. The backing token is still valid, so the next authenticated call
    must reconcile the stored scope set to exactly {read, grep} -- no separate
    migration or grant-issuing code path is involved (NR0003 §2)."""
    from modules.flow_gate.db import remote_tool_grants as db_grants
    from modules.flow_gate.services import token_service

    worker_token = "raw-chat-worker-token-stale-scopes-0431"
    env.make_worker_token(worker_token, token_id="tok_chat_stale", action_scope="chat")

    _pid, pepper = token_service._active_pepper()
    token_hash = token_service._hash_token(worker_token, pepper)
    db_grants.create(
        {
            "grant_id": "worker_tok_chat_stale",
            "token_hash": token_hash,
            "project": PROJECT_ID,
            "module": "default",
            "status": "active",
        },
        [],  # scope-less grant, as chat=none would have left it (or never created it)
    )

    status, payload = _call("read", {"path": "app/main.py"}, token=worker_token)

    assert status == 200
    assert payload["ok"] is True
    scopes = {
        row["scope"]
        for row in env.store._fetch_all(
            "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?",
            ["worker_tok_chat_stale"],
        )
    }
    assert scopes == {"read", "grep"}


def test_chat_worker_token_lazily_gets_read_grep_grant_on_first_call(env):
    """A valid chat token with no grant row at all (never called a remote tool
    before) must get a lazily-created grant scoped to exactly read/grep on its
    first authenticated call, the same lazy path new/edit tokens already use."""
    worker_token = "raw-chat-worker-token-lazy-0431"
    env.make_worker_token(worker_token, token_id="tok_chat_lazy", action_scope="chat")

    status, payload = _call("stat", {"path": "app/main.py"}, token=worker_token)

    assert status == 200
    assert payload["ok"] is True
    grant = env.store._fetch_one(
        "SELECT * FROM remote_tool_grant WHERE grant_id = ?",
        ["worker_tok_chat_lazy"],
    )
    assert grant is not None
    scopes = {
        row["scope"]
        for row in env.store._fetch_all(
            "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?",
            ["worker_tok_chat_lazy"],
        )
    }
    assert scopes == {"read", "grep"}


@pytest.mark.parametrize("op,body,expected_status", [
    ("read", {"path": "app/main.py"}, 200),
    ("grep", {"pattern": "TODO"}, 200),
    ("glob", {"pattern": "**/*.py"}, 200),
    ("stat", {"path": "app/main.py"}, 200),
    ("write", {"path": "docs/chat-should-not-write.md", "content": "no", "mode": "create"}, 403),
    ("patch", {"path": "app/main.py", "old_string": "hi", "new_string": "bye"}, 403),
    ("remove", {"path": "docs/readme.md"}, 403),
])
def test_chat_worker_token_gets_exactly_read_grep_glob_stat(env, op, body, expected_status):
    """R0001's ask, pinned end to end: a chat token can call read/grep/glob/stat and
    is refused write/patch/remove -- never the read_write kind, never a TR/TSR/TS
    write grant inherited through any other path."""
    worker_token = f"raw-chat-worker-token-boundary-{op}-0431"
    env.make_worker_token(worker_token, token_id=f"tok_chat_boundary_{op}", action_scope="chat")

    before = (env.src / "app" / "main.py").read_text(encoding="utf-8")

    status, payload = _call(op, body, token=worker_token)

    assert status == expected_status
    if expected_status == 403:
        assert payload["error"]["code"] == "forbidden"
        assert not (env.src / "docs" / "chat-should-not-write.md").exists()
        assert (env.src / "docs" / "readme.md").exists()
        assert (env.src / "app" / "main.py").read_text(encoding="utf-8") == before
    else:
        assert payload["ok"] is True
