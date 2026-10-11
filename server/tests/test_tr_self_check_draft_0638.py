"""0638 T#1: TR Self-check before the TR document exists (0003-NR §4 option A).

A TR(new) worker has no TR row yet. Its runs are owned by the issuing token
(``owner_token_id`` + ``draft_doc_ref``, ``tr_doc_id`` NULL) and are linked to the TR
when that token registers it (token consumption). The TR-bound edit/review contract is
unchanged; these tests pin the new path and its boundaries.
"""
from __future__ import annotations

import importlib
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import self_check_routes as routes
from modules.flow_gate.db import connection
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import tr_self_check_runs as runs_db
from modules.flow_gate.services import api_server_tools as tools
from modules.flow_gate.services import token_service
from modules.flow_gate.services import tr_self_check_service as service

_MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
_MIGRATION_NAME = "141_tr_self_check_draft_owner.sql"

P, G, G2 = "p_0638", "g_0638_1", "g_0638_2"
SEQ_DOC, TR_DOC, OTHER_TR = "d_0638_b", "d_0638_tr", "d_0638_tr_other_group"
TOKEN = {"token_id": "tok_new_1", "action_scope": "new", "doc_ref": SEQ_DOC,
         "project": P, "group_id": G, "issued_to": "u_0638"}


# ── DB: migration + repository on a fully migrated SQLite ────────────────────

@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_tr_self_check_draft_0638.db")


@pytest.fixture
def store(db_path):
    from sqloader.sqlite3 import SQLiteWrapper

    real = object.__new__(connection.FlowGateStore)
    real._db = SQLiteWrapper(db_path)
    real._sq = None
    import modules.flow_gate.db.connection as _conn
    previous = _conn.get_store
    patched = [importlib.import_module(n) for n in _STORE_MODULES]
    for module in patched:
        module.get_store = lambda real=real: real
    now = datetime.now(timezone.utc).isoformat()
    real._execute("DELETE FROM tr_self_check_runs WHERE project_id = ?", [P])
    real._execute("INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
                  "VALUES ('u_0638', 'u0638', 'u0638@test', 'x', 1, ?, ?) ON CONFLICT(user_id) DO NOTHING", [now, now])
    real._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                  "VALUES (?, 'P 0638', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING", [P, now, now])
    for gid in (G, G2):
        real._execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                      "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?) ON CONFLICT(group_id) DO NOTHING", [gid, P, now, now])
    for seq, (doc_id, gid, type_code) in enumerate(((SEQ_DOC, G, "B"), (TR_DOC, G, "TR"), (OTHER_TR, G2, "TR")), 1):
        real._execute(
            "INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch,"
            " revision_no, rejection_history, created_at, updated_at) VALUES (?, ?, ?, 'default', ?, ?, 't', 'draft',"
            " 'main', 1, '[]', ?, ?) ON CONFLICT(doc_id) DO NOTHING", [doc_id, P, gid, type_code, seq, now, now])
    for token_id in ("tok_new_1", "tok_new_2"):
        _open_token(real, token_id)
    try:
        yield real
    finally:
        for module in patched:
            module.get_store = previous
        try:
            real._db.close()
        except Exception:
            pass


_STORE_MODULES = ("modules.flow_gate.db.connection", "modules.flow_gate.db.tr_self_check_runs",
                  "modules.flow_gate.db.tokens")


def _open_token(store, token_id):
    """An unconsumed, unrevoked TR(new) token row: a draft run's owner must be one."""
    now = datetime.now(timezone.utc).isoformat()
    store._execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, issued_to,"
        " created_at, expires_at) VALUES (?, ?, 'pep_0638', ?, ?, ?, 'new', 'u_0638', ?, '9999-12-31T00:00:00+00:00')"
        " ON CONFLICT(token_id) DO NOTHING", [token_id, f"hash_{token_id}", P, G, SEQ_DOC, now])
    store._execute("UPDATE tokens SET consumed_at = NULL, revoked_at = NULL WHERE token_id = ?", [token_id])


