"""flowgate.v02.0121 T#5 — a card's result writer whose provider failed (``provider_failed``: "Selected model is at
capacity", a dropped stream) and registered no result.

B flowgate.v02.0121.0001-B / NR 0003-NR: the failed AI run's end was cleaned up (its execution ended, no hand-off, the
run idle ``error``) but its result assignment stayed ``ai/active`` and an explicit [다음 단계] / [전체 실행] judged the
node ``none``: the command was accepted and no execution was requested. Now (T#2) that end is written again once, on the
same attempt by the same assigned AI (``RETRY_SAME_ATTEMPT``), only by an explicit command; every other failed end keeps
its own policy.

Two harnesses, both on the real SQLite DB, routes, Graph engine and execution ledger of ``test_fg_question_pending_0111``:

- **The product path** (``_admit`` / ``_Provider`` / ``_worker_ends``, rej_01M6FFMXBCPX855V): the node's AI start
  (``ai_node_starter.start_ai_invoke``, real) reaches the admission's Graph steps in their order — the launcher's claim
  (``scheduling.admit``), its attempt workspace (``admission._require_group_worktree`` -> ``workspace.prepare``), its run
  scratch (``_create_scratch``) and token — and the run the admission builds; then the worker's attempt runs through the
  real provider chain and CLI boundary (``worker._execute_provider_chain`` -> ``provider_cli._cli_execute`` ->
  ``_execute_local``: the execution's acquire, ``execution.launch``), the end classification, the judge and the real
  ``finalize._finalize_run`` (``report_refused``, ``terminal.cleanup`` -> ``execution.finish`` -> the ledger end and the
  run-end hand-off, the lease release, the durable ``ai_invoke_runs`` record, the run-end port, ``handoff_end``,
  ``settle``). Stand-ins: the provider *process* only (``provider_cli._CliProcess``: it "ran" and exited with a code and
  an NDJSON stdout, ``_Provider``), the CLI progress watchdog thread, the 3 s judge settle wait and the 15 s fast-fail
  window (``ORACLE_SETTLE_SEC`` / ``FAST_FAIL_WINDOW_SEC`` 0: the process is taken as having run longer). The worker
  thread itself is the test's (``_worker_ends`` calls the worker's steps in its order, and checks the worker would not
  open a no-output retry). For the attempt workspace (``_git_on``) an isolated Git repository is the group worktree: the
  attempt worktree is prepared by the real ``workspace.prepare`` (Git lock manager, the group slot lookup and the
  project's Git config read stand in).
- **The record harness** (``_failed_writer``): the AI invoke admission is a recorder, the failed AI run's finalized
  ``ai_invoke_runs`` row is written as the finalize writes it and its execution ends in the ledger; the run-end port runs
  inside the execution's run-end hand-off. Used for the failure-type boundaries (each stop code as finalized).

Nothing calls a real AI.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_fg_card_approval_0056 import _current, _run_id
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, AI, FA, _h, _sql, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import (
    WORK_RUN, _admin_and_reviewer, _executions_ended, _result, _review_status, _run_ended, _start_ai,
    _through_instruction,
)
from test_fg_question_pending_0111 import QUESTION, ROOT, _ended, card_outputs_port, questions  # noqa: F401

import config  # noqa: E402,F401 — its import-time env export happens at collection, not in a test (the CLI prompt reads it)

pytestmark = pytest.mark.usefixtures("legacy_cards")

@pytest.fixture(autouse=True)
def document_write_port_0138(monkeypatch):
    # This older harness seeds documents before its app lifespan. Register the
    # current real write gate explicitly; do not depend on earlier test modules.
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from flowgate.card_document_edit import write_gate
    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, write_gate.dispatch)
    # These old helpers load prior instruction/result fixture documents, not an
    # agent's authorized card edit. Keep the real gate on the product path.
    import test_fg_card_settings_review as review_harness
    import sys
    from modules.flow_gate.db import documents
    real_update = documents.update
    def seed_update(doc_id, updates):
        if (set(updates) <= {"file_path", "doc_review_status"}
                and updates.get("file_path") == f"docs/{doc_id}.md"):
            return documents._update_document(doc_id, updates)
        return real_update(doc_id, updates)
    def seed_call(function):
        def call(*args, **kwargs):
            with monkeypatch.context() as m:
                m.setattr(documents, "update", seed_update)
                return function(*args, **kwargs)
        return call
    monkeypatch.setattr(review_harness, "_write_written", seed_call(review_harness._write_written))
    monkeypatch.setattr(sys.modules[__name__], "_result", seed_call(_result))


RETRY_RUN = "air_card_work_retry"
# product-path AI run ids (the admission's own form, ``runtime._RUN_ID_RE``)
FAILED_AI_RUN, RETRY_AI_RUN = "aiv_20261103_900001", "aiv_20261103_900002"
CAPACITY = "Selected model is at capacity. Please try a different model."
CAPACITY_REASON = (f'"AI aip_assignee" failed (exit code 1): {CAPACITY} '
                   "The run ended without completing its work.")
STDOUT = ('{"type":"item.completed","item":{"type":"command_execution","exit_code":0}}\n'
          '{"type":"turn.failed","error":{"message":"' + CAPACITY + '"}}\n')
COMPLETED = '{"type":"turn.completed"}\n'
PROVIDER = {"id": "aip_assignee", "name": "aip_assignee", "exec_type": "cli", "kind": "codex",
            "cli_command": "codex exec --json"}
OTHER_CLIENT = {"Authorization": "Bearer user:u_lee", "x-canvas-client-id": "tab_2"}


# ── record harness ────────────────────────────────────────────────────────────

def _two_cards(env):
    """A confirmed R -> T1 -> T2 Flow (FA) of two AI cards; returns (T1, T2) node keys (the run not started)."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T", assignee=AI), node("tmp_b", "T", assignee=AI)],
                      [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_b")])
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    return first["key_map"]["tmp_a"], first["key_map"]["tmp_b"]


def _command(env, mode="step", expect=(200, 201), headers=None, client=None):
    """[다음 단계] (``step``) / [전체 실행] (``full``) as the GUI sends it (``flowRunApi.startFlowRun``)."""
    res = (client or env["client"]).post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode},
                                         headers=headers or _h())
    assert res.status_code in expect, res.text
    return res


