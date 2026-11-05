"""The ``gate_publish`` job: a confirm's Git apply, archive and completion (flowgate.v02.0085 T#3, D0007 §3.3 / §7.3).

A confirm that needs Git work is accepted with this job (``intake``); the gate stays open and held until the job is
done. The job is a Runner executor (``job_runner``), so it uses the existing claim, lease, fenced writes, lock manager
domains and their order (G, then W or B, then R, then M), backoff and reconcile. Phases, each recorded before its step:

    start -> snapshot (G, M) -> merge (B or W) -> push (merge: B or W, then R; commit: R) -> archive (M)
          -> db_finalize -> done

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
  is unknown, a network failure retries with backoff, anything else needs recovery. Never forced. 0132 T#2 (NR §4.2 /
  §4.3): merge mode holds W(target) or B, then R, for the whole phase, checks the local target and its checkout
  against the applied SHA (pushing the local target, another owner's result on top recorded as ``foreign_included``),
  and a push refused because only the remote went ahead (``push_remote_ahead``) merges the fetched remote tip into the
  target (the ``remote_sync`` effect, its own attempt) and pushes again, at most ``sync_round_limit()`` rounds. A sync
  conflict goes to the merge review like a merge conflict; the decision's applied SHA stays its merge result.
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
    that cannot be read is unknown too. Read only, no lock (a local ref read). The push phase's ``remote_sync`` is one
    of ``repository.EXTERNAL_PHASES`` (0132 T#2): a sync commit is an effect, a sync cut off is unknown."""
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


SYNC_PHASE = "remote_sync"
SYNC_ROUNDS_ENV = "FLOWGATE_GATE_SYNC_ROUNDS"
SYNC_ROUNDS_DEFAULT = 3
FOREIGN_LIST_MAX = 100


def sync_round_limit() -> int:
    """``N`` of 0132 NR §4.2: the remote sync rounds one push phase may run before it parks ``push_remote_race``."""
    import os
    try:
        return max(1, int(os.environ.get(SYNC_ROUNDS_ENV, SYNC_ROUNDS_DEFAULT)))
    except (TypeError, ValueError):
        return SYNC_ROUNDS_DEFAULT


def _target_is_base(plan: Plan, slot: git.Slot) -> bool:
    """The target's lock slot exactly as ``_merge`` takes it: B for the project base, else W(target)."""
    base_branch = (slot.cfg.get("base_branch") or "main").strip() or "main"
    basis = ((repo.get(plan.decision_id) or {}).get("settings") or {}).get("target_basis") or {}
    return bool(basis.get("is_project_base", plan.target_branch == base_branch))


