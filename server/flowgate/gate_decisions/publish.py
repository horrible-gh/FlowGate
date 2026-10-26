"""The ``gate_publish`` job: a confirm's Git apply, archive and completion (flowgate.v02.0085 T#3, D0007 §3.3 / §7.3).

A confirm that needs Git work is accepted with this job (``intake``); the gate stays open and held until the job is
done. The job is a Runner executor (``job_runner``), so it uses the existing claim, lease, fenced writes, lock manager
domains and their order (G, then W or B, then R, then M), backoff and reconcile. Phases, each recorded before its step:

    start -> snapshot (G, M) -> merge (B or W) -> push (R) -> archive (M) -> db_finalize -> done

* order: the gate jobs of one group run in their acceptance order (``job_store.gate_publish_predecessor``: a job whose
  earlier job of the group is unfinished is not claimed), and from its acceptance until its snapshot is pinned a
  decision holds the group branch against the group result writers (``repository.snapshot_reservations``: the attempt
  integration and the end cleanup wait), so nothing integrated after the acceptance enters its snapshot.
* snapshot: under the group's G the group branch HEAD - the result already approved and integrated - is the frozen SHA,
  pinned in ``refs/flowgate/gate-pin/<decision>`` (M) and stored on the decision before G is let go. A pin an earlier
  claim made and could not store is that snapshot: it is taken back (checked to be a commit of the group branch), never
  replaced by the HEAD of now. Uncommitted files of the work tree are never taken (counted in the evidence only).
* merge (merge mode): the frozen SHA into the group's work base branch (``git_adapter.merge``): already contained ->
  nothing changes; a conflict is handed to the existing merge review with the decision id in its session, and the job
  waits (parked, going on by itself once the review closed). commit mode makes no commit: the frozen SHA is the result.
* push (``push`` on): a plain push of the result to origin (merge: the work base branch, commit: the group branch). A
  failed or timed-out command is judged by reading the remote: holding our SHA (or a descendant) is pushed, unreadable
  is unknown, a network failure retries with backoff, anything else needs recovery. Never forced.
* archive (``archive`` on): the intent is stored first (the phase's effect ``unknown``), then
  ``refs/flowgate/gate-archive/<decision>`` at the frozen SHA (and ``-applied`` at a merge result), then the
  checkpoint row with the effect ``succeeded`` in one transaction - the same ref and SHA again is reused, another SHA
  needs recovery. An archive cut off after its ref never reads as "no effect".
* db_finalize: one Flow guard transaction - the job ``succeeded`` (fenced by its lease), the decision ``succeeded``
  (by its version), the gate ``done`` with its attempt ended (``gate_confirmed``, linked to the decision) and what
  follows by the run's own rules (a paused / stopping / idle run's propagation waits). A DB failure after the Git work
  retries this phase only.

Every write of the worker - the job row, the decision, a phase's evidence, the checkpoint - is one short transaction
that first writes the job row under the claim's lease (``_guarded``: a fenced write, so a lost or discarded lease
writes nothing) and checks the decision is still this job's and unfinished. A worker whose lease went away during a
Git command therefore leaves the phase's evidence as it was before the command (``unknown``), and the next owner
judges it from Git. Every step first reads what an earlier claim left (the phase's effect row, the refs, the merge
attempt), so a restart, a lost lease or a reconcile never repeats a merge, a push or an archive. A failure the job
cannot get past parks it: ``recovery_required`` with ``phase_note`` gate_failed / gate_recovery (never retried on its
own) and the decision ``failed`` (no effect outside, D0007 §7.3.1) or ``recovery_required`` (effects present or
unknown, §7.3.2), for the person's retry / cancel / resolve (``recovery``). A Stop's cancel request is honoured at every
phase boundary: with no effect the decision is canceled and the job cancelled (the Stop then puts the gate back to
pending), with effects the job parks for recovery. Locks are never held across the DB phase, and no Git or network
wait happens in a Flow guard.

Nothing of a remote's credentials is kept: errors are safe codes, texts are scrubbed (``redact``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from flowgate.gate_decisions import git_adapter as git
from flowgate.gate_decisions import legacy_adapter as legacy
from flowgate.gate_decisions import redact
from flowgate.gate_decisions import repository as repo

_log = logging.getLogger(__name__)

KIND = "gate_publish"
PHASES = ("start", "snapshot", "merge", "push", "archive", "db_finalize")
JOB_EVENT = "gate_publish_job_changed"
STOP_REQUESTED = "stop_requested"


def _store():
    from modules.flow_gate.services.git import job_store
    return job_store


def _locks():
    from modules.flow_gate.services.git import lock_manager
    return lock_manager


def _runner():
    from modules.flow_gate.services.git import job_runner
    return job_runner


def _db():
    from modules.flow_gate.db.connection import get_store, now_iso
    return get_store, now_iso


_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}


class _Stop(Exception):
    """End this claim: ``result`` for ``finish_run`` (blocked / transient), or None when the outcome is written."""

    def __init__(self, result=None):
        super().__init__(getattr(result, "code", None))
        self.result = result


@dataclass
class Plan:
    decision_id: str
    run_id: str
    node_key: str
    attempt_id: str
    group_id: str
    project_id: str
    mode: Optional[str]                 # merge | commit | None (hold / unset)
    push: bool
    archive: bool
    target_branch: Optional[str]
    group_branch: Optional[str]
    target_sha: Optional[str] = None

    @property
    def push_branch(self) -> Optional[str]:
        return self.target_branch if self.mode == "merge" else self.group_branch


def plan_of(job: dict, decision: dict) -> Plan:
    eff = (decision.get("settings") or {}).get("effective") or {}
    mode = eff.get("merge_mode") if eff.get("merge_mode") in ("merge", "commit") else None
    return Plan(decision["decision_id"], decision["run_id"], decision["node_key"], decision["attempt_id"],
                decision["group_id"], job["project_id"], mode, bool(eff.get("push")) and mode is not None,
                bool(eff.get("archive")), decision.get("target_branch"),
                (decision.get("settings") or {}).get("group_branch"),
                ((decision.get("settings") or {}).get("target_basis") or {}).get("accepted_target_sha"))


def _payload(job: dict) -> dict:
    from modules.flow_gate.services.git.approval_freeze import _json
    return _json(job.get("payload"))


def _evidence(job: dict) -> dict:
    from modules.flow_gate.services.git.approval_freeze import _json
    return _json(job.get("evidence"))


# ── fenced writes: every worker write is one short transaction under the lease ─

def _guarded(ctx, plan: Plan, write: Callable[[dict], object], job_changes: Optional[dict] = None):
    """``write(decision)`` in one short transaction that first writes the job row under this claim's lease (a fenced
    write: ``LeaseLost`` once another claim, a reconcile or a close took the job) and checks the decision is still
    this job's and holds its gate (accepted / failed / recovery_required). A resolve or cancel that ended the decision
    ended the job in the same transaction, so a late worker's write vanishes either way. Returns what ``write``
    returned; the job's local copy is updated only after the commit."""
    store = _store()
    get_store, _now = _db()
    store.ensure_lease(ctx)
    changes = dict(job_changes or {})
    with get_store().transaction():
        written = store.fenced_write(ctx, changes, update_local=False)
        current = repo.get(plan.decision_id)
        if current is None or current.get("job_id") != ctx.job_id or current["status"] not in repo.RESERVING:
            raise store.LeaseLost(ctx.job_id)
        out = write(current)
    ctx.job.update(written)
    return out


