"""The end cleanup of a Flow's group slot and its restore (flowgate.v02.0085 T#3, D0007 §7.1 latter half).

A gate keeps the group's work slot and branch: an intermediate gate never removes them, archive ON included. Only when
the Flow's run is completed (every run target done) the cleanup may look at the slot. It is its own durable job
(``gate_cleanup``, migration 161), made in the transaction that records the completion (``request``, from
``settle``), run by the existing Runner: a lock it waits for leaves it ``blocked`` and a later release wakes it, a
crash or a lost lease is reconciled and resumed at its recorded step. One run of the job:

1. the locks, in the existing order and never inside a Flow guard: the group's G (the group result writers - the
   attempt integration, a gate snapshot, the Time Machine, a new attempt's slot preparation - share it), the W / B of
   every target branch a decision of the group merged into (the targets a basis may be read from; their writers share
   them), then M (refs). They are held from the final check to the last delete;
2. under them, in one short Flow guard, the conditions again: the run still ``idle(completed)``, no decision of the
   group holding a gate (an accepted publish not pinned yet included), no round split pending after an abandoned
   recovery, no Time Machine, no open merge session, no active attempt, execution or integration. Anything else ends
   the job with nothing judged (``skipped``) - the next completion asks again;
3. the judgement, as before: the real HEAD of the group branch and the work tree (uncommitted files keep the slot,
   ``retained``); the keep-branch requests of every decision of every gate and round, OR-ed (one true keeps the slot);
   an abandon's recovery obligation whose ref could not be written keeps the slot until the ref exists (tried again
   here, under M: ``recovery_ref_unpreserved``); otherwise the slot may go only when a basis that stays holds the HEAD
   (the same commit or a descendant, read from the real objects): an archive checkpoint ref at its recorded SHA, a
   recovery ref of an abandoned recovery, or a target branch a decision really merged into, read now. A job pin, an
   older archive that is only an ancestor of the HEAD, a remote tracking ref or an old receipt is no basis
   (``retained_uncovered_head``). The HEAD and the basis are read again right before the delete (a change keeps it);
4. before anything is deleted, the plan - the real end HEAD, the branch, the basis ref and its observed SHA, the
   paths - is stored on the job (a fenced write). Then the steps, each recorded before it runs and each idempotent:
   ``worktree`` (a clean one, never forced), ``branch`` (exactly at the end HEAD, a CAS), ``ledger`` (the slot let go),
   ``record`` (the cleanup record, once per job, with the job ``succeeded`` in one transaction). A step that cannot be
   done ends the job ``failed`` with a ``failed`` record: the slot is never shown as free when it is not.

Every judgement is recorded in ``gate_cleanup_records`` (end HEAD, basis kind / ref / observed SHA, reachability, the
keep request, the result) with its job id. The outcome never touches a gate decision or a run.

``restore_point``: a slot a cleanup let go comes back at the real end HEAD of that cleanup's plan (not an older archive
SHA) when a new run needs it - the branch is created again at that HEAD (create-only) and the existing provisioning
attaches it (``ensure_worktree``'s terminal reopen ``start_point``). A plan is on the job before the first delete, so a
cleanup cut off at any step still restores from it; while such a job is unfinished the slot is busy (the caller waits).
No checkpoint ref is consumed by it.
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Optional

from flowgate.gate_decisions import git_adapter as git
from flowgate.gate_decisions import repository as repo

_log = logging.getLogger(__name__)

KIND = "gate_cleanup"
STEPS = ("worktree", "branch", "ledger", "record")
PHASES = ("start", *STEPS)
HOLDER = "worktree_cleanup"
DELETED, RETAINED, UNCOVERED, FAILED = "deleted", "retained", "retained_uncovered_head", "failed"
KIND_ARCHIVE, KIND_RECOVERY, KIND_MERGED = "archive_checkpoint", "recovery_ref", "merged_target"
COUNTED_STATUSES = (repo.ACCEPTED, repo.FAILED, repo.RECOVERY_REQUIRED, repo.SUCCEEDED, repo.ABANDONED)
BUSY = "git_busy"                                 # the transient refusal a slot preparation waits on


def _locks():
    from modules.flow_gate.services.git import lock_manager
    return lock_manager


def _store():
    from modules.flow_gate.services.git import job_store
    return job_store


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def _json(value) -> dict:
    from modules.flow_gate.services.git.approval_freeze import _json as decode
    return decode(value)


_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}


def keep_requests(group_id: str) -> list[str]:
    """The decisions whose frozen settings ask to keep the group branch (D0007 §7.1)."""
    out = []
    for row in repo.decisions_of_group(group_id):
        if row["decision"] != repo.CONFIRM or row["status"] not in COUNTED_STATUSES:
            continue
        eff = (row.get("settings") or {}).get("effective") or {}
        if eff.get("merge_mode") in ("merge", "commit") and eff.get("keep_branch"):
            out.append(row["decision_id"])
    return out


def blocker(flow_id: str, run_id: Optional[str]) -> Optional[str]:
    """Why the end cleanup may not run now (None: it may). Read inside the cleanup's Flow guard, under its locks."""
    from flowgate.graph_run import execution_store, result_merge
    from flowgate.graph_time_machine.operation import busy
    from modules.flow_gate.graph_workflow.store import run_store
    run = run_store.run_by_id(run_id) if run_id else None
    if run is None or run["status"] != "idle" or run.get("idle_reason") != "completed":
        return "run_not_completed"
    decisions = repo.decisions_of_group(flow_id)
    if not decisions:
        return "no_gate_decision"
    if repo.reservations_of_group(flow_id):
        return "gate_job_unfinished"
    if any(row["recovery_reset_required"] for row in decisions):
        return "recovery_reset_required"
    if busy(flow_id):
        return "time_machine"
    if run_store.has_active_attempts(run_id) or execution_store.working(run_id) or result_merge.in_progress(run_id):
        return "work_unfinished"
    if _gs().db_git.get_open_session_by_group(flow_id) is not None:
        return "merge_session_open"
    return None