def _push(ctx, plan: Plan, applied: str) -> None:
    """The push phase (0132 NR §4.2). commit mode: R, one plain push of the frozen SHA (a non-fast-forward parks
    ``push_remote_ahead``). merge mode: W(target) or B, then R, held for the whole phase. Under them the remote sync
    left by an earlier claim is settled first (``_settle_sync``), then ``A`` (the applied SHA: the sync commit of a
    done sync, else the merge result ``A₀``), ``L`` (the local target ref) and ``C`` (its checkout) are read and
    judged (``_target_check``); the push sends ``P = L``. A plain push refused as ``push_remote_ahead`` fetches the
    remote tip ``h1`` and merges it into the target (``_sync_round``), then pushes again, at most ``N`` rounds
    (``push_remote_race`` after). A conflict goes to the merge review (``gate_conflict``). The decision's applied SHA
    stays ``A₀`` (the archive's ``-applied`` ref, the checkpoint, ``recovery._proof``); the SHA sent and the sync
    commits are evidence only. Never forced, never reset."""
    done = repo.effect(plan.decision_id, "push")
    if done and done["status"] == repo.DONE:
        return
    slot = _slot(plan)
    branch = plan.push_branch
    held: list = []
    try:
        is_base = False
        if plan.mode == "merge":
            is_base = _target_is_base(plan, slot)
            if is_base:
                _acquire(ctx, "B", plan, held=held)
            else:
                _acquire(ctx, "W", plan, target_key=plan.target_branch, held=held)
        _acquire(ctx, "R", plan, held=held)
        _phase(ctx, "push")
        if plan.mode != "merge":
            refused = _push_attempt(ctx, plan, slot, branch, applied, {})
            if refused is not None:
                raise _Failure(refused)
            return
        _settle_sync(ctx, plan, slot, is_base)
        sha, extra = _target_check(plan, slot, applied, is_base)
        # The refusals of earlier claims stay in the evidence a later push writes (0132 T#2 rework: an audit trail
        # across retries, like ``foreign_included``).
        carried = ((repo.effect(plan.decision_id, "push") or {}).get("evidence") or {}).get("rejections")
        if isinstance(carried, list) and carried:
            extra["rejections"] = list(carried)
        limit = sync_round_limit()
        rounds = 0
        while True:
            refused = _push_attempt(ctx, plan, slot, branch, sha, extra)
            if refused is None:
                return
            if refused != "push_remote_ahead":
                raise _Failure(refused)
            rounds += 1
            refused_evidence = (repo.effect(plan.decision_id, "push") or {}).get("evidence") or {}
            extra["rejected_rounds"] = rounds
            extra["rejections"] = [*extra.get("rejections", []), {
                "sent_sha": sha, "code": refused, "exit_code": refused_evidence.get("exit_code"),
                "diagnostic_tail": refused_evidence.get("diagnostic_tail")}]
            if rounds > limit:
                row = repo.effect(plan.decision_id, "push") or {}
                _effect(ctx, plan, "push", status=repo.EFFECT_FAILED, error_code="push_remote_race",
                        evidence={**(row.get("evidence") or {}), "changed": False, "sync_round_limit": limit,
                                  "rejected_rounds": rounds, "rejections": extra["rejections"]})
                raise _Failure("push_remote_race")
            sha = _sync_round(ctx, plan, slot, branch, sha, applied, is_base)
            extra["sync_commit"] = sha
    finally:
        if plan.mode == "merge":
            git._mt().invalidate_check_targets(plan.project_id)
        _release(ctx, held)


def _push_attempt(ctx, plan: Plan, slot: git.Slot, branch: str, sha: str, extra: dict) -> Optional[str]:
    """One plain push of ``sha`` (the remote read first: holding it already is a reconcile). None once the push effect
    is done; the refusal's code once it is recorded failed; ``_Transient`` for an unknown or transient outcome."""
    remote_ref = f"origin:refs/heads/{branch}"
    prior = repo.effect(plan.decision_id, "push")
    h0 = git.remote_head(slot, branch)
    if h0 is git.READ_ERROR:
        if prior is not None and prior["status"] == repo.UNKNOWN:
            raise _Transient("push_result_unknown")
        raise _Transient("remote_head_unknown")
    if h0:
        there = git.remote_contains(slot, branch, sha)
        if there is True:
            pushed_before = prior is not None and prior["status"] in (repo.UNKNOWN, repo.EFFECT_FAILED)
            _effect(ctx, plan, "push", status=repo.DONE, target_ref=remote_ref, sha_before=h0, sha_after=sha,
                    evidence={"remote": "origin", "branch": branch, "remote_head": h0, "changed": pushed_before,
                              "remote_head_before": h0, "sent_sha": sha, "attempt_kind": "reconcile", **extra})
            return None
        if there is git.READ_ERROR:
            raise _Transient("remote_head_unknown")
    evidence = {"remote": "origin", "branch": branch, "remote_head_before": h0 or None, "sent_sha": sha,
                "attempt_kind": "update" if h0 else "create", **extra}
    # Before the push: from here its effect is unknown until its outcome is written.
    _effect(ctx, plan, "push", status=repo.UNKNOWN, target_ref=remote_ref, sha_before=h0 or None, sha_after=sha,
            evidence=evidence, new_attempt=True)
    _store().ensure_lease(ctx)
    outcome, code, command_evidence = git.push(slot, sha, branch)
    if outcome == git.PUSHED:
        _effect(ctx, plan, "push", status=repo.DONE, target_ref=remote_ref, sha_after=sha,
                evidence={**evidence, "changed": True, "result": code, **command_evidence})
        return None
    if outcome == git.PUSH_UNKNOWN:
        _effect(ctx, plan, "push", status=repo.UNKNOWN, error_code=code, evidence={**evidence, **command_evidence})
        raise _Transient(code)
    _effect(ctx, plan, "push", status=repo.EFFECT_FAILED, error_code=code,
            evidence={**evidence, "changed": False, **command_evidence})
    if outcome == git.PUSH_TRANSIENT:
        raise _Transient(code)
    return code