def _finalized(env, run_id, exec_id, *, end_reason="exited", stop_code="provider_failed", exit_code=1,
               stop_reason=CAPACITY_REASON, register_errors=None):
    """Record harness: the AI run's execution ended in the ledger, and its finalized row (as the finalize writes it)."""
    _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'ended', ai_run_id = ?, ended_at = '2026-11-03T07:17:06Z' "
                    "WHERE exec_id = ?", (run_id, exec_id))
    _sql(env["db"], "INSERT INTO ai_invoke_runs (run_id, group_id, project_id, doc_ref, mode, status, outcome, end_reason, "
                    "stop_code, stop_reason, exit_code, stdout_tail, register_errors, provider_id, issued_to, started_at, "
                    "finished_at, created_at, updated_at) VALUES (?, ?, 'flowgate', ?, 'single', 'finished', 'none', ?, "
                    "?, ?, ?, ?, ?, 'aip_assignee', 'u_kim', 't', 't', 't', 't')",
         (run_id, FA, ROOT, end_reason, stop_code, stop_reason, exit_code, STDOUT, register_errors))


def _to_result_stage(env, monkeypatch):
    """Two AI cards started; the first one's instruction stage walked: -> (T1, T2, the result stage's requested job)."""
    _admin_and_reviewer(env)
    first, second = _two_cards(env)
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert res.status_code == 201, res.text
    _through_instruction(env, monkeypatch)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert job["stage"] == "result" and job["node_key"] == first
    return first, second, job


def _failed_writer(env, monkeypatch, *, run_id=WORK_RUN, **ending):
    """Record harness: an AI card through its instruction stage whose result writer ended with ``ending`` (default: the
    provider at capacity, exit 1) and no result. -> (node key, the result stage's job, the second card's key)."""
    first, second, job = _to_result_stage(env, monkeypatch)
    _start_ai(monkeypatch, job, run_id)
    _finalized(env, run_id, job["graph_exec_id"], **ending)
    fields = {k: ending[k] for k in ("end_reason", "stop_code") if k in ending}
    _ended(env, run_id, job["graph_exec_id"], **{"stop_code": "provider_failed", "outcome": "none", **fields})
    return first, job, second


def _row(env, key):
    return _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]