# ── the job ──────────────────────────────────────────────────────────────────

def request(ctx) -> Optional[dict]:
    """``settle``: the run just became ``idle(completed)``, inside its Flow guard. A Flow with gate decisions gets its
    ``gate_cleanup`` job in the same transaction (one unfinished job per group); its wake-up follows the commit."""
    from modules.flow_gate.db.connection import get_store
    flow_id, run_id = ctx.flow_id, ctx.run_id
    if not repo.available() or not get_store().table_exists("operation_job"):
        return None
    if not repo.decisions_of_group(flow_id):
        return None
    store = _store()
    from modules.flow_gate.db import operation_job as db_jobs
    if db_jobs.active_jobs_of_group(flow_id, KIND):
        return None
    project_id = getattr(ctx, "project_id", None)
    if not project_id:
        from modules.flow_gate.db import groups as db_groups
        project_id = (db_groups.get_by_id(flow_id) or {}).get("project_id")
    if not project_id:
        return None
    install()
    job = store.insert_gate_cleanup(project_id, {"group_id": flow_id, "run_id": run_id,
                                                 "cleanup_nonce": secrets.token_hex(8)},
                                    group_id=flow_id, payload={"group_id": flow_id, "run_id": run_id})
    ctx.jobs.append(lambda: store.notify_created(job))
    return job


class _Blocked(Exception):
    def __init__(self, result):
        super().__init__(getattr(result, "code", None))
        self.result = result


def _targets(group_id: str) -> list[str]:
    """The target branches a decision of the group merged into: where a merged-target basis is read."""
    out = set()
    for row in repo.decisions_of_group(group_id):
        eff = (row.get("settings") or {}).get("effective") or {}
        merged = repo.effect(row["decision_id"], "merge")
        if row["status"] == repo.SUCCEEDED and eff.get("merge_mode") == "merge" and row.get("target_branch") \
                and (merged or {}).get("status") == repo.DONE:
            out.add(row["target_branch"])
    return sorted(out)


