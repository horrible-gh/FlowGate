"""0061 T#12 regression specifications: pure scope, persisted ownership and v0.1 gates.

TR Self-check only; formal PASS/gate belongs to TS/TSR. SQLite is in-memory and
external workflow/Git mutation paths are mocks. No provider or Git process starts.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sqlite3

import pytest
from starlette.requests import Request

from flowgate.graph_time_machine import impact, repository, service
from modules.flow_gate.graph_workflow.api import graph_messages, v01_contact
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode
from modules.flow_gate.graph_workflow.services import engine_router
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store


def graph(edges=None):
    keys = ("R", "A", "B", "J", "C", "D", "X")
    edges = edges if edges is not None else (
        ("R", "A"), ("R", "B"), ("A", "J"), ("B", "J"), ("J", "C"), ("C", "D"))
    return Graph(
        nodes=tuple(GraphNode(k, "document", "R" if k == "R" else "T", k) for k in keys),
        edges=tuple(GraphEdge(str(i), a, b) for i, (a, b) in enumerate(edges)))


@pytest.fixture
def snapshot():
    attempts = [
        {"attempt_id": k.lower(), "run_id": "run", "node_key": k, "revision_no": 1,
         "status": "ended", "input_source": "predecessors" if deps else "run_base",
         "input_attempt_ids": json.dumps(deps), "result_commit": "result-" + k,
         "integrated_commit": "merge-" + k, "integration_status": "integrated"}
        for k, deps in (("A", []), ("B", []), ("J", ["a", "b"]), ("C", ["j"]))]
    return impact.Snapshot(
        flow_id="flow", run={"run_id": "run", "revision_no": 1, "status": "idle"},
        nodes=[{"node_key": k, "status": "pending" if k == "D" else "done", "root_bound": k == "R"}
               for k in ("R", "A", "B", "J", "C", "D")],
        attempts=attempts, revisions={1: graph()},
        ledger_rows=[{"id": i, "group_id": "flow", "attempt_id": a["attempt_id"], "node_key": a["node_key"],
                      "state": "live", "commit_sha": a["result_commit"]}
                     for i, a in enumerate(attempts, 1)],
        documents=[{"doc_id": "doc-" + a["node_key"], "attempt_id": a["attempt_id"],
                    "node_key": a["node_key"], "stage": "result"} for a in attempts])


def codes(result):
    return {r["code"] for r in result["reasons"]}


def test_join_rewinds_selected_branch_and_all_descendants(snapshot):
    before = deepcopy(snapshot)
    result = impact.calculate(snapshot, "A")
    assert result["allowed"]
    scope = result["impact"]
    assert scope["node_keys"] == ["A", "C", "D", "J"]
    assert {a["attempt_id"] for a in scope["attempts"]} == {"a", "j", "c"}
    assert {d["doc_id"] for d in scope["documents"]} == {"doc-A", "doc-J", "doc-C"}
    assert {(c["kind"], c["commit_sha"]) for c in scope["commits"]} == {
        (kind, prefix + node) for node in ("A", "J", "C")
        for kind, prefix in (("result", "result-"), ("integration", "merge-"))}
    assert snapshot == before  # The planner cannot mutate a live snapshot.
    assert impact.calculate(snapshot, "J")["impact"]["node_keys"] == ["C", "D", "J"]


def test_consumer_attempt_revision_survives_run_revision_adoption(snapshot):
    # The new revision removes A->J and adds A->B. B/J have already executed
    # with revision 1; neither their inbound dependencies nor scope change.
    snapshot.run["revision_no"] = 2
    snapshot.revisions[2] = graph((("R", "A"), ("A", "B"), ("B", "J"), ("J", "C"), ("C", "D")))
    assert impact.calculate(snapshot, "A")["impact"]["node_keys"] == ["A", "C", "D", "J"]


def test_old_source_revision_cannot_invent_new_consumer_dependency(snapshot):
    snapshot.run["revision_no"] = 2
    snapshot.revisions[2] = graph((("R", "A"), ("R", "B"), ("B", "J"), ("J", "C"), ("C", "D")))
    for a in snapshot.attempts:
        if a["node_key"] in ("J", "C"):
            a["revision_no"] = 2
        if a["node_key"] == "J":
            a["input_attempt_ids"] = '["b"]'
    assert impact.calculate(snapshot, "A")["impact"]["node_keys"] == ["A"]
    # Actual consumed results remain authoritative even across revisions.
    snapshot.attempts[2]["input_attempt_ids"] = '["a", "b"]'
    assert impact.calculate(snapshot, "A")["impact"]["node_keys"] == ["A", "C", "D", "J"]


@pytest.mark.parametrize("status", ["running", "paused", "stopping"])
def test_only_idle_is_allowed(snapshot, status):
    snapshot.run["status"] = status
    assert "run_not_idle" in codes(impact.calculate(snapshot, "A"))


@pytest.mark.parametrize("field,value,reason", [
    ("executions", {"exec_id": "e", "status": "requested"}, "executions_in_progress"),
    ("executions", {"exec_id": "e", "status": "running"}, "executions_in_progress"),
    ("integrations", {"attempt_id": "b", "integration_status": "pending"}, "integrations_in_progress"),
    ("integrations", {"attempt_id": "b", "integration_status": "integrating"}, "integrations_in_progress"),
    ("conflict_sessions", {"merge_id": 42, "attempt_id": "b"}, "merge_conflict_open"),
    ("incomplete_operations", {"operation_id": "tm", "status": "partial_failed"}, "previous_operation_incomplete"),
    ("busy_runs", {"run_id": "other", "status": "stopping"}, "run_not_idle"),
])
def test_flow_wide_busy_and_partial_failure_block_even_unrelated_branch(snapshot, field, value, reason):
    getattr(snapshot, field).append(value)
    result = impact.calculate(snapshot, "A")
    assert not result["allowed"] and reason in codes(result)


def test_all_reasons_return_without_short_circuit(snapshot):
    snapshot.run["status"] = "running"
    snapshot.attempts[0]["input_source"] = "integration_head"
    snapshot.ledger_rows.clear()
    result = impact.calculate(snapshot, "A")
    assert {"run_not_idle", "integration_head_input", "commit_attribution_missing"} <= codes(result)
    assert result["impact"]["node_keys"] == ["A", "C", "D", "J"]


def test_missing_attribution_and_wrong_node_or_group_are_not_evidence(snapshot):
    snapshot.ledger_rows[0]["node_key"] = "B"
    snapshot.ledger_rows[2]["group_id"] = "other"
    result = impact.calculate(snapshot, "A")
    missing = next(r for r in result["reasons"] if r["code"] == "commit_attribution_missing")
    assert missing["attempt_ids"] == ["a", "j"]


def test_no_changes_is_attributed_without_a_git_commit(snapshot):
    snapshot.ledger_rows[0].update(state="no_commit", commit_sha=None, skip_reason="no_changes")
    snapshot.attempts[0]["integrated_commit"] = None
    result = impact.calculate(snapshot, "A")
    assert result["allowed"]
    assert not [c for c in result["impact"]["commits"] if c["attempt_id"] == "a"]


def test_document_only_needs_no_git_ledger_but_keeps_structural_descendants(snapshot):
    for attempt in snapshot.attempts:
        attempt.update(input_source="document_only", input_attempt_ids="[]")
    snapshot.ledger_rows.clear()
    result = impact.calculate(snapshot, "A")
    assert result["allowed"]
    assert result["impact"]["node_keys"] == ["A", "C", "D", "J"]
    assert result["impact"]["commits"] == []


def test_unrelated_integration_head_does_not_block_selected_branch(snapshot):
    snapshot.attempts[1]["input_source"] = "integration_head"
    assert impact.calculate(snapshot, "A")["allowed"]
    assert "integration_head_input" in codes(impact.calculate(snapshot, "B"))


def test_active_downstream_and_missing_legacy_attempts(snapshot):
    snapshot.attempts[3]["status"] = "active"
    snapshot.nodes[4]["status"] = "review"
    assert impact.calculate(snapshot, "A")["impact"]["attempts"][1]["status"] == "active"
    snapshot.attempts = [a for a in snapshot.attempts if a["node_key"] != "C"]
    assert "commit_attribution_missing" in codes(impact.calculate(snapshot, "A"))
    assert impact.calculate(snapshot, "R")["impact"]["node_keys"] == ["A", "B", "C", "D", "J", "R"]


@pytest.mark.parametrize("key", ["D", "missing", "X"])
def test_target_must_be_done_and_in_run_scope(snapshot, key):
    if key == "X":
        snapshot.nodes.append({"node_key": "X", "status": "done"})
    result = impact.calculate(snapshot, key)
    assert not result["allowed"]
    assert ("node_not_done" if key == "D" else "node_outside_run_scope") in codes(result)


@pytest.mark.parametrize("broken", ["revision", "json", "unresolved_input"])
def test_incomplete_history_fails_closed(snapshot, broken):
    if broken == "revision":
        snapshot.revisions.clear()
    elif broken == "json":
        snapshot.attempts[2]["input_attempt_ids"] = "{bad"
    else:
        snapshot.attempts[2]["input_attempt_ids"] = '["old-invalidated-attempt"]'
    assert "execution_history_incomplete" in codes(impact.calculate(snapshot, "A"))


@pytest.fixture
def history_db(monkeypatch, snapshot):
    """Minimal persisted read fixture; mutations outside this fixture are forbidden."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE graph_runs(run_id TEXT, group_id TEXT, status TEXT);
        CREATE TABLE graph_node_attempts(attempt_id TEXT, run_id TEXT, node_key TEXT, revision_no INTEGER,
            status TEXT, created_at TEXT, input_source TEXT, input_attempt_ids TEXT,
            integration_status TEXT, integrated_commit TEXT);
        CREATE TABLE graph_attempt_runs(exec_id TEXT, attempt_id TEXT, run_id TEXT, status TEXT);
        CREATE TABLE tr_commit_ledger(id INTEGER, group_id TEXT, doc_id TEXT, attempt_id TEXT,
            node_key TEXT, state TEXT, commit_sha TEXT, skip_reason TEXT);
        CREATE TABLE tokens(token_id TEXT, action_scope TEXT);
        CREATE TABLE graph_run_token_map(token_id TEXT, run_id TEXT, node_key TEXT, attempt_id TEXT, stage TEXT);
        CREATE TABLE workflow_events(id INTEGER, group_id TEXT, event_type TEXT, metadata TEXT);
        CREATE TABLE git_merge_session(merge_id INTEGER, group_id TEXT, status TEXT, kind TEXT, context TEXT);
        CREATE TABLE graph_run_events(run_id TEXT, event_seq INTEGER, event_type TEXT,
            cause TEXT, payload_json TEXT, created_at TEXT);
        INSERT INTO graph_runs VALUES ('run','flow','idle'), ('other-run','other-flow','running');
    """)
    def all_rows(sql, params):
        return [dict(r) for r in connection.execute(sql, params).fetchall()]
    monkeypatch.setattr(run_store, "_all", all_rows)
    monkeypatch.setattr(run_store, "available", lambda: True)
    monkeypatch.setattr(run_store, "active_run", lambda _flow: dict(snapshot.run))
    monkeypatch.setattr(run_store, "node_runs", lambda _run: deepcopy(snapshot.nodes))
    for a in snapshot.attempts:
        connection.execute("INSERT INTO graph_node_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (a["attempt_id"], a["run_id"], a["node_key"], 1, a["status"], "now",
                            a["input_source"], a["input_attempt_ids"], "integrated", a["integrated_commit"]))
    for r in snapshot.ledger_rows:
        connection.execute("INSERT INTO tr_commit_ledger VALUES (?,?,?,?,?,?,?,?)",
                           (r["id"], "flow", "doc-" + r["node_key"], r["attempt_id"],
                            r["node_key"], r["state"], r["commit_sha"], None))
    monkeypatch.setattr(repository.definition_store, "revision", lambda flow, number: {
        "nodes": [{"node_key": n.key, "node_type": n.node_type, "doc_type": n.doc_type, "label": n.label}
                  for n in snapshot.revisions[number].nodes],
        "edges": [e.as_wire() for e in snapshot.revisions[number].edges]})
    monkeypatch.setattr(repository.definition_store, "draft", Mock(side_effect=AssertionError("no Canvas")))
    monkeypatch.setattr(repository.definition_store, "latest_revision_no",
                        Mock(side_effect=AssertionError("no latest revision")))
    yield connection
    connection.close()


def test_snapshot_uses_current_attempts_and_token_authorship(history_db):
    history_db.executescript("""
        INSERT INTO graph_node_attempts VALUES ('a-old','run','A',1,'ended','before',NULL,NULL,NULL,NULL);
        INSERT INTO graph_node_attempts VALUES ('b-invalid','run','B',1,'invalidated','later',NULL,NULL,NULL,NULL);
        INSERT INTO tokens VALUES ('ta','new'),('tc','edit'),('review','review'),('invalid','new');
        INSERT INTO graph_run_token_map VALUES ('ta','run','A','a','instruction'),
            ('tc','run','C','c','result'),('review','run','B','b','result'),
            ('invalid','run','B','b-invalid','result');
        INSERT INTO workflow_events VALUES
            (1,'flow','token_consumed','{"doc_id":"instruction-A","token_id":"ta"}'),
            (2,'flow','token_consumed','{"doc_id":"report-C","token_id":"tc"}'),
            (3,'flow','token_consumed','{"doc_id":"instruction-A","token_id":"review"}'),
            (4,'flow','token_consumed','{"doc_id":"history-only","token_id":"invalid"}'),
            (5,'other-flow','token_consumed','{"doc_id":"foreign","token_id":"ta"}');
    """)
    result = impact.calculate(repository.read_snapshot("flow"), "A")
    assert result["allowed"]
    assert {a["attempt_id"] for a in result["impact"]["attempts"]} == {"a", "j", "c"}
    assert {d["doc_id"] for d in result["impact"]["documents"]} == {"instruction-A", "report-C"}
    assert next(d for d in result["impact"]["documents"] if d["doc_id"] == "instruction-A")["stage"] == "instruction"


def test_repository_collects_flow_wide_activity_and_only_graph_conflicts(history_db):
    history_db.executescript("""
        INSERT INTO graph_attempt_runs VALUES ('foreign','b','other-run','running'),
            ('local','b','run','requested');
        UPDATE graph_node_attempts SET integration_status='pending' WHERE attempt_id='b';
        INSERT INTO git_merge_session VALUES
            (1,'flow','open','graph_attempt_merge','{"attempt_id":"b"}'),
            (2,'other-flow','open','graph_attempt_merge','{}'),
            (3,'flow','open','group_update','{}'),
            (4,'flow','committed','graph_attempt_merge','{}');
    """)
    snap = repository.read_snapshot("flow")
    assert [e["exec_id"] for e in snap.executions] == ["local"]
    assert [a["attempt_id"] for a in snap.integrations] == ["b"]
    assert snap.conflict_sessions == [{"merge_id": 1, "attempt_id": "b"}]


def operation(db, seq, operation_id, status):
    db.execute("INSERT INTO graph_run_events VALUES (?,?,?,?,?,?)",
               ("run", seq, "graph_run_status_changed", repository.OPERATION_CAUSE,
                json.dumps({"operation_id": operation_id, "operation_status": status}), "now"))


def test_partial_failure_survives_other_operations_until_its_own_recovery(history_db):
    operation(history_db, 1, "old", "partial_failed")
    operation(history_db, 2, "new", "completed")
    assert repository.incomplete_operations("flow") == [
        {"operation_id": "old", "run_id": "run", "status": "partial_failed"}]
    assert "previous_operation_incomplete" in codes(impact.calculate(repository.read_snapshot("flow"), "A"))
    operation(history_db, 3, "old", "completed")
    assert repository.incomplete_operations("flow") == []


def test_require_safe_returns_same_contract_or_409_with_all_reasons(snapshot, monkeypatch):
    monkeypatch.setattr(service, "read_snapshot", lambda _flow: snapshot)
    monkeypatch.setattr(service, "in_transaction", lambda: True)
    allowed = service.require_safe("flow", "A", "en")
    assert allowed["allowed"]
    snapshot.run["status"] = "paused"
    with pytest.raises(GraphApiError) as raised:
        service.require_safe("flow", "A", "ja")
    assert (raised.value.status, raised.value.code) == (409, "graph_time_machine_blocked")
    assert raised.value.params["impact"] == allowed["impact"]
    assert raised.value.params["reasons"][0]["message"] == graph_messages.TIME_MACHINE_REASONS["run_not_idle"][2]


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_all_reason_and_error_messages_are_localized(locale):
    reasons = [{"code": c} for c in graph_messages.TIME_MACHINE_REASONS]
    rendered = graph_messages.time_machine_reasons(reasons, locale)
    assert all(r["message"] and r["message"] != r["code"] for r in rendered)
    assert graph_messages.RUN_EDIT_MESSAGES["graph_time_machine_blocked"][locale]
    assert graph_messages.RUN_EDIT_MESSAGES["graph_engine_owned"][locale]


@pytest.mark.parametrize("owner", ["graph", "conflict", "v01", "unassigned"])
@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_v01_ownership_refusal_preserves_v01(owner, locale, monkeypatch):
    monkeypatch.setattr(v01_contact._contact, "read_ownership",
                        lambda _flow: engine_router.Ownership(owner, None, None))
    monkeypatch.setattr(v01_contact._contact.definition_store, "latest_revision_no", lambda _flow: 3)
    request = Request({"type": "http", "headers": [(b"x-locale", locale.encode())]})
    response = v01_contact.time_machine_refusal({"doc_id": "doc", "group_id": "flow"}, request)
    if owner in ("v01", "unassigned"):
        assert response is None
    else:
        assert response.status_code == 409
        body = json.loads(response.body)
        assert body["error"] == ("graph_engine_owned" if owner == "graph" else "flow_ownership_conflict")
        assert body["message"] and body["group_id"] == "flow"


@pytest.mark.parametrize("name", ["reopen", "restore"])
def test_v01_routes_refuse_before_legacy_mutation(name, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.services import workflow_rework_service
    doc = {"doc_id": "doc", "group_id": "flow", "project_id": "project"}
    monkeypatch.setattr(db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(v01_contact._contact, "read_ownership",
                        lambda _flow: engine_router.Ownership("graph", None, None))
    monkeypatch.setattr(v01_contact._contact.definition_store, "latest_revision_no", lambda _flow: 1)
    forbidden = Mock(side_effect=AssertionError("legacy path must not be entered"))
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", forbidden)
    monkeypatch.setattr(workflow_rework_service, "reopen_to_target", forbidden)
    fn = getattr(routes, name + "_workflow")
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    response = fn(SimpleNamespace(doc_id="doc", target_seq=1, destination_seq=None),
                  current_user={"user_id": "user"})
    assert response.status_code == 409 and json.loads(response.body)["error"] == "graph_engine_owned"
    forbidden.assert_not_called()


def test_v01_reopen_route_calls_existing_service_unchanged(monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.services import workflow_rework_service
    monkeypatch.setattr(db_docs, "get_by_id", lambda _id:
                        {"doc_id": "doc", "group_id": "flow", "project_id": "project"})
    monkeypatch.setattr(v01_contact._contact, "read_ownership",
                        lambda _flow: engine_router.Ownership("v01", None, None))
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", Mock())
    monkeypatch.setattr(routes, "_reject_if_group_disposed", Mock())
    legacy = Mock(return_value={"ok": True, "legacy": True})
    monkeypatch.setattr(workflow_rework_service, "reopen_to_target", legacy)
    fn = routes.reopen_workflow
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    assert fn(SimpleNamespace(doc_id="doc", target_seq=7), current_user={"user_id": "u"}) == legacy.return_value
    assert legacy.call_args.kwargs["target_seq"] == 7


def test_v01_restore_route_preserves_existing_empty_return_point(monkeypatch):
    from contextlib import nullcontext
    from modules.flow_gate.db import connection, documents as db_docs, workflow_return_points
    from modules.flow_gate.documents.routers import documents as routes
    doc = {"doc_id": "doc", "group_id": "flow", "project_id": "project", "doc_review_status": "wf_in_progress"}
    monkeypatch.setattr(db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(v01_contact._contact, "read_ownership",
                        lambda _flow: engine_router.Ownership("v01", None, None))
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", Mock())
    monkeypatch.setattr(routes, "_reject_if_group_disposed", Mock())
    monkeypatch.setattr(routes, "_reject_if_revert_pending", Mock())
    monkeypatch.setattr(routes, "_group_workflow_root_doc", lambda _doc: doc)
    monkeypatch.setattr(connection, "get_store", lambda: SimpleNamespace(transaction=nullcontext))
    monkeypatch.setattr(workflow_return_points, "get_by_group", lambda _group: None)
    fn = routes.restore_workflow
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    assert fn(SimpleNamespace(doc_id="doc", destination_seq=None), current_user={"user_id": "u"}) == {
        "ok": True, "restored": [], "stopped_at": None, "stopped_doc_id": None, "reached_front": False,
        "root_status": "wf_in_progress", "return_point_cleared": False}
