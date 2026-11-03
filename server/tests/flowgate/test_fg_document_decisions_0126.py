"""0126 T#1 B1/B3/B9/B11: isolated HTTP + real migrated SQLite/file storage.

The inherited fixture stubs worker dispatch, default JWT verification and SSE transport.
Decision permissions, guards, body gates, storage and migrations are real. Separate tests
restore real signed-JWT authentication. No operating flow, server or repository is mutated.
"""
from __future__ import annotations

import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_fg_card_settings import (  # noqa: F401
    FA, FB, _h, _sql, db_path, decided, edge, env, legacy_cards, node, put_draft,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")


@pytest.fixture()
def decisions_env(env, monkeypatch):
    from flowgate.card_documents import routes
    from flowgate.graph_policies import fork
    from modules.flow_gate.graph_workflow.domain import policies
    # Install the same branch validators as startup in this isolated fixture.
    validators = dict(policies._registered)
    validators.update({key: impl for key, _value, _version, impl in fork.SLOTS})
    monkeypatch.setattr(policies, "_registered", validators)
    env["client"].app.include_router(routes.router)
    _sql(env["db"], "UPDATE users SET is_admin = 1, is_active = 1 WHERE user_id = 'u_kim'")
    return env


def _url(key, flow=FA):
    return f"/api/v1/flows/{flow}/nodes/{key}/documents"


def _body(doc_id, text="saved body"):
    from modules.flow_gate.db import documents
    from modules.flow_gate.storage.paths import get_storage_root
    from modules.flow_gate.storage.body import record_body_saved
    from modules.flow_gate.graph_workflow.services.flow_guard import run_in_document_guard
    def save():
        path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"---\ntitle: {doc_id}\n---\n{text}"
        path.write_text(content, encoding="utf-8")
        doc = documents.update(doc_id, {"file_path": f"docs/{doc_id}.md"})
        record_body_saved(doc, source="decision_test", file_path=doc["file_path"], size=len(content.encode()))
    run_in_document_guard(doc_id, save)


def _card(env, kind="T", count=1):
    ns = [node("tmp_root", "R")] + [node(f"tmp_{i}", kind) for i in range(count)]
    first = put_draft(env, FA, 0, ns, [edge(f"tmp_e{i}", "tmp_root", f"tmp_{i}") for i in range(count)])
    keys = [first["key_map"][f"tmp_{i}"] for i in range(count)]
    for key in keys:
        for line in first["card_documents"][key]:
            if line["slot"] in ("instruction", "report", "plan"):
                _body(line["doc_id"])
    return keys[0] if count == 1 else keys


def _get(env, key, headers=None):
    response = env["client"].get(_url(key) + "/decisions", headers=headers or _h())
    assert response.status_code == 200, response.text
    return response.json()


def _request(env, key, slot="report", action="approve", request_key="request1"):
    current = _get(env, key)
    doc = current["documents"][slot]
    return {"action": action, "doc_id": doc["doc_id"], "expected_revision": doc["revision_no"],
            "expected_decision_version": current["decision_version"], "expected_context": current["context"],
            "request_key": request_key}


def _post(env, key, body, slot="report", status=200, headers=None):
    response = env["client"].post(_url(key) + f"/{slot}/decision", json=body, headers=headers or _h())
    assert response.status_code == status, response.text
    return response.json()


def _rows(env, table="graph_document_decisions"):
    return _sql(env["db"], f"SELECT * FROM {table}")


def _states(env, key):
    return {s: d["doc_review_status"] for s, d in _get(env, key)["documents"].items() if d}


def test_b1_before_run_round_trip_durable_actor_and_history(decisions_env):
    env = decisions_env
    key = _card(env)
    for i, action in enumerate(("approve", "reject", "approve")):
        answer = _post(env, key, _request(env, key, "instruction", action, f"r{i}"), "instruction")
        assert answer["decision_version"] == i + 1
        assert answer["context"]["run_id"] is None
    assert _states(env, key) == {"instruction": "approved", "report": None}
    rows = _rows(env)
    assert len(rows) == 3 and {r["actor_id"] for r in rows} == {"u_kim"}
    assert {r["actor_kind"] for r in rows} == {"user"}
    assert [r["cause"] for r in rows] == ["human_approved", "human_rejected", "human_approved"]
    assert rows[1]["reason"] == "graph_document_rejected"
    assert _rows(env, "graph_runs") == [] and _rows(env, "graph_card_completions") == []
    assert _rows(env, "document_reviews") == []
    from flowgate.card_documents import decision_repository
    importlib.reload(decision_repository)
    assert _get(env, key)["documents"]["instruction"]["human_decision"]["valid"]
    from modules.flow_gate.db import documents
    doc = documents.get_by_id(rows[0]["doc_id"])
    history = doc["rejection_history"]
    history = json.loads(history) if isinstance(history, str) else history
    assert len(history) == 1 and history[0]["rejected_by"] == "u_kim"


def test_b3_report_cascade_and_instruction_rejection_are_atomic(decisions_env):
    env = decisions_env
    key = _card(env)
    approved = _post(env, key, _request(env, key))
    assert approved["changed_slots"] == ["instruction", "report"]
    assert set(_states(env, key).values()) == {"approved"}
    rows = _rows(env)
    assert len(rows) == 2 and rows[0]["request_id"] == rows[1]["request_id"]
    assert rows[0]["cascade_from"] == rows[1]["doc_id"]
    _post(env, key, _request(env, key, action="reject", request_key="report-reject"))
    assert _states(env, key) == {"instruction": "approved", "report": "rejected"}
    _post(env, key, _request(env, key, request_key="again"))
    _post(env, key, _request(env, key, "instruction", "reject", "instruction-reject"), "instruction")
    assert set(_states(env, key).values()) == {"rejected"}
    assert len({r["decision_version"] for r in _rows(env)}) == 4


def test_b3_failure_after_first_write_rolls_back_badges_history_receipt_and_sse(decisions_env, monkeypatch):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    from flowgate.card_documents import decisions
    original = decisions._record
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        if args[2] == "report":
            raise RuntimeError("fault after second badge, before request commit")
    monkeypatch.setattr(decisions, "_record", fail)
    before_events = len(env["events"])
    _post(env, key, body, status=500)
    assert _rows(env) == [] and _rows(env, "graph_document_decision_requests") == []
    assert set(_states(env, key).values()) == {None}
    assert len(env["events"]) == before_events
    assert _sql(env["db"], "SELECT * FROM events WHERE event_type = 'graph_document_decision'") == []
    monkeypatch.setattr(decisions, "_record", original)
    _post(env, key, body)


@pytest.mark.parametrize("damage", ["empty", "missing", "revision", "body", "same_body_save"])
def test_b3_b9_cascade_checks_both_documents_and_saves(decisions_env, damage):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    instruction = _get(env, key)["documents"]["instruction"]["doc_id"]
    if damage in ("empty", "body", "same_body_save"):
        _body(instruction, "" if damage == "empty" else "saved body" if damage == "same_body_save" else "edited")
    elif damage == "missing":
        _sql(env["db"], "DELETE FROM graph_node_documents WHERE doc_id = ?", (instruction,))
    else:
        _sql(env["db"], "UPDATE documents SET revision_no = revision_no + 1 WHERE doc_id = ?", (instruction,))
    _post(env, key, body, status=409)
    if damage in ("empty", "missing"):
        _post(env, key, _request(env, key, request_key="fresh"), status=409)
    assert _rows(env) == []


def test_b3_omit_and_single_output_use_required_outputs(decisions_env):
    env = decisions_env
    key = _card(env)
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    with_flow_guard(FA, lambda: repository.put(FA, key, "instruction", "omit", "u_kim"))
    instruction = _get(env, key)["documents"]["instruction"]["doc_id"]
    _body(instruction, "")
    answer = _post(env, key, _request(env, key))
    assert answer["changed_slots"] == ["report"] and len(_rows(env)) == 1
    _post(env, key, _request(env, key, "instruction", "approve", "hidden"), "instruction", status=409)


def test_b3_plan_single_output(decisions_env):
    env = decisions_env
    key = _card(env, "WP")
    answer = _post(env, key, _request(env, key, "plan"), "plan")
    assert answer["changed_slots"] == ["plan"] and len(_rows(env)) == 1


def test_b9_duplicate_key_replays_original_and_exposes_current(decisions_env):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    first = _post(env, key, body)
    events = len(env["events"])
    again = _post(env, key, body)
    assert again["request_id"] == first["request_id"] and again["replayed"]
    assert len(_rows(env)) == 2 and len(env["events"]) == events
    _post(env, key, _request(env, key, action="reject", request_key="reject"))
    replay = _post(env, key, body)
    assert replay["decision_version"] == 1 and replay["current"]["decision_version"] == 2
    _post(env, key, {**body, "action": "reject"}, status=409)


@pytest.mark.parametrize("actions", [("approve", "approve"), ("approve", "reject")])
def test_b9_concurrent_cas_has_one_winner(decisions_env, actions):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    barrier = threading.Barrier(2)
    def call(i):
        barrier.wait()
        return env["client"].post(_url(key) + "/report/decision",
            json={**body, "action": actions[i], "request_key": f"race{i}"}, headers=_h()).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(call, range(2))) == [200, 409]
    assert len(_rows(env, "graph_document_decision_requests")) == 1