def _pending(group_id=G, tr_doc_id=None, owner="tok_new_1", draft_ref=SEQ_DOC, **kw):
    return runs_db.create_pending(P, group_id, tr_doc_id, "u_0638", "1", "pytest", ["-q"], "/bin/pytest",
                                  "pytest", "venv", ".", 60, ["PATH"], owner_token_id=owner,
                                  draft_doc_ref=draft_ref, **kw)


def _finish(run_id):
    runs_db.finish_completed(run_id, exit_code=0, timed_out=False, stdout_tail="", stderr_tail="")


def test_migration_141_exists_for_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        text = (_MIGRATIONS / dialect / _MIGRATION_NAME).read_text(encoding="utf-8")
        for column in ("owner_token_id", "draft_doc_ref", "linked_at"):
            assert column in text, (dialect, column)
    assert "ALTER COLUMN tr_doc_id DROP NOT NULL" in (_MIGRATIONS / "postgres" / _MIGRATION_NAME).read_text(encoding="utf-8")
    mysql = (_MIGRATIONS / "mysql" / _MIGRATION_NAME).read_text(encoding="utf-8")
    assert "MODIFY tr_doc_id VARCHAR(191) NULL" in mysql
    # MySQL ER 3823: no CHECK may name tr_doc_id (it carries ON DELETE CASCADE).
    assert "ADD CONSTRAINT" not in mysql


