"""retry / cancel / resolve of an accepted gate decision (flowgate.v02.0085 T#3, D0007 §7.2 table, §7.3.1, §7.3.2).

    POST /runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/retry    {request_id, expected_decision_version}
    POST /runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/cancel   {request_id, expected_decision_version}
    POST /runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/resolve  {request_id, expected_decision_version,
                                                                               resolution, reason, evidence_version,
                                                                               evidence_refs}

The route checks the user's current group write permission and that the decision belongs to the path's run and node
before anything here (``routes``). Then, in this order:

0. the request key (user, decision, action, request id) used before: the same content answers what is stored now, before
   any version or run state check; other content is 409 ``gate_request_conflict``. Only an accepted request is stored
   (in the transaction that did it); a refusal writes nothing at all. The key is read before the Flow guard and again
   as the first thing inside it: two copies of one request that both found nothing are serialized by the guard, and
   the second answers the first's stored result (never a version conflict, never a second row).
1. ``expected_decision_version`` not the decision's now -> 409 ``gate_context_changed`` (``reason=decision_version``);
   for resolve also the reviewed evidence (D0007 §7.2: the version and the references the person checked, both
   required): ``evidence_version`` not the decision's evidence version now (``reason=evidence_version``) or
   ``evidence_refs`` not exactly the decision's evidence references now (``reason=evidence_refs``: one per phase row,
   ``<decision>/<phase>@<row version>``; a row that moved or a reference of another decision is stale). The reviewed
   evidence is part of the request's fingerprint and is kept with the close (the request's stored result and the
   close event's payload).
2. the action's own rules (D0007 §7.2 table):

- retry: the same decision, job, frozen settings and snapshot go on (the parked job back to pending; the phases read
  their evidence, so nothing done is done again). 202 with run / decision / job; the same job already going on is 202 as
  well. A finished decision (succeeded, rejected, canceled, abandoned) is 409 ``gate_decision_not_retryable``, so is a
  conflict whose merge review is still open.
- cancel: only with no effect outside (``repository.effects_state``) and no writer: 200, the decision ``canceled``
  (it no longer holds the attempt) and the job ``cancelled`` (``no_effect_canceled``); the gate stays open in the same
  attempt for new settings and a new request - in a stopping run the Stop then ends that attempt. Effects present /
  unknown -> 409 ``gate_effects_present`` / ``gate_effects_unknown``; a writer -> 409 ``gate_job_active``; anything else
  (finished, an open conflict review) -> 409 ``gate_decision_not_cancelable``.
- resolve (``recovery_required`` only, else 409 ``gate_resolution_not_allowed``; a writer -> 409 ``gate_job_active``):
  * ``effects_confirmed``: every effect the frozen intent asked for must be proven, the snapshot first: an intent with
    Git or archive work needs its frozen SHA stored, a real commit object and the decision's own pin at it
    (``snapshot``); commit mode needs that commit held by the group branch (``commit``); then merge, push, archive -
    by the phase's stored success, or by what the server itself reads now (the target branch holding the frozen SHA,
    origin holding the pushed SHA, the archive ref at the frozen SHA). A client's word is no evidence. Missing -> 409
    ``gate_effects_unverified`` with the missing items (an intent stopped before its snapshot was pinned can never be
    confirmed). Proven: decision / job succeeded, the gate done with its attempt ended and one ``gate_confirmed``, a
    separate ``gate_recovery_resolved`` event; what follows goes as the run allows (a paused / stopping / stopped
    run's propagation waits).
  * ``abandon``: allowed with remote effects unknown or unreachable. The frozen SHA and a local merge result are kept
    as non-consuming recovery refs (``refs/flowgate/gate-recovery/<decision>[-merge]``); a ref that cannot be written
    now is never stood in for by another ref (the pin is not the merge result): its SHA stays an obligation in the
    phase's evidence (``retention``, nothing that holds it may be deleted - the end cleanup keeps the slot and writes
    the ref later). Each phase gets its confirmed / unknown / abandoned recovery state and the reviewed evidence version.
    Decision ``abandoned`` with ``recovery_reset_required``, job ``cancelled`` (``recovery_abandoned``), the attempt
    ended and the gate pending; no ``gate_confirmed`` / ``gate_rejected``. Nothing is reverted or force pushed.

Every close is one Flow guard transaction whose job write is a CAS on a job no writer holds, so a worker that claimed
it first wins and this request is refused (``gate_job_active``); the closed job's lease generation is gone, so a late
worker's write vanishes (``job_write`` is fenced by its lease). Git reads (resolve's proof, the recovery refs) happen
before that transaction, outside every Flow guard.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from flowgate.gate_decisions import git_adapter as git
from flowgate.gate_decisions import history, legacy_adapter as legacy
from flowgate.gate_decisions import redact
from flowgate.gate_decisions import repository as repo

_log = logging.getLogger(__name__)

REQUEST_CONFLICT, CONTEXT_CHANGED = "gate_request_conflict", "gate_context_changed"
NOT_RETRYABLE, NOT_CANCELABLE = "gate_decision_not_retryable", "gate_decision_not_cancelable"
EFFECTS_PRESENT, EFFECTS_UNKNOWN = "gate_effects_present", "gate_effects_unknown"
JOB_ACTIVE, RESOLUTION_NOT_ALLOWED = "gate_job_active", "gate_resolution_not_allowed"
EFFECTS_UNVERIFIED = "gate_effects_unverified"
RESOLUTIONS = (repo.CLOSE_EFFECTS_CONFIRMED, repo.CLOSE_ABANDON)
CAUSE_RESOLVED, CAUSE_ABANDONED = "gate_recovery_resolved", "gate_recovery_abandoned"
CAUSE_CANCELED = "gate_decision_canceled"
# A decision a cancel may end (the decision store moves only these to canceled; recovery_required is resolved).
CANCELABLE = (repo.ACCEPTED, repo.FAILED)


def _store():
    from modules.flow_gate.services.git import job_store
    return job_store


def fingerprint(action: str, body: dict) -> str:
    refs = body.get("evidence_refs")
    content = {"action": action, "expected_decision_version": body.get("expected_decision_version"),
               "resolution": body.get("resolution"), "reason": body.get("reason"),
               "evidence_version": body.get("evidence_version"),
               "evidence_refs": sorted(refs) if isinstance(refs, list) else refs}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ── wire ─────────────────────────────────────────────────────────────────────

def flags(row: dict, job: Optional[dict] = None) -> dict:
    """What the common Git job actions may do now (D0007 §7.3 last paragraph): retryable / cancelable / resolvable,
    the effects state, the evidence version and the current phase."""
    store = _store()
    job = job if job is not None else legacy.get_job(row.get("job_id"))
    status = row["status"]
    effects = repo.effects_state(row["decision_id"])
    writer = store.gate_writer_active(job)
    conflict = (job or {}).get("phase_note") == store.GATE_PARKED_CONFLICT and _conflict_open(row)
    return {
        "retryable": status in (repo.FAILED, repo.RECOVERY_REQUIRED) and not conflict,
        "cancelable": status in CANCELABLE and effects == repo.EFFECTS_NONE and not writer and not conflict,
        "resolvable": status == repo.RECOVERY_REQUIRED and not writer,
        "effects_state": effects, "evidence_version": repo.evidence_version(row["decision_id"]),
        "evidence_refs": repo.evidence_refs(row["decision_id"]),
        "phase": (job or {}).get("phase"), "job_status": (job or {}).get("status"),
        "error_code": row.get("last_error_code") or (job or {}).get("last_error_code"),
    }


def decision_wire(row: dict) -> dict:
    return {**history.entry(row), **flags(row)}


def _job_wire(job: Optional[dict]) -> Optional[dict]:
    if not job:
        return None
    return {"job_id": job["job_id"], "kind": job.get("kind"), "status": job.get("status"), "phase": job.get("phase"),
            "last_error_code": job.get("last_error_code"), "phase_note": job.get("phase_note")}


def answer(run_id: str, decision_id: str, status: int, **extra) -> dict:
    row = repo.get(decision_id)
    return {legacy.STATUS_KEY: status, "run": legacy.run_payload(run_id), "decision": decision_wire(row),
            "job": _job_wire(legacy.get_job(row.get("job_id"))), **extra}


def _error(status: int, code: str, row: Optional[dict], **params) -> legacy.GraphApiError:
    base = {"decision_id": row["decision_id"], "status": row["status"],
            "decision_version": row["decision_version"]} if row else {}
    return legacy.GraphApiError(status, code, {**base, **params})


# ── shared checks ────────────────────────────────────────────────────────────

def _replay(decision_id: str, user_id: str, action: str, request_id: str, fp: str, run_id: str) -> Optional[dict]:
    found = repo.recovery_request(decision_id, user_id, action, request_id)
    if found is None:
        return None
    if found["request_fingerprint"] != fp:
        raise legacy.GraphApiError(409, REQUEST_CONFLICT, {"decision_id": decision_id, "action": action,
                                                           "request_id": request_id})
    row = repo.get(decision_id)
    status = 202 if action == repo.RETRY and row["status"] in repo.RESERVING else 200
    return answer(run_id, decision_id, status, replayed=True)


def _require_version(row: dict, expected: int) -> None:
    if int(expected) != int(row["decision_version"]):
        raise _error(409, CONTEXT_CHANGED, row, reason="decision_version", expected_decision_version=expected)


def _conflict_open(row: dict) -> bool:
    from flowgate.gate_decisions import publish
    job = legacy.get_job(row.get("job_id"))
    merge_id = publish._conflict_merge_id(job) if job else None
    if merge_id is None:
        return False
    from modules.flow_gate.services import git_service as gs
    session = gs.db_git.get_session(merge_id)
    return session is not None and session.get("status") == "open"


def _context_of(run: dict, actor: str, jobs: list):
    group = legacy.load_flow(run["group_id"])
    return legacy.load_context(run, group.get("project_id"), actor, jobs)


def _event(run_ctx, node_key: str, status: str, cause: str, actor: str, reason: Optional[str], payload: dict) -> None:
    from modules.flow_gate.graph_workflow.services.run_apply import emit_node_state
    emit_node_state(run_ctx, node_key, status, status, cause, None,
                    note={"actor_user_id": actor, "reason": reason, "payload": payload})


def _after(flow_id: str, run_id: str, jobs: list) -> None:
    from flowgate.gate_decisions import publish
    jobs.append(lambda: publish.after_terminal(flow_id, run_id))


# ── retry ────────────────────────────────────────────────────────────────────

def retry(run_id: str, node_key: str, decision_id: str, user_id: str, body: dict) -> dict:
    from flowgate.gate_decisions import publish
    publish.install()
    fp = fingerprint(repo.RETRY, body)
    replay = _replay(decision_id, user_id, repo.RETRY, body["request_id"], fp, run_id)
    if replay is not None:
        return replay
    store = _store()

    def body_of(run: dict, jobs: list) -> dict:
        again = _replay(decision_id, user_id, repo.RETRY, body["request_id"], fp, run_id)   # 0, inside the guard
        if again is not None:
            return again
        row = repo.get(decision_id)
        _require_version(row, body["expected_decision_version"])
        if row["status"] not in repo.RESERVING:
            raise _error(409, NOT_RETRYABLE, row)
        job = legacy.get_job(row.get("job_id"))
        if job is None:
            raise _error(409, NOT_RETRYABLE, row, reason="job_missing")
        if row["status"] == repo.ACCEPTED or job["status"] != "recovery_required":
            repo.insert_recovery_request(decision_id, user_id, repo.RETRY, body["request_id"], fp, 202,
                                         {"outcome": "in_progress", "job_status": job["status"]})
            return answer(run_id, decision_id, 202)                     # the same job is already going on
        if job.get("phase_note") == store.GATE_PARKED_CONFLICT and _conflict_open(row):
            raise _error(409, NOT_RETRYABLE, row, reason="merge_conflict_open")
        if store.gate_writer_active(job) or not store.reopen_gate_publish(job["job_id"]):
            raise _error(409, JOB_ACTIVE, row, job_id=job["job_id"])
        if not repo.move(decision_id, row["decision_version"], repo.ACCEPTED, last_error_code=None):
            raise AssertionError("gate decision moved under the Flow guard")
        repo.insert_recovery_request(decision_id, user_id, repo.RETRY, body["request_id"], fp, 202,
                                     {"outcome": "resumed", "job_id": job["job_id"]})
        reopened = legacy.get_job(job["job_id"])
        jobs.append(lambda: legacy.notify_job(reopened))
        return answer(run_id, decision_id, 202)

    return legacy.guarded_run(run_id, body_of)


# ── cancel ───────────────────────────────────────────────────────────────────

def cancel(run_id: str, node_key: str, decision_id: str, user_id: str, body: dict) -> dict:
    from flowgate.gate_decisions import publish
    fp = fingerprint(repo.CANCEL, body)
    replay = _replay(decision_id, user_id, repo.CANCEL, body["request_id"], fp, run_id)
    if replay is not None:
        return replay
    store = _store()
    # The decision's own refs, read before the guard (a local read): an archive ref no phase evidence tells of is an
    # unknown effect, so the intent is never let go as "no effect" (D0007 §7.3.1).
    seen = repo.get(decision_id)
    refs_effects = publish.effects_now(seen) if seen is not None else repo.EFFECTS_NONE

    def body_of(run: dict, jobs: list) -> dict:
        again = _replay(decision_id, user_id, repo.CANCEL, body["request_id"], fp, run_id)  # 0, inside the guard
        if again is not None:
            return again
        row = repo.get(decision_id)
        _require_version(row, body["expected_decision_version"])
        if row["status"] not in repo.RESERVING:
            raise _error(409, NOT_CANCELABLE, row)
        job = legacy.get_job(row.get("job_id"))
        if store.gate_writer_active(job):
            raise _error(409, JOB_ACTIVE, row, job_id=(job or {}).get("job_id"))
        effects = repo.effects_state(decision_id)
        if effects == repo.EFFECTS_NONE:
            effects = refs_effects
        if effects == repo.EFFECTS_PRESENT:
            raise _error(409, EFFECTS_PRESENT, row)
        if effects == repo.EFFECTS_UNKNOWN:
            raise _error(409, EFFECTS_UNKNOWN, row)
        if _conflict_open(row):
            raise _error(409, NOT_CANCELABLE, row, reason="merge_conflict_open")
        if row["status"] not in CANCELABLE:
            raise _error(409, NOT_CANCELABLE, row, reason="recovery_required")
        if job is not None and not store.close_gate_publish(job["job_id"], "cancelled", store.GATE_CANCEL_NO_EFFECT,
                                                            result={"decision_id": decision_id, "canceled_by": user_id}):
            raise _error(409, JOB_ACTIVE, row, job_id=job["job_id"])
        if not repo.move(decision_id, row["decision_version"], repo.CANCELED):
            raise AssertionError("gate decision moved under the Flow guard")
        run_ctx = _context_of(run, user_id, jobs)
        state = legacy.read_run_state(run_ctx)
        node = state.node(node_key)
        if node is not None:
            _event(run_ctx, node_key, node.status, CAUSE_CANCELED, user_id, None, history.decision_link(decision_id))
        if run["status"] in ("running", "idle"):
            legacy.settle_run(run_ctx)
        repo.insert_recovery_request(decision_id, user_id, repo.CANCEL, body["request_id"], fp, 200,
                                     {"outcome": "canceled"})
        _after(run["group_id"], run_id, jobs)
        return answer(run_id, decision_id, 200)

    return legacy.guarded_run(run_id, body_of)


# ── resolve ──────────────────────────────────────────────────────────────────

def _needed(row: dict) -> list[str]:
    """What effects_confirmed must prove for this frozen intent: the snapshot for any Git or archive work, the commit
    in commit mode, then each external phase the settings asked for."""
    eff = (row.get("settings") or {}).get("effective") or {}
    mode = eff.get("merge_mode") if eff.get("merge_mode") in ("merge", "commit") else None
    needed = []
    if mode is not None or eff.get("archive"):
        needed.append("snapshot")
    if mode == "commit":
        needed.append("commit")
    if mode == "merge":
        needed.append("merge")
    if eff.get("push") and mode is not None:
        needed.append("push")
    if eff.get("archive"):
        needed.append("archive")
    return needed


def _proof(row: dict) -> tuple[dict, list[str]]:
    """``({item: how}, missing)`` for effects_confirmed. The snapshot is read from Git every time (the frozen SHA
    stored, a commit object, the decision's pin at it) and so is the commit of commit mode (the group branch holds
    it); without them nothing else counts. Then each needed phase: its stored success, else the server's own read now
    (the target branch, origin under R, the archive ref). Never the request."""
    from flowgate.gate_decisions.publish import plan_of
    decision_id, frozen = row["decision_id"], row.get("frozen_sha")
    proven: dict = {}
    missing: list[str] = []
    needed = _needed(row)
    if not needed:
        return proven, missing
    try:
        slot = git.slot_of(row["group_id"])
    except git.git_error_type():
        slot = None
    snapshot_ok = bool(frozen) and slot is not None and git.commit_exists(slot.base_root, frozen) and \
        git.rev(slot.base_root, git.pin_ref(decision_id)) == frozen
    if not snapshot_ok:
        return proven, list(needed)                  # nothing is proven of an intent with no pinned snapshot
    proven["snapshot"] = "verified"
    job = legacy.get_job(row.get("job_id")) or {"project_id": None}
    plan = plan_of(job, row)
    merge_row = repo.effect(decision_id, "merge")
    applied = (merge_row or {}).get("sha_after") if (merge_row or {}).get("status") == repo.DONE else None
    for phase in needed:
        if phase == "snapshot":
            continue
        if phase == "commit":
            branch = plan.group_branch or slot.group_branch
            head = git.rev(slot.base_root, f"refs/heads/{branch}") if branch else None
            if head not in (None, git.READ_ERROR) and git.covers(slot.base_root, head, frozen) is True:
                proven[phase] = "verified"
            else:
                missing.append(phase)
            continue
        stored = repo.effect(decision_id, phase)
        if stored is not None and stored["status"] == repo.DONE:
            proven[phase] = "stored"
            continue
        if phase == "merge":
            head = git.rev(slot.base_root, f"refs/heads/{plan.target_branch}")
            ok = head not in (None, git.READ_ERROR) and git.covers(slot.base_root, head, frozen) is True
            if ok:
                applied = head
        elif phase == "archive":
            ok = git.rev(slot.base_root, git.archive_ref(decision_id)) == frozen and \
                repo.checkpoint_of_decision(decision_id) is not None
        else:
            sha = applied if plan.mode == "merge" else frozen
            ok = bool(sha) and _remote_holds(slot, row, plan.push_branch, sha)
        if ok:
            proven[phase] = "verified"
        else:
            missing.append(phase)
    return proven, missing


def _remote_holds(slot, row: dict, branch: Optional[str], sha: str) -> bool:
    """origin's branch holds ``sha``, read by the server under R (never the client's claim)."""
    from modules.flow_gate.services.git import lock_manager as locks
    if not branch:
        return False
    ctx = locks.new_context("req")
    o = locks.acquire("R", slot.project_id, holder_kind="publish", mode="interactive", ctx=ctx)
    if not o.ok:
        return False
    try:
        return git.remote_contains(slot, branch, sha) is True
    finally:
        locks.release(ctx, o.lock_key)


def _keep_recovery_refs(row: dict) -> dict:
    """abandon: the frozen SHA and a local merge result become non-consuming recovery refs (create-only, never
    deleted here). ``{phase: {"sha", "ref", "kept"}}`` - one entry per SHA, kept or not. A ref that cannot be written
    now (no slot, M busy, a ref error) is NOT stood in for by another ref: it is reported unkept and becomes a
    retention obligation (D0007 §7.3.2), and the abandon goes on."""
    from modules.flow_gate.services.git import lock_manager as locks
    decision_id, frozen = row["decision_id"], row.get("frozen_sha")
    wanted = []
    if frozen:
        wanted.append(("snapshot", git.recovery_ref(decision_id), frozen))
    merge_row = repo.effect(decision_id, "merge")
    merged = ((merge_row or {}).get("evidence") or {}).get("merge_commit") or (
        (merge_row or {}).get("sha_after") if (merge_row or {}).get("status") == repo.DONE else None)
    if merged and merged != frozen:
        wanted.append(("merge", git.recovery_ref(decision_id, "merge"), merged))
    out = {phase: {"sha": sha, "ref": ref, "kept": False} for phase, ref, sha in wanted}
    if not wanted:
        return out
    try:
        slot = git.slot_of(row["group_id"])
    except git.git_error_type():
        return out
    ctx = locks.new_context("req")
    o = locks.acquire("M", slot.project_id, holder_kind="publish", mode="interactive", ctx=ctx)
    if not o.ok:
        return out
    try:
        for phase, ref, sha in wanted:
            out[phase]["kept"] = git.create_ref(slot.base_root, ref, sha) in ("created", "same")
    finally:
        locks.release(ctx, o.lock_key)
    return out


def resolve(run_id: str, node_key: str, decision_id: str, user_id: str, body: dict) -> dict:
    fp = fingerprint(repo.RESOLVE, body)
    replay = _replay(decision_id, user_id, repo.RESOLVE, body["request_id"], fp, run_id)
    if replay is not None:
        return replay
    store = _store()
    resolution = body["resolution"]
    row = repo.get(decision_id)
    try:
        _check_resolvable(row, body)                 # read-only, before any Git read
        proven, missing = _proof(row) if resolution == repo.CLOSE_EFFECTS_CONFIRMED else ({}, [])
        if missing:
            raise _error(409, EFFECTS_UNVERIFIED, row, missing=missing)
    except legacy.GraphApiError:
        # A copy of this very request may have closed the decision since the first read: its stored result wins.
        again = _replay(decision_id, user_id, repo.RESOLVE, body["request_id"], fp, run_id)
        if again is not None:
            return again
        raise
    kept = _keep_recovery_refs(row) if resolution == repo.CLOSE_ABANDON else {}
    reviewed = int(body["evidence_version"])
    reviewed_refs = sorted(body["evidence_refs"])

    def body_of(run: dict, jobs: list) -> dict:
        from modules.flow_gate.db.connection import now_iso
        again = _replay(decision_id, user_id, repo.RESOLVE, body["request_id"], fp, run_id)  # 0, inside the guard
        if again is not None:
            return again
        current = repo.get(decision_id)
        _check_resolvable(current, body)
        job = legacy.get_job(current.get("job_id"))
        if job is not None and not store.close_gate_publish(
                job["job_id"], "succeeded" if resolution == repo.CLOSE_EFFECTS_CONFIRMED else "cancelled",
                None if resolution == repo.CLOSE_EFFECTS_CONFIRMED else store.GATE_CANCEL_ABANDONED,
                result={"decision_id": decision_id, "resolution": resolution, "closed_by": user_id},
                from_statuses=("recovery_required",)):
            raise _error(409, JOB_ACTIVE, current, job_id=job["job_id"])
        stamp = now_iso()
        close = {"close_kind": resolution, "closed_by": user_id, "close_reason": redact.scrub(body["reason"]),
                 "closed_at": stamp}
        run_ctx = _context_of(run, user_id, jobs)
        state = legacy.read_run_state(run_ctx)
        node = state.node(node_key)
        if node is None or node.status != "open":
            raise _error(409, RESOLUTION_NOT_ALLOWED, current, reason="gate_not_open")
        evidence = {"evidence_version": reviewed, "evidence_refs": reviewed_refs}
        link = {**history.decision_link(decision_id), "resolution": resolution, "reviewed_evidence": evidence}
        if resolution == repo.CLOSE_EFFECTS_CONFIRMED:
            for phase, how in proven.items():
                if phase not in repo.EXTERNAL_PHASES:
                    continue                         # the snapshot / commit proof is read, not an effect row
                repo.record_effect(decision_id, phase, status=repo.DONE, recovery_state="confirmed",
                                   reviewed_evidence_version=reviewed,
                                   **({"evidence": {"verified_by": "server", "changed": True}} if how == "verified"
                                      else {}))
            if not repo.move(decision_id, current["decision_version"], repo.SUCCEEDED, **close):
                raise AssertionError("gate decision moved under the Flow guard")
            from flowgate.gate_decisions import intake
            policies = legacy.policies_of(legacy.run_store.policy_entries(run_id))
            intake._apply(run_ctx, state, policies, legacy.GATE_EVENTS["confirmed"], node_key,
                          current["actor_user_id"], current.get("decision_comment"), repo.get(decision_id))
            _event(run_ctx, node_key, "done", CAUSE_RESOLVED, user_id, close["close_reason"], link)
        else:
            unkept = {phase: item for phase, item in kept.items() if not item["kept"]}
            for effect_row in repo.effects_of(decision_id):
                phase = effect_row["phase"]
                state_of = {repo.DONE: "confirmed", repo.UNKNOWN: "unknown"}.get(effect_row["status"], "abandoned")
                extra: dict = {}
                if phase in kept:
                    item = kept[phase]
                    extra["evidence"] = {**(effect_row.get("evidence") or {}), repo.RETENTION_KEY: {
                        "sha": item["sha"], "ref": item["ref"], "kept": item["kept"]}}
                    if item["kept"]:
                        extra["recovery_ref"] = item["ref"]
                repo.record_effect(decision_id, phase, recovery_state=state_of, reviewed_evidence_version=reviewed,
                                   **extra)
            if "snapshot" in kept and repo.effect(decision_id, "snapshot") is None:
                item = kept["snapshot"]
                repo.record_effect(decision_id, "snapshot", status=repo.DONE, recovery_state="abandoned",
                                   sha_after=item["sha"], reviewed_evidence_version=reviewed,
                                   evidence={repo.RETENTION_KEY: {"sha": item["sha"], "ref": item["ref"],
                                                                  "kept": item["kept"]}},
                                   **({"recovery_ref": item["ref"]} if item["kept"] else {}))
            link["retention"] = {phase: {"sha": item["sha"], "ref": item["ref"], "kept": item["kept"]}
                                 for phase, item in kept.items()}
            if unkept:
                link["retention_obligations"] = sorted(unkept)
            if not repo.move(decision_id, current["decision_version"], repo.ABANDONED, recovery_reset_required=True,
                             **close):
                raise AssertionError("gate decision moved under the Flow guard")
            _pending_again(run_ctx, node_key, user_id, close["close_reason"], link)
        if run["status"] in ("running", "idle"):
            legacy.settle_run(run_ctx)
        repo.insert_recovery_request(decision_id, user_id, repo.RESOLVE, body["request_id"], fp, 200,
                                     {"outcome": resolution, "reviewed_evidence": evidence})
        _after(run["group_id"], run_id, jobs)
        return answer(run_id, decision_id, 200,
                      recovery_reset_required=resolution == repo.CLOSE_ABANDON)

    return legacy.guarded_run(run_id, body_of)


def _check_resolvable(row: dict, body: dict) -> None:
    _require_version(row, body["expected_decision_version"])
    if row["status"] != repo.RECOVERY_REQUIRED:
        raise _error(409, RESOLUTION_NOT_ALLOWED, row)
    now_version = repo.evidence_version(row["decision_id"])
    if int(body["evidence_version"]) != now_version:
        raise _error(409, CONTEXT_CHANGED, row, reason="evidence_version", evidence_version=now_version)
    now_refs = repo.evidence_refs(row["decision_id"])
    if sorted(body["evidence_refs"]) != now_refs:
        raise _error(409, CONTEXT_CHANGED, row, reason="evidence_refs", evidence_refs=now_refs)
    if _store().gate_writer_active(legacy.get_job(row.get("job_id"))):
        raise _error(409, JOB_ACTIVE, row, job_id=row.get("job_id"))


def _pending_again(run_ctx, node_key: str, actor: str, reason: Optional[str], link: dict) -> None:
    """abandon: the gate's attempt ends and the gate is pending again (no confirm / reject event)."""
    from modules.flow_gate.graph_workflow.services.run_apply import emit_node_state
    run_store = legacy.run_store
    moved = run_store._affected(
        "UPDATE graph_node_runs SET status = 'pending', result_doc_id = NULL, opened_at = NULL, finished_at = NULL, "
        "dispatch_token_issued = NULL, dispatch_issued_to = NULL, dispatch_mention_id = NULL, "
        "dispatch_error_code = NULL, stage = NULL, updated_at = ? WHERE run_id = ? AND node_key = ? AND status = 'open'",
        [run_store.utc_now_ms(), run_ctx.run_id, node_key])
    if moved != 1:
        raise AssertionError("abandoned gate missed its open node")
    run_store.end_attempt(run_ctx.run_id, node_key)
    emit_node_state(run_ctx, node_key, "open", "pending", CAUSE_ABANDONED, None,
                    note={"actor_user_id": actor, "reason": reason, "payload": link})