def test_b9_body_edit_invalidates_evidence_and_projection_without_deleting_history(decisions_env):
    env = decisions_env
    key = _card(env)
    _post(env, key, _request(env, key))
    before = _get(env, key)
    _body(before["documents"]["instruction"]["doc_id"], "new revision body")
    current = _get(env, key)
    assert not current["documents"]["instruction"]["human_decision"]["valid"]
    assert current["documents"]["instruction"]["doc_review_status"] == "pending_review"
    assert len(_rows(env)) == 2
    from flowgate.card_documents import lines
    from modules.flow_gate.db import documents
    doc = documents.get_by_id(before["documents"]["instruction"]["doc_id"])
    shown = lines.lines_by_node(FA, {doc["doc_id"]: doc}, [key])[key]
    assert next(s for s in shown if s["slot"] == "instruction")["doc_review_status"] == "pending_review"


@pytest.mark.parametrize("field", ["actor_id", "actor_kind", "is_admin", "verdict_id"])
def test_b11_actor_and_ai_verdict_cannot_be_supplied(decisions_env, field):
    env = decisions_env
    key = _card(env)
    _post(env, key, {**_request(env, key), field: "forged"}, status=422)
    assert _rows(env) == []


def test_b11_actual_action_authority_and_flow_permission(decisions_env, monkeypatch):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    _post(env, key, body, status=403, headers={"Authorization": "Bearer user:u_lee"})
    from modules.flow_gate.graph_workflow.api import envelope
    monkeypatch.setattr(envelope, "has_permission", lambda *args: False)
    _post(env, key, body, status=403)
    assert _rows(env) == []