def _executions(env, attempt_id=None):
    sql = "SELECT exec_id, attempt_id, kind, status, ai_run_id, request_json, assignment_stage, assignment_seq, " \
          "lease_token, lease_expires_at, cancel_requested_at, ended_at " \
          "FROM graph_attempt_runs" + (" WHERE attempt_id = ?" if attempt_id else "") + " ORDER BY created_at, exec_id"
    return _sql(env["db"], sql, (attempt_id,) if attempt_id else ())


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status, invalidated_reason, input_commit, input_source "
                           "FROM graph_node_attempts WHERE node_key = ? ORDER BY created_at", (key,))


def _assignment(env, key, stage="result"):
    rows = _sql(env["db"], "SELECT * FROM graph_stage_assignments WHERE node_key = ? AND stage = ? AND seq = 0",
                (key, stage))
    return rows[0] if rows else None


def _wire(env, key):
    run = _current(env)
    return run, next(n for n in run["node_runs"] if n["node_key"] == key)


def _verdict(env, key):
    from modules.flow_gate.graph_workflow.services import card_settings_ports, run_apply
    from modules.flow_gate.graph_workflow.store import run_store

    graph = run_store.run_by_id(_run_id(env))
    ctx = run_apply.load_context(graph, "flowgate", "u_kim")
    return card_settings_ports.stage_needs_dispatch(ctx, _row(env, key))


def _hooks(name, row):
    from modules.flow_gate.services import graph_execution_hooks

    return graph_execution_hooks.call(name, row)


# ── product path: the admission, the provider process, the worker's end ──────

class _Provider:
    """The provider *process* boundary only (``provider_cli._CliProcess``: spawn / communicate / reap): a CLI that ran,
    printed ``stdout`` and exited with ``exit_code``. ``work(run_id)`` runs while it "runs" — what the AI does meanwhile
    (its source writes, its inbox registration). Everything around it — the CLI launch contract, the execution acquire
    (``_execute_local`` -> ``execution.launch``), the output tails, the invocation journal — is the product's."""

    def __init__(self, monkeypatch, *, exit_code=1, stdout=STDOUT, work=None):
        from modules.flow_gate.services import ai_invoke_service
        from modules.flow_gate.services.ai_invoke import provider_cli

        self.exit_code, self.stdout, self.work, self.spawned = exit_code, stdout, work, []
        boundary = self

        class _Process:
            def __init__(self, run_id):
                self.run_id, self.proc = run_id, None

            def start(self, command, kwargs):
                boundary.spawned.append({"run_id": self.run_id, "cwd": kwargs.get("cwd")})
                code = boundary.exit_code
                self.proc = SimpleNamespace(returncode=code, pid=0, poll=lambda: code)
                return True

            def terminate(self):
                pass

            def communicate(self, data, timeout):
                if boundary.work is not None:
                    boundary.work(self.run_id)
                return boundary.stdout.encode("utf-8"), b"", False, None

            def close(self):
                pass

        monkeypatch.setattr(provider_cli, "_CliProcess", _Process)
        # the no-progress watchdog thread of a live process (not under test here)
        monkeypatch.setattr(provider_cli, "_start_progress_watchdog", lambda run, proc, *a, **kw: (None, None))
        monkeypatch.setattr(provider_cli, "_stop_progress_watchdog", lambda *a, **kw: None)
        # the process is taken as having run past the startup window, and the judge does not wait 3 s
        monkeypatch.setattr(ai_invoke_service, "FAST_FAIL_WINDOW_SEC", 0)
        monkeypatch.setattr(ai_invoke_service, "ORACLE_SETTLE_SEC", 0)