def _target_check(plan: Plan, slot: git.Slot, applied: str, is_base: bool) -> tuple[str, dict]:
    """``A`` / ``L`` / ``C`` of 0132 NR §4.2 under the target's lock: ``(P, push evidence extra)``. ``L`` unreadable is
    transient; a checkout off ``L`` or with tracked changes parks ``target_checkout_mismatch``; ``L`` holding ``A``
    (the same or a descendant: another owner's result on top, recorded as ``foreign_included``) goes on with
    ``P = L``; anything else (diverged, or ``L`` behind ``A``) parks ``target_diverged``.

    ``foreign_included`` is always counted from the approved applied SHA ``A₀`` (WP0004 (1), NR §4.2), never from
    the last sync commit: the commits of ``A₀..L`` that came neither from the remote (the fetched tips ``h`` of the
    decision's sync rounds) nor from the decision's own sync commits. A retry after a sync round therefore still
    records the other owners' results it sends (0132 T#2 rework)."""
    synced = _last_sync_commit(plan.decision_id)
    a = synced or applied
    local = git.rev(slot.base_root, f"refs/heads/{plan.target_branch}")
    if local is git.READ_ERROR:
        raise _Transient("target_ref_unreadable")
    if local is None:
        raise _Failure("merge_target_not_found")
    checkout = git.target_checkout(slot, plan.target_branch, is_base)
    if checkout is git.READ_ERROR:
        raise _Transient("target_checkout_unreadable")
    if not checkout["clean"] or (is_base and not checkout["on_branch"]) or \
            (checkout["checkouts"] and checkout["head"] != local):
        raise _Failure("target_checkout_mismatch")
    extra: dict = {"applied_sha": applied, "local_target": local}
    if synced:
        extra["sync_commit"] = synced
    if local != a:
        holds = git.is_ancestor(slot.base_root, a, local)
        if holds is None:
            raise _Transient("ancestry_unreadable")
        if not holds:
            raise _Failure("target_diverged")
    if local != applied:
        foreign = _foreign_included(plan.decision_id, slot, applied, local)
        if foreign is None:
            raise _Transient("ancestry_unreadable")
        if foreign:
            extra["foreign_included"] = foreign
    return local, extra


def _foreign_included(decision_id: str, slot: git.Slot, applied: str, local: str) -> Optional[list]:
    """The other owners' results ``P = L`` carries (newest first, at most ``FOREIGN_LIST_MAX``): ``A₀..L`` without
    what the remote already had (every ``h`` the decision's sync rounds merged) and without the decision's own sync
    commits. None when Git cannot tell."""
    evidence = (repo.effect(decision_id, SYNC_PHASE) or {}).get("evidence") or {}
    rounds = [r for r in (evidence.get("rounds") or []) if isinstance(r, dict)]
    remote_heads = {r.get("remote_head") for r in rounds} | {evidence.get("remote_head")}
    own = {r.get("sync_commit") for r in rounds} | {evidence.get("sync_commit"), evidence.get("observed_sync_commit")}
    remote_heads.discard(None)
    own.discard(None)
    listed = git.commits_excluding(slot.base_root, local, [applied, *sorted(remote_heads)],
                                   FOREIGN_LIST_MAX + len(own))
    if listed is None:
        return None
    return [sha for sha in listed if sha not in own][:FOREIGN_LIST_MAX]


def _last_sync_commit(decision_id: str) -> Optional[str]:
    """The sync commit of the decision's last completed sync round (a later failed / cut-off round keeps the list)."""
    row = repo.effect(decision_id, SYNC_PHASE)
    evidence = (row or {}).get("evidence") or {}
    done = [r.get("sync_commit") for r in (evidence.get("rounds") or []) if r.get("sync_commit")]
    return done[-1] if done else None