def test_b11_real_jwt_and_worker_token_boundary(decisions_env, monkeypatch):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    from modules.flow_gate.graph_workflow.api import envelope
    from modules.flow_gate.services import auth_outbound
    from modules.flow_gate.auth import jwt_service
    monkeypatch.setattr(envelope, "verify_bearer", auth_outbound.verify_bearer)
    token, _ = jwt_service.create_access_token("u_kim", "kim", [], is_admin=False)
    _post(env, key, body, headers={"Authorization": "Bearer " + token})
    # A signed admin claim cannot grant action permission to a non-admin DB user.
    forged_claim, _ = jwt_service.create_access_token("u_lee", "lee", [], is_admin=True)
    _post(env, key, body, status=403, headers={"Authorization": "Bearer " + forged_claim})
    monkeypatch.setattr(auth_outbound.token_service, "verify", lambda raw: {"issued_to": "u_kim", "project": "flowgate"})
    monkeypatch.setattr(auth_outbound, "has_permission", lambda *args: True)
    _post(env, key, body, status=403, headers={"Authorization": "Bearer worker-admin"})
    assert len(_rows(env, "graph_document_decision_requests")) == 1


def test_b11_other_flow_slot_and_empty_document(decisions_env):
    env = decisions_env
    key, other = _card(env, count=2)
    body = _request(env, key)
    body["doc_id"] = _get(env, other)["documents"]["report"]["doc_id"]
    _post(env, key, body, status=409)
    from test_fg_card_settings import _doc
    body["doc_id"] = _doc(FB, 91, "TR")
    _post(env, key, body, status=409)
    body = _request(env, key)
    _body(body["doc_id"], "")
    _post(env, key, _request(env, key), status=409)
    assert _rows(env) == []


