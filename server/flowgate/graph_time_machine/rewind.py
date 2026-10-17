"""The DB step of a Graph Time Machine operation (0061 T#13), after its Git step, in one Flow guard taken under the
Stop gate's exclusive hold (``fencing.exclusive``: no side effect of a target attempt's token runs across the commit,
as for a Stop's invalidation).

Checked first, written after; a refusal rolls the guard back and the operation is a partial failure:

- the run is idle and no execution of it is requested / running; no target attempt is merging; every attempt of a
  target node that merged into the group branch is one the plan reverted (an attempt the plan does not know whose
  merge stayed on the branch would leave the node pending over its source);
- every attempt of the target nodes (the plan's and any later one) -> ``invalidated`` (``time_machine``), its tokens
  revoked through the existing reclaim path. An invalidated attempt's tokens are no evidence any more
  (``run_store.token_ids``): reconciliation and the result binder never take its result again, and its documents are
  the card's history (``card_approval`` stale lines), never a node's result;
- a target node opened before migration 151 has tokens of no attempt (``attempt_id`` NULL), which ``token_ids`` would
  keep as evidence (a consumed one is not touched by the reclaim path): they are mapped to one attempt row recorded
  for that old opening, invalidated (``time_machine``) at once — the same durable boundary as every other invalidated
  attempt, so ``token_ids``, ``invalidated_token_ids`` (stale documents), the authorship read and the token fence
  (``graph_source_hooks.binding`` -> an invalidated attempt) all leave them out. Nothing of the token, document or
  approval history is deleted;
- the target documents' approval goes back (``approved`` -> ``pending_review``, the existing review status CAS);
  their content, revisions and approval / rejection history stay as the old attempt's output;
- the target nodes (but a root-bound node, which follows its root document and has no attempt) go back to
  ``pending`` with their node run fields cleared, as a Stop does; their pending propagations are spent, so no command
  opens a successor from an old result; candidates are recomputed by the next step / full command only;
- each reverted integration's ledger row (the attempt's result row) is canceled with its revert commit (an empty revert
  with ``empty_revert``) — the ledger names the revert of each attempt's result;
- the run stays idle; a ``completed`` run is completed no more (its idle reason is judged again), and a run written
  before migration 151 that gave its Flow up takes it back. Hold settings are not touched.
"""
from __future__ import annotations

import secrets

from modules.flow_gate.db import documents as db_docs, tr_commit_ledger
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.graph_workflow.store import run_store

from flowgate.graph_run import execution_store, settle

from .repository import OPERATION_CAUSE

INVALIDATED_REASON = "time_machine"
APPROVED, REOPENED = "approved", "pending_review"


class RewindRefused(Exception):
    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def _marks(values) -> str:
    return ", ".join("?" for _ in values)


def _merged(attempt: dict) -> bool:
    commit = attempt.get("integrated_commit")
    return bool(commit) and commit not in (attempt.get("result_commit"), attempt.get("input_commit"))


def _check(ctx, keys: list[str], plan: dict, reverts: list[dict]) -> list[dict]:
    run = ctx.run
    if run["status"] != "idle":
        raise RewindRefused("run_not_idle", status=run["status"])
    busy = run_store._all("SELECT exec_id FROM graph_attempt_runs WHERE run_id = ? "
                          "AND status IN ('requested', 'running') ORDER BY exec_id", [ctx.run_id])
    if busy:
        raise RewindRefused("executions_in_progress", exec_ids=[r["exec_id"] for r in busy])
    attempts = run_store._all(
        f"SELECT * FROM graph_node_attempts WHERE run_id = ? AND status IN ('active', 'ended') "
        f"AND node_key IN ({_marks(keys)}) ORDER BY created_at, attempt_id", [ctx.run_id, *keys]) if keys else []
    planned = set(plan.get("attempt_ids") or ())
    reverted = {r["commit_sha"] for r in reverts}
    for c in plan.get("commits") or ():
        if c["commit_sha"] not in reverted:
            raise RewindRefused("revert_missing", commit_sha=c["commit_sha"])
    for a in attempts:
        if (a.get("integration_status") or "") == "integrating":
            raise RewindRefused("integrations_in_progress", attempt_id=a["attempt_id"])
        if a["attempt_id"] not in planned and _merged(a):
            raise RewindRefused("unplanned_integration", attempt_id=a["attempt_id"], node_key=a["node_key"])
    return attempts


def _revoke(token_ids) -> None:
    from modules.flow_gate.services import ai_invoke_service
    for token_id in dict.fromkeys(token_ids):
        ai_invoke_service._reclaim_orphan_lease_token({"token_id": token_id}, INVALIDATED_REASON)


