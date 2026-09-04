"""flowgate.default.0523 T0004 -- worker-token attachment discovery/read/copy.

Root cause (0523 NR0003): the attachment subsystem
(``modules/flow_gate/documents/attachments``) was wired only into the Console
``/documents/...`` surface. The worker surface (``/api/v1/document/...``) had no
discovery, read or copy-to-source bridge at all -- an AI worker could not learn an
attachment existed, let alone read or use it.

This file exercises the three new worker endpoints end to end over real HTTP
(``TestClient`` + the actual ``document_routes.router``), against a real migrated
sqlite schema and a real attachment file on disk -- the same harness style as
``test_document_attachments_0060.py`` and ``test_document_query_api_0370.py``. Auth
is stubbed at the ``token_service.verify`` boundary (the same pattern
``test_document_query_api_0370.py``'s inbox tests use): the endpoint itself, and all
of tool_registry's real kind judgment, still run unstubbed.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

PROJECT_ID = "flowgate"
GROUP_ID = "flowgate.default.0523"
DOC_ID = "flowgate.default.0523.0001-R"
MISSING_DOC_ID = "flowgate.default.0523.9999-R"

# A second, unrelated group -- used only by the wrong-document-scope test (T0004 s.24/s.36.8).
OTHER_GROUP_ID = "flowgate.default.0599"
OTHER_DOC_ID = "flowgate.default.0599.0001-R"

# A sibling document in DOC_ID's OWN group -- proves the scope check is group-wide,
# not doc_ref-exact (T0004 s.24's "token/group A -> doc/group A attachments -> allowed").
SIBLING_DOC_ID = "flowgate.default.0523.0002-R"


# ── DB / storage fixtures (mirrors test_document_attachments_0060.py) ──────────

@pytest.fixture(scope="module")
def attachments_db(migrated_sqlite_db):
    return migrated_sqlite_db(
        "test_worker_attachments_0523.db",
        seed_sql=f"""
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('{PROJECT_ID}','FlowGate',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,
                                    first_login_required,created_at,updated_at)
            VALUES('usr_worker','worker','worker@test.com','pw',1,1,0,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO project_settings(project_id,group_structure,digits_group,
                                    digits_sub_group,digits_type,updated_at,branch)
            VALUES('{PROJECT_ID}',1,4,3,4,datetime('now'),'main');
        INSERT OR IGNORE INTO groups(group_id,project_id,module,title,status,created_at,updated_at)
            VALUES('{GROUP_ID}','{PROJECT_ID}','default','worker attachments',
                   'in_progress',datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,
                                    file_path,status,created_at,updated_at)
            VALUES('{DOC_ID}','{PROJECT_ID}','default','{GROUP_ID}','R',1,'attachment target',
                   'documents/{PROJECT_ID}/main/default/0523/0001-R_document.md',
                   'open',datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO groups(group_id,project_id,module,title,status,created_at,updated_at)
            VALUES('{OTHER_GROUP_ID}','{PROJECT_ID}','default','unrelated group',
                   'in_progress',datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,
                                    file_path,status,created_at,updated_at)
            VALUES('{OTHER_DOC_ID}','{PROJECT_ID}','default','{OTHER_GROUP_ID}','R',1,'unrelated document',
                   'documents/{PROJECT_ID}/main/default/0599/0001-R_document.md',
                   'open',datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,
                                    file_path,status,created_at,updated_at)
            VALUES('{SIBLING_DOC_ID}','{PROJECT_ID}','default','{GROUP_ID}','R',2,'sibling document',
                   'documents/{PROJECT_ID}/main/default/0523/0002-R_document.md',
                   'open',datetime('now'),datetime('now'));
        """,
    )


class _MockDB:
    """The sqloader-shaped DB surface FlowGateStore._db expects, over one sqlite file.

    Unlike test_document_attachments_0060.py's bespoke ``_Store`` (attachment code only
    needs raw _execute/_fetch_one/_fetch_all), the full document GET pulls in
    q_service/reviews/test-run readers that go through ``FlowGateStore._sql(key)`` —
    so this test subclasses the real ``FlowGateStore`` instead, the same harness
    test_document_query_api_0370.py uses for the same reason.
    """

    def __init__(self, db_path: str) -> None:
        import sqlite3

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
        yield self

    def close(self):
        self._conn.close()


@pytest.fixture
def env(attachments_db, tmp_path, monkeypatch):
    from modules.flow_gate.db import connection as conn_mod

    storage_root = tmp_path / "storage"
    group_dir = storage_root / "documents" / PROJECT_ID / "main" / "default" / "0523"
    group_dir.mkdir(parents=True)
    (group_dir / "0001-R_document.md").write_text("# body", encoding="utf-8")
    other_group_dir = storage_root / "documents" / PROJECT_ID / "main" / "default" / "0599"
    other_group_dir.mkdir(parents=True)
    (other_group_dir / "0001-R_document.md").write_text("# other body", encoding="utf-8")
    (group_dir / "0002-R_document.md").write_text("# sibling body", encoding="utf-8")
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    mock_db = _MockDB(attachments_db)

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    store = _PatchedStore()
    original_store = conn_mod.STORE
    conn_mod.STORE = store
    store._execute("DELETE FROM attachments")
    store._execute("DELETE FROM group_ai_leases")
    try:
        yield {"store": store, "storage_root": storage_root, "group_dir": group_dir}
    finally:
        conn_mod.STORE = original_store


@pytest.fixture
def src_root(tmp_path, monkeypatch):
    """A stand-in for the group worktree. There is no git repo in a unit test."""
    from modules.flow_gate.services import git_service

    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda *_: (root, "worktree"))
    monkeypatch.setattr(git_service, "base_src_root", lambda *_a, **_k: root)
    return root


class FakePart:
    def __init__(self, filename, data: bytes) -> None:
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


ACTOR = {"user_id": "usr_worker"}


def upload(parts, doc_id: str = DOC_ID):
    from modules.flow_gate.documents.attachments import upload_attachments

    return asyncio.run(upload_attachments(doc_id, parts, ACTOR, None))


# ── HTTP client + fake worker token ─────────────────────────────────────────────

def _client() -> TestClient:
    from modules.flow_gate.api.v1.document_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _token(action_scope: str, **extra) -> dict:
    token = {
        "token_id": "tok_test_0523",
        "issued_to": "usr_worker",
        "project": PROJECT_ID,
        "group_id": GROUP_ID,
        "doc_ref": DOC_ID,
        "action_scope": action_scope,
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    token.update(extra)
    return token


@contextmanager
def _auth_as(token_rec: dict):
    """Stub the auth boundary only -- token verification and the RBAC permission
    check -- so the endpoint itself and tool_registry's real kind judgment run
    unstubbed. Mirrors test_document_query_api_0370.py's inbox-test pattern of
    patching token_service.verify directly."""
    from modules.flow_gate.services import auth_outbound

    with patch.object(auth_outbound.token_service, "verify", return_value=token_rec), \
         patch.object(auth_outbound, "has_permission", return_value=True):
        yield


def _get(path: str, token_rec: dict):
    with _auth_as(token_rec):
        return _client().get(path, headers={"Authorization": "Bearer x"})


def _post(path: str, token_rec: dict, json: dict | None = None):
    with _auth_as(token_rec):
        return _client().post(path, headers={"Authorization": "Bearer x"}, json=json or {})


# ── document GET discovery (T0004 s.5 / s.36.1 / s.36.2) ────────────────────────

def test_document_get_reports_attachment_metadata(env):
    upload([FakePart("config.json", b'{"a":1}')])

    resp = _get(f"/api/v1/document/{DOC_ID}", _token("new"))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["attachments"] == [{
        "filename": "config.json",
        "size": 7,
        "content_type": "application/json",
        "uploaded_at": body["attachments"][0]["uploaded_at"],
    }]
    assert "content" in body        # additive -- the existing body key is untouched


def test_document_get_reports_empty_attachments_list_when_none_uploaded(env):
    resp = _get(f"/api/v1/document/{DOC_ID}", _token("new"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["attachments"] == []


def test_document_get_propagates_an_attachment_service_failure_instead_of_reporting_empty(env):
    """T0004 s.19/s.38.1: ``attachments == []`` must mean a confirmed-empty list, never
    an unknown one. Automated-review regression: ``_attachment_briefs`` used to catch
    every exception from ``list_attachments()`` and return ``[]``, making a registry/
    storage failure indistinguishable from a document that genuinely has no
    attachments. It must now surface as an error instead."""
    from modules.flow_gate.documents import attachments

    upload([FakePart("config.json", b"{}")])
    token = _token("new")

    with patch.object(attachments, "list_attachments", side_effect=RuntimeError("boom")):
        resp = _get(f"/api/v1/document/{DOC_ID}", token)

    assert resp.status_code == 500, resp.text
    assert resp.json()["error"]["code"] == "ATTACHMENT_OPERATION_FAILED"


# ── permission gating (T0004 s.6 / s.36.3-36.7) ──────────────────────────────────
#
# kind_for_step (tool_registry.py) is the real, unstubbed judge here:
#   action_scope "review"                -> "read"       (N/NR/review workers)
#   action_scope "resolve_base_dirty"    -> "read_write"  (TR/TS/TSR-class mutation)
#   action_scope "workflow_sequence_edit"-> "none"        (not in {new, edit}, no read carve-out)

def test_read_kind_can_list_and_read_but_copy_is_forbidden(env):
    upload([FakePart("notes.txt", b"hello world")])
    token = _token("review")

    listed = _get(f"/api/v1/document/{DOC_ID}/attachments", token)
    assert listed.status_code == 200, listed.text
    assert [a["filename"] for a in listed.json()["attachments"]] == ["notes.txt"]

    read = _get(f"/api/v1/document/{DOC_ID}/attachments/notes.txt/read", token)
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "hello world"

    copy = _post(
        f"/api/v1/document/{DOC_ID}/attachments/notes.txt/copy",
        token, json={"target_path": "imports/notes.txt"},
    )
    assert copy.status_code == 403, copy.text


def test_none_kind_is_denied_list_read_and_copy(env):
    upload([FakePart("notes.txt", b"x")])
    token = _token("workflow_sequence_edit")

    assert _get(f"/api/v1/document/{DOC_ID}/attachments", token).status_code == 403
    assert _get(f"/api/v1/document/{DOC_ID}/attachments/notes.txt/read", token).status_code == 403
    assert _post(
        f"/api/v1/document/{DOC_ID}/attachments/notes.txt/copy",
        token, json={"target_path": "imports/notes.txt"},
    ).status_code == 403


def test_read_write_kind_can_copy_into_the_callers_own_group_worktree(env, src_root):
    """T0004 s.6/s.34/s.36.6/s.36.11 -- destination is the TOKEN's own group, and the
    base checkout is never touched."""
    upload([FakePart("schema.json", b"{}")])
    token = _token("resolve_base_dirty")

    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
        token, json={"target_path": "assets/schema.json"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["destination"]["group_id"] == GROUP_ID
    assert (src_root / "assets" / "schema.json").read_bytes() == b"{}"
    assert body["content_sha256"] == hashlib.sha256(b"{}").hexdigest()


def test_copy_ignores_a_request_supplied_group_id(env, src_root, tmp_path, monkeypatch):
    """T0004 s.12/s.34 -- unlike the console contract, the worker body carries no
    group_id field at all; even if a caller adds one, only the token's own group is
    ever used as the destination."""
    from modules.flow_gate.services import git_service

    other_root = tmp_path / "other_worktree"
    other_root.mkdir()

    def fake_effective(_project, group_id):
        if group_id == GROUP_ID:
            return src_root, "worktree"
        return other_root, "worktree"

    monkeypatch.setattr(git_service, "effective_src_root_ex", fake_effective)

    upload([FakePart("schema.json", b"{}")])
    token = _token("resolve_base_dirty")

    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
        token, json={"target_path": "assets/schema.json", "group_id": "flowgate.default.9999"},
    )
    assert resp.status_code == 201, resp.text
    assert (src_root / "assets" / "schema.json").exists()
    assert not (other_root / "assets" / "schema.json").exists()


def test_groupless_read_write_token_cannot_copy_into_the_base_checkout(env, tmp_path, monkeypatch):
    """Automated-review regression: kind_for_token judges purely on action_scope
    (tool_registry.kind_for_step), so a token with an exact doc_ref match but NO
    group_id still comes back "read_write". Passing that token's group_id (None)
    straight through used to let resolve_copy_root fall through to its group_id-absent
    branch (service.py:656-674) -- the Console's own group_id:null contract
    (documents.py:2983) -- and write into the base checkout instead of any group
    worktree. The route must refuse before copy_to_source ever resolves a root."""
    from modules.flow_gate.services import git_service

    base_root = tmp_path / "base_checkout"
    base_root.mkdir()
    monkeypatch.setattr(git_service, "base_src_root", lambda *_a, **_k: base_root)

    upload([FakePart("schema.json", b"{}")])
    token = _token("resolve_base_dirty", group_id=None)  # doc_ref=DOC_ID still matches

    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
        token, json={"target_path": "assets/schema.json"},
    )

    assert resp.status_code == 403, resp.text
    assert not (base_root / "assets" / "schema.json").exists()


# ── the group-lease principal bug (0523 T0004) ───────────────────────────────────
#
# attachments.assert_mutable used to build a MutationPrincipal via human_principal(actor)
# unconditionally. A worker mid-run always holds an active group_ai_leases row for its
# own group, and assert_group_mutation_allowed 423s any non-"worker"-kind principal
# against an active lease -- so every worker copy would have failed against its OWN
# lease. assert_mutable must recognise a worker-shaped actor (token_id + action_scope)
# and build a worker_principal instead.

def test_copy_succeeds_under_the_workers_own_active_group_lease(env, src_root):
    from modules.flow_gate.services import mutation_policy as policy

    upload([FakePart("schema.json", b"payload")])
    lease = {
        "group_id": GROUP_ID, "run_id": "aiv_run_0523", "token_id": "tok_test_0523",
        "action_scope": "resolve_base_dirty",
    }
    with patch.object(policy.db_leases, "get_active", lambda gid: dict(lease) if gid == GROUP_ID else None), \
         patch.object(policy.db_leases, "heartbeat", lambda *a, **k: None):
        token = _token("resolve_base_dirty", ai_run_id="aiv_run_0523")

        resp = _post(
            f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
            token, json={"target_path": "assets/schema.json"},
        )

    assert resp.status_code == 201, resp.text
    assert (src_root / "assets" / "schema.json").read_bytes() == b"payload"


def test_copy_is_still_blocked_for_a_lease_owned_by_a_different_run(env, src_root):
    """The fix narrows the false-positive, it does not remove the real guard: a lease
    genuinely held by someone else must still 409 the copy."""
    from modules.flow_gate.services import mutation_policy as policy

    upload([FakePart("schema.json", b"payload")])
    lease = {
        "group_id": GROUP_ID, "run_id": "aiv_other_run", "token_id": "tok_other",
        "action_scope": "resolve_base_dirty",
    }
    with patch.object(policy.db_leases, "get_active", lambda gid: dict(lease) if gid == GROUP_ID else None), \
         patch.object(policy.db_leases, "heartbeat", lambda *a, **k: None):
        token = _token("resolve_base_dirty", ai_run_id="aiv_run_0523")

        resp = _post(
            f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
            token, json={"target_path": "assets/schema.json"},
        )

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_MUTABLE"


# ── not-found / format guards shared with the rest of document_routes.py ────────

def test_attachment_routes_404_for_a_missing_document(env):
    token = _token("review", doc_ref=MISSING_DOC_ID)
    assert _get(f"/api/v1/document/{MISSING_DOC_ID}/attachments", token).status_code == 404
    assert _get(f"/api/v1/document/{MISSING_DOC_ID}/attachments/x/read", token).status_code == 404
    assert _post(
        f"/api/v1/document/{MISSING_DOC_ID}/attachments/x/copy",
        _token("resolve_base_dirty", doc_ref=MISSING_DOC_ID),
        json={"target_path": "a.txt"},
    ).status_code == 404


# ── document scope security (T0004 s.24 / s.36.8) ────────────────────────────────
#
# A token bound to DOC_ID/GROUP_ID must not reach an unrelated document's attachments,
# even a valid one that simply belongs to a different group -- neither its doc_ref nor
# its group_id line up with OTHER_DOC_ID/OTHER_GROUP_ID.

def test_wrong_document_scope_is_denied_for_list_read_and_copy(env):
    upload([FakePart("secret.txt", b"classified")], doc_id=OTHER_DOC_ID)

    list_token = _token("review")  # doc_ref=DOC_ID, group_id=GROUP_ID
    assert _get(f"/api/v1/document/{OTHER_DOC_ID}/attachments", list_token).status_code == 403

    read_token = _token("review")
    assert _get(
        f"/api/v1/document/{OTHER_DOC_ID}/attachments/secret.txt/read", read_token
    ).status_code == 403

    copy_token = _token("resolve_base_dirty")
    assert _post(
        f"/api/v1/document/{OTHER_DOC_ID}/attachments/secret.txt/copy",
        copy_token, json={"target_path": "imports/secret.txt"},
    ).status_code == 403


def test_a_tokens_own_group_may_read_a_sibling_documents_attachment(env):
    """The scope check is group-wide, not doc_ref-exact (T0004 s.24's group-A example):
    a token bound to DOC_ID may still list a DIFFERENT document in its OWN group."""
    upload([FakePart("shared.txt", b"ok")], doc_id=SIBLING_DOC_ID)

    token = _token("review")  # doc_ref=DOC_ID, group_id=GROUP_ID -- NOT SIBLING_DOC_ID
    resp = _get(f"/api/v1/document/{SIBLING_DOC_ID}/attachments", token)
    assert resp.status_code == 200, resp.text
    assert [a["filename"] for a in resp.json()["attachments"]] == ["shared.txt"]


def test_document_get_hides_attachments_for_a_token_outside_the_documents_scope(env):
    """T0004 s.24 applies to the general document GET's attachment discovery too, not
    just the three dedicated endpoints: a token whose doc_ref/group_id don't match
    OTHER_DOC_ID must not learn OTHER_DOC_ID's attachment filenames this way either.
    The document's own content (unrelated to attachments) still comes back 200 --
    only the discovery side-channel is closed."""
    upload([FakePart("secret.txt", b"classified")], doc_id=OTHER_DOC_ID)

    token = _token("review")  # doc_ref=DOC_ID, group_id=GROUP_ID -- NOT OTHER_DOC_ID/OTHER_GROUP_ID
    resp = _get(f"/api/v1/document/{OTHER_DOC_ID}", token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["attachments"] == []


def test_document_get_reports_attachments_for_a_user_jwt(env):
    """Console/human callers authenticate via user JWT, not a doc-scoped worker
    token -- T0004 s.24's scope binding is a worker-token concept, so a user JWT
    (no doc_ref/group_id to match) must still see attachment metadata."""
    upload([FakePart("config.json", b'{"a":1}')])

    resp = _get(
        f"/api/v1/document/{DOC_ID}",
        {"issued_to": "usr_worker", "project": PROJECT_ID, "_is_user_jwt": True},
    )
    assert resp.status_code == 200, resp.text
    assert [a["filename"] for a in resp.json()["attachments"]] == ["config.json"]


# ── copy target_path guards (T0004 s.36.9 / s.36.10) ─────────────────────────────
# _validate_target_path runs before any worktree/git resolution, so these reject on
# format alone -- no src_root fixture, no prior upload needed.

def test_copy_path_escape_is_rejected(env):
    token = _token("resolve_base_dirty")
    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/notes.txt/copy",
        token, json={"target_path": "../../outside"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_PATH"


@pytest.mark.parametrize("target_path", ["/tmp/x", "C:\\temp\\x"])
def test_copy_absolute_target_path_is_rejected(env, target_path):
    token = _token("resolve_base_dirty")
    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/notes.txt/copy",
        token, json={"target_path": target_path},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_PATH"


# ── copy lands as a real, git-visible source change (T0004 s.36.12 / s.36.13) ────
#
# Not a mock: a real git repo backs the worktree, the same harness
# test_tr_scope_0299.py's work_repo fixture uses for collect_scope_changes. This proves
# the copied file shows up as `git status` dirty AND is the exact mechanism the TR
# changed-files/commit flow (tr_scope_service -> git_service.collect_scope_changes)
# reads to decide what a TR's commit will contain -- not just that a file landed on disk.

def _git(repo, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def git_src_root(tmp_path, monkeypatch):
    import shutil as _shutil

    from modules.flow_gate.services import git_service

    if _shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = tmp_path / "git_worktree"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "kept.py").write_text("# kept\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda *_: (repo, "worktree"))
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: {"branch": "main"})
    monkeypatch.setattr(git_service.db_git, "get_config", lambda pid: {"base_branch": "main"})
    return repo


def test_copy_appears_git_dirty_and_in_the_trs_scope_changes(env, git_src_root):
    from modules.flow_gate.services import git_service

    upload([FakePart("schema.json", b"{}")])
    token = _token("resolve_base_dirty")

    resp = _post(
        f"/api/v1/document/{DOC_ID}/attachments/schema.json/copy",
        token, json={"target_path": "assets/schema.json"},
    )
    assert resp.status_code == 201, resp.text

    status = _git(git_src_root, "status", "--porcelain", "--untracked-files=all")
    assert "assets/schema.json" in status
    assert status.strip().startswith("??")  # untracked -- git sees it as dirty

    scope = git_service.collect_scope_changes(PROJECT_ID, GROUP_ID)
    assert scope["available"] is True
    assert "assets/schema.json" in scope["paths"]


# ── binary read regression (T0004 s.36.15) ────────────────────────────────────────

def test_binary_attachment_read_returns_base64_content(env):
    payload = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03binary-body"
    upload([FakePart("image.png", payload)])
    token = _token("review")

    resp = _get(f"/api/v1/document/{DOC_ID}/attachments/image.png/read", token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "binary"
    assert body["content_encoding"] == "base64"
    import base64 as _b64
    assert _b64.b64decode(body["content"]) == payload


# ── help/tools advertises the attachment operations (T0004 s.17 / s.38.14) ──────
# The 2026-09-04 rejections of this TR were both the same finding: a worker asked to
# control a document's attachments called GET /help/tools -- the surface its own ment
# tells it to call first -- saw only the /remote/* source tools, and concluded that
# attachments cannot be controlled at all. These tests pin the second catalog that
# answer now carries, and pin it AGAINST the real worker routes so the advertisement
# can never drift away from what the endpoints actually do.

def _help_client() -> TestClient:
    from modules.flow_gate.api.v1.help_routes import router as help_router

    app = FastAPI()
    app.include_router(help_router)
    return TestClient(app)


def _help_get(path: str, token_rec: dict):
    with _auth_as(token_rec):
        return _help_client().get(path, headers={"Authorization": "Bearer x"})


#: action_scope -> the effective source-access kind tool_registry judges for it. All three
#: are judged from the scope alone (no step-type DB lookup), so the kind under test is the
#: real one rather than a stub.
_SCOPE_KIND = {
    "resolve_base_dirty": "read_write",
    "review": "read",
    "workflow_sequence_edit": "none",
}


@pytest.mark.parametrize("action_scope,kind", sorted(_SCOPE_KIND.items()))
def test_help_tools_answers_for_attachments_as_well_as_source_tools(env, action_scope, kind):
    from modules.flow_gate.services import tool_registry

    resp = _help_get("/api/v1/help/tools", _token(action_scope))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == kind

    block = body["document_attachments"]
    assert block["kind"] == kind
    assert [op["name"] for op in block["operations"]] == tool_registry.attachment_names(kind)
    assert block["available"] is bool(tool_registry.attachment_names(kind))

    # Everything this kind may NOT call is named with a reason, so a short (or empty)
    # operations list is never read as "attachments are unreachable from here".
    named = {op["name"] for op in block["operations"]} | {op["name"] for op in block["denied"]}
    assert named == set(tool_registry.ATTACHMENT_DISPLAY_ORDER)
    assert [op["name"] for op in block["absent"]] == list(tool_registry.ATTACHMENT_ABSENT_OPS)
    assert all(op["reason"] for op in block["denied"] + block["absent"])
    assert block["help_item_url"].endswith("/help/items/document_attachments")

    # The list-level notes say it too, for a worker that reads notes and skips keys.
    note_key = "attachments" if kind != "none" else "attachments_none"
    assert tool_registry.NOTES["ko"][note_key] in body["notes"]


def test_help_tools_read_kind_names_copy_as_denied_not_missing(env):
    body = _help_get("/api/v1/help/tools", _token("review")).json()
    block = body["document_attachments"]

    assert [op["name"] for op in block["operations"]] == ["attachment_list", "attachment_read"]
    assert [op["name"] for op in block["denied"]] == ["attachment_copy"]
    assert "read" in block["denied"][0]["reason"]


@pytest.mark.parametrize("name", ["attachment_list", "attachment_read", "attachment_copy"])
def test_help_tool_detail_serves_an_attachment_operation(env, name):
    resp = _help_get(f"/api/v1/help/tools/{name}", _token("resolve_base_dirty"))
    assert resp.status_code == 200, resp.text
    detail = resp.json()["tool"]

    assert detail["name"] == name
    assert detail["request_fields"]
    assert detail["errors"]
    assert detail["cautions"]
    assert detail["example_request"]["method"] == detail["method"]
    assert detail["example_request"]["url"].endswith(detail["path"])
    if name == "attachment_copy":
        body_fields = [f["name"] for f in detail["request_fields"] if f["in"] == "body"]
        assert body_fields == ["target_path"]
        assert detail["example_request"]["body"] == {"target_path": "assets/schema.json"}


def test_help_tool_detail_refuses_an_attachment_operation_this_kind_cannot_call(env):
    resp = _help_get("/api/v1/help/tools/attachment_copy", _token("review"))
    assert resp.status_code == 403, resp.text


def test_every_advertised_attachment_route_exists_on_the_worker_router():
    """The advertised method+path must be a route that is really mounted.

    A help entry pointing at a path nobody serves is worse than no entry: the worker
    calls it, gets 404, and goes back to believing attachments are unreachable.
    """
    from modules.flow_gate.api.v1.document_routes import router
    from modules.flow_gate.services import tool_registry

    mounted = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    for name in tool_registry.ATTACHMENT_DISPLAY_ORDER:
        method, path = tool_registry.ATTACHMENT_ROUTES[name]
        assert (method, f"/api/v1{path}") in mounted, name


@pytest.mark.parametrize("action_scope,kind", sorted(_SCOPE_KIND.items()))
def test_what_help_advertises_is_exactly_what_the_worker_routes_allow(
    env, src_root, action_scope, kind
):
    """Executable parity: every advertised operation really answers, and every
    operation help calls denied really answers 403 (T0004 s.6/s.25)."""
    from modules.flow_gate.services import tool_registry

    upload([FakePart("parity.txt", b"parity")])
    allowed = set(tool_registry.attachment_names(kind))
    token = _token(action_scope)
    calls = {
        "attachment_list": lambda: _get(f"/api/v1/document/{DOC_ID}/attachments", token),
        "attachment_read": lambda: _get(
            f"/api/v1/document/{DOC_ID}/attachments/parity.txt/read", token
        ),
        "attachment_copy": lambda: _post(
            f"/api/v1/document/{DOC_ID}/attachments/parity.txt/copy",
            token,
            {"target_path": f"advertised/{action_scope}.txt"},
        ),
    }
    for name, call in calls.items():
        resp = call()
        if name in allowed:
            assert resp.status_code != 403, f"{name} is advertised but answers 403: {resp.text}"
        else:
            assert resp.status_code == 403, f"{name} is not advertised but answers {resp.status_code}"


def test_the_attachments_help_item_reads_its_permissions_from_the_same_judge(env):
    """The /help/items manual and /help/tools must not keep two permission tables."""
    from modules.flow_gate.services import help_catalog, tool_registry

    table = help_catalog._attachment_permission_table()
    assert table == {
        kind: [name.replace("attachment_", "", 1) for name in tool_registry.attachment_names(kind)]
        for kind in ("read", "read_write", "none")
    }
    assert table["read"] == ["list", "read"]
    assert table["read_write"] == ["list", "read", "copy"]
    assert table["none"] == []
