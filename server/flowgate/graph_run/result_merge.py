"""0061 T#9: an approved code node is done only once its attempt's result is merged into the Flow's branch.

A card's approval (``review_approved``, the card approved as a whole, 0056) of a node whose active attempt worked in
a Git worktree (T#5) is held back (``defer_done``, inside the approval's Flow guard): the node stays in review, its
attempt's ``integration_status`` becomes ``pending`` and the integration is requested after the commit (the step's
jobs). A gate, a node with no attempt, an attempt never prepared and a document-only attempt are done at once.

Every integration of a Flow runs in that Flow's one drain (``retry``: in attempt order, one at a time), outside every
Flow guard and DB transaction (``integrate``):

(a) only while the run is running / paused / idle and the attempt active and ``pending`` — a stopping run starts
    none. An idle run (a person's approval on it) goes running first (``_wake``: the integration hold ``idle``), so
    the run is working, locked and may be stopped while its integration waits or runs. 0083 WP-3 rework (0061 T#2
    개정): a person's approval whose propagation an idle run admits at once (``command.approval_wakes``) wakes the run
    in its own guard (``run_apply.apply_decision``, cause ``work_resumed``) before this, so no idle hold is taken and
    (e) opens the next cards with no further resume; the idle hold is left to the integrations no approval woke for
    (a step's, the next command's drain, the server start);
(b) the attempt's result commit (T#8 ``result_commit.commit_result``, reused when it exists);
(c) a result equal to its input (no source change) already in the group branch goes to (e) with no merge (T#10: a
    Join's merged input is not, and is merged as any result so the branch keeps it);
(d) ``integrating``, then the group's G (``commit._acquire_group_lock``, no wait) for one merge only: the result is
    merged into the group worktree with a merge commit (``branches.merge_no_ff``, --no-ff), so the result commit
    stays a parent on the group branch;
(e) once G is released, in one Flow guard: ``integrated_commit``, ``integrated``, the held approval applied (the
    node done, its attempt ended, the candidates recomputed and the free slots refilled — T#2 / T#6); then, outside
    the guard, the ended attempt's worktree and branch are removed (T#10 ``workspace.cleanup_ended``).

The integration hold (a run status event of the cause ``integration_hold``; it ends with ``integration_hold_end`` or
with the run leaving running): while a command's or an idle run's waiting integrations go first, a running run opens,
refills and dispatches nothing (``settle.settle_plan``) and a result's propagation waits as on a paused run
(``propagation_held``). The hold of an idle run ends once none of its integrations is pending / integrating: the run
goes idle again and the done's propagation waits for the next command (T#2). The hold of a step / full / resume
command (``hold_command``) ends once the drain has asked every waiting integration in attempt order: the command then
re-dispatches, drains what waits and opens its candidates from the state of that moment (``run_service.go_on_command``).

A conflict keeps the merge in flight as the group's conflict session (``conflict.open_tr_conflict_session``, kind
``graph_attempt_merge``, the group ``conflict``): the attempt is ``conflict``, its node stays in review and nothing
below it opens; other branches go on. One open session per group (DB0007 I3): another session open, or G busy, leaves
the integration ``pending`` (``session_busy`` / ``git_busy``) — no error.

Every session close of a group (a commit or abort on the screen, the TTL sweep, an orphan close, a finalize / branch
merge / group update session, a Stop) reaches ``session_closed`` through the one close notice of the session table
(``db_git.on_session_closed``), once the closing thread holds no Git lock and no transaction: the attempt of a
``graph_attempt_merge`` session goes on — its result in the group branch -> (e) with no second merge, else ``failed``
(``conflict_aborted``) — even when the session closed before its ``conflict`` was recorded; then the waiting
integrations are asked again (the aborted attempt not in that drain).

Git lock releases also retry pending integrations and deferred Stops, after the releasing thread's last lock and
current drain / Stop have ended. W/M releases cover their project; G releases cover their group. Failed integrations
are not explicitly requested by this trigger. Self-generated releases are coalesced to avoid recursive retry loops.

Retries (no scheduler, no thread): when a session closes, when another attempt's integration ends (the drain's next
pass), and on a step / full / resume command (before it opens anything), the ``pending`` / ``failed`` integrations
are asked again in attempt order. ``pending`` / ``integrating`` keep a running / paused run working; only
``conflict`` / ``failed`` left and nothing else working, the run is idle (``merge_conflict``). An integration takes
no execution slot.

Stop (T#7): no integration starts once the run is stopping; one ``integrating`` goes on to its end (done stays done)
and the Stop's idle (stopped) follows it. The run's ``graph_attempt_merge`` sessions are aborted first (the abort
under G, ``conflict.abort_tr_conflict``); while one stays open the Stop does nothing more and stays stopping (a
repeated Stop and the server start retry the abort). ``pending`` / ``failed`` attempts are invalidated and their nodes
pending as any other. Server start: an ``integrating`` attempt whose result is in the group branch is finished, any
other goes back to ``pending``.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import documents, groups as db_groups, git_integration as db_git, tr_commit_ledger
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.services import git_service as git, graph_source_hooks as port, tr_commit_service
from modules.flow_gate.services.git import branches, commit as git_commit, conflict, lock_manager
from modules.flow_gate.services.git.credentials import GitServiceError
from flowgate.graph_run import fencing, result_commit, workspace

log = logging.getLogger(__name__)

NONE, PENDING, INTEGRATING = "none", "pending", "integrating"
INTEGRATED, CONFLICT, FAILED = "integrated", "conflict", "failed"
WAITING = (PENDING, INTEGRATING, CONFLICT, FAILED)       # approved, not done yet
IN_PROGRESS = (PENDING, INTEGRATING)                       # the run is working
HELD = (CONFLICT, FAILED)                                  # waits for a person / the next command
SESSION_KIND = db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE
HOLDER = "graph_attempt_merge"
SESSION_REASON = "merge_session:"
# graph_run_events admits only the run / node status events (migration 144): the record is a run status event with
# the status unchanged, told by its cause (as ``workspace.CAUSE``).
CAUSE = "attempt_integration"
SKIPPED = "skipped"
RUNNING, IDLE, STOPPING = "running", "idle", "stopping"
# The integration hold: a run status event (running -> running, or idle -> running when an idle run wakes) whose
# reason is the hold's kind — ``idle`` or the command that waits (``resume`` / ``command:<mode>``).
HOLD_CAUSE, HOLD_END_CAUSE = "integration_hold", "integration_hold_end"
HOLD_FAILED_CAUSE = "integration_hold_failed"            # a hold released by ``_release_hold``
HOLD_IDLE, HOLD_RESUME, HOLD_COMMAND = "idle", "resume", "command:"


def _status(row: Optional[dict]) -> str:
    return str((row or {}).get("integration_status") or NONE)


def needs_integration(row: dict) -> bool:
    """The attempt worked in its own Git worktree (T#5): its approval waits for the merge. Document-only and
    never-prepared attempts have nothing to merge."""
    record = workspace._mode(row)
    return bool(record and record.get("mode") == "worktree" and row.get("input_commit"))


# ── reads (inside a guard or not) ─────────────────────────────────────────────

def _active(run_id: str, node_key: str) -> Optional[dict]:
    return run_store._one("SELECT attempt_id, integration_status, integration_reason FROM graph_node_attempts "
                          "WHERE run_id = ? AND node_key = ? AND status = 'active'", [run_id, node_key])


def integration_waiting(run_id: str, node_key: str) -> bool:
    """The node's card is approved and its merge is not done: the node stays in review."""
    return _status(_active(run_id, node_key)) in WAITING


def _any(run_id: str, statuses) -> bool:
    marks = ", ".join("?" for _ in statuses)
    return bool(run_store._one(
        f"SELECT 1 AS present FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
        f"AND integration_status IN ({marks}) LIMIT 1", [run_id, *statuses]))


def in_progress(run_id: str) -> bool:
    return _any(run_id, IN_PROGRESS)


def held(run_id: str) -> bool:
    return _any(run_id, HELD)


def waiting(run_id: str) -> bool:
    return _any(run_id, WAITING)


def integration_wire(attempt_id: Optional[str]) -> dict:
    row = run_store._one("SELECT integration_status, integration_reason FROM graph_node_attempts "
                         "WHERE attempt_id = ?", [attempt_id]) if attempt_id else None
    return {"integration_status": (row or {}).get("integration_status"),
            "integration_reason": (row or {}).get("integration_reason")}


def hold_of(run_id: str) -> Optional[str]:
    """The run's integration hold (its kind), or None: the last hold event, unless the run left running after it."""
    from modules.flow_gate.graph_workflow.services import run_events
    row = run_store._one(
        "SELECT cause, reason FROM graph_run_events WHERE run_id = ? AND event_type = ? AND (cause IN (?, ?) "
        "OR (to_status <> 'running' AND COALESCE(from_status, '') <> to_status)) ORDER BY event_seq DESC LIMIT 1",
        [run_id, run_events.RUN_STATUS, HOLD_CAUSE, HOLD_END_CAUSE])
    if not row or row.get("cause") != HOLD_CAUSE:
        return None
    return str(row.get("reason") or HOLD_IDLE)


def holding(run_id: str) -> bool:
    """A running run whose waiting integrations go first: it opens, refills and dispatches nothing."""
    run = run_store.run_by_id(run_id)
    return bool(run) and run["status"] == RUNNING and hold_of(run_id) is not None


def propagation_held(run_id: str) -> bool:
    """``run_apply.read_run_state``: while a run holds for its integrations, a result's propagation waits."""
    return holding(run_id)


def _node(row: dict) -> Optional[dict]:
    return next((n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == row["node_key"]), None)


def _merge_id(row: dict) -> Optional[int]:
    reason = str(row.get("integration_reason") or "")
    if not reason.startswith(SESSION_REASON):
        return None
    try:
        return int(reason[len(SESSION_REASON):])
    except ValueError:
        return None


def _session_open(merge_id: Optional[int]) -> bool:
    session = db_git.get_session(merge_id) if merge_id else None
    return session is not None and session.get("status") == "open"


def session_pending(run_id: str) -> list[str]:
    """The run's ``conflict`` attempts whose merge session is still open (a Stop waits for their abort)."""
    return [r["attempt_id"] for r in run_store._all(
        "SELECT attempt_id, integration_reason FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
        "AND integration_status = 'conflict' ORDER BY created_at, attempt_id", [run_id])
        if _session_open(_merge_id(r))]


def _project_of(flow_id: str) -> Optional[str]:
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


# ── writes ────────────────────────────────────────────────────────────────────

def _note(row: dict, status: str, reason: Optional[str], **extra) -> None:
    """Inside a guard, before any run context of that guard is loaded: the run event of the change."""
    from modules.flow_gate.graph_workflow.services import run_events
    run = run_store.run_by_id(row["run_id"])
    seq = int(run["last_event_seq"]) + 1
    payload = {"kind": CAUSE, "flow_id": run["group_id"], "run_id": run["run_id"],
               "revision_no": run.get("revision_no"), "event_seq": seq, "from": run["status"], "to": run["status"],
               "cause": CAUSE, "reason": status, "attempt_id": row["attempt_id"], "node_key": row["node_key"],
               "integration_status": status, "integration_reason": reason, **extra}
    run_store.append_event(row["run_id"], seq, run_events.RUN_STATUS, payload, node_key=None,
                           from_status=run["status"], to_status=run["status"], cause=CAUSE, result_doc_id=None,
                           canvas_state=None, actor_user_id=None, reason=status)
    run_events.queue(_project_of(run["group_id"]), run["group_id"], run_events.RUN_STATUS, payload)


def _set(attempt_id: str, status: str, reason: Optional[str], expect) -> bool:
    """One guarded conditional move of an active attempt's integration from one of ``expect``."""
    row = workspace.attempt(attempt_id)
    if not row:
        return False

    def body() -> bool:
        current = workspace.attempt(attempt_id)
        before = _status(current)
        if not current or current["status"] != "active" or before not in expect:
            return False
        if run_store._affected(
                "UPDATE graph_node_attempts SET integration_status = ?, integration_reason = ? "
                "WHERE attempt_id = ? AND status = 'active' AND COALESCE(integration_status, 'none') = ?",
                [status, reason, attempt_id, before]) != 1:
            raise AssertionError("attempt integration changed while recording")
        _note(current, status, reason)
        return True
    return with_flow_guard(row["group_id"], body)


def defer_done(ctx, d) -> bool:
    """``run_apply.apply_decision``, inside the step's guard: an approval that may not make its node done yet holds
    the whole decision back (its propagation and openings follow the node's done). A first approval asks the Flow's
    drain after the commit; one already waiting just waits."""
    keys = [t.node for t in d.node_transitions if t.cause == "review_approved" and t.to_status == "done"]
    deferred = requested = False
    for key in keys:
        attempt_id = run_store.active_attempt_id(ctx.run_id, key)
        row = workspace.attempt(attempt_id) if attempt_id else None
        if not row or _status(row) == INTEGRATED or not needs_integration(row):
            continue
        deferred = True
        if _status(row) == NONE and run_store._affected(
                "UPDATE graph_node_attempts SET integration_status = 'pending', integration_reason = 'approved' "
                "WHERE attempt_id = ? AND status = 'active' AND COALESCE(integration_status, 'none') = 'none'",
                [attempt_id]) == 1:
            requested = True
    if requested:
        ctx.jobs.append(lambda flow_id=ctx.flow_id: request(flow_id))
    return deferred


def retry_integrations(ctx) -> bool:
    """``run_service._go_on``, inside a command's guard after its reconcile and before anything opens: the Flow's
    drain (pending and failed integrations) is asked after the commit. True when an integration waits (pending /
    integrating / failed): the command then holds (``hold_command``) until the drain has asked each of them. A failed
    one becomes pending only when the drain takes it (``_requeue``)."""
    if not _any(ctx.run_id, (PENDING, INTEGRATING, FAILED)):
        return False
    ctx.jobs.insert(0, lambda flow_id=ctx.flow_id: request(flow_id, failed=True))
    return True


def hold_command(ctx, command: Optional[str]) -> None:
    """``run_service._go_on``, in the command's guard once the run is running: the command waits for the drain
    (its re-dispatch, drain and openings follow in ``_end_hold``)."""
    from modules.flow_gate.graph_workflow.services import run_apply
    kind = HOLD_RESUME if command is None else HOLD_COMMAND + str(command)
    run_apply.emit_run_status(ctx, RUNNING, RUNNING, HOLD_CAUSE, kind)


# ── the integration ───────────────────────────────────────────────────────────

def _time_machine_busy(flow_id: str) -> bool:
    """T#13: a Flow with an unfinished Graph Time Machine operation (it keeps the run idle; its Git step holds the
    group's G and reverts on the group branch, and its DB step needs the run idle) has no integration woken or started,
    whatever its run's status; a pending integration waits for the next command's drain."""
    from flowgate.graph_time_machine.operation import busy
    return busy(flow_id)


def _runnable(attempt_id: str) -> bool:
    """(a): the run running / paused / idle (never stopping), the attempt active and pending; T#13: no Time Machine
    operation of the Flow unfinished, whatever the run's status."""
    row = workspace.attempt(attempt_id)
    if not row or row["status"] != "active" or _status(row) != PENDING:
        return False
    run = run_store.run_by_id(row["run_id"])
    if not run or fencing._status(run["status"]) not in fencing.OPENING:
        return False
    return not _time_machine_busy(row["group_id"])


def _wake(attempt_id: str) -> None:
    """(a) an idle run's pending integration: the run goes running under the hold ``idle`` before anything of the
    integration happens (working, locked, Stop-able); its propagation keeps waiting for the next command."""
    from modules.flow_gate.graph_workflow.services import run_apply
    row = workspace.attempt(attempt_id)
    if not row:
        return

    def body() -> None:
        current = workspace.attempt(attempt_id)
        if not current or current["status"] != "active" or _status(current) != PENDING:
            return
        run = run_store.run_by_id(current["run_id"])
        if run is None or fencing._status(run["status"]) != IDLE or _time_machine_busy(run["group_id"]):
            return
        ctx = run_apply.load_context(run, _project_of(run["group_id"]), None)
        if run_store.set_run_status(run["run_id"], IDLE, RUNNING) != 1:
            raise AssertionError("integration wake missed its run")
        ctx.run["idle_reason"] = None
        run_apply.emit_run_status(ctx, IDLE, RUNNING, HOLD_CAUSE, HOLD_IDLE)
    with_flow_guard(row["group_id"], body)


def _begin(attempt_id: str, flow_id: str) -> bool:
    """(d) pending -> integrating, in the Flow guard: a Stop's ``stopping`` commits before (none starts) or after
    (this one goes on to its end, and the Stop waits for it)."""
    def body() -> bool:
        if not _runnable(attempt_id):
            return False
        current = workspace.attempt(attempt_id)
        if not current.get("result_commit") or run_store._affected(
                "UPDATE graph_node_attempts SET integration_status = 'integrating', integration_reason = NULL "
                "WHERE attempt_id = ? AND status = 'active' AND integration_status = 'pending'",
                [attempt_id]) != 1:
            return False
        _note(current, INTEGRATING, None)
        return True
    return with_flow_guard(flow_id, body)


def _group_root(row: dict) -> tuple[Optional[str], Optional[Path], Optional[str]]:
    """(project_id, the group worktree, the group branch) or Nones."""
    try:
        project_id = workspace._project(row)
    except port.GraphSourceError:
        return None, None, None
    state = git.db_git.get_state(row["group_id"]) or {}
    name = git._project_name(project_id)
    if not state.get("worktree_registered") or not state.get("branch") or not name:
        return project_id, None, None
    return project_id, git.src_root(name, state["branch"]), state["branch"]


def _in_group(row: dict) -> bool:
    """(c) T#10: the result is already on the group branch (a run base or a predecessor's result). A Join's merged
    input is not: it is merged, else the ended attempt's branch removal would leave it unreferenced."""
    _project_id, root, _branch = _group_root(row)
    return root is not None and git._is_ancestor(root, row["result_commit"], "HEAD")


def _trailer(row: dict) -> str:
    return f"FlowGate-Attempt: {row['attempt_id']}\nFlowGate-Result: {row['result_commit']}"


# The merge session an ``integrating`` attempt opened in this process (its ``conflict`` not recorded yet): a close
# of exactly that session is the attempt's, however early it comes.
_opened: dict[str, int] = {}
_opened_lock = threading.Lock()


def _merge(row: dict) -> tuple[str, object]:
    """(d) under G: ``(INTEGRATED, merge commit)``, ``(CONFLICT, merge_id)``, ``(PENDING, reason)`` or ``(FAILED,
    reason)``. The lock is held for this one merge only."""
    project_id, root, group_branch = _group_root(row)
    if root is None:
        return FAILED, "no_group_worktree"
    flow_id, result = row["group_id"], row["result_commit"]
    lock = git_commit._acquire_group_lock(project_id, flow_id, HOLDER, no_wait=True)
    if lock is None:
        return PENDING, "git_busy"
    try:
        if git.db_git.get_open_session_by_group(flow_id):
            return PENDING, "session_busy"
        if git.get_branch_merge_group_claim(flow_id) is not None:
            return PENDING, "git_busy"
        if not root.is_dir():
            return FAILED, "no_group_worktree"
        if branches.check_git_operation_in_progress(root):
            return PENDING, "session_busy"
        if git._is_ancestor(root, result, "HEAD"):
            return INTEGRATED, _merge_commit_of(root, result)     # already merged (a retried integration)
        if git._dirty(root, include_untracked=False):
            return FAILED, "group_worktree_dirty"
        _directory, _root, attempt_branch = workspace.location(row, project_id)
        subject = f"Merge branch '{attempt_branch}' into {group_branch}"
        try:
            files = branches.merge_no_ff(root, result, subject + "\n\n" + _trailer(row),
                                         attempt_branch, group_branch)
        except GitServiceError as exc:
            return FAILED, exc.code
        if not files:
            head = git._rev_parse(root, "HEAD")
            if not head or not git._is_ancestor(root, result, head):
                return FAILED, "merge_verification_failed"
            return INTEGRATED, head
        doc = documents.get_by_id((_node(row) or {}).get("result_doc_id")) or {}
        ledger = tr_commit_ledger.by_attempt(row["attempt_id"]) or {}
        session = conflict.open_tr_conflict_session(
            {"wt_path": root}, kind=SESSION_KIND, group_id=flow_id, ledger_row_id=int(ledger.get("id") or 0),
            doc_id=doc.get("doc_id") or "", doc_code=tr_commit_service.doc_code(doc) if doc else "",
            target_sha=result, original_sha=None, subject=subject, body=_trailer(row),
            extra_context={"attempt_id": row["attempt_id"], "node_key": row["node_key"], "run_id": row["run_id"],
                           "result_commit": result})
        if session is None:
            proc = git._run_git(["merge", "--abort"], cwd=root)
            if proc.returncode != 0 or git._rev_parse(root, "MERGE_HEAD") is not None:
                return FAILED, "merge_abort_failed"          # the merge state stays: a person must clear it
            return PENDING, "session_busy"
        with _opened_lock:
            _opened[row["attempt_id"]] = int(session["merge_id"])
        return CONFLICT, int(session["merge_id"])
    finally:
        git_commit._release_group_lock(lock)


def _merge_commit_of(root: Path, result: str) -> Optional[str]:
    """The first commit of the group branch that took ``result`` as a parent, else HEAD."""
    proc = git._run_git(["rev-list", "--reverse", "--ancestry-path", "--parents", f"{result}..HEAD"], cwd=root)
    for line in (proc.stdout or "").splitlines() if proc.returncode == 0 else []:
        shas = line.split()
        if result in shas[1:]:
            return shas[0]
    return git._rev_parse(root, "HEAD")


def _finish(attempt_id: str, integrated: Optional[str], expect) -> str:
    """(e) in one Flow guard: integrated, the held approval applied (done, the attempt ended, the run settled). A
    run holding for its integrations keeps the done's propagation waiting and opens nothing."""
    from modules.flow_gate.graph_workflow.services import run_apply, run_service
    row = workspace.attempt(attempt_id)
    if not row:
        return SKIPPED
    jobs: list = []

    def body() -> str:
        current = workspace.attempt(attempt_id)
        before = _status(current)
        if not current or current["status"] != "active" or before not in expect:
            return SKIPPED
        run = run_store.run_by_id(current["run_id"])
        # A merge in progress or kept as a session goes on to its end on a stopping run (done stays done); an
        # integration with no merge yet (pending) finishes only while the run may start one.
        allowed = fencing.OPENING if before == PENDING else (*fencing.OPENING, STOPPING)
        if run is None or fencing._status(run["status"]) not in allowed:
            return SKIPPED
        if run_store._affected(
                "UPDATE graph_node_attempts SET integration_status = 'integrated', integration_reason = NULL, "
                "integrated_commit = ? WHERE attempt_id = ? AND status = 'active' "
                "AND COALESCE(integration_status, 'none') = ?", [integrated, attempt_id, before]) != 1:
            raise AssertionError("attempt integration changed while finishing")
        _note(current, INTEGRATED, None, integrated_commit=integrated)
        ctx = run_apply.load_context(run_store.run_by_id(current["run_id"]), workspace._project(current), None, jobs)
        node = _node(current)
        if node and node["status"] == "review" and node.get("result_doc_id"):
            event = RunEvent("review_approved", current["node_key"], doc_id=node["result_doc_id"])
            d = resolve(ctx.revision, run_apply.read_run_state(ctx), event,
                        policies_of(run_store.policy_entries(ctx.run_id)))
            if not d.ignored:
                run_apply.apply_decision(ctx, d)
                return INTEGRATED
        log.warning("Graph integration found no approval to apply attempt=%s node=%s", attempt_id,
                    current["node_key"])
        run_apply.settle_run(ctx)
        return INTEGRATED
    outcome = with_flow_guard(row["group_id"], body)
    run_service.run_jobs(jobs)
    if outcome == INTEGRATED:
        workspace.cleanup_ended(attempt_id)          # T#10: only once the attempt ended (the approval applied)
    return outcome


def _settle_closed(row: dict, expect) -> str:
    """The attempt's merge session is closed: its result in the group branch -> (e) with no second merge (the
    resolution's commit, or one made outside), else ``failed`` (``conflict_aborted``)."""
    _project_id, root, _branch = _group_root(row)
    result = row.get("result_commit")
    if root is not None and result and git._is_ancestor(root, result, "HEAD"):
        return _finish(row["attempt_id"], _merge_commit_of(root, result), expect)
    return FAILED if _set(row["attempt_id"], FAILED, "conflict_aborted", expect) else SKIPPED


_local = threading.local()


@contextmanager
def defer_lock_retries():
    """Finish the current drain / Stop before consuming its own lock release notices."""
    _local.retry_depth = getattr(_local, "retry_depth", 0) + 1
    try:
        yield
    finally:
        _local.retry_depth -= 1
        _dispatch()


def _quiet() -> bool:
    """A Stop's own abort (``abort_for_stop``): the Stop goes on by itself, the session's end does not call it."""
    return bool(getattr(_local, "quiet", False))


def _after(attempt_id: str) -> None:
    """After an integration's end: a Stop waiting for it goes on; a hold may end; any other run is judged again."""
    from flowgate.graph_run import execution, stop
    row = workspace.attempt(attempt_id)
    run = run_store.run_by_id(row["run_id"]) if row else None
    if run is None:
        return
    if run["status"] == STOPPING:
        if not _quiet():
            stop.advance(run["group_id"], run["run_id"])
        return
    _end_hold(run["group_id"], drained=False)
    execution.settle(run["group_id"])


def integrate(attempt_id: str) -> str:
    """(a)~(e) for one pending integration, inside the Flow's drain. Returns its outcome (``integrated`` /
    ``pending`` / ``conflict`` / ``failed`` / ``skipped``). Outside every Flow guard and DB transaction."""
    _wake(attempt_id)
    if not _runnable(attempt_id):
        return SKIPPED
    row = workspace.attempt(attempt_id)
    node = _node(row)
    if not node or node["status"] != "review" or not node.get("result_doc_id"):
        return SKIPPED
    try:
        outcome = result_commit.commit_result(attempt_id, node["result_doc_id"])             # (b)
    except port.GraphAttemptInactive as exc:
        why = (exc.details or {}).get("reason")
        if why == "document_attempt_mismatch":
            _set(attempt_id, FAILED, "result_commit:" + why, (PENDING,))
            _after(attempt_id)
            return FAILED
        return SKIPPED
    if outcome.get("skipped_reason") == "document_only":
        status = _finish(attempt_id, None, (PENDING,))
    elif not outcome.get("result_commit"):
        reason = str(outcome.get("skipped_reason") or "commit_failed")
        status = PENDING if reason == "git_busy" else FAILED
        _set(attempt_id, status, "result_commit:" + reason, (PENDING,))
    else:
        row = workspace.attempt(attempt_id)
        if row["result_commit"] == row["input_commit"] and _in_group(row):
            status = _finish(attempt_id, row["result_commit"], (PENDING,))                  # (c)
        elif not _begin(attempt_id, row["group_id"]):
            return SKIPPED
        else:
            try:
                status, value = _merge(workspace.attempt(attempt_id))                       # (d)
            except Exception as exc:  # noqa: BLE001 — the merge ends as failed, never stays integrating
                log.exception("Graph attempt merge failed attempt=%s", attempt_id)
                status, value = FAILED, type(exc).__name__
            try:
                if status == INTEGRATED:
                    status = _finish(attempt_id, value, (INTEGRATING,))                     # (e)
                elif status == CONFLICT:
                    _set(attempt_id, CONFLICT, SESSION_REASON + str(value), (INTEGRATING,))
                    # A session closed before its conflict was recorded: its close found the attempt integrating
                    # (``session_closed``); one closed in between is settled here. Never lost, never twice.
                    current = workspace.attempt(attempt_id)
                    if _status(current) == CONFLICT and _merge_id(current) == int(value) and \
                            not _session_open(int(value)):
                        _settle_closed(current, (CONFLICT,))
                else:
                    _set(attempt_id, status, str(value), (INTEGRATING,))
            finally:
                with _opened_lock:
                    _opened.pop(attempt_id, None)
    _after(attempt_id)
    return status


def request(flow_id: str, failed: bool = False) -> None:
    """A post-commit job (an approval's, a command's): the Flow's drain. Never raises (the approval stands)."""
    try:
        retry(flow_id, failed=failed)
    except Exception:  # noqa: BLE001
        log.exception("Graph attempt integration failed flow=%s", flow_id)


# ── the drain and the hold's end ──────────────────────────────────────────────

_drain_lock = threading.Lock()
_draining: set[str] = set()
_again: dict[str, bool] = {}                     # flow -> one more pass asked (failed integrations too)
_skips: dict[str, set] = {}                      # flow -> attempts aborted by a close this drain took


def _waiting(flow_id: str, skip, statuses) -> list[str]:
    run = run_store.active_run(flow_id)
    if run is None or fencing._status(run["status"]) not in fencing.OPENING:
        return []
    marks = ", ".join("?" for _ in statuses)
    return [r["attempt_id"] for r in run_store._all(
        f"SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
        f"AND integration_status IN ({marks}) ORDER BY created_at, attempt_id", [run["run_id"], *statuses])
        if r["attempt_id"] not in skip]


def _closed_conflicts(flow_id: str) -> set[str]:
    """``conflict`` attempts whose session closed with no notice taken (a close before a restart, a lost notice):
    settled as ``session_closed`` would. Returns the aborted ones (not asked again in this drain)."""
    run = run_store.active_run(flow_id)
    aborted: set[str] = set()
    if run is None:
        return aborted
    for r in run_store._all("SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
                            "AND integration_status = 'conflict' ORDER BY created_at, attempt_id", [run["run_id"]]):
        row = workspace.attempt(r["attempt_id"])
        if row and not _session_open(_merge_id(row)) and _settle_closed(row, (CONFLICT,)) == FAILED:
            aborted.add(row["attempt_id"])
    return aborted


def _requeue(attempt_id: str) -> bool:
    return _status(workspace.attempt(attempt_id)) == PENDING or _set(attempt_id, PENDING, "retry", (FAILED,))


@defer_lock_retries()
def retry(flow_id: str, skip=(), *, failed: bool = True) -> None:
    """The Flow's drain: its pending / failed integrations (``failed=False``: pending only), in attempt order, one
    at a time. A retry asked while one runs in this process is taken by it (one more pass); a pass repeats while an
    integration succeeded (another attempt's integration ended). The attempts aborted by a session close this drain
    took (``skip``) are not merged again before the drain ends. When it ends, a hold that waited for it ends
    (``_end_hold``)."""
    with _drain_lock:
        _skips.setdefault(flow_id, set()).update(skip)
        if flow_id in _draining:
            _again[flow_id] = _again.get(flow_id, False) or failed
            return
        _draining.add(flow_id)
    try:
        while True:
            with _drain_lock:
                _again.pop(flow_id, None)
            aborted = _closed_conflicts(flow_id)
            with _drain_lock:
                _skips[flow_id] |= aborted
                skip = set(_skips[flow_id])
            progressed = False
            for attempt_id in _waiting(flow_id, skip, (PENDING, FAILED) if failed else (PENDING,)):
                if _requeue(attempt_id) and integrate(attempt_id) == INTEGRATED:
                    progressed = True
            with _drain_lock:
                more = _again.get(flow_id)
                if not progressed and more is None:
                    _draining.discard(flow_id)
                    _skips.pop(flow_id, None)
                    break
            failed = True if more is None else (failed or more)
    except BaseException as exc:
        with _drain_lock:
            _draining.discard(flow_id)
            _again.pop(flow_id, None)
            _skips.pop(flow_id, None)
        if isinstance(exc, Exception):
            _release_hold(flow_id)             # the drain failed (a Flow lock timeout, ...): no hold stays
        raise
    _end_hold(flow_id, drained=True)


def _end_hold(flow_id: str, *, drained: bool) -> None:
    """A hold ends: an idle run's once none of its integrations is pending / integrating (the run goes idle again,
    the done's propagation waiting for the next command); a command's once the drain has asked every waiting one
    (``drained``: the command goes on from the state of that moment). Never raises."""
    from modules.flow_gate.graph_workflow.services import run_apply, run_service
    from flowgate.graph_run import execution_store, settle
    jobs: list = []

    def body() -> None:
        run = run_store.active_run(flow_id)
        if run is None or run["status"] != RUNNING:
            return
        kind = hold_of(run["run_id"])
        if kind is None or (kind == HOLD_IDLE and in_progress(run["run_id"])) or (kind != HOLD_IDLE and not drained):
            return
        ctx = run_apply.load_context(run, _project_of(flow_id), None, jobs)
        run_apply.emit_run_status(ctx, RUNNING, RUNNING, HOLD_END_CAUSE, kind)
        if kind != HOLD_IDLE:
            command = None if kind == HOLD_RESUME else kind[len(HOLD_COMMAND):] or None
            run_service.go_on_command(ctx, command, policies_of(run_store.policy_entries(ctx.run_id)))
            return
        if execution_store.working(ctx.run_id):
            run_apply.settle_run(ctx)               # an explicit review request took the run meanwhile
            return
        rows = run_store.node_runs(ctx.run_id)
        reason = settle.idle_reason(ctx, rows, run_apply.read_run_state(ctx))
        if run_store.set_run_status(ctx.run_id, RUNNING, IDLE, reason) != 1:
            raise AssertionError("integration hold end missed its run")
        ctx.run["idle_reason"] = reason
        run_apply.emit_run_status(ctx, RUNNING, IDLE,
                                  settle.CAUSE_COMPLETED if reason == settle.IDLE_COMPLETED else settle.CAUSE_IDLE)

    try:
        with_flow_guard(flow_id, body)
        run_service.run_jobs(jobs)
    except Exception:  # noqa: BLE001 — rolled back: at the drain's end the hold is released instead
        log.exception("Graph integration hold could not end flow=%s", flow_id)
        if drained:                            # an ``_after`` inside the drain: the drain's own end follows
            _release_hold(flow_id)


def _release_hold(flow_id: str) -> None:
    """A hold whose end or drain failed (``go_on_command`` raised — ``root_binding_lost``, a dispatch error —, a Flow
    lock timeout, ...) never stays, else the running run opens nothing until a pause or a restart. In its own guard,
    after the failed one rolled back: a command's hold -> paused (open nodes keep their work, the Flow stays locked; a
    resume asks the waiting integrations again and goes on with the same command, refused as any command when it
    cannot), an idle run's hold -> idle (``error``), as its end would have left it. Never raises: failing again, the
    hold stays for the next drain or the server start."""
    from modules.flow_gate.graph_workflow.services import run_apply, run_service
    from flowgate.graph_run import settle
    jobs: list = []

    def body() -> None:
        run = run_store.active_run(flow_id)
        if run is None or run["status"] != RUNNING:
            return
        kind = hold_of(run["run_id"])
        if kind is None:
            return
        ctx = run_apply.load_context(run, _project_of(flow_id), None, jobs)
        to, reason = (IDLE, settle.IDLE_ERROR) if kind == HOLD_IDLE else (run_store.PAUSED, None)
        if run_store.set_run_status(ctx.run_id, RUNNING, to, reason) != 1:
            raise AssertionError("integration hold release missed its run")
        ctx.run["idle_reason"] = reason
        run_apply.emit_run_status(ctx, RUNNING, to, HOLD_FAILED_CAUSE, kind)

    try:
        with_flow_guard(flow_id, body)
        run_service.run_jobs(jobs)
    except Exception:  # noqa: BLE001 — the hold stays; the next drain or the server start ends it
        log.exception("Graph integration hold could not be released flow=%s", flow_id)


# ── the session close notice ──────────────────────────────────────────────────

def _groups_of(session: Optional[dict]) -> list[str]:
    """The Flows a closed session kept busy: its group, or the target group a branch merge claimed."""
    if not session:
        return []
    groups = [session.get("group_id")]
    if db_git.is_branch_merge_session(session):
        from modules.flow_gate.services.git import branch_merge, merge_target
        context = db_git.session_context(session)
        groups += [(context.get(merge_target.TARGET_RECORD_KEY) or {}).get("target_group_id"),
                   (context.get(branch_merge.BRANCH_MERGE_KEY) or {}).get("target_group_id")]
    return [g for g in dict.fromkeys(groups) if g]


def on_session_closed(merge_id: int, _status_: str) -> None:
    """``db_git.close_session``'s notice (every close: a commit, an abort, the TTL sweep, an orphan close, a
    finalize / branch merge / group update end). Taken at once when the closing thread holds no Git lock and no
    transaction, else when that thread releases its last Git lock (a close inside a transaction waits for it too, never
    for the commit: a Flow guard's lock is still held there). A notice never taken is made up by the next drain of the
    Flow (``_closed_conflicts``). Never raises."""
    try:
        groups = _groups_of(db_git.get_session(merge_id))
    except Exception:  # noqa: BLE001
        log.warning("Graph integration could not read closed session %s", merge_id, exc_info=True)
        return
    if not groups:
        return
    queue = _local.__dict__.setdefault("closed", [])
    queue.extend((g, int(merge_id)) for g in groups)
    _dispatch()


def on_lock_released(held) -> None:
    """Reuse the post-release dispatch for pending integrations and deferred Stops."""
    if held.domain in ("G", "W", "M"):
        scopes = _local.__dict__.setdefault("released", set())
        scopes.add((held.project_id, held.group_id if held.domain == "G" else None))
    _dispatch()


def _retry_released(project_id: str, group_id: Optional[str], seen: set[str]) -> None:
    from flowgate.graph_run import stop
    if not run_store.available():
        return
    sql = ("SELECT run_id, group_id, status FROM graph_runs WHERE "
           "(status = 'stopping' OR (status IN ('running', 'paused', 'idle') AND EXISTS "
           "(SELECT 1 FROM graph_node_attempts a WHERE a.run_id = graph_runs.run_id "
           "AND a.status = 'active' AND a.integration_status IN ('pending', 'integrating'))))")
    args = []
    if group_id:
        sql += " AND group_id = ?"
        args.append(group_id)
    for run in run_store._all(sql, args):
        flow_id = run["group_id"]
        if flow_id in seen or _project_of(flow_id) != project_id:
            continue
        seen.add(flow_id)
        try:
            if run["status"] == STOPPING:
                stop.advance(flow_id, run["run_id"])
            else:
                request(flow_id, failed=False)
        except Exception:  # noqa: BLE001 — a release must not fail its original operation
            log.exception("Graph retry after Git lock release failed flow=%s", flow_id)


def _dispatch() -> None:
    if getattr(_local, "dispatching", False) or getattr(_local, "retry_depth", 0) or in_transaction():
        return
    if lock_manager.current_thread_holds_locks():
        return
    queue = _local.__dict__.setdefault("closed", [])
    scopes = _local.__dict__.setdefault("released", set())
    seen: set[str] = set()
    _local.dispatching = True
    try:
        while queue or scopes:
            while queue:
                group_id, merge_id = queue.pop(0)
                try:
                    session_closed(group_id, merge_id)
                except Exception:  # noqa: BLE001
                    log.exception("Graph integration after session %s failed for %s", merge_id, group_id)
            if scopes:
                project_id, group_id = scopes.pop()
                try:
                    # A retry may release G/W/M even if Git fails. Retry each Flow only
                    # once per dispatch so these self-generated notices cannot spin.
                    _retry_released(project_id, group_id, seen)
                except Exception:  # noqa: BLE001
                    log.exception("Graph retry could not read released scope %s/%s", project_id, group_id)
    finally:
        _local.dispatching = False


_installed = False


def install() -> None:
    """Once, from the composition root (``execution.register``)."""
    global _installed
    if _installed:
        return
    _installed = True
    db_git.on_session_closed(on_session_closed)
    lock_manager.on_released(on_lock_released)


def session_closed(group_id: str, merge_id: int) -> None:
    """A group's session closed: the attempt of a ``graph_attempt_merge`` session goes on — committed (its result in
    the group branch) -> (e), else ``failed`` (``conflict_aborted``) — whether its ``conflict`` was recorded yet or
    not (the session it opened while ``integrating``); then the waiting integrations are asked again, the aborted
    attempt not in this drain."""
    session = db_git.get_session(merge_id)
    skip: set[str] = set()
    if session is not None and session.get("status") != "open" and db_git.session_kind(session) == SESSION_KIND \
            and session.get("group_id") == group_id:
        context = db_git.session_context(session)
        attempt_id = context.get("attempt_id")
        row = workspace.attempt(attempt_id) if attempt_id else None
        with _opened_lock:
            opened = _opened.get(attempt_id) if attempt_id else None
        if row and row["status"] == "active" and row.get("result_commit") == context.get("result_commit") and (
                (_status(row) == CONFLICT and _merge_id(row) == int(merge_id))
                or (_status(row) == INTEGRATING and opened == int(merge_id))):
            if _settle_closed(row, (INTEGRATING, CONFLICT)) == FAILED:
                skip.add(attempt_id)
            _after(attempt_id)
    retry(group_id, skip)


# ── Stop and the server start ─────────────────────────────────────────────────

def abort_for_stop(flow_id: str, run_id: str) -> list[str]:
    """Stop, outside every guard: the run's open ``graph_attempt_merge`` sessions are aborted (the abort under G,
    which checks the session is still open and its merge the one in flight); their attempts fail and the Stop then
    invalidates them and returns their nodes to pending. A session a person committed first finishes its attempt
    (done stays done). Returns the attempts whose session is still open (lock busy, Git failed): the Stop does
    nothing more and stays stopping until a repeated Stop or the server start aborts them."""
    left: list[str] = []
    _local.quiet = True
    try:
        for r in run_store._all("SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
                                "AND integration_status = 'conflict' ORDER BY created_at, attempt_id", [run_id]):
            row = workspace.attempt(r["attempt_id"])
            merge_id = _merge_id(row) if row else None
            if _session_open(merge_id):
                try:
                    conflict.abort_tr_conflict(flow_id, merge_id)
                except Exception:  # noqa: BLE001 — the session stays; the Stop stays stopping and retries
                    log.warning("Graph stop could not abort the merge session %s of attempt %s", merge_id,
                                r["attempt_id"], exc_info=True)
            if _session_open(merge_id):
                left.append(r["attempt_id"])
                continue
            row = workspace.attempt(r["attempt_id"])
            if row and _status(row) == CONFLICT:
                _settle_closed(row, (CONFLICT,))
    finally:
        _local.quiet = False
    return left


def integrating(run_id: str) -> list[dict]:
    """The run's attempts whose merge is in progress: a Stop leaves them to their end."""
    return run_store._all("SELECT attempt_id, node_key FROM graph_node_attempts WHERE run_id = ? "
                          "AND status = 'active' AND integration_status = 'integrating'", [run_id])


def _recover_one(row: dict) -> None:
    project_id, root, _branch = _group_root(row)
    open_session = git.db_git.get_open_session_by_group(row["group_id"])
    if open_session is not None and db_git.session_kind(open_session) == SESSION_KIND and \
            db_git.session_context(open_session).get("attempt_id") == row["attempt_id"]:
        _set(row["attempt_id"], CONFLICT, SESSION_REASON + str(open_session["merge_id"]), (INTEGRATING,))
        return
    result = row.get("result_commit")
    if root is not None and result and git._is_ancestor(root, result, "HEAD"):
        _finish(row["attempt_id"], _merge_commit_of(root, result), (INTEGRATING,))
        return
    if root is not None and result and open_session is None and git._rev_parse(root, "MERGE_HEAD") == result:
        # The merge was left in flight with no session: it is undone before the integration is asked again.
        lock = git_commit._acquire_group_lock(project_id, row["group_id"], HOLDER, no_wait=True)
        if lock is None:
            return                                   # stays integrating: the next start recovers it
        try:
            proc = git._run_git(["merge", "--abort"], cwd=root)
            if proc.returncode != 0 or git._rev_parse(root, "MERGE_HEAD") is not None:
                log.warning("Graph integration recovery could not undo the merge of %s", row["attempt_id"])
                return                               # stays integrating: the next start recovers it
        finally:
            git_commit._release_group_lock(lock)
    _set(row["attempt_id"], PENDING, "recovered", (INTEGRATING,))


def recover() -> None:
    """Server start, before the Stop recovery: every ``integrating`` attempt is finished (its result in the group
    branch) or pending again; then the drain of every run that is not stopping and has a waiting integration or a
    hold (pending ones only: a failed one waits for its trigger), which ends the hold it finds."""
    if not run_store.available():
        return
    for r in run_store._all("SELECT a.attempt_id FROM graph_node_attempts a WHERE a.status = 'active' "
                            "AND a.integration_status = 'integrating' ORDER BY a.created_at, a.attempt_id", []):
        try:
            row = workspace.attempt(r["attempt_id"])
            if row:
                _recover_one(row)
        except Exception:  # noqa: BLE001
            log.exception("Graph integration recovery failed for %s", r["attempt_id"])
    flows = [r["group_id"] for r in run_store._all(
        "SELECT DISTINCT g.group_id FROM graph_node_attempts a JOIN graph_runs g ON g.run_id = a.run_id "
        "WHERE a.status = 'active' AND g.status IN ('running', 'paused', 'idle') "
        "AND a.integration_status IN ('pending', 'failed', 'conflict')", [])]
    flows += [r["group_id"] for r in run_store._all("SELECT run_id, group_id FROM graph_runs WHERE status = 'running'",
                                                    []) if hold_of(r["run_id"]) is not None]
    for flow_id in dict.fromkeys(flows):
        try:
            retry(flow_id, failed=False)
        except Exception:  # noqa: BLE001
            log.exception("Graph integration retry at start failed for %s", flow_id)