def _move_in(current: dict, to_status: Optional[str] = None, **changes) -> None:
    """One decision change at the version read inside the caller's fenced transaction."""
    if not repo.move(current["decision_id"], current["decision_version"], to_status, **changes):
        raise AssertionError(f"gate decision {current['decision_id']} moved under its job's lease")


def _clean_effect(fields: dict) -> dict:
    if "evidence" in fields:
        fields["evidence"] = redact.scrub_value(fields["evidence"])
    if fields.get("error_code"):
        fields["error_code"] = redact.safe_code(fields["error_code"])
    return fields


def _effect(ctx, plan: Plan, phase: str, **fields) -> dict:
    fields = _clean_effect(fields)
    return _guarded(ctx, plan, lambda _current: repo.record_effect(plan.decision_id, phase, **fields))


def _phase(ctx, phase: str, **evidence) -> None:
    store = _store()
    changes: dict = {"phase": phase}
    if evidence:
        ev = _evidence(ctx.job)
        ev.update(redact.scrub_value(evidence))
        changes["evidence"] = store._canonical_json(ev)
    store.ensure_lease(ctx)
    store.job_write(ctx, **changes)


def effects_now(decision: dict) -> str:
    """``repository.effects_state`` checked against the decision's own refs: an archive ref of this decision that
    exists while no phase says so (an archive cut off before its evidence) is an unknown effect, never none. A ref
    that cannot be read is unknown too. Read only, no lock (a local ref read)."""
    state = repo.effects_state(decision["decision_id"])
    if state != repo.EFFECTS_NONE:
        return state
    eff = (decision.get("settings") or {}).get("effective") or {}
    if not eff.get("archive"):
        return state
    try:
        slot = git.slot_of(decision["group_id"])
    except git.git_error_type():
        return state
    for ref in (git.archive_ref(decision["decision_id"]), git.applied_ref(decision["decision_id"])):
        found = git.rev(slot.base_root, ref)
        if found is git.READ_ERROR or found:
            return repo.EFFECTS_UNKNOWN
    return state


# ── outcomes ─────────────────────────────────────────────────────────────────

