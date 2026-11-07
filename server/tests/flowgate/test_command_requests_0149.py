"""T-B: real SQLite, HTTP and Flow guard; discarded responses never authorize another step."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from pathlib import Path

import pytest
from test_fg_revision_adoption_precheck_0010 import (
    FA, _h, _sql, db_path, env, decided, candidate, document_write_ports, confirm_candidate, assess,
)
from test_fg_card_settings import node, put_draft, _run_one_card
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_service, result_binder
from modules.flow_gate.graph_workflow.domain.run_request import parse_start, parse_mode

RUN = "run_precheck"
BASE = f"/api/v1/flows/{FA}/runs"
X = "12345678-1234-4567-89ab-123456789abc"
Y = "12345678-1234-4567-89ab-123456789abd"


def post(env, cid=X, mode="step", user="u_kim"):
    body = {"mode": mode}
    if cid is not None: body["command_request_id"] = cid
    env.setdefault("arrived_command_ids", []).append(cid)
    return env["client"].post(BASE, json=body, headers={**_h(), "Authorization": "Bearer user:" + user})


def lookup(env, cid=X):
    return env["client"].get(BASE + "/commands/" + cid, headers=_h())


def void(env, cid=X, user="u_kim"):
    return env["client"].post(BASE + "/commands/" + cid + "/void", headers={**_h(), "Authorization": "Bearer user:" + user})


def payload(response):
    data = response.json()
    assert isinstance(data.pop("server_ts"), str)
    return data


def facts(env):
    tables = ["graph_runs", "graph_node_runs", "graph_node_attempts", "graph_run_events", "graph_propagations", "graph_run_command_requests"]
    return {t: _sql(env["db"], "SELECT * FROM " + t + " ORDER BY rowid") for t in tables}


def starts(env):
    return _sql(env["db"], "SELECT run_id,event_seq,cause FROM graph_run_events WHERE cause IN ('user_start','user_command') ORDER BY event_seq")


def forbidden(*args, **kwargs):
    raise AssertionError("registry/replay must not reconcile, settle or start jobs")


def watch_no_execution(monkeypatch):
    monkeypatch.setattr(run_service, "settle_if_needed", forbidden)
    monkeypatch.setattr(run_service, "_go_on", forbidden)
    monkeypatch.setattr(run_store, "create_run", forbidden)
    monkeypatch.setattr(result_binder, "reconcile_links", forbidden)


def test_b12_normal_response_replay_and_query_are_read_only(candidate, monkeypatch):
    env, *_ = candidate
    response = post(env)  # Normal response contract; discarded-response cases below use no response body.
    assert response.status_code == 201, response.text
    command = response.json()["command"]
    assert command == {"command_request_id": X, "run_id": RUN, "command_seq": 1, "replayed": False}
    assert len(starts(env)) == 1
    before = facts(env)
    jobs = []
    monkeypatch.setattr(run_service, "run_jobs", lambda work: jobs.extend(work))
    watch_no_execution(monkeypatch)
    found = lookup(env)
    assert found.status_code == 200
    assert payload(found) == {"ok": True, "found": True, "run_id": RUN, "command_seq": 1, "mode": "step", "cause": "user_command", "accepted_at": before["graph_run_command_requests"][0]["created_at"]}
    assert facts(env) == before  # Query alone.
    assert void(env).json()["found"] is True
    assert facts(env) == before  # Accepted void alone.
    replay = post(env)
    assert replay.status_code == 201, replay.text
    assert replay.json()["command"] == {**command, "replayed": True}
    assert facts(env) == before  # Replay alone; no extra step or event.
    assert jobs == []
    print("B12_IDLE", json.dumps({"arrived_ids": [X, X], "command_events": len(starts(env)), "additional_steps": 0}))


def test_b12_running_replays_before_active_rejection(candidate):
    env, *_ = candidate
    command = post(env).json()["command"]
    _sql(env["db"], "UPDATE graph_runs SET status='running',idle_reason=NULL WHERE run_id=?", (RUN,))
    before = facts(env)
    replay = post(env)
    assert replay.status_code == 201 and replay.json()["command"] == {**command, "replayed": True}
    assert facts(env) == before


@pytest.fixture
def fresh(env, decided):
    _run_one_card(env, {"kind": "human", "user_id": "u_kim", "provider_id": None})
    return env


def test_b7b_b12c_first_run_lost_and_replay(fresh):
    env = fresh
    post(env)  # Discard the entire HTTP response after the actual commit.
    accepted = run_store.command_request_of(FA, X)  # DB oracle, never a client response.
    command = {"command_request_id": X, "run_id": accepted["run_id"],
               "command_seq": accepted["command_seq"], "replayed": False}
    before = facts(env)
    assert len(before["graph_runs"]) == 1 and len(starts(env)) == 1
    assert starts(env)[0]["cause"] == "user_start"
    found = lookup(env).json()
    assert (found["run_id"], found["command_seq"]) == (command["run_id"], command["command_seq"])
    assert facts(env) == before
    second = post(env)
    assert second.status_code == 201 and second.json()["command"] == {**command, "replayed": True}
    assert facts(env) == before


@pytest.mark.parametrize("mode,user", [("full", "u_kim"), ("step", "u_lee")])
def test_b12d_conflict_does_not_execute(candidate, mode, user):
    env, *_ = candidate
    assert post(env).status_code == 201
    before = facts(env)
    response = post(env, mode=mode, user=user)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "command_request_conflict"
    assert response.json()["error"]["params"] == {"flow_id": FA, "command_request_id": X}
    assert facts(env) == before


def test_b12f_threads_serialize_same_identity(candidate):
    env, *_ = candidate
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(env), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    commands = [r.json()["command"] for r in responses]
    assert sorted(c["replayed"] for c in commands) == [False, True]
    assert {(c["run_id"], c["command_seq"]) for c in commands} == {(RUN, 1)}
    assert len(starts(env)) == 1
    assert len(_sql(env["db"], "SELECT * FROM graph_run_command_requests")) == 1
    indexes = _sql(env["db"], "PRAGMA index_list(graph_run_command_requests)")
    assert any(i["unique"] for i in indexes)
    print("B12_CONCURRENT", [c["command_request_id"] for c in commands], "events=1")


def test_b12e_rejection_has_no_registry_and_same_id_is_reassessed(candidate):
    env, *_ = candidate
    _sql(env["db"], "UPDATE graph_runs SET status='running' WHERE run_id=?", (RUN,))
    assert post(env).status_code == 409
    assert lookup(env).json()["found"] is False
    _sql(env["db"], "UPDATE graph_runs SET status='idle' WHERE run_id=?", (RUN,))
    assert post(env).status_code == 201
    assert len(starts(env)) == 1


def test_b12e_adoption_refusal_keeps_ta1_params_and_rolls_back(candidate):
    env, keys, *_ = candidate
    _sql(env["db"], "UPDATE graph_node_runs SET status='review' WHERE node_key=?", (keys["tmp_g"],))
    expected = assess(env)["error"]["params"]
    confirm_candidate()
    before = facts(env)
    response = post(env)
    assert response.status_code == 409
    assert response.json()["error"]["params"] == expected
    assert facts(env) == before
    assert lookup(env).json()["found"] is False


def test_b12e_transaction_failure_after_registry_insert_rolls_back(candidate, monkeypatch):
    env, *_ = candidate
    real = run_store.insert_command_request
    def fail_after(*args):
        real(*args)
        raise RuntimeError("injected transaction failure")
    before = facts(env)
    monkeypatch.setattr(run_store, "insert_command_request", fail_after)
    assert post(env).status_code == 500
    assert facts(env) == before
    monkeypatch.setattr(run_store, "insert_command_request", real)
    assert post(env).status_code == 201


def test_b12g_void_tombstone_idempotency_actor_and_late_arrival(candidate, monkeypatch):
    env, *_ = candidate
    before = facts(env)
    watch_no_execution(monkeypatch)
    assert payload(void(env)) == {"ok": True, "voided": True}
    assert payload(void(env)) == {"ok": True, "voided": True}
    assert payload(lookup(env)) == {"ok": True, "found": False, "voided": True}
    after = facts(env)
    assert {k:v for k,v in before.items() if k != "graph_run_command_requests"} == {k:v for k,v in after.items() if k != "graph_run_command_requests"}
    assert len(after["graph_run_command_requests"]) == 1
    denied = void(env, user="u_lee")
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "command_request_conflict"
    response = post(env)
    assert response.status_code == 409 and response.json()["error"]["code"] == "command_request_voided"
    assert facts(env) == after


@pytest.mark.parametrize("bad", ["invalid", "12345678-1234-1567-89ab-123456789abc", "12345678-1234-4567-79ab-123456789abc", " " + X])
def test_b12_invalid_uuid_all_routes(candidate, bad):
    env, *_ = candidate
    for response in (post(env, bad), lookup(env, bad), void(env, bad)):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
    assert len(starts(env)) == 0


def test_b12_uuid_uppercase_and_non_string(candidate):
    env, *_ = candidate
    for bad in [0, True, [], {}]: assert post(env, bad).status_code == 422
    assert post(env, X.upper()).json()["command"]["command_request_id"] == X
    assert lookup(env, X.upper()).json()["found"] is True


def test_b18_legacy_body_and_old_parser(candidate):
    env, *_ = candidate
    before = len(starts(env))
    response = post(env, None)
    assert response.status_code == 201
    assert set(response.json()) == {"ok", "server_ts", "run"}
    assert len(starts(env)) == before + 1
    assert _sql(env["db"], "SELECT * FROM graph_run_command_requests") == []
    body = {"revision_no": None, "mode": "step", "command_request_id": "ignored-by-old-parser"}
    assert parse_start(body) == (None, []) and parse_mode(body) == ("step", [])


def test_b12_read_write_permissions_and_missing_flow(candidate, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    env, *_ = candidate
    calls = []
    def permitted(user, project, permission):
        calls.append(permission)
        return permission == envelope.READ_PERMISSION
    monkeypatch.setattr(envelope, "has_permission", permitted)
    assert lookup(env).status_code == 200
    assert void(env).status_code == 403
    assert calls == [envelope.READ_PERMISSION, envelope.WRITE_PERMISSION]
    missing = env["client"].get("/api/v1/flows/missing/runs/commands/" + X, headers=_h())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "flow_not_found"


@pytest.mark.parametrize("days", [1, 30, 365])
def test_b17e_b19_server_age_never_allows_an_extra_step(candidate, days, monkeypatch):
    env, *_ = candidate
    post(env)  # Accepted and committed; discard the response completely.
    row = run_store.command_request_of(FA, X)
    accepted = {"command_request_id": X, "run_id": row["run_id"],
                "command_seq": row["command_seq"], "replayed": False}
    assert void(env, Y).json()["voided"] is True
    server_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = (server_now - timedelta(days=days)).isoformat()
    _sql(env["db"], "UPDATE graph_run_command_requests SET created_at=?", (old,))
    monkeypatch.setattr(run_store, "utc_now_ms", lambda: (server_now + timedelta(days=days)).isoformat())
    before = facts(env)
    assert lookup(env).json()["found"] is True
    assert lookup(env, Y).json()["voided"] is True
    assert facts(env) == before
    assert post(env).json()["command"] == {**accepted, "replayed": True}
    assert post(env, Y).json()["error"]["code"] == "command_request_voided"
    assert facts(env) == before
    # Only the next deliberate new logical command adds a start event.
    z = str(uuid4())
    assert post(env, z).status_code == 201
    intended_commands = {X, z}
    assert len(starts(env)) == len(intended_commands)
    assert {r["command_request_id"] for r in _sql(env["db"], "SELECT command_request_id FROM graph_run_command_requests WHERE state='accepted'")} == intended_commands
    print("B19_SERVER_AGE", json.dumps({"days": days, "arrived_ids": env["arrived_command_ids"], "events_before_new_click": 1, "extra_step": 0, "events_after_new_click": 2}))


def test_b17_registry_survives_run_deletion_and_only_flow_deletion_cleans_up(candidate):
    env, *_ = candidate
    command = post(env).json()["command"]
    assert void(env, Y).status_code == 200
    _sql(env["db"], "DELETE FROM graph_runs WHERE run_id=?", (RUN,))
    assert lookup(env).json()["found"] is True
    replay = post(env)
    assert replay.status_code == 201 and replay.json()["run"] is None
    assert replay.json()["command"] == {**command, "replayed": True}
    assert lookup(env, Y).json()["voided"] is True
    _sql(env["db"], "DELETE FROM groups WHERE group_id=?", (FA,))
    assert _sql(env["db"], "SELECT * FROM graph_run_command_requests") == []


def test_b12_answer_after_jobs_preserves_identity(candidate):
    env, *_ = candidate
    response = post(env)
    result = response.json()
    group = {"group_id": FA, "project_id": "flowgate"}
    answer = run_service.answer_after_jobs(result, group["project_id"], [lambda: None], True)
    assert answer["command"] == result["command"]


def test_b17_no_ttl_or_registry_purge_in_product():
    root = Path(__file__).resolve().parents[2]
    for dialect in ("sqlite", "mysql", "postgres"):
        ddl = (root / "sql/migrations" / dialect / "180_graph_run_command_requests.sql").read_text(encoding="utf-8")
        assert "expires" not in ddl.lower() and "ttl" not in ddl.lower()
        assert "REFERENCES graph_runs" not in ddl
    import re
    files = list((root / "flowgate").rglob("*.py")) + list((root / "modules/flow_gate").rglob("*.py"))
    for file in files:
        text = file.read_text(encoding="utf-8")
        assert not re.search(r"DELETE\s+FROM\s+graph_run_command_requests", text, re.I), file


def test_old_unregistered_get_and_post_are_not_envelopes(candidate):
    env, *_ = candidate
    # The old app has no matching API route; main's GET catch-all returns this same body.
    get = env["client"].get("/api/v1/old-unregistered", headers=_h())
    post_response = env["client"].post("/api/v1/flows/unknown/not-registered", headers=_h())
    assert get.status_code == 404 and get.json() == {"detail": "Not Found"}
    assert post_response.status_code == 404 and post_response.json() == {"detail": "Not Found"}
    # Real GET-only fallback gives POST 405 (Starlette routing).
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    old = FastAPI()
    @old.get("/{path:path}")
    def catch_all(path):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    client = TestClient(old)
    assert client.post(BASE + "/commands/" + X + "/void").status_code == 405


def test_b12_real_postcommit_work_is_not_repeated(candidate, monkeypatch):
    fresh = candidate[0]
    _sql(fresh["db"], "UPDATE graph_runs SET idle_reason='step_done' WHERE run_id=?", (RUN,))
    calls = []
    real_jobs = run_service.run_jobs
    def run_jobs(work):
        calls.extend(work)
        return real_jobs(work)
    monkeypatch.setattr(run_service, "run_jobs", run_jobs)
    first = post(fresh)
    assert first.status_code == 201, first.text
    command = first.json()["command"]
    count = len(calls)
    assert count >= 2  # Real reconcile_stage + command_review post-commit jobs.
    before = facts(fresh)
    replay = post(fresh)
    assert replay.status_code == 201 and replay.json()["command"] == {**command, "replayed": True}
    assert len(calls) == count and facts(fresh) == before
    assert len(starts(fresh)) == 1
    print("B12_REAL_JOBS", count, "repeat_jobs=0", "events=1")


def test_b12_unaccepted_and_current_read_have_separate_effects(candidate):
    env, *_ = candidate
    before = facts(env)
    assert lookup(env).json()["found"] is False
    assert facts(env) == before
    response = env["client"].get(BASE + "/current", headers=_h())
    assert response.status_code == 200
    after_current = facts(env)
    # /current may reconcile; its measured facts are deliberately separate from registry reads.
    assert after_current["graph_run_command_requests"] == []
    assert after_current["graph_runs"] == before["graph_runs"]
    assert after_current["graph_node_runs"] == before["graph_node_runs"]
    assert len(starts(env)) == 0
    print("B7_NOT_DELIVERED", "lookup_found=false", "current_read_measured=true", "events=0")


@pytest.mark.parametrize("body", [None, [], 1, "body"])
def test_b18_no_id_validation_retains_legacy_error_shape(candidate, body):
    env, *_ = candidate
    response = env["client"].post(BASE, json=body, headers=_h())
    if body is None:
        assert response.status_code == 201 and "command" not in response.json()
    else:
        assert response.status_code == 422
        assert response.json()["error"]["params"]["errors"] == [{"field": "body", "reason": "not_an_object"}]


def test_b12_authentication_precedes_command_validation(candidate):
    env, *_ = candidate
    paths = [("get", BASE + "/commands/invalid"), ("post", BASE + "/commands/invalid/void"), ("post", BASE)]
    for method, path in paths:
        response = getattr(env["client"], method)(path)
        assert response.status_code == 401
    assert _sql(env["db"], "SELECT * FROM graph_run_command_requests") == []


@pytest.mark.parametrize("days", [1, 30])
def test_b19_undelivered_after_age_retries_same_id_once(candidate, monkeypatch, days):
    env, *_ = candidate
    monkeypatch.setattr(run_store, "utc_now_ms", lambda: (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)).isoformat())
    before = facts(env)
    assert lookup(env).json()["found"] is False
    assert facts(env) == before
    assert post(env).status_code == 201
    command = lookup(env).json()
    assert command["found"] is True
    before_replay = facts(env)
    assert post(env).json()["command"]["replayed"] is True
    assert facts(env) == before_replay
    assert env["arrived_command_ids"] == [X, X]
    assert len(starts(env)) == 1
    print("B19_UNDELIVERED", days, "arrived_ids=", env["arrived_command_ids"], "intended_commands=1 events=1 extra_step=0")


def test_b12_held_branch_records_its_actual_command_sequence(candidate, monkeypatch):
    from modules.flow_gate.services import graph_execution_hooks
    env, *_ = candidate
    _sql(env["db"], "UPDATE graph_runs SET idle_reason='step_done' WHERE run_id=?", (RUN,))
    real = graph_execution_hooks.call
    held = []
    def call(name, *args, **kwargs):
        if name == "retry_integrations": return True
        if name == "hold_command": held.append(args[0].run_id); return None
        return real(name, *args, **kwargs)
    monkeypatch.setattr(graph_execution_hooks, "call", call)
    response = post(env)
    assert response.status_code == 201, response.text
    assert held == [RUN]
    assert response.json()["command"]["command_seq"] == starts(env)[0]["event_seq"]
    before = facts(env)
    assert post(env).json()["command"]["replayed"] is True
    assert facts(env) == before and held == [RUN]
