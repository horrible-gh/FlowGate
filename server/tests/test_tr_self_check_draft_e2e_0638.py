"""0638 T#2: TR Self-check before and after the TR exists, end to end.

T#1 pinned each piece of the pre-registration path on its own (repository, service, REST
with a mocked service, consumption with a mocked link). This file runs them as one flow on
a fully migrated SQLite, through the real pieces:

- real bearer verification (``auth_outbound.verify_bearer`` -> ``token_service.verify``
  against hashed token rows), so a consumed token really answers 401;
- the real workflow head lookup (sequence B -> T(approved) -> TR(pending));
- the real service start path (policy, Group G lock, git probe, worker thread, child
  process) in a throwaway git repository standing in for the managed group worktree;
- registration through the real ``token_service.consume(require_claim=True)``, the single
  point the inbox and the API provider both pass with the created TR id.

What is replaced: the worktree root resolver (points at the throwaway repository), RBAC
(``has_permission``), the user-JWT decoder, and the SSE broadcast (captured, to pin the
event payload the client panels key on). The inbox body/file handling around consumption
is not exercised here.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import self_check_routes as routes
from modules.flow_gate.db import connection
from modules.flow_gate.services import auth_outbound
from modules.flow_gate.services import token_service
from modules.flow_gate.services import tr_self_check_service as service

P, G, G2 = "p_0638e", "g_0638e_1", "g_0638e_2"
SEQ_DOC, T_DOC, TR_DOC = "d_0638e_b", "d_0638e_t", "d_0638e_tr"
OTHER_SEQ = "d_0638e_b2"
PEPPER_ID, PEPPER = "E2E0638", "pepper-0638-e2e"
RAW = {"new": "raw-tr-new-0638", "new_other": "raw-tr-new-other-0638",
       "edit": "raw-tr-edit-0638", "review": "raw-tr-review-0638"}
USER_JWT = "user-jwt-0638"
DRAFT_RUNS = "/api/v1/self-check/draft/runs"
TR_RUNS = f"/api/v1/documents/{TR_DOC}/self-check/runs"
TERMINAL = {"completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def flow(migrated_sqlite_db, monkeypatch, tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = migrated_sqlite_db("test_tr_self_check_draft_e2e_0638.db")
    store = object.__new__(connection.FlowGateStore)
    store._db, store._sq = SQLiteWrapper(db_path), None
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", PEPPER_ID)
    monkeypatch.setenv(f"FLOWGATE_TOKEN_PEPPER_{PEPPER_ID}", PEPPER)

    now = _now()
    ex = store._execute
    ex("INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
       "VALUES ('u_0638e', 'u0638e', 'u0638e@test', 'x', 1, ?, ?)", [now, now])
    ex("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
       "VALUES (?, 'P 0638 e2e', 1, ?, ?)", [P, now, now])
    ex("INSERT INTO project_settings (project_id, updated_at, tr_self_check_enabled) VALUES (?, ?, 1)", [P, now])
    for gid in (G, G2):
        ex("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
           "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?)", [gid, P, now, now])
    for seq, (doc_id, gid, type_code, status) in enumerate((
            (SEQ_DOC, G, "B", "draft"), (T_DOC, G, "T", "approved"), (OTHER_SEQ, G2, "B", "draft")), 1):
        _document(store, doc_id, gid, type_code, seq, status)
    # B -> T(approved) -> TR(pending): the effective head is TR, as when the TR(new) token is issued.
    for doc_id, items in ((SEQ_DOC, (("T", T_DOC), ("TR", None))), (OTHER_SEQ, (("T", None), ("TR", None)))):
        ex("INSERT INTO workflow_sequences (doc_id) VALUES (?)", [doc_id])
        seq_id = store._fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", [doc_id])["id"]
        for item_seq, (type_code, result) in enumerate(items, 1):
            ex("INSERT INTO workflow_sequence_items (sequence_id, item_seq, type, label, doc_class, sort_order,"
               " result_doc_id) VALUES (?, ?, ?, ?, 'B', ?, ?)", [seq_id, item_seq, type_code, type_code, item_seq, result])
    _token(store, "tok_e2e_new", RAW["new"], G, SEQ_DOC, "new")
    _token(store, "tok_e2e_new_other", RAW["new_other"], G2, OTHER_SEQ, "new")  # head is T, not TR

    # The managed group worktree: a throwaway git repository with two check scripts.
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / "check_ok.py").write_text("print('selfcheck-0638-ok')\n", encoding="utf-8")
    (repo / "check_slow.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "user.email=e2e@test", "-c", "user.name=e2e", "commit", "-q", "-m", "init")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    monkeypatch.setattr(service.git_service, "effective_src_root_ex",
                        lambda project_id, group_id, state=None: (repo, "worktree"))

    events: list[dict] = []
    monkeypatch.setattr(service.git_service, "_emit",
                        lambda event_type, project, group_id, payload: events.append({"type": event_type, **payload}))
    monkeypatch.setattr(auth_outbound, "has_permission", lambda *_: True)
    monkeypatch.setattr(routes, "has_permission", lambda *_: True)
    monkeypatch.setattr(auth_outbound, "_verify_user_jwt",
                        lambda raw: {"_is_user_jwt": True, "issued_to": "u_0638e"} if raw == USER_JWT else None)

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.draft_router)
    client = TestClient(app)
    try:
        yield {"store": store, "client": client, "events": events}
    finally:
        _drain(store)
        try:
            store._db.close()
        except Exception:
            pass


def _document(store, doc_id, group_id, type_code, seq, status="draft"):
    now = _now()
    review = "approved" if status == "approved" else None
    store._execute(
        "INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch,"
        " revision_no, rejection_history, doc_review_status, created_at, updated_at) VALUES (?, ?, ?, 'default',"
        " ?, ?, 't', ?, 'main', 1, '[]', ?, ?, ?)", [doc_id, P, group_id, type_code, seq, status, review, now, now])


def _token(store, token_id, raw, group_id, doc_ref, scope):
    store._execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, issued_to,"
        " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'u_0638e', ?, '9999-12-31T00:00:00+00:00')",
        [token_id, token_service._hash_token(raw, PEPPER), PEPPER_ID, P, group_id, doc_ref, scope, _now()])


def _as(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def _wait(client, path, headers, statuses=TERMINAL, timeout=60.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(path, headers=headers).json()
        if body.get("status") in statuses:
            return body
        assert time.monotonic() < deadline, f"run did not reach {statuses}: {body}"
        time.sleep(0.2)


def _drain(store):
    """Never leave a child process or worker behind: cancel and wait for every live run."""
    rows = store._fetch_all("SELECT self_check_run_id FROM tr_self_check_runs WHERE project_id = ?"
                            " AND status IN ('pending', 'running')", [P]) or []
    for row in rows:
        try:
            service._cancel(row["self_check_run_id"], lambda rid=row["self_check_run_id"]: service.db_runs.get_run(rid))
        except Exception:
            pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and store._fetch_one(
            "SELECT 1 AS x FROM tr_self_check_runs WHERE project_id = ? AND status IN ('pending', 'running')", [P]):
        time.sleep(0.2)


def _row(store, run_id) -> dict:
    return store._fetch_one("SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ?", [run_id])


def _run_count(store) -> int:
    return store._fetch_one("SELECT COUNT(*) AS n FROM tr_self_check_runs WHERE project_id = ?", [P])["n"]


def test_tr_new_runs_before_registration_and_the_runs_follow_the_tr(flow):
    store, client, events = flow["store"], flow["client"], flow["events"]
    new, user = _as(RAW["new"]), _as(USER_JWT)

    # 1. No TR yet: the TR(new) token runs on the draft path, owned by the token.
    started = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_ok.py"]})
    assert started.status_code == 202, started.text
    quick = started.json()
    assert quick["tr_doc_id"] is None and quick["linked_at"] is None
    done = _wait(client, f"{DRAFT_RUNS}/{quick['self_check_run_id']}", new)
    assert (done["status"], done["exit_code"]) == ("completed", 0), done
    assert "selfcheck-0638-ok" in done["stdout_tail"]
    assert done["source_changed_during_run"] is False and done["worktree_state_changed"] is False
    assert _row(store, quick["self_check_run_id"])["owner_token_id"] == "tok_e2e_new"

    # 2. Read side before registration: the token sees its run, a console user sees the group's.
    assert [r["self_check_run_id"] for r in client.get(DRAFT_RUNS, headers=new).json()["runs"]] == [quick["self_check_run_id"]]
    listed = client.get(DRAFT_RUNS, headers=user, params={"group_id": G})
    assert [r["self_check_run_id"] for r in listed.json()["runs"]] == [quick["self_check_run_id"]]
    assert client.get(DRAFT_RUNS, headers=user, params={"group_id": G2}).json()["runs"] == []
    assert client.get(f"{DRAFT_RUNS}/{quick['self_check_run_id']}", headers=user).json()["status"] == "completed"
    # A console user owns no TR(new) token and cannot start one.
    before = _run_count(store)
    assert client.post(DRAFT_RUNS, headers=user, json={"program": "python", "args": ["check_ok.py"]}).status_code == 403
    # A TR(new) token whose head is not TR is refused and creates nothing.
    refused = client.post(DRAFT_RUNS, headers=_as(RAW["new_other"]), json={"program": "python", "args": ["check_ok.py"]})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    assert client.get(f"{DRAFT_RUNS}/{quick['self_check_run_id']}", headers=_as(RAW["new_other"])).status_code == 403
    assert _run_count(store) == before

    # 3. A long run holds the group's single active slot; a second start is refused.
    slow = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_slow.py"]}).json()
    _wait(client, f"{DRAFT_RUNS}/{slow['self_check_run_id']}", new, statuses={"running"})
    busy = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_ok.py"]})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "selfcheck_already_running"
    assert busy.json()["error"]["details"]["self_check_run_id"] == slow["self_check_run_id"]

    # 4. The worker registers the TR while the long run is still running.
    _document(store, TR_DOC, G, "TR", 3)
    store._execute("UPDATE workflow_sequence_items SET result_doc_id = ? WHERE type = 'TR' AND sequence_id ="
                   " (SELECT id FROM workflow_sequences WHERE doc_id = ?)", [TR_DOC, SEQ_DOC])
    events.clear()
    assert token_service.consume("tok_e2e_new", P, doc_id=TR_DOC, require_claim=True) is True

    for run_id in (quick["self_check_run_id"], slow["self_check_run_id"]):
        row = _row(store, run_id)
        assert row["tr_doc_id"] == TR_DOC and row["linked_at"], row
    linked_events = [e for e in events if e["type"] == "self_check_run_updated"]
    assert {e["self_check_run_id"] for e in linked_events} == {quick["self_check_run_id"], slow["self_check_run_id"]}
    assert all(e["draft"] is False and e["tr_doc_id"] == TR_DOC and e["group_id"] == G for e in linked_events)

    # 5. After registration: the consumed token is gone, the draft list is empty, the TR owns the runs.
    assert client.get(DRAFT_RUNS, headers=new).status_code == 401
    assert client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_ok.py"]}).status_code == 401
    assert client.get(DRAFT_RUNS, headers=user, params={"group_id": G}).json()["runs"] == []
    assert client.get(f"{DRAFT_RUNS}/{quick['self_check_run_id']}", headers=user).status_code == 404
    on_tr = {r["self_check_run_id"]: r for r in client.get(TR_RUNS, headers=user).json()["runs"]}
    assert set(on_tr) == {quick["self_check_run_id"], slow["self_check_run_id"]}
    assert all(r["tr_doc_id"] == TR_DOC and r["linked_at"] for r in on_tr.values())
    assert client.get(f"{TR_RUNS}/{quick['self_check_run_id']}", headers=user).json()["stdout_tail"].strip() == "selfcheck-0638-ok"

    # 6. The existing TR-bound contract holds for the linked runs: review reads, only edit mutates.
    _token(store, "tok_e2e_edit", RAW["edit"], G, TR_DOC, "edit")
    _token(store, "tok_e2e_review", RAW["review"], G, TR_DOC, "review")
    edit, review = _as(RAW["edit"]), _as(RAW["review"])
    assert client.get(f"{TR_RUNS}/{slow['self_check_run_id']}", headers=review).json()["status"] == "running"
    assert client.post(f"{TR_RUNS}/{slow['self_check_run_id']}/cancel", headers=review).status_code == 403
    assert client.post(TR_RUNS, headers=review, json={"program": "python", "args": ["check_ok.py"]}).status_code == 403
    # An edit/review token never owns a draft run.
    assert client.post(DRAFT_RUNS, headers=edit, json={"program": "python", "args": ["check_ok.py"]}).status_code == 403
    assert client.get(DRAFT_RUNS, headers=review).status_code == 403
    cancelled = client.post(f"{TR_RUNS}/{slow['self_check_run_id']}/cancel", headers=edit)
    assert cancelled.status_code == 200 and cancelled.json()["cancel_requested"] is True
    assert _wait(client, f"{TR_RUNS}/{slow['self_check_run_id']}", edit)["status"] == "cancelled"

    # 7. The TR edit token runs on the TR-bound route as before; that run was never a draft.
    bound = client.post(TR_RUNS, headers=edit, json={"program": "python", "args": ["check_ok.py"]})
    assert bound.status_code == 202, bound.text
    assert bound.json()["tr_doc_id"] == TR_DOC
    finished = _wait(client, f"{TR_RUNS}/{bound.json()['self_check_run_id']}", review)
    assert (finished["status"], finished["exit_code"], finished["linked_at"]) == ("completed", 0, None)
    assert _row(store, bound.json()["self_check_run_id"])["owner_token_id"] is None
    assert len(client.get(TR_RUNS, headers=review).json()["runs"]) == 3


def test_a_tr_new_token_that_registers_without_running_links_nothing(flow):
    store, client = flow["store"], flow["client"]
    _document(store, TR_DOC, G, "TR", 3)
    assert token_service.consume("tok_e2e_new", P, doc_id=TR_DOC, require_claim=True) is True
    assert client.get(TR_RUNS, headers=_as(USER_JWT)).json()["runs"] == []
    assert _run_count(store) == 0


def test_a_cancelled_draft_run_is_still_linked_as_evidence(flow):
    store, client = flow["store"], flow["client"]
    new, user = _as(RAW["new"]), _as(USER_JWT)
    slow = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_slow.py"]}).json()
    _wait(client, f"{DRAFT_RUNS}/{slow['self_check_run_id']}", new, statuses={"running"})
    # The console user cancels it from the T screen (draft route, user JWT).
    assert client.post(f"{DRAFT_RUNS}/{slow['self_check_run_id']}/cancel", headers=user).json()["cancel_requested"] is True
    assert _wait(client, f"{DRAFT_RUNS}/{slow['self_check_run_id']}", new)["status"] == "cancelled"

    _document(store, TR_DOC, G, "TR", 3)
    assert token_service.consume("tok_e2e_new", P, doc_id=TR_DOC, require_claim=True) is True
    runs = client.get(TR_RUNS, headers=user).json()["runs"]
    assert [(r["self_check_run_id"], r["status"]) for r in runs] == [(slow["self_check_run_id"], "cancelled")]
    assert runs[0]["linked_at"]