def _blocked(domain: str, o) -> _Stop:
    store, locks = _store(), _locks()
    b = o.blocker or {}
    if o.kind in (locks.BUSY, locks.GROUP_FROZEN):
        return _Stop(store.RunResult(store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
                                     blocked_holder=b.get("holder_ctx_id") or b.get("job_id"),
                                     blocked_operation=b.get("holder_kind") or b.get("kind")))
    if o.kind == locks.RECOVERY_REQUIRED:
        return _Recovery(f"gate_lock_{domain}_recovery_required")
    return _Transient((o.cause or o.kind or "lock_store_error"))


class _Transient(_Stop):
    def __init__(self, code: str):
        super().__init__(None)
        self.code = redact.safe_code(code)


class _Failure(_Stop):
    """Parks the job: decision ``failed`` with no effect outside, else ``recovery_required``."""

    def __init__(self, code: str, note: Optional[str] = None, auto: bool = False):
        super().__init__(None)
        self.code = redact.safe_code(code)
        self.note = note
        self.auto = auto


class _Recovery(_Failure):
    def __init__(self, code: str):
        super().__init__(code)
        self.force_recovery = True


def _park(ctx, plan: Plan, code: str, *, note: Optional[str] = None, auto: bool = False,
          force_recovery: bool = False) -> None:
    store = _store()
    _get_store, now_iso = _db()
    effects = repo.effects_state(plan.decision_id)
    to = repo.RECOVERY_REQUIRED if force_recovery or effects != repo.EFFECTS_NONE else repo.FAILED
    if note is None:
        note = store.GATE_PARKED_RECOVERY if to == repo.RECOVERY_REQUIRED else store.GATE_PARKED_FAILED

    def write(current: dict) -> None:
        if current["status"] == to:
            _move_in(current, last_error_code=code)
        else:
            _move_in(current, to, last_error_code=code)

    _guarded(ctx, plan, write, dict(status="recovery_required", last_error_code=code, phase_note=note, retryable=1,
                                    recovery_since=now_iso() if auto else None, **_LEASE_CLEARED))
    _log.warning("gate publish parked: %s decision=%s code=%s (%s)", ctx.job_id, plan.decision_id, code, to)
    # A Stop waiting for this gate goes on: a parked job with no effect is canceled by it, one with effects waits.
    _outside_job_context(ctx, lambda: after_terminal(plan.group_id, plan.run_id))


def _transient(ctx, plan: Plan, code: str):
    store = _store()
    if int(ctx.job.get("attempt_count") or 0) + 1 > store.retry_max_attempts():
        _park(ctx, plan, code)
        return None
    return store.RunResult(store.TRANSIENT, code=code)


def _self_cancel(ctx, plan: Plan) -> None:
    """A Stop asked to cancel and nothing was done outside (D0007 §7.3): decision canceled, job cancelled."""
    store = _store()
    _get_store, now_iso = _db()
    _guarded(ctx, plan, lambda current: _move_in(current, repo.CANCELED, last_error_code=STOP_REQUESTED),
             dict(status="cancelled", phase_note=store.GATE_CANCEL_NO_EFFECT, result=store.GATE_CANCEL_NO_EFFECT,
                  finished_at=now_iso(), **_LEASE_CLEARED))


def _boundary(ctx, plan: Plan) -> None:
    """A phase boundary: the lease is still ours, and a Stop's cancel request is honoured here."""
    store = _store()
    store.ensure_lease(ctx)
    fresh = store.get_job(ctx.job_id)
    if not fresh or int(fresh.get("cancel_requested") or 0) != 1:
        return
    decision = repo.get(plan.decision_id)
    if decision is not None and effects_now(decision) == repo.EFFECTS_NONE:
        _self_cancel(ctx, plan)
        _outside_job_context(ctx, lambda: after_terminal(plan.group_id, plan.run_id))
    else:
        _park(ctx, plan, STOP_REQUESTED, force_recovery=True)
    raise _Stop(None)


# ── locks ────────────────────────────────────────────────────────────────────

def _mode() -> str:
    return "job"


def _acquire(ctx, domain: str, plan: Plan, *, target_key: Optional[str] = None, held: list) -> None:
    store, locks = _store(), _locks()
    store.ensure_lease(ctx)
    o = locks.acquire(domain, plan.project_id, group_id=plan.group_id if domain == "G" else None,
                      target_key=target_key, holder_kind="publish", mode=_mode(), hold_class="long",
                      ctx=ctx.lock_ctx)
    if o.ok and domain == "G" and not o.reentrant:
        o = locks._guard_group_admission(ctx.lock_ctx, o.lock_key, plan.project_id, plan.group_id, o)
    if not o.ok:
        raise _blocked(domain, o)
    held.append(o.lock_key)


def _release(ctx, held: list) -> None:
    locks = _locks()
    for key in reversed(held):
        if ctx.lock_ctx.find_held(key) is not None:
            locks.release(ctx.lock_ctx, key)
    held.clear()


