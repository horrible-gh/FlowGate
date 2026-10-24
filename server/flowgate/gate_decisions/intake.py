"""The decision intake of the product gate rule ``human_confirm_v1`` (flowgate.v02.0085 T#2, D0007 §3.1 / §3.3 / §7.2).

``POST /runs/{run_id}/nodes/{node_key}/gate-decision`` of a run whose snapshot holds the product rule comes here
(``gate_service`` asks the run's own rule for its ``intake``). Everything runs in the run's Flow guard: one transaction,
the checks first and writing nothing, then the decision. The checks, in the order that fixes which error wins:

0. a request key (``request_id``) used before by the same user on the same run and gate: the same content answers the
   stored decision as it is now (200 / 202), whatever the run or the gate became since - never a new decision; other
   content is 409 ``gate_request_conflict``. The route has checked the user's current permission before.
1. the run not running or idle (paused, stopping) -> 409 ``run_not_running``, nothing written
2. engine ownership; 3. not a gate of the run's revision -> 409 ``node_not_gate``; 4. the policy snapshot cannot be
   restored -> 500; 5. a needed policy ``undecided`` -> 409 ``run_policy_undecided`` (as before)
6. the gate not open (pending - also a stopped run's gate, which waits for the next command -, done, rejected, or
   pending after an abandoned recovery) -> 409 ``node_not_open``
7. a decision already holds the gate (accepted / failed / recovery_required) -> 409 ``gate_decision_in_progress``; an
   old client's request (no request key) is refused the same way
8. an unfinished Time Machine operation (409 ``graph_time_machine_in_progress``) or an open merge conflict session of
   the Flow (409 ``gate_decision_blocked``) holds the Flow's changes
9. a new client's ``expected_attempt_id`` / ``expected_settings_version`` not the gate's round / settings version now
   -> 409 ``gate_context_changed`` (``reason``: ``attempt`` / ``settings_version``)
10. a confirm's settings and Git checks (``preflight``) -> 409 ``gate_settings_invalid`` / ``gate_git_unavailable``

Time Machine rounds use ``rounds.insert_decision`` to link ``previous_decision_id`` / ``time_machine_ref``
and retarget the predecessor's ``superseded_by_decision_id``, including replacement of a no-effect canceled intent.

Then the decision (its run event carries ``gate_decision_id``, ``history.decision_link``):

- reject: stored ``rejected``, the gate ``rejected`` and its attempt ended (``gate_rejected``); what follows stays
  pending, other branches go on and the run settles (``blocked_rejected`` once nothing else is left). No Git or
  archive job. -> 200
- confirm with neither a Git apply nor an archive: stored ``succeeded`` and the gate ``done`` (``gate_confirmed``) in
  the same transaction; what follows opens by the existing approval rules (an idle run waiting for this approval in its
  command goes on at once, a step stays within its targets, a paused / stopped run's propagation waits). -> 200
- confirm with work: stored ``accepted`` with its settings snapshot, target and ``gate_publish`` job in the same
  transaction; the gate stays open and held (no other decision, no settings change, nothing follows it). An idle run
  whose command waited for this gate goes on running while the job is unfinished; the settlement never takes the run
  for completed or for a person's wait meanwhile (``flowgate.graph_run.settle``). Running the job is a later card's.
  -> 202 ``{"run", "decision", "job"}``

An idle run stopped with its gate still open (the existing defence) takes the decision, but its propagation and
completion wait for the next command (the resolver defers on an idle run; a stopped run settles nothing).

Old requests (no request key) get a server-made key: they are never replayed, and a second one after a finished
decision is ``node_not_open`` as before. Each attempt holds one valid decision (canceled excluded) - the guard
serializes requests and the unique index stays the last word.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Optional

from flowgate.gate_decisions import history, legacy_adapter as legacy, preflight, repository as repo
from flowgate.gate_decisions import settings_lock

RUNNING, IDLE = "running", "idle"
DECIDABLE_RUN_STATUSES = (RUNNING, IDLE)
LEGACY_REQUEST_PREFIX = "legacy-"
PRE151_ATTEMPT_PREFIX = "pre151:"
WAKE_CAUSE, WAKE_REASON = "work_resumed", "gate_publish"
REQUEST_CONFLICT, IN_PROGRESS = "gate_request_conflict", "gate_decision_in_progress"
CONTEXT_CHANGED, BLOCKED = "gate_context_changed", "gate_decision_blocked"
BLOCK_TIME_MACHINE, BLOCK_MERGE_CONFLICT = "time_machine", "merge_conflict"


def fingerprint(decision: str, comment: Optional[str], intent: dict) -> str:
    """The content a request key is bound to: the decision, the comment and the expected values (D0007 §7.2)."""
    body = {"decision": decision, "comment": comment, "expected_attempt_id": intent.get("expected_attempt_id"),
            "expected_settings_version": intent.get("expected_settings_version")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _job_wire(job: Optional[dict]) -> Optional[dict]:
    if not job:
        return None
    return {"job_id": job["job_id"], "kind": job.get("kind"), "status": job.get("status"), "phase": job.get("phase"),
            "last_error_code": job.get("last_error_code")}


def _answer(run_id: str, row: dict) -> dict:
    """200 for a finished decision (rejected, succeeded, canceled, abandoned), 202 while it holds the gate. T#3: the
    decision also tells what the common Git job actions may do with it (``recovery.flags``)."""
    from flowgate.gate_decisions import recovery
    row = repo.get(row["decision_id"]) or row
    status = 202 if row["status"] in repo.RESERVING else 200
    return {legacy.STATUS_KEY: status, "run": legacy.run_payload(run_id), "decision": recovery.decision_wire(row),
            "job": _job_wire(legacy.get_job(row.get("job_id")))}


def _merge_sessions(flow_id: str) -> list:
    from modules.flow_gate.db.connection import get_store
    store = get_store()
    if not store.table_exists("git_merge_session"):
        return []
    rows = store._fetch_all("SELECT merge_id FROM git_merge_session WHERE group_id = ? AND status = 'open' "
                            "ORDER BY merge_id", [flow_id]) or []
    return [dict(r)["merge_id"] for r in rows]


def blocked_reason(flow_id: str) -> Optional[str]:
    """Another apply operation holds the Flow's changes (D0007 §3.1 last row): a Time Machine or a conflict session."""
    from flowgate.graph_time_machine.operation import busy
    if busy(flow_id):
        return BLOCK_TIME_MACHINE
    if _merge_sessions(flow_id):
        return BLOCK_MERGE_CONFLICT
    return None