def _detach_legacy_tokens(ctx, keys: list[str], stamp: str) -> list[tuple[str, list[str]]]:
    """The target nodes' tokens of no attempt (mapped before migration 151) -> one invalidated attempt per node, the
    record of that old opening; ``[(attempt_id, token_ids)]``."""
    if not keys:
        return []
    found: dict[str, list[dict]] = {}
    for r in run_store._all(
            f"SELECT token_id, node_key, mapped_at FROM graph_run_token_map WHERE run_id = ? AND attempt_id IS NULL "
            f"AND node_key IN ({_marks(keys)}) ORDER BY mapped_at, token_id", [ctx.run_id, *keys]):
        found.setdefault(r["node_key"], []).append(r)
    out = []
    for key in sorted(found):
        attempt_id = run_store.ATTEMPT_ID_PREFIX + secrets.token_hex(12)
        opened = min(str(r["mapped_at"]) for r in found[key])
        if run_store._affected(
                "INSERT INTO graph_node_attempts (attempt_id, run_id, node_key, revision_no, status, active_node_key, "
                "invalidated_reason, created_at, ended_at) VALUES (?, ?, ?, ?, 'invalidated', NULL, ?, ?, ?)",
                [attempt_id, ctx.run_id, key, int(ctx.run.get("revision_no") or 0), INVALIDATED_REASON, opened,
                 stamp]) != 1:
            raise AssertionError(f"time machine missed the old opening of {key}")
        token_ids = [r["token_id"] for r in found[key]]
        if run_store._affected(
                f"UPDATE graph_run_token_map SET attempt_id = ? WHERE run_id = ? AND node_key = ? AND attempt_id IS NULL "
                f"AND token_id IN ({_marks(token_ids)})", [attempt_id, ctx.run_id, key, *token_ids]) != len(token_ids):
            raise AssertionError(f"time machine missed the old tokens of {key}")
        out.append((attempt_id, token_ids))
    return out


def rewind(ctx, plan: dict, reverts: list[dict]) -> dict:
    """Inside the operation's Flow guard (``ctx`` loaded there). Returns what changed; raises RewindRefused."""
    run_id = ctx.run_id
    keys = sorted(set(plan.get("node_keys") or ()))
    attempts = _check(ctx, keys, plan, reverts)
    stamp = execution_store.now()

    ids = [a["attempt_id"] for a in attempts]
    for attempt_id in ids:
        if run_store._affected("UPDATE graph_node_attempts SET status = 'invalidated', active_node_key = NULL, "
                               "invalidated_reason = ?, ended_at = COALESCE(ended_at, ?) "
                               "WHERE attempt_id = ? AND status IN ('active', 'ended')",
                               [INVALIDATED_REASON, stamp, attempt_id]) != 1:
            raise AssertionError(f"time machine missed attempt {attempt_id}")
    tokens = [r["token_id"] for r in run_store._all(
        f"SELECT token_id FROM graph_run_token_map WHERE attempt_id IN ({_marks(ids)}) ORDER BY mapped_at, token_id",
        ids)] if ids else []
    legacy = _detach_legacy_tokens(ctx, keys, stamp)
    tokens += [t for _attempt, node_tokens in legacy for t in node_tokens]
    _revoke(tokens)

    reopened = []
    for doc in plan.get("documents") or ():
        if db_docs.update_review_status_cas(doc["doc_id"], APPROVED, REOPENED) is not None:
            reopened.append(doc["doc_id"])

    reset = []
    for row in run_store.node_runs(run_id):
        key, status = row["node_key"], row["status"]
        if key not in keys or status == "pending" or int(row.get("root_bound") or 0):
            continue
        if run_store._affected(
                "UPDATE graph_node_runs SET status = 'pending', result_doc_id = NULL, opened_at = NULL, "
                "finished_at = NULL, dispatch_token_issued = NULL, dispatch_issued_to = NULL, "
                "dispatch_mention_id = NULL, dispatch_error_code = NULL, stage = NULL, "
                "instruction_doc_id = NULL, result_event_floor = NULL, updated_at = ? "
                "WHERE run_id = ? AND node_key = ? AND status = ?",
                [run_store.utc_now_ms(), run_id, key, status]) != 1:
            raise AssertionError(f"time machine missed node {key}")
        run_apply.emit_node_state(ctx, key, status, "pending", OPERATION_CAUSE, None)
        reset.append(key)
    for p in run_store.pending_propagations(run_id):
        if p["node_key"] in keys:
            run_store.mark_propagation_done(run_id, int(p["source_event_seq"]))

    canceled = []
    for r in reverts:
        row = tr_commit_ledger.by_attempt(r["attempt_id"])
        if not row or row.get("state") != "live":
            continue
        if r.get("revert_commit"):
            done = tr_commit_ledger.mark_canceled(int(row["id"]), cancel_commit=r["revert_commit"])
        else:
            done = tr_commit_ledger.mark_canceled(int(row["id"]), cancel_commit=None, reason="empty_revert")
        if done:
            canceled.append(int(row["id"]))

    flow_id = ctx.flow_id
    if not ctx.run.get("active_group_id") and run_store.active_run(flow_id) is None:
        if run_store.claim_flow(run_id, flow_id) != 1:
            raise AssertionError("time machine missed its Flow")
        ctx.run["active_group_id"] = flow_id
    if ctx.run.get("idle_reason") != settle.IDLE_STOPPED:
        reason = settle.idle_reason(ctx, run_store.node_runs(run_id), run_apply.read_run_state(ctx))
        if reason != ctx.run.get("idle_reason") and run_store.set_idle_reason(run_id, reason) == 1:
            ctx.run["idle_reason"] = reason
    return {"invalidated_attempt_ids": ids, "active_attempt_ids": [a["attempt_id"] for a in attempts
                                                                    if a["status"] == "active"],
            "detached_legacy_attempt_ids": [attempt_id for attempt_id, _tokens in legacy],
            "reset_node_keys": reset, "reopened_document_ids": reopened, "canceled_ledger_row_ids": canceled,
            "revoked_token_count": len(set(tokens)), "idle_reason": ctx.run.get("idle_reason")}