def _acquire_all(ctx, project_id: str, group_id: str, targets: list, base_branch: str, held: list) -> None:
    """G, then the W of every non-base target (in lock key order), then B for the base target, then M - the existing
    order (L 2.4). A busy lock ends this claim ``blocked`` (its release wakes the job)."""
    store, locks = _store(), _locks()
    wanted = [("G", {"group_id": group_id})]
    others = [t for t in targets if t != base_branch]
    wanted += [("W", {"target_key": t}) for t in sorted(others, key=lambda t: locks.lock_key("W", project_id,
                                                                                          target_key=t))]
    if base_branch in targets:
        wanted.append(("B", {}))
    wanted.append(("M", {}))
    for domain, extra in wanted:
        store.ensure_lease(ctx)
        o = locks.acquire(domain, project_id, holder_kind=HOLDER, mode="job", hold_class="long", ctx=ctx.lock_ctx,
                          **extra)
        if not o.ok:
            b = o.blocker or {}
            if o.kind in (locks.BUSY, locks.GROUP_FROZEN):
                raise _Blocked(store.RunResult(store.BLOCKED, code=o.kind, blocked_domain=domain,
                                               blocked_lock_key=o.lock_key,
                                               blocked_holder=b.get("holder_ctx_id") or b.get("job_id"),
                                               blocked_operation=b.get("holder_kind") or b.get("kind")))
            raise _Blocked(store.RunResult(store.TRANSIENT, code=f"lock_{domain}_{o.kind}"[:64]))
        held.append(o.lock_key)


def _release(ctx, held: list) -> None:
    locks = _locks()
    for key in reversed(held):
        if ctx.lock_ctx.find_held(key) is not None:
            locks.release(ctx.lock_ctx, key)
    held.clear()


def _fenced(ctx, write=None, **changes):
    """A write of this job (``changes`` on its row, ``write()`` beside it) in one transaction under its lease."""
    from modules.flow_gate.db.connection import get_store
    store = _store()
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, changes, update_local=False)
        out = write() if write is not None else None
    ctx.job.update(written)
    return out


def _end(ctx, record: Optional[dict], *, failed: Optional[str] = None, skipped: Optional[str] = None) -> Optional[dict]:
    """The job's end: its record (once per job) and the job ``succeeded`` (``failed`` when a step failed)."""
    from modules.flow_gate.db.connection import now_iso

    def write():
        if record is None:
            return None
        return repo.cleanup_of_job(ctx.job_id) or repo.insert_cleanup({**record, "job_id": ctx.job_id})

    result = {"skipped": skipped} if skipped else {"result": (record or {}).get("result"),
                                                   "reason": (record or {}).get("result_reason")}
    status = dict(status="failed", last_error_code=failed, retryable=0) if failed else dict(status="succeeded")
    stored = _fenced(ctx, write, result=result, finished_at=now_iso(), **status, **_LEASE_CLEARED)
    if skipped:
        _log.info("gate end cleanup of %s skipped: %s", ctx.job.get("group_id"), skipped)
    return stored


def _conditions(flow_id: str, run_id: Optional[str]) -> Optional[str]:
    """The conditions again, in one short Flow guard under the cleanup's Git locks."""
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    return with_flow_guard(flow_id, lambda: blocker(flow_id, run_id))


def _bases(slot: git.Slot, head: str) -> tuple[Optional[dict], list[dict]]:
    """The first basis that holds ``head`` and the evidence of every candidate looked at."""
    seen: list[dict] = []
    candidates: list[dict] = []
    for cp in repo.checkpoints_of_group(slot.group_id):
        for ref, sha in ((cp.get("ref_name"), cp.get("source_sha")), (cp.get("applied_ref"), cp.get("applied_sha"))):
            if ref and sha:
                candidates.append({"kind": KIND_ARCHIVE, "ref": ref, "recorded": sha, "archive_id": cp["archive_id"]})
    for row in repo.decisions_of_group(slot.group_id):
        for effect in repo.effects_of(row["decision_id"]):
            if effect.get("recovery_ref") and effect["recovery_ref"].startswith(git.REF_RECOVERY):
                candidates.append({"kind": KIND_RECOVERY, "ref": effect["recovery_ref"], "recorded": None,
                                   "decision_id": row["decision_id"]})
        eff = (row.get("settings") or {}).get("effective") or {}
        merged = repo.effect(row["decision_id"], "merge")
        if row["status"] == repo.SUCCEEDED and eff.get("merge_mode") == "merge" and row.get("target_branch") \
                and (merged or {}).get("status") == repo.DONE:
            candidates.append({"kind": KIND_MERGED, "ref": f"refs/heads/{row['target_branch']}", "recorded": None,
                               "decision_id": row["decision_id"]})
    found = None
    for c in candidates:
        observed = git.rev(slot.base_root, c["ref"])
        entry = {**c, "observed": None if observed is git.READ_ERROR else observed}
        if observed in (None, git.READ_ERROR) or (c["recorded"] and observed != c["recorded"]):
            entry["reachable"] = None if observed is git.READ_ERROR else False
        else:
            entry["reachable"] = git.covers(slot.base_root, observed, head)
        seen.append(entry)
        if found is None and entry["reachable"] is True:
            found = entry
    return found, seen