def _require_unblocked(flow_id: str, node_key: str) -> None:
    from flowgate.graph_time_machine.operation import require_no_operation
    require_no_operation(flow_id)                     # 409 graph_time_machine_in_progress, naming its operations
    sessions = _merge_sessions(flow_id)
    if sessions:
        raise legacy.GraphApiError(409, BLOCKED, {"flow_id": flow_id, "node_key": node_key,
                                                  "reason": BLOCK_MERGE_CONFLICT, "merge_ids": sessions})


def _attempt_of(run_id: str, node_key: str) -> str:
    """The open gate's attempt; a gate opened before migration 151 has none recorded and is named by its run."""
    return legacy.run_store.active_attempt_id(run_id, node_key) or f"{PRE151_ATTEMPT_PREFIX}{run_id}:{node_key}"


def _wake(ctx) -> None:
    """An idle run whose command waited for this gate (the approval wake's own test, ``command._waiting_command``)
    goes on running while the accepted confirm's job is unfinished, so its end propagates as a running command's
    would. Any other idle run stays idle (a stopped one waits for the next command)."""
    from flowgate.graph_run import command
    run = ctx.run
    if run.get("status") != IDLE or run.get("idle_reason") != "waiting_approval":
        return
    if run.get("run_mode") not in ("full", "step") or (run["run_mode"] == "step" and run.get("step_node_keys") is None):
        return
    if run.get("active_group_id") != ctx.flow_id or not command._waiting_command(ctx):
        return
    if legacy.run_store.set_run_status(ctx.run_id, IDLE, RUNNING) != 1:
        raise AssertionError("gate publish wake missed its run")
    run["idle_reason"] = None
    legacy.emit_run_status(ctx, IDLE, RUNNING, WAKE_CAUSE, WAKE_REASON)