def test_b11_time_machine_fence_and_ownership_conflict(decisions_env, monkeypatch):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    from flowgate.card_documents import decision_context
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from types import SimpleNamespace
    monkeypatch.setattr(decision_context.engine_router, "read_ownership",
                        lambda flow: SimpleNamespace(state="conflict", sequence_root_doc_id=None))
    _post(env, key, body, status=409)
    monkeypatch.setattr(decision_context.engine_router, "read_ownership", lambda flow: SimpleNamespace(state="graph"))
    def fence(flow):
        raise GraphApiError(409, "graph_time_machine_in_progress", {})
    monkeypatch.setattr(decision_context.editing, "require_settings_editable", fence)
    _post(env, key, body, status=409)
    assert _rows(env) == []


def test_b9_historical_context_and_settings_changes_refuse(decisions_env):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    body["expected_context"]["graph_revision_no"] = 99
    _post(env, key, body, status=409)
    body = _request(env, key)
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    with_flow_guard(FA, lambda: repository.bump_version(FA, key))
    _post(env, key, body, status=409)
    assert _rows(env) == []


def test_commit_events_have_context_and_never_emit_before_commit(decisions_env, monkeypatch):
    env = decisions_env
    key = _card(env)
    from modules.flow_gate.graph_workflow.services import canvas_events
    from modules.flow_gate.db.connection import in_transaction
    seen = []
    def send(event):
        assert not in_transaction()
        if event.event_type == "doc_review_status_changed":
            assert len(_rows(env)) == 2
            assert event.payload["node_key"] == key and event.payload["decision_version"] == 1
            assert event.payload["actor_id"] == "u_kim"
        seen.append(event)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", send)
    _post(env, key, _request(env, key))
    assert [e.event_type for e in seen].count("doc_review_status_changed") == 2
    assert "graph_canvas_changed" in [e.event_type for e in seen]


def _runtime(env, keys):
    from modules.flow_gate.graph_workflow.services import graph_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import definition_store, run_store
    from modules.flow_gate.db.connection import get_store
    response = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert response.status_code == 201, response.text
    attempts = {}
    def seed():
        graph = graph_service.as_graph(definition_store.revision(FA, 1))
        run_store.create_run("run_decision_test", FA, 1, "u_kim", {
            "source": "system_default", "set_id": "fixture", "set_version": "1", "digest": "0" * 64, "entries": {}}, graph)
        for key in keys:
            attempts[key] = run_store.open_attempt("run_decision_test", key, 1)
            get_store()._execute("UPDATE graph_node_runs SET status = 'review', stage = 'result' "
                                 "WHERE run_id = ? AND node_key = ?", ["run_decision_test", key])
    with_flow_guard(FA, seed)
    return attempts


@pytest.mark.parametrize("activity", ["requested", "running", "review", "handoff"])
def test_card_activity_locks_only_its_node(decisions_env, decided, monkeypatch, activity):
    env = decisions_env
    key, independent = _card(env, count=2)
    attempts = _runtime(env, [key, independent])
    body = _request(env, key)
    from flowgate.card_settings import legacy_adapter, review_stage
    if activity in ("requested", "running"):
        _sql(env["db"], "INSERT INTO graph_attempt_runs "
             "(exec_id, attempt_id, run_id, kind, status, created_at, request_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
             ("exec_busy", attempts[key], "run_decision_test", "write", activity, "now", "{}"))
    elif activity == "review":
        doc_id = body["doc_id"]
        monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda d: d == doc_id)
    else:
        monkeypatch.setattr(review_stage, "_handoff_pending", lambda flow, k: k == key)
    answer = _post(env, key, body, status=409)
    assert answer["error"]["code"] == "document_decision_locked"
    assert _get(env, key)["lock"]["code"] == "document_decision_locked"
    accepted = _post(env, independent, _request(env, independent))
    assert accepted["context"]["attempt_id"] == attempts[independent]
    assert {r["node_key"] for r in _rows(env)} == {independent}