def sync_commit_to_keep(decision: dict, slot: Optional[git.Slot]) -> Optional[str]:
    """abandon's ``-sync`` recovery ref target (NR §4.3, 0132 T#2 rework): the newest sync commit of the decision that
    is in Git - not only the rounds recorded done. A round whose row is still ``unknown`` / ``pending`` / ``failed``
    (its commit landed and the claim ended before writing it, or the resume could not settle it) is judged from what
    is read now, read only: the commit recorded for its own attempt (``observed_sync_commit``, the attempt's result)
    holding both its ``pre_head`` and ``h1``, else the merge commit with exactly those parents on the local target.
    Nothing landed of that round: the last done round's commit. No slot: the attempt's own recorded commit, unverified
    (it becomes a retention obligation either way)."""
    row = repo.effect(decision["decision_id"], SYNC_PHASE)
    if row is None:
        return None
    evidence = row.get("evidence") or {}
    last = _last_sync_commit(decision["decision_id"])
    if row["status"] == repo.DONE:
        return evidence.get("sync_commit") or last
    if slot is None:
        return evidence.get("observed_sync_commit") or last
    return _landed_sync_commit(slot, decision.get("target_branch"), evidence) or last


def _landed_sync_commit(slot: git.Slot, target_branch: Optional[str], evidence: dict) -> Optional[str]:
    """The current round's sync commit if it landed (read only, no lock: object and ref reads), else None."""
    pre_head, h1 = evidence.get("pre_head"), evidence.get("remote_head")
    if not pre_head or not h1:
        return None
    root = slot.base_root
    for sha in (evidence.get("observed_sync_commit"), git.sync_recorded_commit(slot, evidence.get("merge_id"))):
        if sha and sha != pre_head and git.commit_exists(root, sha) and \
                git.is_ancestor(root, pre_head, sha) is True and git.is_ancestor(root, h1, sha) is True:
            return sha
    if not target_branch:
        return None
    local = git.rev(root, f"refs/heads/{target_branch}")
    if local in (None, git.READ_ERROR) or local == pre_head:
        return None
    return git.merge_of(root, pre_head, h1, local)


def _sync_effect(ctx, plan: Plan, **fields) -> None:
    _effect(ctx, plan, SYNC_PHASE, target_ref=f"refs/heads/{plan.target_branch}", **fields)


def _sync_round(ctx, plan: Plan, slot: git.Slot, branch: str, pre_head: str, applied: str, is_base: bool) -> str:
    """One remote sync round after a ``push_remote_ahead``: fetch ``h1``, record the round ``unknown`` (with the
    attempt's merge id, before the merge), merge ``h1`` into the target and verify the result ``S`` (``S^1 = L``,
    ``S^2 = h1``, the target ref and its checkout at ``S``). Returns the next ``P``: ``S``, or ``L`` when the remote tip
    is already held. A conflict parks ``gate_conflict`` (the review has the attempt); anything unproven parks
    ``sync_unsettled``."""
    _store().ensure_lease(ctx)
    if not git.fetch_branch(slot, branch):
        raise _Transient("remote_fetch_failed")
    h1 = git.remote_tracking_head(slot, branch)
    if h1 is git.READ_ERROR or not h1:
        raise _Transient("remote_head_unknown")
    prior = repo.effect(plan.decision_id, SYNC_PHASE)
    rounds = list(((prior or {}).get("evidence") or {}).get("rounds") or [])
    current = {"round": len(rounds) + 1, "pre_head": pre_head, "remote_head": h1, "local_applied": applied}
    started_ids: list = []

    def started(evidence: dict) -> None:
        started_ids.append(evidence.get("merge_id"))
        _sync_effect(ctx, plan, status=repo.UNKNOWN, sha_before=pre_head, sha_after=None, error_code=None,
                     evidence={**current, "merge_id": evidence.get("merge_id"), "status": repo.UNKNOWN,
                               "changed": None, "rounds": rounds}, new_attempt=True)

    target_ref = f"refs/heads/{plan.target_branch}"
    try:
        result = git.sync_merge(slot, plan.decision_id, ctx.job_id, plan.target_branch, pre_head, h1, started)
    except git.git_error_type() as exc:
        code = exc.code or "sync_merge_failed"
        observed = git.rev(slot.base_root, target_ref)
        if not started_ids:
            if observed == pre_head:
                if int(getattr(exc, "status", 409) or 409) >= 500:
                    raise _Transient(code)
                raise _Failure(code)
            raise _Recovery("sync_unsettled")
        evidence = {**current, "merge_id": started_ids[-1], "rounds": rounds}
        if observed == pre_head:
            _sync_effect(ctx, plan, status=repo.EFFECT_FAILED, error_code=code,
                         evidence={**evidence, "status": repo.EFFECT_FAILED, "changed": False})
            raise _Failure(code)
        _sync_effect(ctx, plan, status=repo.UNKNOWN, error_code="sync_unsettled",
                     evidence={**evidence, "status": repo.UNKNOWN, "changed": None})
        raise _Recovery("sync_unsettled")
    outcome = result.get("outcome")
    if outcome == git.CONTAINED:
        return pre_head
    evidence = {**current, "merge_id": result.get("merge_id"), "rounds": rounds}
    if outcome == git.CONFLICT:
        _sync_effect(ctx, plan, status=repo.PENDING, error_code="merge_conflict",
                     evidence={**evidence, "status": "conflict", "conflict": True, "changed": False,
                               "conflict_count": len(result.get("files") or [])})
        raise _Failure("merge_conflict", note=_store().GATE_PARKED_CONFLICT, auto=True)
    s = result.get("merge_commit")
    if not s or git.parents(slot.base_root, s) != [pre_head, h1] or not _target_at(slot, plan, is_base, s):
        _sync_effect(ctx, plan, status=repo.UNKNOWN, error_code="sync_unsettled",
                     evidence={**evidence, "status": repo.UNKNOWN, "changed": None, "observed_sync_commit": s})
        raise _Recovery("sync_unsettled")
    _sync_done(ctx, plan, evidence, pre_head, s, reviewed=False)
    return s