def _admit(monkeypatch, job, run_id):
    """The node's AI start (``ai_node_starter.start_ai_invoke``, real) into the admission's Graph steps, in the order
    ``admission.start_run`` takes them for a ``graph_exec_id``: the launcher's claim (``admit``), its attempt workspace
    (``_require_group_worktree`` -> ``prepare_workspace``), its run scratch, its token (the start's issuer) and the run
    as the admission builds it (the fields the worker / finalize read). -> the run."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service as svc, graph_execution_hooks, mention_service
    from modules.flow_gate.services.ai_invoke import admission

    made = []

    def start_run(**kw):
        exec_id, group_id, project_id = kw["graph_exec_id"], kw["group_id"], kw["project_id"]
        from uuid import uuid4
        from modules.flow_gate.db import ai_run_identity
        started_at = admission.now_iso()
        execution_nonce = str(uuid4())
        ai_run_identity.initialize(scratch_roots=[], all_issuers_stopped=True)
        ai_run_identity.claim({"run_id": run_id, "execution_nonce": execution_nonce,
                               "project_id": project_id, "group_id": group_id, "started_at": started_at})
        baseline = max([int(d.get("seq") or 0) for d in db_docs.get_documents_by_group_id(group_id)] or [0])
        attempt_id = graph_execution_hooks.call("admit", exec_id, run_id, group_id, kw.get("graph_attempt_id"))
        root = admission._require_group_worktree(project_id, kw["module"], group_id, "main",
                                                 action_scope=kw["action_scope"], graph_exec_id=exec_id)
        scratch = svc._create_scratch(project_id, run_id, execution_nonce)
        issue = kw["issue_builder"](ai_run_id=run_id)
        restart = kw.get("node_restart_max_attempts")
        made.append({
            "graph_exec_id": exec_id, "graph_attempt_id": attempt_id,
            "invocation_origin": dict(kw["invocation_origin"]) if kw.get("invocation_origin") else None,
            "run_id": run_id, "execution_nonce": execution_nonce, "status": "running", "mode": "single", "project_id": project_id, "module": kw["module"],
            "group_id": group_id, "doc_ref": kw["doc_ref"], "action_scope": kw["action_scope"],
            "issued_to": kw["issued_to"], "docs_target": 1, "chain_id": run_id, "chain_docs_target": 1,
            "chain_docs_reached": 0, "chain_docs_accounted": False, "baseline_seq": baseline, "timeout_sec": 3600,
            "provider": None, "provider_id": None, "requested_provider_id": kw.get("provider_id"),
            "attempt_no": 0, "fallback_history": [], "register_errors": [], "tool_call_misses": 0,
            "turn_limit_exhausted": False, "oracle_mismatch": False, "started_at": started_at,
            "started_mono": time.monotonic(), "attempt_started_mono": time.monotonic(),
            "cancel_event": threading.Event(), "proc": None, "timed_out": False, "end_reason": None,
            "exit_code": None, "last_message": None, "last_message_received": False, "outcome": None,
            "docs_reached": 0, "reached_doc_ids": [], "source_dirty": None, "source_dirty_files": [],
            "stdout_tail": None, "stderr_tail": None, "scratch_dir": str(scratch),
            "token_scratch_dir": issue["scratch_dir"], "raw_token": issue["raw_token"], "token_id": issue["token_id"],
            "dirty_baseline": svc._git_status_paths(root), "source_root": str(root) if root else None,
            "api_base_url": kw["api_base_url"],
            "node_restart_open": kw["action_scope"] == "new" and restart is not None, "attempts_max": restart,
        })
        return {"run_id": run_id, "status": "running"}

    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: "mention")
    monkeypatch.setattr(svc, "start_run", start_run)
    ai_node_starter.start_ai_invoke(job)
    [run] = made
    return run


def _worker_ends(run):
    """The worker's attempt and end for ``run`` (``worker._worker`` without its thread): the provider chain through the
    CLI boundary, the end classification, the judge — the worker opens no no-output retry here — then the real
    ``finalize._finalize_run``."""
    from modules.flow_gate.services.ai_invoke import finalize, worker

    run["_provider_active"] = True
    try:
        started = worker._execute_provider_chain(run, [dict(PROVIDER)], "prompt")
    finally:
        run["_provider_active"] = False
    worker._classify_end_reason(run, started)
    finalize._judge_hop(run)
    run["attempts_used"] = int(run.get("attempts_used") or 0) + 1
    assert worker._retry_eligible(run) is False, run.get("retry_block_reason")   # the worker ends the hop here
    finalize._finalize_run(run)
    return run


def _ai_failed(env, monkeypatch, *, run_id=FAILED_AI_RUN, work=None, stdout=STDOUT, exit_code=1):
    """Two AI cards; the first one's result writer admitted and run through the product path, its provider exiting
    ``exit_code`` with ``stdout`` (default: ``turn.failed`` "at capacity", exit 1). -> (T1, T2, its job, its run,
    the provider boundary)."""
    first, second, job = _to_result_stage(env, monkeypatch)
    run = _admit(monkeypatch, job, run_id)
    provider = _Provider(monkeypatch, exit_code=exit_code, stdout=stdout, work=work)
    _worker_ends(run)
    return first, second, job, run, provider


def _stale_copy(run):
    """The same AI run's end told again later by another holder (a second finalizer, a recovery): the durable facts of
    ``run`` and its old lease token, none of this process' in-memory terminal marks."""
    keep = ("graph_exec_id", "graph_attempt_id", "invocation_origin", "run_id", "execution_nonce", "mode", "project_id", "module", "group_id",
            "doc_ref", "action_scope", "issued_to", "docs_target", "chain_id", "chain_docs_target", "baseline_seq",
            "timeout_sec", "provider", "provider_id", "requested_provider_id", "attempt_no", "started_at", "started_mono",
            "end_reason", "exit_code", "stdout_tail", "stderr_tail", "outcome", "docs_reached", "scratch_dir",
            "token_scratch_dir", "token_id", "source_root", "api_base_url", "graph_lease_token")
    stale = {k: run.get(k) for k in keep}
    stale.update(status="running", cancel_event=threading.Event(), proc=None, timed_out=False, fallback_history=[],
                 register_errors=[], reached_doc_ids=[], last_message=None, last_message_received=False)
    return stale