def _slot(plan: Plan) -> git.Slot:
    try:
        return git.slot_of(plan.group_id)
    except git.git_error_type() as exc:
        shown = ((repo.get(plan.decision_id) or {}).get("settings") or {}).get("shown") or {}
        if "merge_target_branch" in shown:
            raise _Failure("gate_git_unavailable") from exc
        raise _Failure(exc.code or "gate_git_unavailable")


# ── the steps ────────────────────────────────────────────────────────────────

def _snapshot(ctx, plan: Plan) -> str:
    decision = repo.get(plan.decision_id)
    slot = _slot(plan)
    pin = git.pin_ref(plan.decision_id)
    held: list = []
    try:
        frozen = decision.get("frozen_sha")
        if frozen and (repo.effect(plan.decision_id, "snapshot") or {}).get("status") == repo.DONE:
            if git.rev(slot.base_root, pin) == frozen:
                return frozen
        _acquire(ctx, "G", plan, held=held)
        adopted = False
        if not frozen:
            head = git.rev(slot.base_root, f"refs/heads/{slot.group_branch}")
            if head is git.READ_ERROR:
                raise _Transient("group_branch_unreadable")
            if head is None:
                raise _Failure("group_branch_missing")
            pinned = git.rev(slot.base_root, pin)
            if pinned is git.READ_ERROR:
                raise _Transient("pin_ref_unreadable")
            if pinned:
                # An earlier claim pinned this decision's snapshot and ended before storing it: that pin is the
                # snapshot (it is named by the decision id). It must be a commit of the group branch; the HEAD of now
                # is never taken instead.
                if not git.commit_exists(slot.base_root, pinned) or \
                        git.covers(slot.base_root, head, pinned) is not True:
                    raise _Recovery("pin_ref_mismatch")
                frozen, adopted = pinned, True
            else:
                frozen = head
        uncommitted = git.uncommitted(slot.wt_path)
        if not adopted:
            _acquire(ctx, "M", plan, held=held)
            made = git.create_ref(slot.base_root, pin, frozen)
            if made == "error":
                raise _Transient("pin_ref_unwritable")
            if made == "other":
                raise _Recovery("pin_ref_mismatch")
        accepted_head = (decision.get("settings") or {}).get("group_head")
        job_ev = _evidence(ctx.job)
        job_ev.update({"frozen_sha": frozen, "group_branch": slot.group_branch, "uncommitted_excluded": uncommitted})
        store = _store()
        effect = _clean_effect({
            "status": repo.DONE, "target_ref": pin, "sha_after": frozen, "sha_before": accepted_head,
            "evidence": {"group_branch": slot.group_branch, "uncommitted_excluded": uncommitted, "changed": False,
                         "head_at_accept": accepted_head, "pin_taken_back": adopted,
                         "moved_since_accept": bool(accepted_head) and accepted_head != frozen}})

        def write(current: dict) -> None:
            if current.get("frozen_sha") != frozen:
                _move_in(current, frozen_sha=frozen)
            repo.record_effect(plan.decision_id, "snapshot", **effect)

        _guarded(ctx, plan, write, {"phase": "snapshot", "evidence": store._canonical_json(job_ev)})
        return frozen
    finally:
        _release(ctx, held)