def test_migrated_schema_relaxes_tr_doc_id_and_adds_owner_columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(tr_self_check_runs)")}
        assert cols["tr_doc_id"][3] == 0  # notnull flag
        assert {"owner_token_id", "draft_doc_ref", "linked_at"} <= set(cols)
        fks = {(row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list(tr_self_check_runs)")}
        assert ("documents", "tr_doc_id") in fks
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(tr_self_check_runs)")}
        assert {"idx_selfcheck_doc_created", "idx_selfcheck_owner_created", "idx_selfcheck_finished"} <= indexes
    finally:
        conn.close()


def test_check_rejects_an_ownerless_unbound_row(db_path, store):
    now = datetime.now(timezone.utc).isoformat()
    with pytest.raises(Exception, match="(?i)check|constraint"):
        store._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, policy_version,"
            " program, args_json, resolved_executable_path, resolved_executable_name, executable_origin,"
            " cwd_relative, timeout_seconds, env_keys_json, status, created_at, updated_at)"
            " VALUES ('scr_bad', ?, ?, NULL, '1', 'pytest', '[]', 'x', 'pytest', 'venv', '.', 1, '[]',"
            " 'completed', ?, ?)", [P, G, now, now])
    with pytest.raises(ValueError):
        runs_db.create_pending(P, G, None, None, "1", "pytest", [], "x", "pytest", "venv", ".", 1, [])


def test_draft_run_is_owned_by_the_token_and_shares_the_group_active_slot(store):
    row = _pending()
    assert row["tr_doc_id"] is None and row["owner_token_id"] == "tok_new_1" and row["draft_doc_ref"] == SEQ_DOC
    assert runs_db.get_run_for_owner(row["self_check_run_id"], "tok_new_1")["self_check_run_id"] == row["self_check_run_id"]
    assert runs_db.get_run_for_owner(row["self_check_run_id"], "tok_other") is None
    assert [r["self_check_run_id"] for r in runs_db.list_by_owner("tok_new_1")] == [row["self_check_run_id"]]
    assert [r["self_check_run_id"] for r in runs_db.list_drafts_by_group(P, G)] == [row["self_check_run_id"]]
    # One active run per (project, group) whatever owns it: a TR-bound run must wait.
    with pytest.raises(runs_db.SelfCheckAlreadyRunningError):
        runs_db.create_pending(P, G, TR_DOC, "u_0638", "1", "pytest", [], "x", "pytest", "venv", ".", 1, [])
    _finish(row["self_check_run_id"])


def test_link_moves_only_this_tokens_unlinked_rows_of_the_same_group(store):
    mine = _pending()
    _finish(mine["self_check_run_id"])
    other_token = _pending(owner="tok_new_2")
    _finish(other_token["self_check_run_id"])
    other_group = _pending(group_id=G2)
    _finish(other_group["self_check_run_id"])

    assert runs_db.link_owner_runs("tok_new_1", TR_DOC, P, G) == 1
    linked = runs_db.get_run(mine["self_check_run_id"])
    assert linked["tr_doc_id"] == TR_DOC and linked["linked_at"] and linked["owner_token_id"] == "tok_new_1"
    assert [r["self_check_run_id"] for r in runs_db.list_by_doc(TR_DOC)] == [mine["self_check_run_id"]]
    assert runs_db.get_run_for_owner(mine["self_check_run_id"], "tok_new_1") is None  # no longer a draft
    assert runs_db.get_run(other_token["self_check_run_id"])["tr_doc_id"] is None
    assert runs_db.get_run(other_group["self_check_run_id"])["tr_doc_id"] is None
    # A linked row is never re-pointed.
    assert runs_db.link_owner_runs("tok_new_1", OTHER_TR, P, G) == 0
    assert runs_db.get_run(mine["self_check_run_id"])["tr_doc_id"] == TR_DOC


def test_linked_runs_follow_the_tr_document_cascade(store):
    row = _pending()
    _finish(row["self_check_run_id"])
    runs_db.link_owner_runs("tok_new_1", TR_DOC, P, G)
    with store.transaction():  # transaction() turns foreign_keys on, as in production
        store._execute("DELETE FROM documents WHERE doc_id = ?", [TR_DOC])
    assert runs_db.get_run(row["self_check_run_id"]) is None


def test_terminal_draft_rows_are_purged_like_any_other(store):
    row = _pending()
    _finish(row["self_check_run_id"])
    assert runs_db.purge_old_terminal("9999-12-31T00:00:00+00:00") >= 1
    assert runs_db.get_run(row["self_check_run_id"]) is None


# ── Registration vs. draft start race (rev1) ─────────────────────────────────
#
# The review scenario: a start request passes draft_target while its token is open, then
# pauses before create_pending; the TR registers meanwhile (token consumed, link_owner_runs
# sees no row). The insert that arrives after must not land as a permanently unlinked row.

def _register(token_id, tr_doc_id=TR_DOC):
    """What token_service.consume does for an inbox `new` TR: claim the token, then link."""
    assert db_tokens.consume_claim(token_id) is True
    return runs_db.link_owner_runs(token_id, tr_doc_id, P, G)


def _owned_rows(store, token_id):
    rows = store._fetch_all("SELECT self_check_run_id, tr_doc_id FROM tr_self_check_runs WHERE owner_token_id = ?",
                            [token_id]) or []
    return [dict(row) for row in rows]


def test_a_draft_insert_after_its_tokens_registration_is_refused_and_leaves_nothing(store):
    _open_token(store, "tok_race_late")
    assert _register("tok_race_late") == 0           # registration's link found nothing yet
    with pytest.raises(runs_db.DraftOwnerClosedError):
        _pending(owner="tok_race_late")              # the paused start request resumes
    assert _owned_rows(store, "tok_race_late") == []
    assert runs_db.get_active(P, G) is None          # the rolled-back row holds no active slot


@pytest.mark.parametrize("close", ["revoke", "missing"])
def test_a_draft_insert_needs_a_live_owner_token(store, close):
    token_id = f"tok_closed_{close}"
    if close == "revoke":
        _open_token(store, token_id)
        db_tokens.revoke(token_id)
    with pytest.raises(runs_db.DraftOwnerClosedError):
        _pending(owner=token_id)
    assert _owned_rows(store, token_id) == []


def test_a_tr_bound_insert_does_not_look_at_any_token(store):
    row = runs_db.create_pending(P, G, TR_DOC, "u_0638", "1", "pytest", [], "x", "pytest", "venv", ".", 1, [])
    assert row["tr_doc_id"] == TR_DOC and row["owner_token_id"] is None
    _finish(row["self_check_run_id"])


@pytest.fixture
def thread_stores(store, db_path, monkeypatch):
    """One SQLite connection per thread, like the live backend, for real interleavings."""
    from sqloader.sqlite3 import SQLiteWrapper

    local = threading.local()
    opened = []

    def _thread_store():
        current = getattr(local, "store", None)
        if current is None:
            current = object.__new__(connection.FlowGateStore)
            current._db, current._sq = SQLiteWrapper(db_path), None
            local.store = current
            opened.append(current)
        return current

    for name in _STORE_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "get_store", _thread_store)
    yield store
    for current in opened:
        try:
            current._db.close()
        except Exception:
            pass