def _target_at(slot: git.Slot, plan: Plan, is_base: bool, sha: str, *, or_descendant: bool = False) -> bool:
    """The target ref is ``sha`` (or holds it) and so is its checkout (a base checkout on the branch, clean)."""
    local = git.rev(slot.base_root, f"refs/heads/{plan.target_branch}")
    if local in (None, git.READ_ERROR):
        return False
    if local != sha and not (or_descendant and git.is_ancestor(slot.base_root, sha, local) is True):
        return False
    checkout = git.target_checkout(slot, plan.target_branch, is_base)
    if checkout is git.READ_ERROR or not checkout["clean"] or (is_base and not checkout["on_branch"]):
        return False
    return not checkout["checkouts"] or checkout["head"] == local


def _sync_done(ctx, plan: Plan, evidence: dict, pre_head: str, s: str, *, reviewed: bool) -> None:
    rounds = list(evidence.get("rounds") or [])
    rounds.append({"round": evidence.get("round"), "pre_head": pre_head, "remote_head": evidence.get("remote_head"),
                   "merge_id": evidence.get("merge_id"), "sync_commit": s, "reviewed": reviewed})
    _sync_effect(ctx, plan, status=repo.DONE, sha_before=pre_head, sha_after=s, error_code=None,
                 evidence={**evidence, "status": repo.DONE, "sync_commit": s, "changed": True, "reviewed": reviewed,
                           "rounds": rounds})