def _merge(ctx, plan: Plan, frozen: str) -> str:
    """The merge into the recorded local target branch; returns the applied SHA (the target head holding the frozen SHA)."""
    done = repo.effect(plan.decision_id, "merge")
    if done and done["status"] == repo.DONE and done.get("sha_after"):
        return done["sha_after"]
    slot = _slot(plan)
    base_branch = (slot.cfg.get("base_branch") or "main").strip() or "main"
    basis = ((repo.get(plan.decision_id) or {}).get("settings") or {}).get("target_basis") or {}
    is_base = basis.get("is_project_base", plan.target_branch == base_branch)
    held: list = []
    try:
        if is_base:
            _acquire(ctx, "B", plan, held=held)
        else:
            _acquire(ctx, "W", plan, target_key=plan.target_branch, held=held)
        _phase(ctx, "merge")
        def started(evidence: dict) -> None:
            _effect(ctx, plan, "merge", status=repo.UNKNOWN, target_ref=f"refs/heads/{plan.target_branch}",
                    sha_before=evidence.get("pre_head"), evidence={**evidence, "frozen_sha": frozen},
                    new_attempt=True)
        try:
            git.validate_merge_target(slot, repo.get(plan.decision_id), ctx.job_id)
            left = git.merge_state(slot, plan.decision_id, ctx.job_id)
            if left is not None:
                return _merge_outcome(ctx, plan, left, frozen)
            result = git.merge(slot, plan.decision_id, ctx.job_id, plan.target_branch, frozen, started,
                               target_sha=plan.target_sha)
        except git.git_error_type() as exc:
            code = exc.code or "merge_failed"
            row = repo.effect(plan.decision_id, "merge")
            prior = (row or {}).get("evidence") or {}
            safe = {"target_branch": plan.target_branch, "accepted_target_sha": plan.target_sha,
                    "stage": "merge_check", "merge_id": prior.get("merge_id")}
            safe.update({k: v for k, v in (exc.details or {}).items() if k in (
                "target_branch", "accepted_target_sha", "observed_target_sha", "target_deleted", "stage",
                "merge_id", "reason", "changed", "observed_unavailable")})
            if row and row["status"] == repo.UNKNOWN and "changed" not in safe:
                mid = prior.get("merge_id")
                session = git._gs().db_git.get_session(mid) if mid else None
                observed = git.rev(slot.base_root, f"refs/heads/{plan.target_branch}")
                if session and session.get("status") == "aborted" and observed == row.get("sha_before"):
                    safe.update(changed=False, observed_target_sha=observed, stage="merge_failed_closed")
            # An unknown effect remains unknown unless no external change is proved.
            unknown = row is not None and row["status"] == repo.UNKNOWN and safe.get("changed") is not False
            _effect(ctx, plan, "merge", status=repo.UNKNOWN if unknown else repo.EFFECT_FAILED,
                    target_ref=f"refs/heads/{plan.target_branch}", error_code=code,
                    evidence={**prior, **safe, "changed": None if unknown else safe.get("changed", False)})
            if plan.target_sha is None and int(getattr(exc, "status", 409) or 409) >= 500:
                raise _Transient(code)
            raise _Recovery(code) if unknown or safe.get("changed") else _Failure(code)
        return _merge_outcome(ctx, plan, result, frozen)
    finally:
        git._mt().invalidate_check_targets(plan.project_id)
        _release(ctx, held)


def _merge_outcome(ctx, plan: Plan, result: dict, frozen: str) -> str:
    outcome = result.get("outcome")
    target_ref = f"refs/heads/{plan.target_branch}"
    basis = ((repo.get(plan.decision_id) or {}).get("settings") or {}).get("target_basis") or {}
    pre_head = result.get("pre_head") or basis.get("accepted_target_sha")
    if outcome == git.CONTAINED:
        _effect(ctx, plan, "merge", status=repo.DONE, target_ref=target_ref, sha_before=pre_head,
                sha_after=result["merge_commit"], evidence={"changed": False, "frozen_sha": frozen})
        return result["merge_commit"]
    if outcome == git.MERGED and result.get("merge_commit"):
        fields = _clean_effect({"status": repo.DONE, "target_ref": target_ref, "sha_before": pre_head,
                                "sha_after": result["merge_commit"],
                                "evidence": {"changed": True, "merge_id": result.get("merge_id"),
                                             "merge_commit": result["merge_commit"], "frozen_sha": frozen}})

        def write(current: dict) -> None:
            repo.record_effect(plan.decision_id, "merge", **fields)
            _move_in(current, target_sha=result["merge_commit"])

        _guarded(ctx, plan, write)
        return result["merge_commit"]
    store = _store()
    if outcome == git.CONFLICT:
        _effect(ctx, plan, "merge", status=repo.PENDING, target_ref=target_ref, error_code="merge_conflict",
                evidence={"changed": False, "merge_id": result.get("merge_id"), "conflict": True,
                          "conflict_count": len(result.get("files") or [])})
        raise _Failure("merge_conflict", note=store.GATE_PARKED_CONFLICT, auto=True)
    if outcome == "conflict_closed":
        _effect(ctx, plan, "merge", status=repo.EFFECT_FAILED, target_ref=target_ref,
                error_code="merge_conflict_aborted", evidence={"changed": False, "merge_id": result.get("merge_id")})
        raise _Failure("merge_conflict_aborted")
    _effect(ctx, plan, "merge", status=repo.UNKNOWN, target_ref=target_ref, error_code="merge_attempt_unsettled",
            evidence={"merge_id": result.get("merge_id")})
    raise _Recovery("merge_attempt_unsettled")