def _run_threads(*targets):
    errors = []

    def _wrap(target):
        def _body():
            try:
                target()
            except BaseException as exc:  # surfaced to the test; a thread must not swallow it
                errors.append(exc)
        return _body

    threads = [threading.Thread(target=_wrap(t), daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "deadlock: a thread did not finish"
    return errors


def test_a_registration_racing_an_open_draft_insert_waits_and_links_it(thread_stores, monkeypatch):
    """The other order: the insert holds its owner check open; registration must not slip past it."""
    store = thread_stores
    _open_token(store, "tok_race_wait")
    inserted = threading.Event()
    original = runs_db._require_open_owner

    def _held_open(current, owner_token_id):
        original(current, owner_token_id)
        inserted.set()
        time.sleep(0.5)                              # transaction still open, row uncommitted

    monkeypatch.setattr(runs_db, "_require_open_owner", _held_open)
    created, linked = [], []

    def _start():
        created.append(_pending(owner="tok_race_wait"))

    def _registration():
        assert inserted.wait(10)
        linked.append(_register("tok_race_wait"))

    assert _run_threads(_start, _registration) == []
    assert linked == [1]
    assert _owned_rows(store, "tok_race_wait") == [
        {"self_check_run_id": created[0]["self_check_run_id"], "tr_doc_id": TR_DOC}]
    _finish(created[0]["self_check_run_id"])


def test_concurrent_start_and_registration_never_strand_a_draft_row(thread_stores):
    store = thread_stores
    for attempt in range(20):
        token_id = f"tok_race_{attempt}"
        _open_token(store, token_id)
        gate = threading.Barrier(2)
        outcome = {}

        def _start():
            gate.wait()
            try:
                outcome["row"] = _pending(owner=token_id)
            except runs_db.DraftOwnerClosedError:
                outcome["row"] = None

        def _registration():
            gate.wait()
            outcome["linked"] = _register(token_id)

        assert _run_threads(_start, _registration) == []
        rows = _owned_rows(store, token_id)
        if outcome["row"] is None:
            assert rows == [] and outcome["linked"] == 0
        else:
            assert rows == [{"self_check_run_id": outcome["row"]["self_check_run_id"], "tr_doc_id": TR_DOC}]
            assert outcome["linked"] == 1
            _finish(outcome["row"]["self_check_run_id"])


def test_start_maps_a_closed_owner_to_forbidden_and_releases_the_group_lock(monkeypatch, tmp_path):
    released = []
    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(service.db_runs, "has_group_recovery_incomplete", lambda *_: False)
    monkeypatch.setattr(service.db_runs, "get_active", lambda *_: None)
    monkeypatch.setattr(service, "_worktree", lambda _t: tmp_path)
    monkeypatch.setattr(service, "_probe", lambda _root: {})
    monkeypatch.setattr(service.policy, "controlled_path", lambda root, host: ("PATH", None))
    monkeypatch.setattr(service.policy, "resolve_command", lambda *a: SimpleNamespace(
        executable="pytest", name="pytest", origin="venv", argv_prefix=[]))
    monkeypatch.setattr(service.policy, "validate_cwd", lambda root, rel: tmp_path)
    monkeypatch.setattr(service.policy, "scrubbed_env", lambda path, runtime: {"PATH": path})
    monkeypatch.setattr(service.lock_manager, "new_context", lambda *_: SimpleNamespace(ctx_id="ctx_0638"))
    monkeypatch.setattr(service.lock_manager, "acquire", lambda *a, **k: SimpleNamespace(ok=True, lock_key="G:0638"))
    monkeypatch.setattr(service.lock_manager, "release", lambda ctx, key: released.append(key))

    def _closed(*_a, **_k):
        raise runs_db.DraftOwnerClosedError("consumed")

    monkeypatch.setattr(service.db_runs, "create_pending", _closed)
    monkeypatch.setattr(service.threading, "Thread", lambda *a, **k: pytest.fail("no worker for a refused run"))
    target = {"project_id": P, "group_id": G, "tr_doc_id": None, "owner_token_id": "tok_new_1",
              "draft_doc_ref": SEQ_DOC}
    with pytest.raises(service.SelfCheckError) as caught:
        service._start(target, {"program": "pytest", "args": ["-q"]}, "u_0638")
    assert (caught.value.status, caught.value.code) == (403, "selfcheck_forbidden")
    assert released == ["G:0638"]


# ── Service: who may own a draft run ─────────────────────────────────────────

@pytest.fixture
def admitted(monkeypatch):
    from modules.flow_gate.services import remote_tool_service

    docs = {SEQ_DOC: {"doc_id": SEQ_DOC, "type_code": "B", "project_id": P, "group_id": G},
            TR_DOC: {"doc_id": TR_DOC, "type_code": "TR", "project_id": P, "group_id": G},
            OTHER_TR: {"doc_id": OTHER_TR, "type_code": "TR", "project_id": P, "group_id": G2}}
    head = {"type": ("TR", False)}
    monkeypatch.setattr(service.db_documents, "get_by_id", lambda doc_id: docs.get(doc_id))
    monkeypatch.setattr(service.db_groups, "get_by_id", lambda gid: {"group_id": gid, "project_id": P, "status": "OPEN"})
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: head["type"])
    return {"docs": docs, "head": head}


def test_draft_target_admits_a_tr_new_token(admitted):
    target = service.draft_target(TOKEN)
    assert target == {"project_id": P, "group_id": G, "tr_doc_id": None,
                      "owner_token_id": "tok_new_1", "draft_doc_ref": SEQ_DOC}


@pytest.mark.parametrize("change", [
    {"action_scope": "edit"}, {"action_scope": "review"}, {"token_id": None},
    {"group_id": G2}, {"project": "p_other"}, {"doc_ref": "missing"}, {"group_id": None},
])
def test_draft_target_rejects_anything_but_the_tokens_own_tr_new_step(admitted, change):
    with pytest.raises(service.SelfCheckError) as caught:
        service.draft_target({**TOKEN, **change})
    assert (caught.value.status, caught.value.code) == (403, "selfcheck_forbidden")


@pytest.mark.parametrize("head", [("T", False), ("TS", False), (None, False), ("TR", True)])
def test_draft_target_requires_a_pending_tr_head(admitted, head):
    admitted["head"]["type"] = head
    with pytest.raises(service.SelfCheckError) as caught:
        service.draft_target(TOKEN)
    assert caught.value.status == 403


def test_draft_target_refuses_a_disposed_group(admitted, monkeypatch):
    monkeypatch.setattr(service.db_groups, "get_by_id", lambda gid: {"group_id": gid, "status": "DISPOSED"})
    with pytest.raises(service.SelfCheckError) as caught:
        service.draft_target(TOKEN)
    assert (caught.value.status, caught.value.code) == (409, "selfcheck_worktree_unavailable")


def test_start_draft_reuses_the_one_start_path_with_the_token_as_owner(admitted, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "_start", lambda target, request, by: seen.append((target, request, by)) or {"ok": 1})
    service.start_draft(TOKEN, {"program": "pytest"})
    target, request, by = seen[0]
    assert target["tr_doc_id"] is None and target["owner_token_id"] == "tok_new_1"
    assert request == {"program": "pytest"} and by == "u_0638"


def test_start_draft_keeps_every_existing_admission_gate(admitted, monkeypatch):
    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: False)
    with pytest.raises(service.SelfCheckError) as caught:
        service.start_draft(TOKEN, {"program": "pytest"})
    assert caught.value.code == "selfcheck_disabled"
    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(service.db_runs, "has_group_recovery_incomplete", lambda *_: True)
    with pytest.raises(service.SelfCheckError) as caught:
        service.start_draft(TOKEN, {"program": "pytest"})
    assert caught.value.code == "selfcheck_recovery_incomplete"
    monkeypatch.setattr(service.db_runs, "has_group_recovery_incomplete", lambda *_: False)
    monkeypatch.setattr(service.git_service, "effective_src_root_ex", lambda *_: (None, "base"))
    with pytest.raises(service.SelfCheckError) as caught:
        service.start_draft(TOKEN, {"program": "pytest"})
    assert caught.value.code == "selfcheck_worktree_unavailable"