@pytest.mark.parametrize("effect", ["done", "recorded", "applied", "integrated", "conflict", "ledger"])
def test_t3_required_before_any_completed_rejection_write(decisions_env, decided, effect):
    env = decisions_env
    key = _card(env)
    _post(env, key, _request(env, key))
    attempts = _runtime(env, [key])
    doc = _get(env, key)["documents"]["report"]
    if effect == "done":
        _sql(env["db"], "UPDATE graph_node_runs SET status = 'done' WHERE node_key = ?", (key,))
    elif effect in ("recorded", "applied"):
        from flowgate.card_completion import repository
        repository.insert({"run_id": "run_decision_test", "group_id": FA, "node_key": key,
                           "attempt_id": attempts[key], "result_doc_id": doc["doc_id"],
                           "revision_no": doc["revision_no"], "cause": "human_submitted", "actor_kind": "user"})
        if effect == "applied":
            _sql(env["db"], "UPDATE graph_card_completions SET status = 'applied'")
    elif effect == "ledger":
        _sql(env["db"], "INSERT INTO tr_commit_ledger (group_id, doc_id, node_key, state, commit_sha, created_at, updated_at) "
             "VALUES (?, ?, ?, 'live', ?, 'now', 'now')", (FA, doc["doc_id"], key, "a" * 40))
    else:
        _sql(env["db"], "UPDATE graph_node_attempts SET integration_status = ? WHERE attempt_id = ?", (effect, attempts[key]))
    before = {t: _rows(env, t) for t in ["graph_document_decisions", "graph_document_decision_requests", "graph_node_attempts",
                                        "graph_card_completions", "graph_node_runs", "tr_commit_ledger"]}
    for slot in ("instruction", "report"):
        res = _post(env, key, _request(env, key, slot, "reject", slot), slot, status=409)
        assert res["error"]["code"] == "document_rejection_recovery_required"
    assert set(_states(env, key).values()) == {"approved"}
    for table, rows in before.items():
        assert _rows(env, table) == rows


def test_b9_run_and_attempt_change_refuse_old_context(decisions_env, decided):
    env = decisions_env
    key = _card(env)
    before_run = _request(env, key)
    _runtime(env, [key])
    _post(env, key, before_run, status=409)
    before_attempt = _request(env, key)
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    def next_attempt():
        run_store.end_attempt("run_decision_test", key)
        run_store.open_attempt("run_decision_test", key, 1)
    with_flow_guard(FA, next_attempt)
    _post(env, key, before_attempt, status=409)
    assert _rows(env) == []


def test_b9_concurrent_duplicate_key_commits_once(decisions_env):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    barrier = threading.Barrier(2)
    def send(_):
        barrier.wait()
        return _post(env, key, body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(send, range(2)))
    assert len({a["request_id"] for a in answers}) == 1
    assert sorted(a["replayed"] for a in answers) == [False, True]
    assert len(_rows(env, "graph_document_decision_requests")) == 1 and len(_rows(env)) == 2


def test_existing_approved_badge_never_becomes_human_evidence(decisions_env):
    env = decisions_env
    key = _card(env)
    doc = _get(env, key)["documents"]["report"]
    _sql(env["db"], "UPDATE documents SET doc_review_status = 'approved' WHERE doc_id = ?", (doc["doc_id"],))
    current = _get(env, key)
    assert current["documents"]["report"]["human_decision"] is None
    assert current["decision_version"] == 0
    assert current["capabilities"]["report"]["reject"]["allowed"]


def test_additive_migration_can_rerun_without_erasing_history(decisions_env):
    import sqlite3
    from pathlib import Path
    env = decisions_env
    key = _card(env)
    _post(env, key, _request(env, key))
    before = _rows(env)
    sql = (Path(__file__).resolve().parents[2] / "sql/migrations/sqlite/171_graph_document_decisions.sql").read_text(encoding="utf-8")
    with sqlite3.connect(env["db"]) as conn:
        conn.executescript(sql)
        conn.executescript(sql)
    assert _rows(env) == before
    assert _get(env, key)["decision_version"] == 1