def _push(ctx, plan: Plan, sha: str) -> None:
    done = repo.effect(plan.decision_id, "push")
    if done and done["status"] == repo.DONE:
        return
    slot = _slot(plan)
    branch = plan.push_branch
    remote_ref = f"origin:refs/heads/{branch}"
    held: list = []
    try:
        _acquire(ctx, "R", plan, held=held)
        _phase(ctx, "push")
        h0 = git.remote_head(slot, branch)
        if h0 is git.READ_ERROR:
            if done is not None and done["status"] == repo.UNKNOWN:
                raise _Transient("push_result_unknown")
            raise _Transient("remote_head_unknown")
        if h0:
            there = git.remote_contains(slot, branch, sha)
            if there is True:
                pushed_before = done is not None and done["status"] in (repo.UNKNOWN, repo.EFFECT_FAILED)
                _effect(ctx, plan, "push", status=repo.DONE, target_ref=remote_ref, sha_before=h0, sha_after=sha,
                        evidence={"remote": "origin", "branch": branch, "remote_head": h0, "changed": pushed_before,
                                  "remote_head_before": h0, "sent_sha": sha, "attempt_kind": "reconcile"})
                return
            if there is git.READ_ERROR:
                raise _Transient("remote_head_unknown")
        # Before the push: from here its effect is unknown until its outcome is written.
        _effect(ctx, plan, "push", status=repo.UNKNOWN, target_ref=remote_ref, sha_before=h0 or None, sha_after=sha,
                evidence={"remote": "origin", "branch": branch, "remote_head_before": h0 or None,
                          "sent_sha": sha, "attempt_kind": "update" if h0 else "create"}, new_attempt=True)
        _store().ensure_lease(ctx)
        outcome, code = git.push(slot, sha, branch)
        if outcome == git.PUSHED:
            _effect(ctx, plan, "push", status=repo.DONE, target_ref=remote_ref, sha_after=sha,
                    evidence={"remote": "origin", "branch": branch, "changed": True, "result": code,
                              "remote_head_before": h0 or None, "sent_sha": sha,
                              "attempt_kind": "update" if h0 else "create"})
            return
        if outcome == git.PUSH_UNKNOWN:
            _effect(ctx, plan, "push", status=repo.UNKNOWN, error_code=code)
            raise _Transient(code)
        _effect(ctx, plan, "push", status=repo.EFFECT_FAILED, error_code=code,
                evidence={"remote": "origin", "branch": branch, "changed": False,
                          "remote_head_before": h0 or None, "sent_sha": sha,
                          "attempt_kind": "update" if h0 else "create"})
        if outcome == git.PUSH_TRANSIENT:
            raise _Transient(code)
        raise _Failure(code)
    finally:
        _release(ctx, held)


def _archive(ctx, plan: Plan, frozen: str, applied: Optional[str]) -> None:
    done = repo.effect(plan.decision_id, "archive")
    checkpoint = repo.checkpoint_of_decision(plan.decision_id)
    if done and done["status"] == repo.DONE and checkpoint:
        return
    slot = _slot(plan)
    ref, extra = git.archive_ref(plan.decision_id), git.applied_ref(plan.decision_id)
    with_applied = bool(applied) and applied != frozen
    _phase(ctx, "archive")
    if done is None or done["status"] != repo.UNKNOWN:
        # The intent before any ref (D0007 §7.3): an archive cut off after its ref reads as unknown, never as none.
        _effect(ctx, plan, "archive", status=repo.UNKNOWN, target_ref=ref, sha_after=frozen,
                evidence={"applied_ref": extra if with_applied else None, "applied_sha": applied}, new_attempt=True)
    held: list = []
    try:
        _acquire(ctx, "M", plan, held=held)
        first = git.create_ref(slot.base_root, ref, frozen)
        second = git.create_ref(slot.base_root, extra, applied) if with_applied else None
    finally:
        _release(ctx, held)
    if "error" in (first, second):
        raise _Transient("archive_ref_unwritable")
    if "other" in (first, second):
        _effect(ctx, plan, "archive", status=repo.UNKNOWN, target_ref=ref, error_code="archive_ref_mismatch")
        raise _Recovery("archive_ref_mismatch")

    def write(_current: dict) -> None:
        found = repo.checkpoint_of_decision(plan.decision_id)
        if found is None:
            found = repo.insert_checkpoint({
                "decision_id": plan.decision_id, "group_id": plan.group_id, "run_id": plan.run_id,
                "node_key": plan.node_key, "attempt_id": plan.attempt_id, "source_sha": frozen,
                "applied_sha": applied, "ref_name": ref, "applied_ref": extra if second is not None else None,
                "restore_info": {"kind": "gate_checkpoint", "consumable": False, "branch": plan.group_branch,
                                 "mode": plan.mode}})
        # The refs are this decision's own (named by its id): found already there, they are still its effect.
        repo.record_effect(plan.decision_id, "archive", **_clean_effect({
            "status": repo.DONE, "target_ref": ref, "sha_after": frozen, "archive_id": found["archive_id"],
            "evidence": {"changed": True, "reused": first == "same", "applied_ref": found.get("applied_ref"),
                         "applied_sha": applied}}))

    _guarded(ctx, plan, write)


# ── db_finalize (one Flow guard transaction) ─────────────────────────────────