def decide(group: dict, run_id: str, node_key: str, decision: str, comment: Optional[str], user_id: str,
           intent: dict) -> dict:
    """The gate decision of a run on ``human_confirm_v1`` (module docstring). Returns the answer body; its
    ``legacy.STATUS_KEY`` is the HTTP status (200 / 202)."""
    request_id = intent.get("request_id")
    fp = fingerprint(decision, comment, intent)
    project_id = group.get("project_id")

    def body_of(run: dict, jobs: list) -> dict:
        flow_id = run["group_id"]
        if request_id is not None:                                                      # 0
            found = repo.by_request(run_id, node_key, user_id, request_id)
            if found is not None:
                if found["request_fingerprint"] != fp:
                    raise legacy.GraphApiError(409, REQUEST_CONFLICT, {
                        "run_id": run_id, "node_key": node_key, "request_id": request_id,
                        "decision_id": found["decision_id"]})
                return _answer(run_id, found)
        if run["status"] not in DECIDABLE_RUN_STATUSES:                                 # 1
            raise legacy.GraphApiError(409, "run_not_running", {"run_id": run_id, "status": run["status"]})
        own = legacy.read_ownership(flow_id)                                            # 2
        if own.state != "graph":
            raise legacy.ownership_error(flow_id, own)
        ctx = legacy.load_context(run, project_id, user_id, jobs)
        row = ctx.node_rows.get(node_key)                                               # 3
        if row is None or row.get("node_type") != "gate":
            raise legacy.GraphApiError(409, "node_not_gate", {"run_id": run_id, "node_key": node_key,
                                                              "node_type": (row or {}).get("node_type")})
        entries = legacy.run_store.policy_entries(run_id)
        policies = legacy.policies_of(entries)                                          # 4 (E-33 -> 500)
        missing = legacy.missing_policies(ctx, entries, frozenset({legacy.GATE_POLICY}))  # 5
        if missing:
            raise legacy.gate_undecided(flow_id, int(run["revision_no"]), missing)
        state = legacy.read_run_state(ctx)
        node = state.node(node_key)
        if node is None or node.status != "open":                                       # 6
            params = {"run_id": run_id, "node_key": node_key, "status": node.status if node else None}
            if repo.reset_required(flow_id, node_key) is not None:
                params["reason"] = "recovery_reset_required"
            raise legacy.GraphApiError(409, "node_not_open", params)
        held = repo.reservation(run_id, node_key)                                       # 7
        if held is not None:
            raise legacy.GraphApiError(409, IN_PROGRESS, {"run_id": run_id, "node_key": node_key,
                                                          "decision_id": held["decision_id"], "status": held["status"]})
        _require_unblocked(flow_id, node_key)                                           # 8
        attempt_id = _attempt_of(run_id, node_key)                                      # 9
        shown, version = preflight.settings_of(flow_id, node_key)
        expected_attempt = intent.get("expected_attempt_id")
        if expected_attempt is not None and expected_attempt != attempt_id:
            raise legacy.GraphApiError(409, CONTEXT_CHANGED, {
                "run_id": run_id, "node_key": node_key, "reason": "attempt",
                "expected_attempt_id": expected_attempt, "attempt_id": attempt_id})
        expected_version = intent.get("expected_settings_version")
        if expected_version is not None and expected_version != version:
            raise legacy.GraphApiError(409, CONTEXT_CHANGED, {
                "run_id": run_id, "node_key": node_key, "reason": "settings_version",
                "expected_settings_version": expected_version, "settings_version": version})
        holder = repo.valid_for_attempt(attempt_id)
        if holder is not None:                       # defence: the gate's round already has its decision
            raise legacy.GraphApiError(409, IN_PROGRESS if holder["status"] in repo.RESERVING else "node_not_open", {
                "run_id": run_id, "node_key": node_key, "decision_id": holder["decision_id"],
                "status": holder["status"]})
        try:
            outcome = policies[legacy.GATE_POLICY].judge(node_key, decision, comment)
        except legacy.PolicyUndecided as exc:                                            # E-21 (defence)
            raise legacy.gate_undecided(flow_id, int(run["revision_no"]),
                                        [{"policy": exc.key, "rule_ref": legacy.RULE_REF[exc.key]}])
        kind = legacy.GATE_EVENTS.get(outcome)
        if kind is None:
            raise legacy.ProgrammingError(f"gate_node policy returned {outcome!r}")
        from flowgate.gate_decisions import rounds
        entry = entries.get(legacy.GATE_POLICY) or {}
        base = {"group_id": flow_id, "run_id": run_id, "node_key": node_key, "attempt_id": attempt_id,
                "revision_no": int(run["revision_no"]),
                "policy_version": f"{entry.get('value')}@{entry.get('impl_version')}", "actor_user_id": user_id,
                "request_id": request_id or LEGACY_REQUEST_PREFIX + secrets.token_hex(12),
                "request_fingerprint": fp, "decision": decision, "decision_comment": comment,
                "settings_version": version}
        if outcome == "rejected":
            stored = rounds.insert_decision({**base, "status": repo.REJECTED, "settings": {"shown": shown}})
            _apply(ctx, state, policies, kind, node_key, user_id, comment, stored)
            return _answer(run_id, stored)
        plan = preflight.plan(project_id, flow_id, node_key)                            # 10
        snapshot = {"shown": plan["settings"], "effective": plan["effective"]}
        target = {"target_remote": plan["target_remote"], "target_branch": plan["target_branch"],
                  "target_sha": plan["target_sha"]}
        if not plan["needs_work"]:
            stored = rounds.insert_decision({**base, **target, "status": repo.SUCCEEDED, "settings": snapshot})
            _apply(ctx, state, policies, kind, node_key, user_id, comment, stored)
            return _answer(run_id, stored)
        from flowgate.gate_decisions import publish
        publish.install()                            # T#3: the executor that runs it (idempotent)
        decision_id = repo.new_decision_id()
        job = legacy.insert_gate_publish(
            project_id, {"decision_id": decision_id, "group_id": flow_id, "run_id": run_id, "node_key": node_key},
            group_id=flow_id, target_key=node_key, requested_by=user_id,
            payload={"decision_id": decision_id, "run_id": run_id, "node_key": node_key, "attempt_id": attempt_id})
        stored = rounds.insert_decision({**base, **target, "decision_id": decision_id, "status": repo.ACCEPTED,
                                       "settings": {**snapshot, "group_branch": plan["group_branch"],
                                                    "group_head": plan["group_head"]},
                                       "job_id": job["job_id"]})
        _wake(ctx)
        legacy.settle_run(ctx)
        jobs.append(lambda: legacy.notify_job(job))
        return _answer(run_id, stored)

    return legacy.guarded_run(run_id, body_of)