def _obligations_unmet(ctx, slot: git.Slot) -> list[dict]:
    """An abandon's recovery refs it could not write: written now (create-only, M is held). What still cannot be
    written stays an obligation and keeps the slot."""
    unmet = []
    for item in repo.retention_obligations(slot.group_id):
        made = git.create_ref(slot.base_root, item["ref"], item["sha"]) if item.get("ref") else "error"
        if made not in ("created", "same"):
            unmet.append(item)
            continue
        row = repo.effect(item["decision_id"], item["phase"]) or {}
        evidence = dict(row.get("evidence") or {})
        evidence[repo.RETENTION_KEY] = {**(evidence.get(repo.RETENTION_KEY) or {}), "kept": True,
                                        "kept_by": "gate_cleanup"}
        _fenced(ctx, lambda: repo.record_effect(item["decision_id"], item["phase"], recovery_ref=item["ref"],
                                                evidence=evidence))
    return unmet


def _judge(ctx, slot: git.Slot, run_id: Optional[str], targets: list, base_branch: str) -> tuple[str, dict]:
    """``("record", record)`` for a slot that stays (or cannot be read), ``("plan", plan)`` for one that may go."""
    branch_ref = f"refs/heads/{slot.group_branch}"
    head = git.rev(slot.base_root, branch_ref)
    base = {"group_id": slot.group_id, "run_id": run_id, "branch_name": slot.group_branch}
    if head in (None, git.READ_ERROR):
        return "record", {**base, "end_head_sha": "unknown", "result": UNCOVERED, "result_reason": "head_unreadable"}
    base["end_head_sha"] = head
    keep = keep_requests(slot.group_id)
    dirty = git.uncommitted(slot.wt_path)
    evidence: dict = {"keep_decisions": keep, "uncommitted": dirty}
    if dirty is None or dirty > 0:
        return "record", {**base, "keep_requested": bool(keep), "result": RETAINED,
                          "result_reason": "uncommitted_files" if dirty else "worktree_unreadable",
                          "evidence": evidence}
    if keep:
        return "record", {**base, "keep_requested": True, "result": RETAINED,
                          "result_reason": "keep_branch_requested", "evidence": evidence}
    unmet = _obligations_unmet(ctx, slot)
    if unmet:
        evidence["unpreserved"] = unmet
        return "record", {**base, "result": RETAINED, "result_reason": "recovery_ref_unpreserved",
                          "evidence": evidence}
    found, seen = _bases(slot, head)
    evidence["bases"] = seen
    if found is None:
        return "record", {**base, "reachable": False if seen else None, "result": UNCOVERED,
                          "result_reason": "no_basis_holds_head", "evidence": evidence}
    record = {**base, "basis_kind": found["kind"], "basis_ref": found["ref"], "basis_sha": found["observed"],
              "reachable": True, "evidence": evidence}
    # Right before the delete: the HEAD and the basis as they are now (the locks hold them from here on).
    if git.rev(slot.base_root, branch_ref) != head or git.rev(slot.base_root, found["ref"]) != found["observed"]:
        return "record", {**record, "result": UNCOVERED, "result_reason": "moved_before_delete"}
    plan = {"project_id": slot.project_id, "group_id": slot.group_id, "run_id": run_id, "branch": slot.group_branch,
            "branch_ref": branch_ref, "end_head": head, "base_root": str(slot.base_root), "wt_path": str(slot.wt_path),
            "targets": targets, "base_branch": base_branch, "archive_id": found.get("archive_id"),
            "record": record}
    return "plan", plan