def _ledger_view(env):
    return [(e["exec_id"], e["status"], e["lease_token"], e["ai_run_id"], e["cancel_requested_at"])
            for e in _executions(env)]


# ── 1: the failure's whole end through the product path (T#1) ────────────────

def test_a_provider_failure_ends_through_the_real_finalize_and_releases_what_its_execution_held(env, decided,
                                                                                                 monkeypatch, questions):
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.db import ai_invoke_runs, group_ai_leases

    seen = {}

    def while_running(run_id):
        # the provider process is alive: its execution is acquired and the card is locked as running
        run, wire = _wire(env, key_box["key"])
        row = ledger.get(job_box["job"]["graph_exec_id"])
        seen.update(row=dict(row), working=ledger.working(_run_id(env)), status=run["status"],
                    node_running=wire["node_running"])

    key_box, job_box = {}, {}
    first, second, job = _to_result_stage(env, monkeypatch)
    key_box["key"], job_box["job"] = first, job
    run = _admit(monkeypatch, job, FAILED_AI_RUN)
    exec_id = job["graph_exec_id"]
    assert ledger.get(exec_id)["ai_run_id"] == FAILED_AI_RUN and ledger.get(exec_id)["status"] == "requested"  # admitted
    provider = _Provider(monkeypatch, work=while_running)
    _worker_ends(run)

    # while it ran: the execution was acquired with this run's lease token, the run worked, the card was locked
    assert seen["row"]["status"] == "running" and seen["row"]["lease_token"] == run["graph_lease_token"]
    assert seen["working"] is True and seen["status"] == "running" and seen["node_running"] is True
    assert [s["run_id"] for s in provider.spawned] == [FAILED_AI_RUN]

    # the end, classified from the provider's own words and recorded (T#1.1)
    assert (run["end_reason"], run["outcome"], run["exit_code"]) == ("exited", "none", 1)
    assert run["stop_code"] == "provider_failed" and "at capacity" in run["stop_reason"]
    assert run["status"] == "finished"
    stored = ai_invoke_runs.get(FAILED_AI_RUN)
    assert (stored["status"], stored["end_reason"], stored["stop_code"], stored["exit_code"], stored["outcome"]) == \
        ("finished", "exited", "provider_failed", 1, "none")
    assert stored["graph_exec_id"] == exec_id and "turn.failed" in (stored["stdout_tail"] or "")   # the log is kept
    # the terminal cleanup owned its end: the ledger took it with the run's own token, nothing pending or failed
    assert run["graph_execution_finished"] is True and run["graph_end_owned"] is True
    assert not run.get("graph_lease_lost") and run["lease_cleanup_pending"] is False
    assert not {"graph_execution_end", "lease_release"} & set(run.get("terminal_cleanup_errors") or {})
    ended = ledger.get(exec_id)
    assert (ended["status"], ended["lease_token"], ended["ai_run_id"]) == ("ended", run["graph_lease_token"],
                                                                           FAILED_AI_RUN)
    assert ended["ended_at"]
    # what it held is released (T#1.2): no execution works, no hand-off, no group AI lease, the run settled idle/error
    graph_run = _run_id(env)
    assert not ledger.working(graph_run) and not ledger.handing_off(graph_run)
    assert group_ai_leases.get(FA) is None
    graph, wire = _wire(env, first)
    assert (graph["status"], graph["idle_reason"]) == ("idle", "error")
    assert wire["node_running"] is False                                                   # the card is not locked
    assert wire["status"] == "open" and wire["result_doc_id"] is None                       # not completed, not skipped
    assert wire["dispatch"]["error_code"] == "ai_invoke_failed"
    assert wire["dispatch"]["error_detail"] == "provider_capacity"                          # T#4: the short cause
    assert CAPACITY not in json.dumps(wire)                                                  # never the provider's output
    assert _row(env, second)["status"] == "pending"
    a = _assignment(env, first)
    assert (a["actor"], a["stage_state"], a["fail_cause"]) == ("ai", "active", None)        # T#1.5: no state rewritten
    assert _hooks("provider_failure_retry_due", _row(env, first)) is True
    assert _verdict(env, first) == "retry_same_attempt"                                     # was ``none`` (NR §4.2)
    assert env["jobs"] == [] and questions["responders"] == []                              # nothing started by itself

    # [다음 단계]: the same card, the same attempt, a new execution; the failed one kept as it ended
    _command(env, "step")
    [again] = env["jobs"]
    assert (again["node_key"], again["stage"], again["provider_id"]) == (first, "result", job["provider_id"])
    assert again["invocation_origin"]["purpose"] == "result_write_retry"
    rows = _executions(env, _attempts(env, first)[0]["attempt_id"])
    assert [(e["exec_id"], e["status"]) for e in rows if e["assignment_stage"] == "result"] == \
        [(exec_id, "ended"), (again["graph_exec_id"], "requested")]
    assert ledger.get(exec_id) == ended
    graph, wire = _wire(env, first)
    assert graph["status"] == "running" and wire["node_running"] is True and "error_code" not in (wire["dispatch"] or {})


