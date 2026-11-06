"""flowgate.v02.0140 T0004 T#1-3 / T#2-3 — a scratch path conflict refused by the AI invoke admission keeps its safe
detail (code, message, phase, retryable, run id) in the Graph's failure state and its read.

The harness of ``test_fg_ai_roles_l2_assignment`` (real SQLite DB with every migration, the real Graph routes, the
stage assignment feature on). The AI start raises the admission's real 409 for a scratch path conflict
(``ai_invoke_service.start_run``'s ``scratch_path_conflict`` detail); everything after it is the product path:
``node_dispatch.start_ai`` -> ``graph_run.execution.start_failed`` (ledger) -> ``record_ai_failure`` ->
``stage_assignment.start_failed`` / ``fail`` -> ``graph_node_runs`` / ``graph_run_events`` -> the run read
(``GET /api/v1/flows/{flow}/runs/current``). The review loop's refused start goes through
``legacy_adapter.start_review_loop`` -> ``assigned_review._start_loop`` the same way. No AI, server or shared DB is used.

The suite's shared ``env`` fixture writes documents through the card document write gate; this module wires that
gate (``flowgate.card_document_edit.write_gate``, as the server startup does) before ``env`` and takes it back after
each test.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, X, _sql, assignments, command, db_path, decided, env, events, flow, ledger, node_run, one, roles,
    save_instruction, start, wired,
)

CONFLICT_RUN = "aiv_20261106_000101"
CONFLICT = {"code": "scratch_path_conflict",
            "message": "This run's scratch folder already exists for another run, so nothing was started. "
                       "Start the run again.",
            "phase": "scratch_create", "run_id": CONFLICT_RUN, "retryable": True}
SECRETS = ("C:\\storage", "C:/storage", "\\scratch\\", "/scratch/", "WinError", "Traceback", "execution_nonce")


@pytest.fixture(autouse=True)
def card_document_write_gate(monkeypatch):
    """The write gate the server startup registers (``card_document_edit.write_gate``), wired before ``env`` seeds its
    documents (autouse fixtures of this module are set up in name order: before ``wired``) and taken back after the
    test, as ``test_fg_provider_failure_retry_0121`` does."""
    from flowgate.card_document_edit import write_gate
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, write_gate.dispatch)
    yield


def _conflict(**overrides) -> HTTPException:
    return HTTPException(status_code=409, detail={**CONFLICT, **overrides})


def _read(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _wire(env, key):
    return next(n for n in _read(env)["node_runs"] if n["node_key"] == key)


def _node_events(env, key):
    rows = _sql(env["db"], "SELECT event_seq, cause, payload_json FROM graph_run_events WHERE node_key = ? "
                           "ORDER BY event_seq", (key,))
    return [(r["cause"], json.loads(r["payload_json"])) for r in rows]


def _seeded(monkeypatch, function, *args):
    """``test_fg_provider_failure_retry_0121``'s seed bypass: the old harness helpers write a fixture document's
    ``file_path`` directly (not an agent's card edit); every other document write keeps the real gate."""
    from modules.flow_gate.db import documents

    real_update = documents.update

    def seed_update(doc_id, updates):
        if set(updates) <= {"file_path", "doc_review_status"} and updates.get("file_path") == f"docs/{doc_id}.md":
            return documents._update_document(doc_id, updates)
        return real_update(doc_id, updates)

    with monkeypatch.context() as m:
        m.setattr(documents, "update", seed_update)
        return function(*args)


def _no_secret(value) -> None:
    text = json.dumps(value, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in text, secret


# ── the unit of the kept detail ───────────────────────────────────────────────

def test_the_kept_detail_is_the_safe_fields_of_the_refusal_only():
    from modules.flow_gate.graph_workflow.services import node_dispatch

    assert node_dispatch._start_failure_info(_conflict()) == CONFLICT
    assert node_dispatch._start_failure(_conflict()) == "start_refused:scratch_path_conflict"     # the code kept as is
    # a message that looks like a path / credential / stack is dropped, the code and phase stay
    for leaked in ("FileExistsError: [WinError 183] 'C:\\storage\\flowgate\\scratch\\FlowGate\\aiv_20261106_000101'",
                   "failed under /var/lib/flowgate/scratch/aiv_20261106_000101", "token tok_abc revoked",
                   "Traceback (most recent call last)", "nonce 5f0c mismatch"):
        info = node_dispatch._start_failure_info(_conflict(message=leaked))
        assert info == {k: v for k, v in CONFLICT.items() if k != "message"}, leaked
    # extra fields of a detail are never copied; an unsafe run id / phase is not kept
    info = node_dispatch._start_failure_info(_conflict(run_id="../../x", phase="x" * 80, scratch_dir="C:\\s", nonce="n"))
    assert info == {"code": "scratch_path_conflict", "message": CONFLICT["message"], "retryable": True}
    assert node_dispatch._start_failure_info(RuntimeError("spawn failed")) is None                 # nothing guessed
    assert node_dispatch._start_failure_info(HTTPException(409, detail="plain text")) is None
    long = node_dispatch._start_failure_info(_conflict(message="m" * 1000))
    assert len(long["message"]) == node_dispatch.START_FAILURE_MESSAGE_MAX


# ── the result stage's refused start: failure state, durable records and the run read ───────────────

def test_a_scratch_conflict_refusal_keeps_its_detail_in_the_graph_failure_state_and_read(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch

    started, accept = [], {"now": False}

    def starter(job):
        # recovery may redeliver a durable execution until it starts: each execution counted once (``record_job``)
        if job.get("graph_exec_id") not in {j.get("graph_exec_id") for j in started}:
            started.append(job)
        if not accept["now"]:
            raise _conflict()

    monkeypatch.setattr(node_dispatch, "_ai_starter", starter)
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)

    # the assignment failed once with the refusal's own code (no busy / duplicate retry)
    ra = one(env, key, "result")
    assert (ra["stage_state"], ra["fail_cause"]) == ("failed", "start_refused:scratch_path_conflict")
    row = node_run(env, key)
    assert (row["status"], row["dispatch_error_code"]) == ("open", "ai_unavailable:start_refused:scratch_path_conflict")
    # the ledger request was cancelled once by execution.start_failed (no requested / running execution remains)
    writes = [r for r in ledger(env) if r["kind"] == "write"]
    assert writes and all(r["status"] not in ("requested", "running") for r in writes), writes

    # durable: the stage_failed run event keeps the safe detail; the node's dispatch_failed event keeps it too
    failed = [p for _r, p in events(env, "stage_failed") if p.get("node_key") == key]
    assert len(failed) == 1
    assert failed[0]["fail_cause"] == "start_refused:scratch_path_conflict"
    assert failed[0]["fail_detail"] == CONFLICT
    node_failed = [p for cause, p in _node_events(env, key) if cause == "dispatch_failed"]
    assert len(node_failed) == 1
    assert node_failed[0]["dispatch"]["error_code"] == "ai_unavailable:start_refused:scratch_path_conflict"
    assert node_failed[0]["dispatch"]["start_failure"] == CONFLICT
    # the SSE payload the Graph published carries the same detail
    published = [e.payload for e in env["events"] if getattr(e, "payload", {}).get("cause") == "dispatch_failed"
                 and e.payload.get("node_key") == key]
    assert published and published[-1]["dispatch"]["start_failure"] == CONFLICT

    # the read path: the node wire carries the code and the detail, never a path / credential / stack
    wire = _wire(env, key)
    assert wire["dispatch"]["error_code"] == "ai_unavailable:start_refused:scratch_path_conflict"
    assert wire["dispatch"]["start_failure"] == CONFLICT
    assert wire["dispatch"]["start_failure"]["phase"] == "scratch_create"
    assert wire["dispatch"]["start_failure"]["retryable"] is True
    assert wire["status"] == "open" and not wire["node_running"]                   # no worker-less running card
    _no_secret(wire)
    run = _read(env)
    assert run["idle_reason"] == "error"                                           # the error family, settled
    assert len(started) == 1                                                       # a read never restarts it

    # an explicit command opens a new attempt; its start (accepted now) clears the error and the kept detail
    accept["now"] = True
    command(env)
    assert len(started) == 2 and started[-1]["provider_id"] == X                  # the same assigned AI
    wire = _wire(env, key)
    assert "start_failure" not in (wire["dispatch"] or {})
    assert not (wire["dispatch"] or {}).get("error_code")
    print("0140 graph:", ra["fail_cause"], "->", json.dumps(failed[0]["fail_detail"], sort_keys=True))


def test_a_start_failure_without_a_refusal_detail_keeps_the_old_record(env, decided, monkeypatch):
    """Any other start failure records exactly what it did before 0140: ``start_failed``, no detail."""
    from modules.flow_gate.graph_workflow.services import node_dispatch

    def failing(job):
        raise RuntimeError("spawn failed C:\\storage\\flowgate\\scratch\\x")

    monkeypatch.setattr(node_dispatch, "_ai_starter", failing)
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)
    ra = one(env, key, "result")
    assert (ra["stage_state"], ra["fail_cause"]) == ("failed", "start_failed")
    assert node_run(env, key)["dispatch_error_code"] == "ai_unavailable:start_failed"
    failed = [p for _r, p in events(env, "stage_failed") if p.get("node_key") == key]
    assert len(failed) == 1 and "fail_detail" not in failed[0]
    node_failed = [p for cause, p in _node_events(env, key) if cause == "dispatch_failed"]
    assert node_failed and "start_failure" not in node_failed[-1]["dispatch"]
    wire = _wire(env, key)
    assert "start_failure" not in wire["dispatch"]
    _no_secret(wire)


# ── the review loop's refused start (legacy_adapter.start_review_loop -> assigned_review._start_loop) ───────────

def test_a_refused_review_loop_start_keeps_the_conflict_detail_on_its_failed_assignment(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    calls = []

    def start_run(**kw):
        calls.append(kw)
        if kw.get("document_review_loop"):
            raise _conflict()
        return {"run_id": "aiv_20261106_000201", "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    _seeded(monkeypatch, save_instruction, env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["stage_state"], ir["fail_cause"]) == ("failed", "start_refused:scratch_path_conflict")
    assert node_run(env, key)["dispatch_error_code"] == "review_not_started:start_refused:scratch_path_conflict"
    failed = [p for _r, p in events(env, "stage_failed")
              if p.get("node_key") == key and p.get("stage") == "instruction_review" and p.get("seq") == 1]
    assert len(failed) == 1 and failed[0]["fail_detail"] == CONFLICT
    _no_secret(failed[0])
    assert len([c for c in calls if c.get("document_review_loop")]) == 1
