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
        baseline = max([int(d.get("seq") or 0) for d in db_docs.get_documents_by_group_id(group_id)] or [0])
        attempt_id = graph_execution_hooks.call("admit", exec_id, run_id, group_id, kw.get("graph_attempt_id"))
        root = admission._require_group_worktree(project_id, kw["module"], group_id, "main",
                                                 action_scope=kw["action_scope"], graph_exec_id=exec_id)
        scratch = svc._create_scratch(project_id, run_id)
        issue = kw["issue_builder"](ai_run_id=run_id)
        started_at = admission.now_iso()
        restart = kw.get("node_restart_max_attempts")
        made.append({
            "graph_exec_id": exec_id, "graph_attempt_id": attempt_id,
            "invocation_origin": dict(kw["invocation_origin"]) if kw.get("invocation_origin") else None,
            "run_id": run_id, "status": "running", "mode": "single", "project_id": project_id, "module": kw["module"],
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
    keep = ("graph_exec_id", "graph_attempt_id", "invocation_origin", "run_id", "mode", "project_id", "module", "group_id",
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


def test_the_retry_works_in_the_failed_writer_s_own_worktree_with_its_commits_and_changes_kept(env, decided, monkeypatch,
                                                                                               questions, request):
    from pathlib import Path
    from flowgate.graph_run import workspace

    first, second, job = _to_result_stage(env, monkeypatch)
    repo = _git_on(monkeypatch, request)
    try:
        failed = _admit(monkeypatch, job, FAILED_AI_RUN)
    except Exception as exc:
        raise AssertionError(f"attempt workspace not prepared: {repo.failures}") from exc
    tree = Path(failed["source_root"])
    attempt_id = _attempts(env, first)[0]["attempt_id"]
    ref = workspace.attempt_head(workspace.attempt(attempt_id), "flowgate")
    assert ref and tree.resolve() != repo.root.resolve() and tree.is_dir()
    assert _token_tree(failed["token_id"]).resolve() == tree.resolve()        # its source tools work in that tree

    def works(run_id):
        # the failing AI's work before its provider fails: a commit, a tracked change and an untracked file
        root = _token_tree(failed["token_id"])
        (root / "made.txt").write_text("made by the failed writer\n", encoding="utf-8")
        repo.git("add", "made.txt", cwd=root)
        repo.git("-c", "user.name=AI", "-c", "user.email=ai@example.invalid", "commit", "-m", "failed writer work",
                 cwd=root)
        (root / "value.txt").write_text("changed, not committed\n", encoding="utf-8")
        (root / "notes").mkdir()
        (root / "notes" / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    _Provider(monkeypatch, work=works)
    _worker_ends(failed)
    assert failed["stop_code"] == "provider_failed"
    kept = _tree_state(repo, tree, ref)
    assert kept["branch"] is None and kept["head"] != kept["ref"]                # detached, ahead on its own commit
    assert kept["log"].splitlines()[0].endswith("failed writer work")
    assert " M value.txt" in kept["status"].splitlines() and "?? notes/untracked.txt" in kept["status"].splitlines()
    assert all(kept["files"].values())
    attempt_before = _attempts(env, first)

    _command(env, "step")
    [again] = env["jobs"]
    env["jobs"].clear()
    assert _tree_state(repo, tree, ref) == kept                                    # the command touched nothing

    # the retry's admission prepares the same attempt's workspace: the same tree, as the failure left it
    retry = _admit(monkeypatch, again, RETRY_AI_RUN)
    assert Path(retry["source_root"]).resolve() == tree.resolve()
    assert _token_tree(retry["token_id"]).resolve() == tree.resolve()
    dirty = set(retry["dirty_baseline"] or ())                                   # the retry starts on the dirty tree
    assert "value.txt" in dirty and any(path.startswith("notes") for path in dirty)
    assert _tree_state(repo, tree, ref) == kept
    entered = {}

    def looks(run_id):
        # at its execution's entry (acquired, the process running) the retry's tools see the failed writer's tree
        entered["state"] = _tree_state(repo, _token_tree(retry["token_id"]), ref)

    _Provider(monkeypatch, work=looks)
    _worker_ends(retry)                                                            # it fails at capacity again
    assert entered["state"] == kept
    assert _tree_state(repo, tree, ref) == kept                                    # and its end removes nothing
    assert _attempts(env, first) == attempt_before and attempt_before[0]["status"] == "active"
    assert attempt_before[0]["input_commit"] == repo.git("rev-parse", "HEAD")      # the attempt's input, unchanged
    assert repo.cleaned == []                                                      # no workspace cleanup on either end
    assert _verdict(env, first) == "retry_same_attempt"                           # and it may be retried again


# ── 4: concurrency and late ends, overlapped for real (T#1.4, T#3) ───────────

def test_a_late_finalize_of_the_failed_run_touches_neither_the_requested_nor_the_running_retry(env, decided,
                                                                                               monkeypatch, questions):
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.db import ai_invoke_runs
    from modules.flow_gate.services import graph_execution_hooks
    from modules.flow_gate.services.ai_invoke import finalize

    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    stored = ai_invoke_runs.get(FAILED_AI_RUN)
    _command(env, "step")
    [again] = env["jobs"]
    env["jobs"].clear()
    requested = _ledger_view(env)

    # the failed run's end told again while the retry is requested
    late = _stale_copy(failed)
    finalize._finalize_run(late)
    assert late["graph_report_refused"] is True and late["graph_lease_lost"] is True     # its report reaches nothing
    assert "graph_execution_end" in (late.get("terminal_cleanup_errors") or {})          # the ledger refused its end
    assert not late.get("graph_end_owned") and not late.get("graph_settle_skipped")
    assert _ledger_view(env) == requested
    graph, wire = _wire(env, first)
    assert graph["status"] == "running" and wire["node_running"] is True                 # never put back to idle/error
    # the harness's recorder never acquires, so the scheduler may deliver the same requested execution again: never a
    # new one
    assert {j["graph_exec_id"] for j in env["jobs"]} <= {again["graph_exec_id"]}
    assert not ledger.handing_off(_run_id(env))
    assert _hooks("write_failure", _row(env, first)) is None and _verdict(env, first) == "none"
    for field in ("status", "end_reason", "stop_code", "exit_code", "graph_exec_id"):
        assert ai_invoke_runs.get(FAILED_AI_RUN)[field] == stored[field]

    # the retry's run is admitted and its execution acquired (running, its own lease token)
    retry = _admit(monkeypatch, again, RETRY_AI_RUN)
    assert graph_execution_hooks.call("launch", retry) is True
    running = ledger.get(again["graph_exec_id"])
    assert (running["status"], running["lease_token"], running["ai_run_id"]) == \
        ("running", retry["graph_lease_token"], RETRY_AI_RUN)
    acquired = _ledger_view(env)

    # the failed run's end told once more, by a terminal cleanup / execution finish alone too
    finalize._finalize_run(_stale_copy(failed))
    with pytest.raises(ledger.LeaseLost):
        graph_execution_hooks.call("finish", _stale_copy(failed))                    # refused: not its execution
    assert _ledger_view(env) == acquired                                                  # the retry keeps its lease
    assert ledger.get(again["graph_exec_id"])["lease_expires_at"] == running["lease_expires_at"]
    assert ledger.working(_run_id(env)) and not ledger.handing_off(_run_id(env))
    graph, wire = _wire(env, first)
    assert graph["status"] == "running" and wire["node_running"] is True
    assert {j["graph_exec_id"] for j in env["jobs"]} <= {again["graph_exec_id"]}
    assert len([e for e in _executions(env) if e["status"] in ("requested", "running")]) == 1


def test_a_command_while_the_failed_run_hands_off_is_refused_and_the_next_one_retries_once(env, decided, monkeypatch,
                                                                                         questions):
    """The failure's real end paused inside its run-end hand-off (its execution ended, the hand-off open): another
    client's [다음 단계] is answered 409 and requests nothing; once the hand-off is over the card is retryable and one
    command retries it once."""
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.services import ai_run_end_hooks

    first, second, job = _to_result_stage(env, monkeypatch)
    failed = _admit(monkeypatch, job, FAILED_AI_RUN)
    _Provider(monkeypatch)
    inside, release, out = threading.Event(), threading.Event(), {}
    real_run_ended = ai_run_end_hooks.run_ended

    def run_ended(run):
        inside.set()
        assert release.wait(30)
        real_run_ended(run)

    monkeypatch.setattr(ai_run_end_hooks, "run_ended", run_ended)

    def ends():
        try:
            _worker_ends(failed)
        except BaseException as exc:  # noqa: BLE001 — reported below
            out["error"] = exc

    worker = threading.Thread(target=ends)
    worker.start()
    try:
        assert inside.wait(30), out.get("error")
        assert ledger.get(job["graph_exec_id"])["status"] == "ended"                     # the ledger end is done
        assert ledger.handing_off(_run_id(env)) and not ledger.working(_run_id(env))     # its hand-off is open
        assert _hooks("provider_failure_retry_due", _row(env, first)) is False
        before = _ledger_view(env)
        other = TestClient(env["client"].app)
        res = _command(env, "step", expect=(409,), headers=OTHER_CLIENT, client=other)
        assert res.json()["error"]["code"] == "run_already_active"
        assert _ledger_view(env) == before and env["jobs"] == []
    finally:
        release.set()
        worker.join(60)
    assert not worker.is_alive() and "error" not in out, out.get("error")
    graph, wire = _wire(env, first)
    assert (graph["status"], graph["idle_reason"], wire["node_running"]) == ("idle", "error", False)
    assert _verdict(env, first) == "retry_same_attempt"
    _command(env, "step")
    assert len(env["jobs"]) == 1 and len([e for e in _executions(env) if e["status"] == "requested"]) == 1


def test_two_clients_commanding_at_once_start_one_execution(env, decided, monkeypatch, questions):
    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    clients = [(env["client"], _h(), "step"), (TestClient(env["client"].app), OTHER_CLIENT, "full")]
    barrier, codes, errors = threading.Barrier(2), [], []

    def click(client, headers, mode):
        try:
            barrier.wait(timeout=10)
            res = client.post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=headers)
            codes.append((res.status_code, res.json().get("error", {}).get("code") if res.status_code >= 400 else None))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=click, args=c) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [] and len(codes) == 2
    assert all(code in (200, 201) or (code, why) == (409, "run_already_active") for code, why in codes), codes
    assert any(code in (200, 201) for code, _why in codes)
    attempt = _attempts(env, first)[0]["attempt_id"]
    assert [e["status"] for e in _executions(env, attempt) if e["assignment_stage"] == "result"] == ["ended", "requested"]
    assert len(env["jobs"]) == 1 and env["jobs"][0]["node_key"] == first
    graph, wire = _wire(env, first)
    assert graph["status"] == "running" and wire["node_running"] is True


def test_a_failure_end_racing_a_command_never_requests_twice_nor_strands_the_card(env, decided, monkeypatch,
                                                                                 questions):
    """The failure's real end and a command released at once: whichever comes first, the card is retried at most once
    and never left idle with an execution, nor running with none."""
    first, second, job = _to_result_stage(env, monkeypatch)
    failed = _admit(monkeypatch, job, FAILED_AI_RUN)
    _Provider(monkeypatch)
    barrier, out = threading.Barrier(2), {"codes": []}

    def ends():
        try:
            barrier.wait(timeout=10)
            _worker_ends(failed)
        except BaseException as exc:  # noqa: BLE001
            out["end_error"] = exc

    def click():
        try:
            barrier.wait(timeout=10)
            res = _command(env, "step", expect=(200, 201, 409), headers=OTHER_CLIENT,
                           client=TestClient(env["client"].app))
            out["codes"].append(res.status_code)
        except BaseException as exc:  # noqa: BLE001
            out["click_error"] = exc

    threads = [threading.Thread(target=ends), threading.Thread(target=click)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert "end_error" not in out and "click_error" not in out, out
    attempt = _attempts(env, first)[0]["attempt_id"]
    open_execs = [e for e in _executions(env, attempt) if e["status"] in ("requested", "running")]
    assert len(open_execs) <= 1 and len(env["jobs"]) == len(open_execs)
    graph, wire = _wire(env, first)
    if open_execs:                                                     # the command came after the end: retried once
        assert graph["status"] == "running" and wire["node_running"] is True
    else:                                                              # it came during the end: refused, nothing left
        assert (graph["status"], graph["idle_reason"], wire["node_running"]) == ("idle", "error", False)
        _command(env, "step")
    attempt_execs = [e for e in _executions(env, attempt) if e["assignment_stage"] == "result"]
    assert [e["status"] for e in attempt_execs] == ["ended", "requested"]
    assert len(env["jobs"]) == 1


# ── 5: the retry's protections (T#2.6) ───────────────────────────────────────

def test_a_person_without_write_permission_cannot_retry_and_the_card_stays_retryable(env, decided, monkeypatch,
                                                                                    questions):
    from modules.flow_gate.graph_workflow.api import envelope

    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    monkeypatch.setattr(envelope, "has_permission", lambda user_id, project_id, permission: user_id != "u_lee")
    before = _ledger_view(env)
    res = _command(env, "step", expect=(403,), headers=OTHER_CLIENT)
    assert res.json()["error"]["code"] == "flow_access_denied"
    assert _ledger_view(env) == before and env["jobs"] == []
    assert _verdict(env, first) == "retry_same_attempt"
    _command(env, "step")                                                           # the permitted person's command
    assert len(env["jobs"]) == 1


def test_an_unfinished_time_machine_operation_refuses_the_retry(env, decided, monkeypatch, questions):
    from flowgate.graph_time_machine import operation

    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    before = _ledger_view(env)
    with monkeypatch.context() as m:
        m.setattr(operation, "_unfinished", lambda flow_id: [{"operation_id": "gto_0121", "status": "started"}])
        res = _command(env, "full", expect=(409,))
        assert res.json()["error"]["code"] == "graph_time_machine_in_progress"
        assert _ledger_view(env) == before and env["jobs"] == []
    graph, _wire_row = _wire(env, first)
    assert (graph["status"], graph["idle_reason"]) == ("idle", "error")
    _command(env, "full")                                                           # the operation ended
    assert len(env["jobs"]) == 1


def test_a_waiting_merge_holds_the_command_and_nothing_is_retried_meanwhile(env, decided, monkeypatch, questions):
    from flowgate.graph_run import result_merge

    first, second, job, failed, _provider = _ai_failed(env, monkeypatch)
    real_any, drains = result_merge._any, []
    waiting = (result_merge.PENDING, result_merge.INTEGRATING, result_merge.FAILED)
    monkeypatch.setattr(result_merge, "_any", lambda run_id, statuses: tuple(statuses) == waiting
                        or real_any(run_id, statuses))
    monkeypatch.setattr(result_merge, "request", lambda flow_id, failed=False: drains.append((flow_id, failed)))
    before = _ledger_view(env)
    _command(env, "step")
    assert drains == [(FA, True)]                                                  # the merge drain goes first
    assert _ledger_view(env) == before and env["jobs"] == []                       # held: no retry meanwhile


def test_a_registration_whose_contact_was_lost_is_reconciled_never_written_again(env, decided, monkeypatch, questions):
    """The writer registered its result (the inbox consumed its token) but the Graph contact was lost and the provider
    then failed: the registration is reconciled; the card is never retried."""
    from modules.flow_gate.services import token_service

    box = {}
    first, second, job = _to_result_stage(env, monkeypatch)
    failed = _admit(monkeypatch, job, FAILED_AI_RUN)

    def registers_without_contact(run_id):
        box["doc"] = _result(83, "TR")
        token_service.consume(failed["token_id"], "flowgate", box["doc"])

    _Provider(monkeypatch, work=registers_without_contact)
    _worker_ends(failed)
    row = _row(env, first)
    assert _hooks("write_failure", row) is None and _hooks("provider_failure_retry_due", row) is False
    _command(env, "step", expect=(200, 201, 409))
    assert [j for j in env["jobs"] if j["node_key"] == first] == []                # never written again
    attempt = _attempts(env, first)[0]["attempt_id"]
    assert [e["status"] for e in _executions(env, attempt) if e["assignment_stage"] == "result"] == ["ended"]
    assert _row(env, first)["result_doc_id"] == box["doc"]                         # the registration is the card's


# ── record harness: the retry's details and every other end's own policy ─────

def test_an_exit_with_a_failure_message_and_no_capacity_words_is_told_a_provider_failure(env, decided, monkeypatch,
                                                                                         questions):
    key, _job, _second = _failed_writer(env, monkeypatch, stop_reason='"AI" failed (exit code 1): stream disconnected '
                                                                      "The run ended without completing its work.")
    _run, wire = _wire(env, key)
    assert wire["dispatch"]["error_detail"] == "provider_failed"
    assert _verdict(env, key) == "retry_same_attempt"


@pytest.mark.parametrize("mode", ["step", "full"])
def test_an_explicit_command_retries_the_failed_writer_once_on_the_same_attempt_and_the_card_goes_on(
        env, decided, monkeypatch, questions, mode):
    from flowgate.graph_run import workspace
    from modules.flow_gate.db import ai_invoke_runs
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    key, job, second = _failed_writer(env, monkeypatch)
    attempts = _attempts(env, key)
    [attempt] = attempts
    failed_exec = job["graph_exec_id"]
    instruction = _row(env, key)["instruction_doc_id"]
    settings = _sql(env["db"], "SELECT * FROM graph_node_settings WHERE node_key = ?", (key,))
    failed_run = ai_invoke_runs.get(WORK_RUN)
    cleaned = []
    monkeypatch.setattr(workspace, "cleanup", lambda attempt_id: cleaned.append(attempt_id))

    _command(env, mode)

    [again] = env["jobs"]                                                                 # one AI start
    env["jobs"].clear()
    assert (again["node_key"], again["stage"], again["provider_id"]) == (key, "result", job["provider_id"])
    assert again["assignment_ref"] == {"attempt_id": attempt["attempt_id"], "stage": "result", "seq": 0}
    assert again["invocation_origin"]["purpose"] == "result_write_retry"
    assert again["ref_doc_ids"] == [instruction]                                          # the instruction is attached
    execs = _executions(env, attempt["attempt_id"])
    assert [e["status"] for e in execs if e["kind"] == "write"][-2:] == ["ended", "requested"]
    new_exec = execs[-1]
    assert new_exec["exec_id"] == again["graph_exec_id"] != failed_exec                    # a new execution id
    assert (new_exec["assignment_stage"], new_exec["assignment_seq"]) == ("result", 0)
    old = next(e for e in execs if e["exec_id"] == failed_exec)
    assert (old["status"], old["ai_run_id"]) == ("ended", WORK_RUN)                         # the failure is kept
    assert ai_invoke_runs.get(WORK_RUN) == failed_run
    assert _attempts(env, key) == attempts and cleaned == []                               # same attempt, no cleanup
    assert _row(env, key)["instruction_doc_id"] == instruction
    assert _sql(env["db"], "SELECT * FROM graph_node_settings WHERE node_key = ?", (key,)) == settings
    run, wire = _wire(env, key)
    assert run["status"] == "running" and wire["node_running"] is True                     # the GUI shows it running
    assert "error_code" not in (wire["dispatch"] or {})
    assert _row(env, key)["status"] == "open" and _row(env, second)["status"] == "pending"   # nothing skipped

    # the retried writer succeeds: its result is registered, its run ends on its own -> approved, the card is done
    _heads, token = _start_ai(monkeypatch, again, RETRY_RUN)
    result = _result(82, "TR")
    v01_contact.after_inbox_registered(FA, result, "TR", token)
    token_service.consume(token, "flowgate", result)
    _executions_ended()
    _run_ended(RETRY_RUN, provider_id=job["provider_id"])
    assert (_row(env, key)["status"], _row(env, key)["result_doc_id"]) == ("done", result)
    assert _review_status(result) == "approved"
    if _row(env, second)["status"] != "open":
        _command(env, "step")                                                              # a step: the next one
    assert _row(env, second)["status"] == "open"                                           # the next card follows
    assert [j["node_key"] for j in env["jobs"] if j["node_key"] == second] == [second]
    # the harness's recorder never acquires, so the scheduler may deliver the same requested execution again: never a
    # new one of the retried card
    assert {j["graph_exec_id"] for j in env["jobs"] if j["node_key"] == key} <= {again["graph_exec_id"]}
    results = [e for e in _executions(env, attempt["attempt_id"]) if e["assignment_stage"] == "result"]
    assert [e["exec_id"] for e in results] == [failed_exec, again["graph_exec_id"]]     # the failed one and its retry


def test_a_failure_recorded_before_the_fix_is_retried_the_same_way(env, decided, monkeypatch, questions):
    """A card left ``ai/active`` + ``ai_invoke_failed`` with no result (the B card's records): the judgement reads the
    durable records only, so the stored state of before is retried by the first explicit command, never by reads."""
    key, job, _second = _failed_writer(env, monkeypatch)
    before = (_assignment(env, key), _attempts(env, key), _executions(env))
    for _ in range(3):
        _current(env)                                                                      # reads, F5, re-selects
    assert (_assignment(env, key), _attempts(env, key), _executions(env)) == before and env["jobs"] == []
    _command(env, "step")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == len(before[2]) + 1


def test_reads_settles_and_other_sessions_never_call_the_ai_again(env, decided, monkeypatch, questions):
    from flowgate.card_settings import legacy_adapter

    key, _job, _second = _failed_writer(env, monkeypatch)
    execs = _executions(env)
    for _ in range(3):
        _current(env)                                                                      # F5 / another session's read
        legacy_adapter.settle_graph_run(FA)                                               # a settle after any end
        res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=OTHER_CLIENT)
        assert res.status_code == 200, res.text                                            # another person's session
    assert _executions(env) == execs and env["jobs"] == []
    assert _current(env)["status"] == "idle"


def test_a_second_click_and_a_run_end_told_twice_start_one_execution(env, decided, monkeypatch, questions):
    """Record harness: a second click / another mode after the accepted command (409), and the failed run's run-end port
    told again (the run-end hook alone, the real duplicate finalize is tested above)."""
    key, job, _second = _failed_writer(env, monkeypatch)
    attempt = _attempts(env, key)[0]["attempt_id"]
    _command(env, "step")
    assert len(env["jobs"]) == 1
    new_exec = env["jobs"][0]["graph_exec_id"]
    _command(env, "step", expect=(409,))                                                   # a second click
    _command(env, "full", expect=(409,), headers=OTHER_CLIENT)                             # another client, after it
    _ended(env, WORK_RUN, job["graph_exec_id"], stop_code="provider_failed", outcome="none")
    writes = [e for e in _executions(env, attempt) if e["kind"] == "write"]
    assert len(env["jobs"]) == 1 and [e["exec_id"] for e in writes if e["status"] == "requested"] == [new_exec]
    assert _row(env, key)["status"] == "open" and _row(env, key)["dispatch_error_code"] is None
    run, wire = _wire(env, key)
    assert run["status"] == "running" and wire["node_running"] is True                     # not put back to idle/error
    assert _hooks("write_failure", _row(env, key)) is None and _verdict(env, key) == "none"


@pytest.mark.parametrize("ending", [
    {"end_reason": "cancelled", "stop_code": "cancelled", "exit_code": None, "stop_reason": None},
    {"end_reason": "user_paused", "stop_code": "user_paused", "exit_code": None, "stop_reason": None},
    {"end_reason": "timeout", "stop_code": "timeout", "exit_code": None, "stop_reason": None},
    {"end_reason": "all_providers_failed", "stop_code": "providers_exhausted", "exit_code": None, "stop_reason": None},
    {"end_reason": "exited", "stop_code": "group_lease_denied", "exit_code": 1, "stop_reason": None},
    {"end_reason": "exited", "stop_code": "provider_failed", "exit_code": 1, "register_errors": '["refused"]'},
    {"end_reason": "worker_error", "stop_code": None, "exit_code": None, "stop_reason": None},
], ids=["cancelled", "paused", "timeout", "providers_exhausted", "lease_denied", "registration_refused",
        "worker_error"])
def test_other_failed_ends_are_not_retried_in_place_but_get_a_new_attempt_from_a_command(
        env, decided, monkeypatch, questions, ending):
    """0126 (A4): these ends are still never written again on the same attempt (the 0121 policy), but an explicit
    command no longer leaves the card stuck on them (it was ``none``: accepted, nothing requested): it resolves a new
    assignment in a new attempt; the failed attempt, its assignment and its executions stay as history."""
    key, _job, _second = _failed_writer(env, monkeypatch, **ending)
    row = _row(env, key)
    assert _hooks("write_failure", row) == "ai_invoke_failed"
    assert _hooks("provider_failure_retry_due", row) is False
    assert _verdict(env, key) == "reopen_new_attempt"
    old = _attempts(env, key)[0]["attempt_id"]
    execs = _executions(env)
    _command(env, "step")
    [job] = env["jobs"]
    assert job["assignment_ref"]["attempt_id"] != old and job["assignment_ref"]["stage"] == "result"
    attempts = _attempts(env, key)
    assert [a["status"] for a in attempts] == ["invalidated", "active"] and attempts[1]["attempt_id"] != old
    assert _executions(env)[:len(execs)] == execs                                           # the failed run's rows kept
    assert _assignment(env, key)["stage_state"] == "active"                                 # its assignment row kept


def test_a_clean_end_with_no_result_is_still_retried_as_before(env, decided, monkeypatch, questions):
    key, _job, _second = _failed_writer(env, monkeypatch, stop_code=None, exit_code=0, stop_reason=None)
    assert _hooks("write_failure", _row(env, key)) == "no_result"
    _run, wire = _wire(env, key)
    assert "error_detail" not in wire["dispatch"]
    _command(env, "step")
    assert len(env["jobs"]) == 1


def test_a_writer_that_asked_and_waits_for_its_answers_is_not_retried(env, decided, monkeypatch, questions):
    questions["asked"][WORK_RUN] = [dict(QUESTION, id=903)]
    key, _job, _second = _failed_writer(env, monkeypatch, stop_code="question_pending", exit_code=0, stop_reason=None)
    assert _hooks("write_failure", _row(env, key)) == "question_pending"
    _command(env, "step")
    assert env["jobs"] == []


def test_a_held_card_s_provider_failure_is_not_retried(env, decided, monkeypatch, questions):
    from flowgate.card_settings import legacy_adapter

    key, _job, _second = _failed_writer(env, monkeypatch)
    monkeypatch.setattr(legacy_adapter, "held_nodes", lambda flow_id: frozenset({key}))
    assert _verdict(env, key) == "none"


def test_a_failed_assignment_keeps_its_new_attempt_policy(env, decided, monkeypatch, questions):
    """A failed result assignment (``start_failed`` / ``invoke_failed``) keeps ``REOPEN_NEW_ATTEMPT``; only an
    ``active`` one is retried on its attempt."""
    key, _job, _second = _failed_writer(env, monkeypatch)
    _sql(env["db"], "UPDATE graph_stage_assignments SET stage_state = 'failed', fail_cause = 'invoke_failed' "
                    "WHERE node_key = ? AND stage = 'result'", (key,))
    assert _verdict(env, key) == "reopen_new_attempt"


# ── 7: the server's answer and the screen agree ──────────────────────────────

def test_a_command_refused_on_a_running_run_is_a_409_and_requests_nothing(env, decided, monkeypatch, questions):
    key, _job, _second = _failed_writer(env, monkeypatch)
    _command(env, "step")
    execs = _executions(env)
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": "step"}, headers=_h())
    assert res.status_code == 409, res.text                                               # refused, told so
    assert _executions(env) == execs and len(env["jobs"]) == 1