def _complete(ctx, plan: Plan) -> None:
    from flowgate.gate_decisions import intake
    store = _store()
    _get_store, now_iso = _db()
    written: dict = {}
    store.ensure_lease(ctx)

    def body_of(run: dict, jobs: list) -> dict:
        decision = repo.get(plan.decision_id)
        if decision is None or decision.get("job_id") != ctx.job_id:
            raise store.LeaseLost(ctx.job_id)
        if decision["status"] != repo.ACCEPTED:
            raise _Recovery("gate_decision_not_accepted")
        run_ctx = legacy.load_context(run, plan.project_id, decision["actor_user_id"], jobs)
        state = legacy.read_run_state(run_ctx)
        node = state.node(plan.node_key)
        if node is None or node.status != "open":
            raise _Recovery("gate_not_open")
        policies = legacy.policies_of(legacy.run_store.policy_entries(plan.run_id))
        written.update(store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                                    result={"decision_id": plan.decision_id, "confirmed": True},
                                                    phase_note=None, **_LEASE_CLEARED), update_local=False))
        if not repo.move(plan.decision_id, decision["decision_version"], repo.SUCCEEDED):
            raise AssertionError("gate decision moved under its job's lease")
        stored = repo.get(plan.decision_id)
        intake._apply(run_ctx, state, policies, legacy.GATE_EVENTS["confirmed"], plan.node_key,
                      decision["actor_user_id"], decision.get("decision_comment"), stored)
        return {"status": run["status"]}

    try:
        outcome = legacy.guarded_run(plan.run_id, body_of)
    except (_Stop, store.LeaseLost):
        raise
    except Exception as exc:  # noqa: BLE001 — Git is done; only this phase is retried
        _log.warning("gate publish db_finalize failed: %s (%s)", ctx.job_id, type(exc).__name__, exc_info=True)
        raise _DbRetry("gate_db_finalize_failed")
    ctx.job.update(written)
    _outside_job_context(ctx, lambda: after_terminal(plan.group_id, plan.run_id))
    _log.info("gate publish done: %s decision=%s run=%s", ctx.job_id, plan.decision_id, outcome.get("status"))


class _DbRetry(_Stop):
    def __init__(self, code: str):
        super().__init__(None)
        self.code = code


def _outside_job_context(ctx, call) -> None:
    """Run ``call`` with the job's lock context unbound (it holds nothing between phases), so what it locks is its
    own; the context is bound again for the Runner's own end."""
    locks = _locks()
    locks.unbind(ctx.lock_ctx)
    try:
        call()
    finally:
        locks.bind(ctx.lock_ctx)


def wake_next(flow_id: str) -> None:
    """The group's next gate job in acceptance order may run now (its predecessor ended): its wake-up signal. The
    Sweeper's candidate scan picks it up as well; this only saves the wait. Never raises."""
    try:
        store = _store()
        job = store.next_gate_publish(flow_id)
        if job is not None and job.get("status") in ("pending", "blocked", "retry_wait"):
            store.notify_created(job)
    except Exception:  # noqa: BLE001
        _log.warning("gate publish could not wake the next job of %s", flow_id, exc_info=True)


def after_terminal(flow_id: str, run_id: str) -> None:
    """After a gate job ended or parked (and its commit): a Stop waiting for it goes on, and the group's next gate job
    may run. Never raises."""
    try:
        run = legacy.run_store.run_by_id(run_id)
        if run is not None and run["status"] == "stopping":
            from flowgate.graph_run import stop
            stop.advance(flow_id, run_id)
    except Exception:  # noqa: BLE001
        _log.exception("gate publish could not let the Stop go on run=%s", run_id)
    wake_next(flow_id)


# ── executor ─────────────────────────────────────────────────────────────────

def _close_after_terminal(ctx, decision: dict) -> None:
    """The decision ended while its job did not (a resolve wrote both; a job row left behind): end the job too."""
    store = _store()
    get_store, now_iso = _db()
    ok = decision["status"] == repo.SUCCEEDED
    note = None if ok else (store.GATE_CANCEL_ABANDONED if decision["status"] == repo.ABANDONED
                            else store.GATE_CANCEL_NO_EFFECT)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded" if ok else "cancelled", phase="done" if ok else
                                               ctx.job.get("phase"), phase_note=note, finished_at=now_iso(),
                                               result={"decision_id": decision["decision_id"],
                                                       "decision_status": decision["status"]},
                                               **_LEASE_CLEARED), update_local=False)
    ctx.job.update(written)


