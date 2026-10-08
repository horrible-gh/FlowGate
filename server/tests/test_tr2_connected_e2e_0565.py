"""TR2 connected E2E (0565 T0028 §3): real DB, real Git worktree, real HTTP routes.

Nothing on the approval path is replaced by a double. The only seams are the ones every
connected suite in this tree uses (0332 ``git_active``): where the group worktree lives
(``git_service.src_root`` -> a scratch repo outside the source tree) and who the HTTP
caller is (``get_current_user``). Everything else is the product:

* SQLite with every migration, a connection-per-call backend with real ``BEGIN
  IMMEDIATE`` transactions, and the SQL from ``queries.json``;
* ``POST /api/v1/inbox`` with a real issued token for the AI-direct T2/TR2 submissions,
  ``create_next_approved_core`` for the auto-approved T2;
* the TR2 routes (read, whole/item CRUD, precheck) and the generic
  ``POST /api/v1/documents/review_transitions/approve`` for the human approval;
* ``settle_completed_step`` for the unmanned approval;
* the strong approval dispatcher, the DB project lock, the apply adapter, the
  validation registry and ``process_runner``, ``git commit``, ``tr_commit_ledger`` and the
  workflow head.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from scratch_support import remove_tree, session_scratch
from group_lock_stub import hold_group_lock

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")

_SCRATCH = session_scratch("tr2-connected-0565")

PROJECT = "tr2e2e"
MODULE = "default"
USER = "usr_tr2e2e"
GATE_OK = "python -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('gate.ok').exists() else 3)\""

_QUERIES: dict[str, str] = {}
for _section, _entries in json.loads(_QUERIES_JSON.read_text(encoding="utf-8")).items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


# ── Database: every migration, one connection per call, real transactions ─────────

class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cursor = None

    def execute(self, sql, params=None):
        self._cursor = self._conn.execute(sql, params or [])
        return self._cursor

    @property
    def cursor(self):
        return self._cursor

    def fetch_one(self):
        row = self._cursor.fetchone() if self._cursor is not None else None
        return dict(row) if row else None

    def fetch_all(self):
        return [dict(r) for r in self._cursor.fetchall()] if self._cursor is not None else []


class _Db:
    """Mirrors the live SQLite backend: a fresh connection per statement."""

    db_type = None

    def __init__(self, path: str):
        self._path = path

    def _connect(self):
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute(self, sql, params=None):
        conn = self._connect()
        try:
            conn.execute(sql, params or [])
            conn.commit()
        finally:
            conn.close()

    def fetch_one(self, sql, params=None):
        conn = self._connect()
        try:
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def fetch_all(self, sql, params=None):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
        finally:
            conn.close()

    @contextmanager
    def begin_transaction(self):
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield _Txn(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from modules.flow_gate.db import connection as conn_mod
    # Always our own storage root: a developer shell often exports the live one, and
    # document bodies, approval backups and token scratch must never land there.
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    from modules.flow_gate.db import meta_cache

    path = str(tmp_path / "tr2_e2e.db")
    conn = sqlite3.connect(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _Db(path)
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    previous = conn_mod.STORE
    conn_mod.STORE = _Store()
    meta_cache.clear_all()
    try:
        yield conn_mod.STORE
    finally:
        conn_mod.STORE = previous
        meta_cache.clear_all()


# ── Git: the group worktree is a real repository in scratch ───────────────────────

def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def repo():
    path = _SCRATCH / f"wt-{os.urandom(6).hex()}"
    (path / "src").mkdir(parents=True)
    git(path, "init", "-b", "work")
    git(path, "config", "core.autocrlf", "false")  # byte-exact on every host
    (path / "src" / "app.txt").write_bytes(b"greeting = 'hello'\ncount = 1\n")
    (path / ".gitignore").write_bytes(b"gate.ok\n")
    git(path, "add", "-A")
    git(path, "commit", "-m", "base")
    yield path
    remove_tree(path)


# ── Seed: project, user with approve rights, git config, validation registry ──────

@pytest.fixture
def env(store, repo, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import project_test_commands as db_commands
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.services import git_service, test_command_service

    now = now_iso()
    projects.create({"project_id": PROJECT, "project_name": "TR2 E2E"})
    users.create({"user_id": USER, "username": "tr2reviewer", "email": "tr2@test.com",
                  "password": "hashed", "is_admin": 1})
    store._execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", [USER])
    store._execute("INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) "
                   "VALUES (?,?,?,?)", ["role_tr2", "TR2", now, now])
    for perm in ("document.create", "document.read", "document.update", "document.approve",
                 "document.reject", "perm_document_create", "perm_document_read",
                 "perm_document_update", "perm_document_approve"):
        store._execute("INSERT OR IGNORE INTO permissions (permission_id, permission_name, "
                       "created_at) VALUES (?,?,?)", [perm, perm, now])
        store._execute("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) "
                       "VALUES (?,?)", ["role_tr2", perm])
    store._execute("INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, "
                   "granted_at) VALUES (?,?,?,?)", [USER, PROJECT, "role_tr2", now])
    db_git.upsert_config(PROJECT, {"repo_url": "https://example.invalid/tr2.git",
                                   "enabled": True, "base_branch": "main",
                                   "secret_enc": None})
    db_commands.insert(PROJECT, test_command_service.normalize_command(GATE_OK),
                       "TR2 gate", "manual", None, verified_os=os.name)
    from modules.flow_gate.rbac import permission_service
    permission_service.invalidate_all()
    monkeypatch.setattr(git_service, "src_root", lambda _name, _branch: repo)
    return {"repo": repo, "store": store}


# ── Workflow seed: R root → [T2, TR2, T] ──────────────────────────────────────────

def make_group(code: str) -> dict:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    gid = f"{PROJECT}.{MODULE}.{code}"
    db_groups.create({"group_id": gid, "project_id": PROJECT, "module": MODULE,
                      "title": f"TR2 E2E {code}"})
    root = f"{gid}.0001-R"
    db_docs.create({"doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 1,
                    "title": "TR2 connected requirement", "group_id": gid, "module": MODULE,
                    "owner_id": USER, "revision_no": 0})
    db_docs.update(root, {"doc_review_status": "wf_in_progress"})
    db_wfseq.insert_sequence(root)
    seq = db_wfseq.get_sequence_by_doc_id(root)
    for order, (item_seq, type_code) in enumerate(((1, "T2"), (2, "TR2"), (3, "T"))):
        db_wfseq.insert_sequence_item(seq["id"], item_seq, type_code, type_code, "doc", order)
    db_git.register_worktree(gid, PROJECT, "work")
    return {"group_id": gid, "code": code, "root": root,
            "items": db_wfseq.get_sequence_items(seq["id"])}


def issue_token(group: dict, scope: str, doc_ref: str) -> str:
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=_SCRATCH / "tokens"):
        return token_service.issue(project=PROJECT, group_id=group["group_id"],
                                   action_scope=scope, doc_ref=doc_ref,
                                   issued_to=USER)["raw_token"]


def post_inbox(group: dict, body: dict, doc_ref: str):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    raw = issue_token(group, body["action"], doc_ref)
    envelope = {"project": PROJECT, "module": MODULE, "group_name": group["group_id"], **body}
    return TestClient(app).post("/api/v1/inbox", json=envelope,
                                headers={"Authorization": f"Bearer {raw}"})


def api():
    """The human surface: TR2 routes and the generic review transition routes."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import tr2 as tr2_routes
    from modules.flow_gate.workflow.routers import workflow as workflow_routes

    app = FastAPI()
    app.include_router(tr2_routes.router, prefix="/api/v1")
    app.include_router(workflow_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    return TestClient(app)


def edit_spec(**over) -> dict:
    spec = {
        "termination": "ready_to_apply",
        "edits": [{"id": "e1", "kind": "edit", "file": "src/app.txt",
                   "anchor_old": "greeting = 'hello'", "replacement_new": "greeting = 'hi'",
                   "rationale": "shorter greeting", "confidence": "high"}],
        "deferred": [{"id": "d1", "reason": "needs_runtime", "rationale": "measure later",
                      "file": "src/later.txt"}],
        "gate": {"commands": [GATE_OK], "apply": False},
    }
    spec.update(over)
    return spec


def tr2_body(t2_doc_id: str, spec: dict | None = None) -> dict:
    return {"tr2_version": 1, "source_t2_doc_id": t2_doc_id, "edit_spec": spec or edit_spec()}


def auto_approved_t2(group: dict) -> str:
    from modules.flow_gate.documents.routers import documents as routes
    created = routes.create_next_approved_core(
        project_id=PROJECT, group_id=group["group_id"], module=MODULE,
        prev_doc_id=group["root"], type_code="T2", actor_user_id=USER,
        approver_perms={"document.approve", "document.update", "perm_document_create"})
    return created["doc_id"]


def submit_tr2(group: dict, t2_doc_id: str, spec: dict | None = None) -> str:
    resp = post_inbox(group, {"action": "new", "doc_type": "TR2", "prev_doc_id": t2_doc_id,
                              "title": "TR2 connected proposal",
                              "content": json.dumps(tr2_body(t2_doc_id, spec))}, t2_doc_id)
    assert resp.status_code == 201, resp.text
    return resp.json()["doc_id"]


def view(client, doc_id: str) -> dict:
    resp = client.get(f"/api/v1/documents/{doc_id}/tr2")
    assert resp.status_code == 200, resp.text
    return resp.json()


def effective_head(doc_id: str) -> dict:
    from modules.flow_gate.documents import tr2_service
    return tr2_service.effective_head_for(doc_id)


def git_head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def approve(client, doc_id: str, revision: int, request_key: str | None = None):
    return client.post("/api/v1/documents/review_transitions/approve",
                       json={"doc_id": doc_id, "comment": None, "expected_revision": revision,
                             "request_key": request_key})


CREATED = {"id": "c1", "kind": "create_file", "file": "src/new_module.txt",
           "content": "created by TR2\n", "rationale": "new file", "confidence": "medium"}


def test_auto_approved_t2_human_crud_and_strong_approval(env):
    """T2 auto_approved -> AI TR2 -> human CRUD -> human approve -> commit/ledger/advance."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    repo = env["repo"]
    group = make_group("0001")
    client = api()

    # ── 3.1 auto_approved T2 → TR2 head, AI-direct TR2 through the real inbox ──
    t2 = auto_approved_t2(group)
    assert db_docs.get_by_id(t2)["doc_review_status"] == "approved"
    assert db_wfseq.get_pending_head_by_group(group["group_id"], PROJECT)["type"] == "TR2"
    tr2 = submit_tr2(group, t2)
    assert effective_head(tr2)["result_doc_id"] == tr2
    first = view(client, tr2)
    assert first["document"]["revision_no"] == 1
    assert first["document"]["doc_review_status"] == "pending_review"
    assert first["mutation"] == {"allowed": True, "reason": None}
    assert first["approval"]["retry"]["reason"] == "no_attempt"
    assert [f["path"] for f in first["derived"]["files"]] == ["src/app.txt"]

    # ── 3.5 CRUD round-trips, every one a new revision through the canonical writer ──
    base = f"/api/v1/documents/{tr2}/tr2"
    resp = client.post(f"{base}/items", json={"expected_revision": 1, "collection": "edits",
                                              "item": CREATED})
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_revision"] == 2
    after_add = view(client, tr2)
    assert {f["path"]: f["kind"] for f in after_add["derived"]["files"]} == {
        "src/app.txt": "edit", "src/new_module.txt": "create_file"}
    assert (after_add["derived"]["live_precheck"]["baseline_fingerprint"]
            != first["derived"]["live_precheck"]["baseline_fingerprint"])
    item = client.get(f"{base}/items/c1").json()
    assert item["collection"] == "edits" and item["item"]["content"] == "created by TR2\n"

    replaced = dict(CREATED, content="created by TR2, reviewed\n")
    resp = client.put(f"{base}/items/c1", json={"expected_revision": 2, "item": replaced})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 3, resp.text
    assert client.get(f"{base}/items/c1").json()["item"]["content"] == "created by TR2, reviewed\n"

    moved = {"id": "d1", "reason": "multi_file_design", "rationale": "needs a design pass",
             "file": "src/later.txt"}
    resp = client.put(f"{base}/items/d1", json={"expected_revision": 3, "collection": "deferred",
                                                "item": moved})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 4, resp.text

    # a stale screen cannot overwrite anything, whole or individual
    for stale in (
        client.put(f"{base}/items/c1", json={"expected_revision": 2, "item": CREATED}),
        client.delete(f"{base}/items/c1", params={"expected_revision": 3}),
        client.put(base, json={"expected_revision": 1, "body": tr2_body(t2)}),
        client.delete(f"{base}/spec", params={"expected_revision": 1}),
    ):
        assert stale.status_code == 409 and stale.json()["code"] == "tr2_spec_changed", stale.text
    assert view(client, tr2)["document"]["revision_no"] == 4

    # an invalid individual item goes through the same validator; nothing is saved
    bad = client.post(f"{base}/items", json={"expected_revision": 4, "collection": "edits",
                                             "item": {"id": "e1", "file": "src/app.txt"}})
    assert bad.status_code == 422 and bad.json()["code"] == "tr2_spec_invalid", bad.text
    unsafe = client.post(f"{base}/items", json={"expected_revision": 4, "collection": "edits",
                                                "item": dict(CREATED, id="c2", file="../escape")})
    assert unsafe.status_code == 422 and unsafe.json()["code"] == "tr2_path_unsafe", unsafe.text
    assert view(client, tr2)["document"]["revision_no"] == 4

    resp = client.delete(f"{base}/items/c1", params={"expected_revision": 4})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 5, resp.text
    after_delete = view(client, tr2)
    assert [f["path"] for f in after_delete["derived"]["files"]] == ["src/app.txt"]
    assert (after_delete["derived"]["live_precheck"]["baseline_fingerprint"]
            == first["derived"]["live_precheck"]["baseline_fingerprint"])
    assert after_delete["derived"]["live_precheck"]["drift"] is False

    # whole delete (reset to an empty proposal), then whole create/update
    resp = client.delete(f"{base}/spec", params={"expected_revision": 5})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 6, resp.text
    reset = view(client, tr2)["body"]["edit_spec"]
    assert reset["edits"] == [] and reset["termination"] == "needs_more_work"
    final_spec = edit_spec(edits=[edit_spec()["edits"][0], CREATED])
    resp = client.put(base, json={"expected_revision": 6, "body": {
        **tr2_body(t2, final_spec), "baseline_fingerprint": "sha256:" + "0" * 64}})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 7, resp.text
    ready = view(client, tr2)
    assert ready["body"]["baseline_fingerprint"] != "sha256:" + "0" * 64  # server-owned
    assert ready["derived"]["live_precheck"]["anchors"] is not None
    assert client.post(f"{base}/precheck").json()["ready"] is True

    # ── 3.3 human approval through the strong dispatcher ──
    (repo / "gate.ok").write_bytes(b"ok")
    before = git_head(repo)
    resp = approve(client, tr2, 7, request_key="human:e2e-0001")
    assert resp.status_code == 200, resp.text
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "approved"
    head = git_head(repo)
    assert head != before and git(repo, "rev-parse", "HEAD^").strip() == before
    changed = sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert changed == ["src/app.txt", "src/new_module.txt"]
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    assert (repo / "src" / "new_module.txt").read_bytes() == b"created by TR2\n"
    assert git(repo, "status", "--porcelain") == ""
    ledger = [row for row in db_ledger.list_by_group(group["group_id"]) if row["doc_id"] == tr2]
    assert len(ledger) == 1 and ledger[0]["commit_sha"] == head and ledger[0]["state"] == "live"
    attempts = db_attempts.list_by_doc(tr2)
    assert [(a["state"], a["phase"], a["result_code"]) for a in attempts] == [
        ("succeeded", "complete", "succeeded")]
    assert attempts[0]["commit_sha"] == head and attempts[0]["ledger_row_id"] == ledger[0]["id"]
    assert json.loads(attempts[0]["validation_json"])["status"] == "passed"

    # workflow advanced past TR2
    nxt = effective_head(tr2)
    assert nxt["result_doc_id"] is None and nxt["type"] == "T"

    # approved read model: immutable, aligned, no retry
    approved = view(client, tr2)
    assert approved["mutation"] == {"allowed": False, "reason": "approved"}
    assert approved["history"]["source_history_state"] == "aligned"
    assert approved["approval"]["retry"]["allowed"] is False

    # the same request key replays; it does not run a second attempt
    replay = approve(client, tr2, 7, request_key="human:e2e-0001")
    assert replay.status_code == 200, replay.text
    assert len(db_attempts.list_by_doc(tr2)) == 1 and git_head(repo) == head

    # the approved terminal revision refuses every mutation entry point
    for blocked in (
        client.put(base, json={"expected_revision": 7, "body": tr2_body(t2, final_spec)}),
        client.delete(f"{base}/items/c1", params={"expected_revision": 7}),
        client.post(f"{base}/items", json={"expected_revision": 7, "collection": "deferred",
                                           "item": dict(moved, id="d9")}),
        client.delete(f"{base}/spec", params={"expected_revision": 7}),
    ):
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "tr2_spec_immutable"
    assert db_docs.get_by_id(tr2)["revision_no"] == 7


T2_BODY = "# T2 proposal\n\n## Proposal\n\nShorten the greeting in src/app.txt.\n"


def ai_direct_t2(group: dict, client) -> str:
    """AI writes the T2 through the inbox; a human approves it on the generic path."""
    from modules.flow_gate.db import documents as db_docs
    resp = post_inbox(group, {"action": "new", "doc_type": "T2", "prev_doc_id": group["root"],
                              "title": "T2 proposal", "content": T2_BODY}, group["root"])
    assert resp.status_code == 201, resp.text
    t2 = resp.json()["doc_id"]
    assert db_docs.get_by_id(t2)["doc_review_status"] == "pending_review"
    approved = client.post("/api/v1/documents/review_transitions/approve",
                           json={"doc_id": t2, "comment": None})
    assert approved.status_code == 200, approved.text
    assert db_docs.get_by_id(t2)["doc_review_status"] == "approved"
    return t2


def settle(group: dict, tr2: str) -> dict:
    from modules.flow_gate.api import inbox_routes
    return inbox_routes.settle_completed_step(
        project=PROJECT, group_id=group["group_id"], doc_id=tr2, doc_type="TR2",
        actor_user_id=USER, completed_seq=2, target_seq=None,
        user_paused_probe=lambda *_a, **_k: False, locale="en")


def test_ai_direct_t2_unmanned_settle_validation_rollback_and_retry(env):
    """ai_direct T2 -> AI TR2 -> unmanned settle (same dispatcher) -> validation failure
    -> rollback -> server-offered retry -> success; duplicate retries replay."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts

    repo = env["repo"]
    group = make_group("0002")
    client = api()
    t2 = ai_direct_t2(group, client)
    tr2 = submit_tr2(group, t2)
    base_head = git_head(repo)

    # gate.ok is absent: validation fails after apply, rollback restores the baseline
    outcome = settle(group, tr2)
    assert outcome["outcome"] == "stopped" and outcome["stop_code"] == "approve_failed", outcome
    attempts = db_attempts.list_by_doc(tr2)
    # the unmanned settle retries a retryable failure exactly once, with its own key
    assert [(a["state"], a["result_code"], a["error_code"]) for a in attempts] == [
        ("failed", "recovered_rollback", "tr2_validation_failed")] * 2
    assert all(a["request_key"].startswith("auto:") for a in attempts)
    assert git_head(repo) == base_head and git(repo, "status", "--porcelain") == ""
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "pending_review"
    assert db_ledger.list_by_group(group["group_id"]) == []

    failed = view(client, tr2)
    retry = failed["approval"]["retry"]
    assert retry["allowed"] is True and retry["mode"] == "new_attempt"
    assert retry["request_key"] == f"retry:{attempts[0]['attempt_id']}"
    assert failed["approval"]["latest_attempt"]["retryable"] is True
    assert failed["mutation"]["allowed"] is True  # rollback finished: proposal editable again

    # the cause is fixed outside the proposal; retry through the human approve route
    (repo / "gate.ok").write_bytes(b"ok")
    resp = approve(client, tr2, failed["document"]["revision_no"], request_key=retry["request_key"])
    assert resp.status_code == 200, resp.text
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "approved"
    assert len(db_attempts.list_by_doc(tr2)) == 3
    # a double click on the same retry replays; it never creates a fourth attempt
    again = approve(client, tr2, failed["document"]["revision_no"], request_key=retry["request_key"])
    assert again.status_code == 200, again.text
    rows = db_attempts.list_by_doc(tr2)
    assert len(rows) == 3 and rows[0]["state"] == "succeeded"
    assert [r["approval_round"] for r in reversed(rows)] == [1, 2, 3]  # history preserved
    ledger = db_ledger.list_by_group(group["group_id"])
    assert [r["commit_sha"] for r in ledger] == [git_head(repo)]
    assert effective_head(tr2)["type"] == "T"


GATE_DIRTY = ("python -c \"import pathlib,sys; pathlib.Path('stray.txt').write_text('x'); "
              "sys.exit(4)\"")


def register_command(command: str) -> None:
    from modules.flow_gate.db import project_test_commands as db_commands
    from modules.flow_gate.services import test_command_service
    db_commands.insert(PROJECT, test_command_service.normalize_command(command),
                       "TR2 gate", "manual", None, verified_os=os.name)


def ready_tr2(code: str, spec: dict | None = None) -> tuple[dict, str, str]:
    group = make_group(code)
    t2 = auto_approved_t2(group)
    return group, t2, submit_tr2(group, t2, spec)


def test_drift_is_not_retryable_and_a_new_revision_regrounds(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    repo = env["repo"]
    client = api()
    group, t2, tr2 = ready_tr2("0003")
    (repo / "gate.ok").write_bytes(b"ok")
    # another change lands on the target after the baseline was taken
    (repo / "src" / "app.txt").write_bytes(b"greeting = 'hello'\ncount = 2\n")
    git(repo, "commit", "-am", "concurrent change")
    drifted = view(client, tr2)
    assert drifted["derived"]["live_precheck"]["drift"] is True
    resp = approve(client, tr2, 1, request_key="human:drift")
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_source_drift", resp.text
    rows = db_attempts.list_by_doc(tr2)
    assert [(r["state"], r["error_code"]) for r in rows] == [("failed", "tr2_source_drift")]
    assert rows[0]["backup_bundle_id"] is None  # stopped before any source mutation
    retry = view(client, tr2)["approval"]["retry"]
    assert retry["allowed"] is False and retry["reason"] == "not_retryable"
    # re-grounding = saving a new revision; the server recomputes the baseline
    body = view(client, tr2)["body"]
    resp = client.put(f"/api/v1/documents/{tr2}/tr2",
                      json={"expected_revision": 1, "body": {k: body[k] for k in (
                          "tr2_version", "source_t2_doc_id", "edit_spec")}})
    assert resp.status_code == 200, resp.text
    assert view(client, tr2)["derived"]["live_precheck"]["drift"] is False
    assert approve(client, tr2, 2, request_key="human:regrounded").status_code == 200
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hi'\ncount = 2\n"
    assert len(db_attempts.list_by_doc(tr2)) == 2


def test_source_lock_is_retryable_and_creates_no_attempt(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    client = api()
    group, t2, tr2 = ready_tr2("0004")
    with hold_group_lock(PROJECT, group["group_id"]):
        resp = approve(client, tr2, 1, request_key="human:locked")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "tr2_source_locked" and resp.json()["retryable"] is True
        assert db_attempts.list_by_doc(tr2) == []


def test_proposal_is_read_only_while_applying(env, monkeypatch):
    from modules.flow_gate.documents import tr2_approval_service as approval
    from modules.flow_gate.documents import tr2_service
    repo = env["repo"]
    client = api()
    group, t2, tr2 = ready_tr2("0005")
    (repo / "gate.ok").write_bytes(b"ok")
    seen = {}
    real_validation = approval._run_validation

    def validation_with_concurrent_edits(root, commands, attempt_id):
        # a reviewer's screen and an AI save both try to change the proposal mid-approval
        seen["view"] = view(client, tr2)
        seen["http"] = client.post(f"/api/v1/documents/{tr2}/tr2/items",
                                   json={"expected_revision": 1, "collection": "edits",
                                         "item": CREATED})
        try:
            tr2_service.save(tr2, tr2_body(t2), actor=USER, expected_revision=1)
        except tr2_service.Tr2ValidationError as exc:
            seen["writer"] = exc.code
        return real_validation(root, commands, attempt_id)

    monkeypatch.setattr(approval, "_run_validation", validation_with_concurrent_edits)
    assert approve(client, tr2, 1, request_key="human:applying").status_code == 200
    assert seen["view"]["mutation"] == {"allowed": False, "reason": "applying"}
    assert seen["view"]["approval"]["latest_attempt"]["phase"] == "validation"
    assert seen["view"]["approval"]["retry"]["reason"] == "in_progress"
    assert seen["http"].status_code == 409 and seen["http"].json()["code"] == "tr2_in_progress"
    assert seen["writer"] == "tr2_in_progress"


def test_recovery_required_is_distinct_from_rollback_and_blocks_everything(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    repo = env["repo"]
    register_command(GATE_DIRTY)
    client = api()
    group, t2, tr2 = ready_tr2("0006", edit_spec(gate={"commands": [GATE_DIRTY],
                                                       "apply": False}))
    resp = approve(client, tr2, 1, request_key="human:dirty")
    # the gate left an untracked file: restore cannot prove a clean baseline
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_recovery_required", resp.text
    row = db_attempts.list_by_doc(tr2)[0]
    assert (row["state"], row["phase"], row["error_code"]) == (
        "recovery_required", "rollback", "tr2_recovery_required")
    stuck = view(client, tr2)
    assert stuck["mutation"] == {"allowed": False, "reason": "recovery_required"}
    assert stuck["approval"]["retry"]["allowed"] is False
    assert stuck["approval"]["retry"]["reason"] == "recovery_required"
    blocked = client.put(f"/api/v1/documents/{tr2}/tr2",
                         json={"expected_revision": 1, "body": tr2_body(t2)})
    assert blocked.status_code == 409 and blocked.json()["code"] == "tr2_spec_immutable"
    again = approve(client, tr2, 1, request_key="human:dirty-2")
    assert again.status_code == 409 and again.json()["code"] == "tr2_recovery_required"
    assert len(db_attempts.list_by_doc(tr2)) == 1
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "pending_review"
    assert (repo / "stray.txt").exists()  # left for a person to inspect


def test_restart_recovers_a_stale_attempt_before_the_retry_runs(env, monkeypatch):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.documents import tr2_approval_service as approval
    repo = env["repo"]
    client = api()
    group, t2, tr2 = ready_tr2("0007")
    (repo / "gate.ok").write_bytes(b"ok")
    base_head = git_head(repo)
    real_validation = approval._run_validation

    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt("process died during validation")  # not an Exception

    monkeypatch.setattr(approval, "_run_validation", crash)
    with pytest.raises(KeyboardInterrupt):
        approve(client, tr2, 1, request_key="human:crash")
    monkeypatch.setattr(approval, "_run_validation", real_validation)
    row = db_attempts.list_by_doc(tr2)[0]
    assert (row["state"], row["phase"]) == ("in_progress", "validation")
    assert (repo / "src" / "app.txt").read_bytes().startswith(b"greeting = 'hi'")  # applied
    # a live attempt is not retryable; only a stale one is recovered
    assert view(client, tr2)["approval"]["retry"]["reason"] == "in_progress"
    db_attempts.update(row["attempt_id"], heartbeat_at="2000-01-01T00:00:00+00:00")
    stale = view(client, tr2)["approval"]["retry"]
    assert stale["allowed"] is True and stale["mode"] == "recover_stale"
    resp = approve(client, tr2, 1, request_key=stale["request_key"])
    assert resp.status_code == 200, resp.text
    rows = db_attempts.list_by_doc(tr2)
    assert [(r["state"], r["result_code"]) for r in reversed(rows)] == [
        ("failed", "recovered_rollback"), ("succeeded", "succeeded")]
    assert git(repo, "rev-parse", "HEAD^").strip() == base_head  # exactly one TR2 commit
    assert git(repo, "status", "--porcelain") == ""


def test_time_machine_revert_and_reapply_move_source_history(env):
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.services import tr2_file_policy, tr_commit_service
    repo = env["repo"]
    client = api()
    group, t2, tr2 = ready_tr2("0008")
    (repo / "gate.ok").write_bytes(b"ok")
    assert approve(client, tr2, 1, request_key="human:tm").status_code == 200
    assert view(client, tr2)["history"]["source_history_state"] == "aligned"
    assert "src/app.txt" in tr2_file_policy.managed_paths(group["group_id"])

    tr_commit_service.cancel_tr_commits(group["group_id"], [tr2])
    row = [r for r in db_ledger.list_by_group(group["group_id"]) if r["doc_id"] == tr2][0]
    assert row["state"] == "canceled" and row["cancel_commit"], row
    assert "src/app.txt" not in tr2_file_policy.managed_paths(group["group_id"])
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    reverted = view(client, tr2)
    assert reverted["history"]["source_history_state"] == "restore_pending"
    assert reverted["mutation"]["allowed"] is False  # still the approved terminal revision

    tr_commit_service.reapply_tr_commits(group["group_id"], [tr2])
    assert "src/app.txt" in tr2_file_policy.managed_paths(group["group_id"])
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    assert view(client, tr2)["history"]["source_history_state"] == "aligned"


def test_sse_sources_for_save_attempt_phases_and_review_status(env, monkeypatch):
    """What the client refresh hangs off (T0028 §B-2): every event is emitted by the
    real save/approval path, project-wide, naming the TR2 document."""
    import modules.flow_gate.api.v1.events.publisher as publisher
    from modules.flow_gate.api.v1.events.event_types import EventType
    repo = env["repo"]
    client = api()
    group, t2, tr2 = ready_tr2("0009")
    events = []

    async def capture_async(event):
        events.append(event)
        return 1

    monkeypatch.setattr(publisher, "broadcast_event_threadsafe",
                        lambda event: events.append(event) or 1)
    monkeypatch.setattr(publisher, "broadcast_event", capture_async)

    resp = client.post(f"/api/v1/documents/{tr2}/tr2/items",
                       json={"expected_revision": 1, "collection": "edits", "item": CREATED})
    assert resp.status_code == 200, resp.text
    saved = [e for e in events if e.event_type == EventType.DOCUMENT_EXPLORER_REFRESH]
    assert [(e.audience, e.doc_id, e.payload["revision_no"]) for e in saved] == [("*", tr2, 2)]

    events.clear()
    (repo / "gate.ok").write_bytes(b"ok")
    assert approve(client, tr2, 2, request_key="human:sse").status_code == 200
    phases = [(e.payload["state"], e.payload["phase"]) for e in events
              if e.event_type == EventType.GROUP_VIEW_REFRESH
              and e.payload.get("reason") == "tr2_approval_changed"]
    assert phases == [("in_progress", "created"), ("in_progress", "precheck"),
                      ("in_progress", "snapshot"), ("in_progress", "apply"),
                      ("in_progress", "validation"), ("in_progress", "commit"),
                      ("in_progress", "finalize"), ("succeeded", "complete")]
    assert all(e.audience == "*" and e.doc_id == tr2 and e.project == PROJECT
               for e in events if e.event_type == EventType.GROUP_VIEW_REFRESH)
    review = [e for e in events if e.event_type == EventType.DOC_REVIEW_STATUS_CHANGED]
    assert [(e.payload["doc_id"], e.payload["next_status"]) for e in review] == [
        (tr2, "approved")]