def _step_worktree(plan: dict) -> Optional[str]:
    gs = _gs()
    base_root, wt = Path(plan["base_root"]), Path(plan["wt_path"])
    if wt.exists():
        removed = gs._run_git(["worktree", "remove", str(wt)], cwd=base_root)
        if removed.returncode != 0 or wt.exists():
            return "worktree_remove_failed"
    gs._run_git(["worktree", "prune"], cwd=base_root)
    return None


def _step_branch(plan: dict) -> Optional[str]:
    base_root = Path(plan["base_root"])
    current = git.rev(base_root, plan["branch_ref"])
    if current is git.READ_ERROR:
        return "branch_unreadable"
    if current is None:
        return None                                      # deleted by this job before it was cut off
    if current != plan["end_head"]:
        return "branch_moved"
    return None if git.delete_ref(base_root, plan["branch_ref"], plan["end_head"]) else "branch_delete_failed"


def _step_ledger(ctx, plan: dict) -> Optional[str]:
    _fenced(ctx, lambda: _gs().db_git.unregister_worktree(plan["group_id"]))
    return None


def _delete(ctx, plan: dict):
    phase = ctx.job.get("phase")
    start = STEPS.index(phase) if phase in STEPS else 0
    record = {**plan["record"], "result": DELETED, "result_reason": "head_covered"}
    for step in STEPS[start:]:
        if step != phase:
            _fenced(ctx, phase=step)
            phase = step
        if step == "worktree":
            failed = _step_worktree(plan)
        elif step == "branch":
            failed = _step_branch(plan)
        elif step == "ledger":
            failed = _step_ledger(ctx, plan)
        else:
            _end(ctx, record)
            return None
        if failed == "branch_unreadable":
            store = _store()
            return store.RunResult(store.TRANSIENT, code=failed)
        if failed:
            _end(ctx, {**plan["record"], "result": FAILED, "result_reason": failed}, failed=failed)
            return None
    return None


def run(ctx, entry: str):
    """Runner executor of ``gate_cleanup``. None once the outcome is written, else the claim's result."""
    store = _store()
    if entry != store.PUBLISH:
        raise store.JobProgramError("gate_cleanup_entry_not_publish", job_id=ctx.job_id, entry=entry)
    payload = _json(ctx.job.get("payload"))
    flow_id, run_id = payload.get("group_id") or ctx.job.get("group_id"), payload.get("run_id")
    plan = _json(ctx.job.get("evidence")).get("plan")
    held: list = []
    try:
        if plan is None:
            try:
                slot = git.slot_of(flow_id)
            except git.git_error_type():
                _end(ctx, None, skipped="no_git_slot")
                return None
            base_branch = (slot.cfg.get("base_branch") or "main").strip() or "main"
            targets = _targets(flow_id)
            _acquire_all(ctx, slot.project_id, flow_id, targets, base_branch, held)
            reason = _conditions(flow_id, run_id)
            if reason is not None:
                _end(ctx, None, skipped=reason)
                return None
            kind, value = _judge(ctx, slot, run_id, targets, base_branch)
            if kind == "record":
                _end(ctx, value)
                return None
            plan = value
            evidence = {**_json(ctx.job.get("evidence")), "plan": plan}
            # The plan is stored before anything is deleted: a cleanup cut off at any step restores from it.
            _fenced(ctx, phase=STEPS[0], evidence=store._canonical_json(evidence))
        else:
            _acquire_all(ctx, plan["project_id"], flow_id, plan.get("targets") or [], plan.get("base_branch") or "main",
                         held)
        return _delete(ctx, plan)
    except _Blocked as blocked:
        return blocked.result
    finally:
        _release(ctx, held)