def test_read_and_cancel_draft_see_only_the_tokens_runs(admitted, monkeypatch):
    rows = {("scr_1", "tok_new_1"): {"self_check_run_id": "scr_1", "tr_doc_id": None, "status": "running"}}
    monkeypatch.setattr(service.db_runs, "get_run_for_owner", lambda rid, owner: rows.get((rid, owner)))
    monkeypatch.setattr(service.db_runs, "request_cancel", lambda rid: None)
    monkeypatch.setattr(service, "_emit", lambda row: None)
    assert service.read_draft(TOKEN, "scr_1")["self_check_run_id"] == "scr_1"
    assert service.cancel_draft(TOKEN, "scr_1")["status"] == "running"
    other = {**TOKEN, "token_id": "tok_new_2"}
    for call in (service.read_draft, service.cancel_draft):
        with pytest.raises(service.SelfCheckError) as caught:
            call(other, "scr_1")
        assert caught.value.code == "selfcheck_run_not_found"


def test_link_draft_runs_only_for_the_tokens_own_tr(admitted, monkeypatch):
    calls = []
    monkeypatch.setattr(service.db_runs, "link_owner_runs", lambda *a: calls.append(a) or 2)
    monkeypatch.setattr(service.db_runs, "list_by_doc", lambda *a, **k: [])
    assert service.link_draft_runs(TOKEN, TR_DOC) == 2
    assert calls == [("tok_new_1", TR_DOC, P, G)]
    assert service.link_draft_runs(TOKEN, SEQ_DOC) == 0          # not a TR
    assert service.link_draft_runs(TOKEN, OTHER_TR) == 0         # other group
    assert service.link_draft_runs({**TOKEN, "action_scope": "edit"}, TR_DOC) == 0
    assert service.link_draft_runs(TOKEN, None) == 0
    assert len(calls) == 1