def _apply(ctx, state, policies, kind: str, node_key: str, user_id: str, comment: Optional[str], stored: dict) -> None:
    """The node event of a finished decision, linked to it, through the resolver and ``apply_decision``."""
    d = legacy.resolve(ctx.revision, state, legacy.RunEvent(kind, node_key), policies)
    if d.ignored:                                                                       # open + gate event is §3.2
        raise AssertionError(f"gate event {kind} ignored on open node {node_key}")
    legacy.apply_decision(ctx, d, note={"actor_user_id": user_id, "reason": comment,
                                        "payload": history.decision_link(stored["decision_id"])})


# ── the gate part of a run read (D0007 §7.2) ─────────────────────────────────

def _summary(entry: Optional[dict]) -> Optional[dict]:
    if not entry:
        return None
    keys = ("decision_id", "source", "run_id", "attempt_id", "decision", "result", "status", "decision_version",
            "actor_user_id", "comment", "decided_at", "completed_at", "job_id", "last_error_code", "event_seq",
            "target_remote", "target_branch", "target_sha", "frozen_sha", "effects", "archived", "archive_id",
            "unknown")
    return {k: entry.get(k) for k in keys}


def recovery_flags(row: dict, job: Optional[dict]) -> dict:
    from flowgate.gate_decisions import recovery
    return recovery.flags(row, job)


def run_wire(run: dict, rows: list[dict]) -> dict:
    """``{gate node key: …}`` for every gate of the run: the current decision (the newest of the gate's history), whether
    a decision holds the gate and its job, ``can_decide`` (the server's judgement of the confirm / reject buttons:
    a running or idle run, the gate open, nothing holding it or the Flow), the settings lock and whether the current
    confirm's archive is done (``archived``: the newest decision succeeded with its own checkpoint, on a done gate).

    Any run is read, also one whose stored snapshot is ``undecided`` (``policy_undecided``: its decision is refused with
    ``run_policy_undecided``, so ``can_decide`` is false); its legacy entries keep their unknown Git / archive facts."""
    flow_id = run["group_id"]
    types = legacy.node_types(flow_id, int(run["revision_no"]))
    gates = [r for r in rows if types.get(r["node_key"]) == "gate"]
    if not gates:
        return {}
    reserved = repo.reservations_of_run(run["run_id"])
    blocked = blocked_reason(flow_id)
    decided = legacy.gate_rule_decided(run["run_id"])
    out = {}
    for r in gates:
        key = r["node_key"]
        latest = history.history(flow_id, key, limit=1)["latest"]
        held = reserved.get(key)
        lock = settings_lock.lock_reason(flow_id, key)
        reset = repo.reset_required(flow_id, key) is not None
        job = legacy.get_job(held.get("job_id")) if held else None
        out[key] = {
            "decision": _summary(latest), "decision_in_progress": held is not None,
            "job": _job_wire(job),
            # T#3 (D0007 §7.3): what the common Git job actions may do with the held decision now
            "recovery": ({"decision_id": held["decision_id"], "decision_version": held["decision_version"],
                          "status": held["status"], **recovery_flags(held, job)} if held else None),
            "can_decide": (decided and run["status"] in DECIDABLE_RUN_STATUSES and r["status"] == "open"
                           and held is None and blocked is None and not reset),
            "policy_undecided": not decided, "blocked_reason": blocked, "settings_locked": lock is not None, "settings_lock_reason": lock,
            "archived": bool(latest and latest.get("archived")) and r["status"] == "done",
            "recovery_reset_required": reset,
        }
    return out