def _settle_sync(ctx, plan: Plan, slot: git.Slot, is_base: bool) -> None:
    """At the push phase's entry, under the target's lock: the remote sync an earlier claim left is judged from its
    attempt (``git.sync_state``, read by its own merge id) before anything else - the decision's own merge is never
    run again. A review that completed is verified (its result holds ``pre_head`` and ``h1``, the target holds the
    result) and done; an aborted review with the target still at ``pre_head`` is failed and parks
    ``push_sync_conflict_aborted``; an attempt cut off with no merge commit and the target clean at ``pre_head`` is
    failed (the loop runs a new round); a landed merge commit is done; an open review parks again; anything else
    parks ``sync_unsettled``."""
    row = repo.effect(plan.decision_id, SYNC_PHASE)
    if row is None or row["status"] in (repo.DONE, repo.EFFECT_FAILED):
        return
    evidence = dict(row.get("evidence") or {})
    pre_head, h1 = evidence.get("pre_head"), evidence.get("remote_head")
    state = git.sync_state(slot, evidence.get("merge_id"))
    outcome = state.get("outcome")
    if outcome == git.CONFLICT:
        # A claim cut off between the conflict hand-off and its ``pending`` record left the row ``unknown``: the open
        # review is recorded now as the decision's active conflict (``_conflict_merge_id``) before the job parks, so
        # retry / cancel, the Sweeper and the Stop all wait for that review (0132 T#2 rework).
        if row["status"] != repo.PENDING or not evidence.get("conflict"):
            _sync_effect(ctx, plan, status=repo.PENDING, error_code="merge_conflict",
                         evidence={**evidence, "merge_id": state.get("merge_id", evidence.get("merge_id")),
                                   "status": "conflict", "conflict": True, "changed": False,
                                   "hand_off_recovered": row["status"] == repo.UNKNOWN})
        raise _Failure("merge_conflict", note=_store().GATE_PARKED_CONFLICT, auto=True)
    if outcome == git.MERGED:
        s = state.get("merge_commit")
        reviewed = bool(state.get("reviewed")) or row["status"] == repo.PENDING
        if reviewed:
            ok = bool(s) and git.is_ancestor(slot.base_root, pre_head, s) is True and \
                git.is_ancestor(slot.base_root, h1, s) is True
        else:
            ok = bool(s) and git.parents(slot.base_root, s) == [pre_head, h1]
        if ok and _target_at(slot, plan, is_base, s, or_descendant=True):
            _sync_done(ctx, plan, {**evidence, "conflict": evidence.get("conflict", False)}, pre_head, s,
                       reviewed=reviewed)
            return
        _sync_effect(ctx, plan, status=repo.UNKNOWN, error_code="sync_unsettled",
                     evidence={**evidence, "status": repo.UNKNOWN, "changed": None, "observed_sync_commit": s})
        raise _Recovery("sync_unsettled")
    at_pre_head = bool(pre_head) and _target_at(slot, plan, is_base, pre_head)
    if outcome == git.SYNC_ABORTED and at_pre_head:
        _sync_effect(ctx, plan, status=repo.EFFECT_FAILED, error_code="push_sync_conflict_aborted",
                     evidence={**evidence, "status": "aborted", "changed": False})
        raise _Failure("push_sync_conflict_aborted")
    if outcome == git.SYNC_INTERRUPTED and at_pre_head:
        _sync_effect(ctx, plan, status=repo.EFFECT_FAILED, error_code="sync_interrupted",
                     evidence={**evidence, "status": "interrupted", "changed": False})
        return
    _sync_effect(ctx, plan, status=repo.UNKNOWN, error_code="sync_unsettled",
                 evidence={**evidence, "status": repo.UNKNOWN, "changed": None, "sync_state": outcome})
    raise _Recovery("sync_unsettled")


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
        from flowgate.gate_decisions.run_hold import gate_resume_wake
        gate_resume_wake(run_ctx, decision)
        # An asynchronous publish must not use either generic approval wake/continuation hook to bypass intent.
        run_ctx.gate_resume_deferred = run_ctx.run.get("status") != "running"
        state = legacy.read_run_state(run_ctx)
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
        # All claims, including Sweeper and db_finalize-only retries, share the same command-intent fence.
        from flowgate.gate_decisions.run_hold import gate_resume_wake
        def wake(run, jobs):
            store.ensure_lease(ctx)
            current = repo.get(plan.decision_id)
            if current is None or current.get("job_id") != ctx.job_id or current["status"] not in repo.RESERVING:
                raise store.LeaseLost(ctx.job_id)
            gate_resume_wake(legacy.load_context(run, plan.project_id, current["actor_user_id"], jobs), current)
        _outside_job_context(ctx, lambda: legacy.guarded_run(plan.run_id, wake))
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
    """The merge id of the decision's active conflict (0132 NR §4.3): a remote sync waiting for its review
    (``remote_sync`` pending with ``conflict``) first, else the decision's own merge attempt. Every reader of "is the
    conflict review still open" - the Sweeper's ``_decide``, ``recovery._conflict_open`` (retry / cancel) and the
    Stop's ``_settle_gate_decisions`` (through it) - asks here. A sync row still ``unknown`` names the attempt of a
    round not yet settled (a claim cut off after its conflict hand-off, before the ``pending`` record): that attempt
    is the newest of the decision (the merge is done before any sync), so it is the one read too."""
    decision_id = _payload(job).get("decision_id")
    if not decision_id:
        return None
    sync = repo.effect(decision_id, SYNC_PHASE)
    sync_evidence = (sync or {}).get("evidence") or {}
    if sync is not None and sync_evidence.get("merge_id") is not None and (
            sync["status"] == repo.UNKNOWN or (sync["status"] == repo.PENDING and sync_evidence.get("conflict"))):
        merge_id = sync_evidence.get("merge_id")
    else:
        row = repo.effect(decision_id, "merge")
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