# ── Token consumption links; a link failure never fails registration ─────────

def _consume_with(monkeypatch, token_rec, link):
    monkeypatch.setattr(token_service.db_tokens, "consume", lambda _tid: None)
    monkeypatch.setattr(token_service.db_tokens, "get_by_id", lambda _tid: dict(token_rec))
    monkeypatch.setattr(token_service.db_events, "create", lambda _e: None)
    monkeypatch.setattr(service, "link_draft_runs", link)
    return token_service.consume(token_rec["token_id"], P, TR_DOC)


def test_consume_of_a_new_token_links_its_draft_runs(monkeypatch):
    seen = []
    assert _consume_with(monkeypatch, TOKEN, lambda tok, doc: seen.append((tok["token_id"], doc)) or 1) is True
    assert seen == [("tok_new_1", TR_DOC)]


def test_consume_of_other_scopes_does_not_link(monkeypatch):
    seen = []
    _consume_with(monkeypatch, {**TOKEN, "action_scope": "edit"}, lambda *a: seen.append(a))
    assert seen == []


def test_link_failure_does_not_fail_consumption(monkeypatch):
    def _boom(*_):
        raise RuntimeError("db down")
    assert _consume_with(monkeypatch, TOKEN, _boom) is True


# ── API provider tools ────────────────────────────────────────────────────────