def test_ts_admission_and_human_decision_share_transaction_and_commit_notifications(decisions_env, monkeypatch, tmp_path):
    from test_fg_card_approval_0056 import TS_AUTOMATED
    from modules.flow_gate.services import test_basis_service, test_run_service as runner
    from modules.flow_gate.db.connection import in_transaction
    from modules.flow_gate.storage.paths import get_storage_root
    env = decisions_env
    key = _card(env, "TS")
    ts = _get(env, key)["documents"]["instruction"]["doc_id"]
    path = get_storage_root("flowgate", create=True) / "docs" / f"{ts}.md"
    path.write_text(TS_AUTOMATED, encoding="utf-8")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests/test_x.py").write_text("def test_tc_1():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(test_basis_service, "source_root", lambda doc: tmp_path)
    monkeypatch.setattr(runner, "open_execution_tsr", lambda *args, **kwargs: "isolated-tsr")
    notices = []
    def notice(doc, run):
        assert not in_transaction()
        assert len(_rows(env)) == 1
        notices.append(run["run_id"])
    monkeypatch.setattr(runner, "_emit_started", notice)
    monkeypatch.setattr(runner, "_broadcast", lambda *args, **kwargs: None)
    body = _request(env, key, "instruction")
    _post(env, key, body, "instruction")
    _post(env, key, body, "instruction")
    assert len(_sql(env["db"], "SELECT * FROM test_runs WHERE doc_id = ?", (ts,))) == 1
    assert len(notices) == 1
    assert _get(env, key)["documents"]["instruction"]["human_decision"]["actor_id"] == "u_kim"
    _post(env, key, _request(env, key, "instruction", "reject", "reject-ts"), "instruction", status=409)


def test_ts_admission_failure_rolls_back_human_decision(decisions_env, monkeypatch, tmp_path):
    from test_fg_card_approval_0056 import TS_AUTOMATED
    from modules.flow_gate.services import test_basis_service, test_run_service as runner, spec_execution_service
    from modules.flow_gate.storage.paths import get_storage_root
    env = decisions_env
    key = _card(env, "TS")
    ts = _get(env, key)["documents"]["instruction"]["doc_id"]
    (get_storage_root("flowgate", create=True) / "docs" / f"{ts}.md").write_text(TS_AUTOMATED, encoding="utf-8")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests/test_x.py").write_text("def test_tc_1():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(test_basis_service, "source_root", lambda doc: tmp_path)
    def fail(*args, **kwargs):
        assert len(_rows_in_transaction()) == 1
        raise RuntimeError("admission failed after decision")
    def _rows_in_transaction():
        from modules.flow_gate.db.connection import get_store
        return get_store()._fetch_all("SELECT * FROM graph_document_decisions")
    monkeypatch.setattr(spec_execution_service, "admit_on_approval", fail)
    before = len(env["events"])
    _post(env, key, _request(env, key, "instruction"), "instruction", status=500)
    assert _rows(env) == [] and _rows(env, "graph_document_decision_requests") == []
    assert _get(env, key)["documents"]["instruction"]["doc_review_status"] is None
    assert len(env["events"]) == before


def test_b9_body_save_racing_approval_never_keeps_old_evidence(decisions_env):
    env = decisions_env
    key = _card(env)
    body = _request(env, key)
    barrier = threading.Barrier(2)
    def save():
        barrier.wait()
        _body(body["doc_id"], "a new body saved concurrently")
    def decide():
        barrier.wait()
        return env["client"].post(_url(key) + "/report/decision", json=body, headers=_h())
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, decided = pool.submit(save), pool.submit(decide)
        saved.result()
        response = decided.result()
    assert response.status_code in (200, 409), response.text
    current = _get(env, key)
    human = current["documents"]["report"]["human_decision"]
    assert human is None or human["valid"] is False
    assert current["documents"]["report"]["doc_review_status"] != "approved"
    assert len(_rows(env)) == (2 if response.status_code == 200 else 0)


def test_tr2_apply_gate_cannot_be_bypassed_by_human_cascade(decisions_env):
    env = decisions_env
    key = _card(env, "T2")
    response = _post(env, key, _request(env, key), status=409)
    assert response["error"]["code"] == "document_decision_effect_refused"
    assert _rows(env) == [] and _rows(env, "graph_document_decision_requests") == []
    assert set(_states(env, key).values()) == {None}