def test_the_retry_admitted_through_the_product_path_completes_the_card_and_the_next_one_follows(env, decided,
                                                                                                monkeypatch, questions):
    """T#2 / T#5.2: after the failure's real end, [전체 실행] retries the card once; the retry's own run (admitted, acquired,
    its result registered while it runs, ended by the real finalize) completes the card and the next card opens."""
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    _command(env, "full")
    [again] = env["jobs"]
    env["jobs"].clear()
    retry = _admit(monkeypatch, again, RETRY_AI_RUN)
    registered = {}

    def registers(run_id):
        # the AI registers its result through the inbox while it runs (the Graph contact, then its token's consume)
        result = _result(82, "TR")
        v01_contact.after_inbox_registered(FA, result, "TR", retry["token_id"])
        token_service.consume(retry["token_id"], "flowgate", result)
        registered["doc"] = result

    _Provider(monkeypatch, exit_code=0, stdout=COMPLETED, work=registers)
    _worker_ends(retry)

    assert (retry["end_reason"], retry["outcome"], retry["stop_code"]) == ("exited", "complete", None)
    assert retry["graph_end_owned"] is True
    result = registered["doc"]
    assert (_row(env, first)["status"], _row(env, first)["result_doc_id"]) == ("done", result)
    assert _review_status(result) == "approved"
    assert _row(env, second)["status"] == "open"                                           # the next card follows
    assert [j["node_key"] for j in env["jobs"]] == [second]
    attempt = _attempts(env, first)[0]["attempt_id"]
    results = [e for e in _executions(env, attempt) if e["assignment_stage"] == "result"]
    assert [(e["exec_id"], e["status"], e["ai_run_id"]) for e in results] == \
        [(job["graph_exec_id"], "ended", FAILED_AI_RUN), (again["graph_exec_id"], "ended", RETRY_AI_RUN)]
    assert results[0]["lease_token"] == failed["graph_lease_token"] != results[1]["lease_token"] == \
        retry["graph_lease_token"]
    assert not ledger.handing_off(_run_id(env))


# ── 3 / 6: the attempt workspace is the retry's (T#3) ────────────────────────