RUN = {"action_scope": "new", "doc_ref": SEQ_DOC, "group_id": G, "project_id": P}


def test_tr_new_tool_call_dispatches_to_the_draft_path(monkeypatch):
    monkeypatch.setattr(tools.token_service, "verify", lambda _raw: dict(TOKEN))
    monkeypatch.setattr(tools.tr_self_check_service, "start_draft",
                        lambda tok, body: {"self_check_run_id": "scr_d", "status": "pending", "tr_doc_id": None})
    monkeypatch.setattr(tools.tr_self_check_service, "list_draft_runs", lambda tok, limit: [{"self_check_run_id": "scr_d"}])
    monkeypatch.setattr(tools.tr_self_check_service, "read_draft", lambda tok, rid: {"self_check_run_id": rid})
    monkeypatch.setattr(tools.tr_self_check_service, "cancel_draft", lambda tok, rid: {"self_check_run_id": rid, "status": "cancelled"})
    assert tools.self_check_call(RUN, "raw", "run_self_check", {"program": "pytest"})[0] == 202
    assert tools.self_check_call(RUN, "raw", "read_self_check", {})[1]["runs"][0]["self_check_run_id"] == "scr_d"
    assert tools.self_check_call(RUN, "raw", "read_self_check", {"self_check_run_id": "scr_d"})[0] == 200
    assert tools.self_check_call(RUN, "raw", "cancel_self_check", {"self_check_run_id": "scr_d"})[1]["status"] == "cancelled"


@pytest.mark.parametrize("change", [{"doc_ref": "other"}, {"group_id": G2}, {"project": "p_other"}, {"action_scope": "edit"}])
def test_tr_new_tool_call_must_be_the_tokens_own_run(monkeypatch, change):
    monkeypatch.setattr(tools.token_service, "verify", lambda _raw: {**TOKEN, **change})
    monkeypatch.setattr(tools.tr_self_check_service, "start_draft", lambda *_: pytest.fail("must not start"))
    with pytest.raises(tools.ToolError) as caught:
        tools.self_check_call(RUN, "raw", "run_self_check", {"program": "pytest"})
    assert (caught.value.status, caught.value.reason) == (403, "selfcheck_forbidden")


def test_tr_new_tool_call_surfaces_the_admission_refusal(monkeypatch, admitted):
    admitted["head"]["type"] = ("T", False)
    monkeypatch.setattr(tools.token_service, "verify", lambda _raw: dict(TOKEN))
    with pytest.raises(tools.ToolError) as caught:
        tools.self_check_call(RUN, "raw", "run_self_check", {"program": "pytest"})
    assert caught.value.status == 403


@pytest.mark.parametrize("head, expected", [("TR", True), ("T", False), ("TS", False), ("TSR", False)])
def test_tr_new_advertisement_follows_the_workflow_head(monkeypatch, head, expected):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": "B"})
    monkeypatch.setattr(tools.remote_tool_service, "_worker_token_step_type_result", lambda _rec: (head, False))
    names = {d["name"] for d in tools.definitions_for_run(dict(RUN))}
    assert (set(tools.SELF_CHECK_NAMES) <= names) is expected


# ── REST: the draft router ───────────────────────────────────────────────────

def _client(monkeypatch, bearer):
    monkeypatch.setattr(routes, "verify_bearer", lambda _request: bearer)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.draft_router)
    return TestClient(app)


DRAFT_RUNS = "/api/v1/self-check/draft/runs"