def run(ctx, entry: str):
    """Runner executor. None once the outcome is written, else the claim's result (blocked / transient)."""
    store = _store()
    if entry != store.PUBLISH:
        raise store.JobProgramError("gate_publish_entry_not_publish", job_id=ctx.job_id, entry=entry)
    job = ctx.job
    decision_id = _payload(job).get("decision_id")
    decision = repo.get(decision_id) if decision_id else None
    if decision is None:                             # never run blindly: parked for a person, never retried alone
        store.job_write(ctx, status="recovery_required", last_error_code="gate_decision_missing",
                        phase_note=store.GATE_PARKED_RECOVERY, retryable=0, recovery_since=None, **_LEASE_CLEARED)
        return None
    if decision["status"] in repo.TERMINAL:
        _close_after_terminal(ctx, decision)
        return None
    plan = plan_of(job, decision)
    try:
        if (job.get("phase") or "start") != "db_finalize":
            if decision["status"] != repo.ACCEPTED:
                _guarded(ctx, plan, lambda current: current["status"] == repo.ACCEPTED or
                         _move_in(current, repo.ACCEPTED, last_error_code=None))
            _boundary(ctx, plan)
            frozen = _snapshot(ctx, plan)
            applied = frozen
            _boundary(ctx, plan)
            if plan.mode == "merge":
                applied = _merge(ctx, plan, frozen)
                _boundary(ctx, plan)
            if plan.push:
                _push(ctx, plan, applied)
                _boundary(ctx, plan)
            if plan.archive:
                _archive(ctx, plan, frozen, applied if plan.mode == "merge" else None)
                _boundary(ctx, plan)
            _phase(ctx, "db_finalize")
        _complete(ctx, plan)
        return None
    except _DbRetry as stop:
        return store.RunResult(store.TRANSIENT, code=stop.code, db_finalize_only=True)
    except _Transient as stop:
        return _transient(ctx, plan, stop.code)
    except _Failure as stop:
        if plan.mode == "merge" and stop.code == "gate_git_unavailable" and repo.effect(plan.decision_id, "merge") is None:
            basis = ((repo.get(plan.decision_id) or {}).get("settings") or {}).get("target_basis") or {}
            _effect(ctx, plan, "merge", status=repo.EFFECT_FAILED, error_code=stop.code,
                    target_ref=f"refs/heads/{plan.target_branch}", evidence={"changed": False, "stage": "slot_check",
                    "target_branch": plan.target_branch, "accepted_target_sha": basis.get("accepted_target_sha")})
        _park(ctx, plan, stop.code, note=stop.note, auto=stop.auto,
              force_recovery=getattr(stop, "force_recovery", False))
        return None
    except _Stop as stop:
        return stop.result
    except git.git_error_type() as exc:
        _park(ctx, plan, exc.code or "git_error")
        return None


# ── Runner registration ──────────────────────────────────────────────────────

def _decide(job: dict):
    """L 4.4 rows of this kind: an interrupted claim resumes at its recorded phase - every step reads its own
    evidence first. A job parked for a person stays parked; a conflict hand-off goes on once its review closed."""
    runner, store = _runner(), _store()
    note = job.get("phase_note")
    if note == store.GATE_PARKED_CONFLICT:
        merge_id = (_conflict_merge_id(job))
        if merge_id is not None:
            from modules.flow_gate.services import git_service as gs
            session = gs.db_git.get_session(merge_id)
            if session is not None and session.get("status") == "open":
                return runner.Decision(runner.UNDECIDABLE, code="merge_conflict")
        return runner.Decision(runner.NOT_APPLIED)
    if note in (store.GATE_PARKED_FAILED, store.GATE_PARKED_RECOVERY):
        return runner.Decision(runner.UNDECIDABLE, code=job.get("last_error_code") or "gate_parked")
    return runner.Decision(runner.NOT_APPLIED)


def _conflict_merge_id(job: dict) -> Optional[int]:
    decision_id = _payload(job).get("decision_id")
    row = repo.effect(decision_id, "merge") if decision_id else None
    merge_id = ((row or {}).get("evidence") or {}).get("merge_id")
    try:
        return int(merge_id) if merge_id is not None else None
    except (TypeError, ValueError):
        return None


def notify(job: dict) -> None:
    """After a Runner / Sweeper body moved the job (its commit done): the screen's refresh signal, credential-free."""
    try:
        from modules.flow_gate.services import git_service as gs
        p = _payload(job)
        gs._emit(JOB_EVENT, job.get("project_id"), job.get("group_id"), redact.scrub_value({
            "project": job.get("project_id"), "group_id": job.get("group_id"), "job_id": job.get("job_id"),
            "status": job.get("status"), "phase": job.get("phase"),
            "last_error_code": redact.safe_code(job.get("last_error_code"), "") or None,
            "decision_id": p.get("decision_id"), "run_id": p.get("run_id"), "node_key": p.get("node_key")}))
    except Exception:  # noqa: BLE001
        _log.warning("gate publish event failed: %s", job.get("job_id"), exc_info=True)


def install() -> None:
    """Executor, deciders and observer of ``gate_publish`` - and of the end cleanup job (``cleanup``). Idempotent."""
    runner = _runner()
    runner.register_executor(KIND, run)
    for phase in PHASES:
        runner.register_decider(KIND, phase, _decide)
    runner.register_observer(KIND, notify)
    from flowgate.gate_decisions import cleanup
    cleanup.install()