def _git_on(monkeypatch, request):
    """An isolated Git repository as the Flow's group worktree (the project's Git on): attempt worktrees are prepared by the
    real ``workspace.prepare`` under ``<storage>/graph_attempts``. Stand-ins: the group slot lookup / provisioning, the
    project's Git config read and the Git lock manager. Both live in a short temporary directory (removed after the
    test): Git refuses a worktree whose Git directory path is too long (``'$GIT_DIR' too big`` under pytest's tmp_path
    on Windows)."""
    import os
    import shutil
    import stat
    import tempfile
    from pathlib import Path
    from flowgate.graph_run import workspace

    if not workspace.git.git_available():
        pytest.skip("Git executable is required for the attempt worktree")
    base = Path(tempfile.mkdtemp(prefix="fg"))

    def remove():
        def writable(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(base, onexc=writable)

    request.addfinalizer(remove)
    root = base / "g"
    root.mkdir()

    def git(*args, cwd=root):
        proc = workspace.git._run_git(list(args), cwd=cwd)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    git("init", "-b", "group")
    (root / "value.txt").write_text("initial\n", encoding="utf-8")
    git("add", "value.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "initial")
    monkeypatch.setattr(workspace.git, "get_storage_root", lambda: base / "s")
    monkeypatch.setattr(workspace.git, "effective_src_root", lambda *_: root)
    monkeypatch.setattr(workspace.git, "ensure_worktree",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("the group worktree exists")))
    monkeypatch.setattr(workspace.git.db_git, "get_config", lambda _project: {"enabled": True})
    monkeypatch.setattr(workspace.locks, "new_context", lambda *_: object())
    monkeypatch.setattr(workspace.locks, "acquire",
                        lambda domain, project, **kw: SimpleNamespace(ok=True, lock_key=(domain, kw.get("target_key"))))
    monkeypatch.setattr(workspace.locks, "release", lambda _ctx, _key: None)
    failures, real_run_git = [], workspace.git._run_git

    def run_git(args, *a, **kw):                           # the Git commands that failed, told in a failure message
        proc = real_run_git(args, *a, **kw)
        if proc.returncode and list(args)[:2] != ["symbolic-ref", "-q"]:
            failures.append((list(args), (proc.stderr or proc.stdout or "").strip()[-400:]))
        return proc

    monkeypatch.setattr(workspace.git, "_run_git", run_git)
    cleaned = []
    real_cleanup = workspace.cleanup
    monkeypatch.setattr(workspace, "cleanup", lambda attempt_id: cleaned.append(attempt_id) or real_cleanup(attempt_id))
    return SimpleNamespace(root=root, git=git, cleaned=cleaned, failures=failures)


FILES = ("value.txt", "made.txt", "notes/untracked.txt")


def _tree_state(repo, tree, ref):
    """The attempt tree as Git and the file system tell it: its path, HEAD (detached or a branch), the attempt ref, the
    log, the status (tracked and untracked) and each file's hash."""
    from flowgate.graph_run import workspace

    attached = workspace.git._run_git(["symbolic-ref", "-q", "HEAD"], cwd=tree)
    return {"path": str(tree.resolve()), "head": repo.git("rev-parse", "HEAD", cwd=tree),
            "branch": attached.stdout.strip() if attached.returncode == 0 else None,
            "ref": repo.git("rev-parse", ref, cwd=tree),
            "log": repo.git("log", "--format=%H %s", cwd=tree),
            "status": workspace.git._run_git(["status", "--porcelain", "--untracked-files=all"], cwd=tree).stdout,
            "files": {name: (hashlib.sha256((tree / name).read_bytes()).hexdigest() if (tree / name).exists() else None)
                      for name in FILES}}


def _token_tree(token_id):
    """The tree an AI run's remote source tools work in: its token's attempt (``graph_source_hooks.source_root``)."""
    from pathlib import Path
    from modules.flow_gate.services import graph_source_hooks

    return Path(graph_source_hooks.source_root({"token_id": token_id, "project": "flowgate", "group_id": FA}))




# ── 4: concurrency and late ends, overlapped for real (T#1.4, T#3) ───────────









# ── 5: the retry's protections (T#2.6) ───────────────────────────────────────









# ── record harness: the retry's details and every other end's own policy ─────





















# ── 7: the server's answer and the screen agree ──────────────────────────────