def test_rest_tr_new_token_runs_lists_reads_and_cancels(monkeypatch, admitted):
    monkeypatch.setattr(service, "start_draft", lambda tok, body: {"self_check_run_id": "scr_d", "status": "pending"})
    monkeypatch.setattr(service, "list_draft_runs", lambda tok, limit: [{"self_check_run_id": "scr_d"}])
    monkeypatch.setattr(service, "read_draft", lambda tok, rid: {"self_check_run_id": rid, "status": "completed"})
    monkeypatch.setattr(service, "cancel_draft", lambda tok, rid: {"self_check_run_id": rid, "status": "cancelled"})
    client = _client(monkeypatch, dict(TOKEN))
    started = client.post(DRAFT_RUNS, json={"program": "pytest"})
    assert started.status_code == 202 and started.json()["self_check_run_id"] == "scr_d"
    assert client.get(DRAFT_RUNS).json()["runs"] == [{"self_check_run_id": "scr_d"}]
    assert client.get(DRAFT_RUNS + "/scr_d").json()["status"] == "completed"
    assert client.post(DRAFT_RUNS + "/scr_d/cancel").json()["status"] == "cancelled"


@pytest.mark.parametrize("token", [
    {**TOKEN, "action_scope": "edit"},          # an edit token uses the TR-bound routes
    {**TOKEN, "group_id": G2},                  # another group's token
])
def test_rest_draft_routes_refuse_tokens_that_do_not_own_a_tr_new_step(monkeypatch, admitted, token):
    monkeypatch.setattr(service, "start_draft", lambda *_: pytest.fail("must not start"))
    response = _client(monkeypatch, token).post(DRAFT_RUNS, json={"program": "pytest"})
    assert response.status_code == 403
    assert response.json() == {"ok": False, "error": {"code": "forbidden", "message": "forbidden", "details": {}}}


def test_rest_tr_new_token_still_cannot_use_the_tr_bound_routes(monkeypatch, admitted):
    # The existing contract: TR(new) never reaches a TR id route, even for an existing TR.
    response = _client(monkeypatch, dict(TOKEN)).post(f"/api/v1/documents/{TR_DOC}/self-check/runs", json={"program": "pytest"})
    assert response.status_code == 403


def test_rest_user_cannot_start_but_can_list_a_groups_drafts(monkeypatch):
    user = {"_is_user_jwt": True, "issued_to": "u_console"}
    monkeypatch.setattr(service, "list_group_draft_runs", lambda gid, limit: (P, [{"self_check_run_id": "scr_d"}]))
    granted = {"perm": True}
    monkeypatch.setattr(routes, "has_permission", lambda *_: granted["perm"])
    client = _client(monkeypatch, user)
    assert client.post(DRAFT_RUNS, json={"program": "pytest"}).status_code == 403
    assert client.get(DRAFT_RUNS).status_code == 422
    assert client.get(DRAFT_RUNS, params={"group_id": G}).json()["runs"] == [{"self_check_run_id": "scr_d"}]
    granted["perm"] = False
    assert client.get(DRAFT_RUNS, params={"group_id": G}).status_code == 403


def test_rest_user_read_and_cancel_check_the_runs_project(monkeypatch):
    user = {"_is_user_jwt": True, "issued_to": "u_console"}
    row = {"self_check_run_id": "scr_d", "project_id": P, "tr_doc_id": None, "status": "running"}
    monkeypatch.setattr(service, "group_draft_run", lambda rid: row if rid == "scr_d" else None)
    monkeypatch.setattr(service, "cancel_group_draft", lambda rid: {**row, "status": "cancelled"})
    perms = []
    monkeypatch.setattr(routes, "has_permission", lambda uid, project, perm: perms.append((project, perm)) or True)
    client = _client(monkeypatch, user)
    assert client.get(DRAFT_RUNS + "/scr_d").json()["status"] == "running"
    assert client.post(DRAFT_RUNS + "/scr_d/cancel").json()["status"] == "cancelled"
    assert perms == [(P, "perm_document_read"), (P, "perm_document_update")]
    assert client.get(DRAFT_RUNS + "/scr_missing").status_code == 404