def _decide(job: dict):
    """L 4.4: an interrupted cleanup resumes at its recorded step (every step reads what is there first)."""
    from modules.flow_gate.services.git import job_runner
    return job_runner.Decision(job_runner.NOT_APPLIED)


def install() -> None:
    """Executor and deciders of ``gate_cleanup``. Idempotent."""
    from modules.flow_gate.services.git import job_runner
    job_runner.register_executor(KIND, run)
    for phase in PHASES:
        job_runner.register_decider(KIND, phase, _decide)


# ── the slot restore ─────────────────────────────────────────────────────────

def release_source(group_id: str) -> Optional[dict]:
    """The latest cleanup that let (or is letting) the slot go: ``{end_head, branch, archive_id, in_flight}`` from the
    plan of the newest ``gate_cleanup`` job that has one, else from the newest ``deleted`` record (a record without a
    job). None when no gate cleanup released this slot."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    if get_store().table_exists("operation_job"):
        rows = db_jobs.jobs_of_group(group_id, KIND, db_jobs.WAITING_STATUSES + db_jobs.EXECUTING_STATUSES
                                     + db_jobs.TERMINAL_STATUSES + ("recovery_required",))
        for job in reversed(rows):
            plan = _json(job.get("evidence")).get("plan")
            if plan:
                return {"end_head": plan["end_head"], "branch": plan["branch"], "archive_id": plan.get("archive_id"),
                        "in_flight": job["status"] not in db_jobs.TERMINAL_STATUSES}
    records = [r for r in repo.cleanups_of_group(group_id) if not r.get("job_id")]
    if records and records[-1]["result"] == DELETED:
        last = records[-1]
        archive_id = None
        if last.get("basis_kind") == KIND_ARCHIVE:
            for entry in (last.get("evidence") or {}).get("bases") or []:
                if entry.get("ref") == last.get("basis_ref") and entry.get("archive_id"):
                    archive_id = entry["archive_id"]
        return {"end_head": last["end_head_sha"], "branch": last.get("branch_name"), "archive_id": archive_id,
                "in_flight": False}
    return None


def restore_point(project_id: str, group_id: str) -> tuple[Optional[str], Optional[str]]:
    """``(start point, error)`` for a slot a gate end cleanup let go: the recorded real end HEAD, with the branch
    created again there (create-only, under M). ``(None, None)`` when no gate cleanup released this slot or the slot is
    there (the provisioning goes as before); ``(None, "git_busy")`` while that cleanup is still running (the caller
    waits); an error code when the slot cannot come back at that HEAD (the run must not start)."""
    gs = _gs()
    source = release_source(group_id)
    if source is None:
        return None, None
    if source["in_flight"]:
        return None, BUSY
    state = gs.db_git.get_state(group_id) or {}
    branch = source.get("branch") or state.get("branch")
    base_root = gs._base_root_of(project_id)
    if not branch or base_root is None:
        return None, "gate_restore_head_missing"
    current = git.rev(Path(base_root), f"refs/heads/{branch}")
    if state.get("worktree_registered") and current not in (None, git.READ_ERROR):
        return None, None                                 # the slot is there (restored already, or never let go)
    sha = source["end_head"]
    if not git.commit_exists(Path(base_root), sha):
        return None, "gate_restore_head_missing"
    locks = _locks()
    ctx = locks.new_context("gate_restore")
    o = locks.acquire("M", project_id, holder_kind="worktree_provision", mode="interactive", ctx=ctx)
    if not o.ok:
        return None, BUSY
    try:
        made = git.create_ref(Path(base_root), f"refs/heads/{branch}", sha)
    finally:
        locks.release(ctx, o.lock_key)
    if made not in ("created", "same"):
        return None, "gate_restore_branch_conflict"
    if source.get("archive_id"):
        repo.mark_restored(source["archive_id"])                  # noted, never consumed
    return sha, None


def slot_released(group_id: str) -> bool:
    """A gate cleanup let this group's slot go (or is letting it go): a preparation that finds no slot waits for the
    restore instead of failing."""
    try:
        return release_source(group_id) is not None
    except Exception:  # noqa: BLE001
        return False
