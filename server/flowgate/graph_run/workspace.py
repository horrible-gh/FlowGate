"""Attempt-owned linked worktrees (0061 T#5).

Git changes precede journal changes. Git locks are never acquired inside a Flow
guard/transaction and are released before admission or any provider is started.

0061 T#10: the attempt starts from its predecessors' results (``_inputs``): a start node from the group branch HEAD
(``run_base``), one predecessor from its ended attempt's ``result_commit``, a Join from those results merged in the
attempt worktree in node_key order (``predecessors``); a predecessor with no attempt commit (a gate) hands its own
input on. Inputs that reach the start without a commit (an approved root, a gate) start from the run's base
(``_run_base``): the commit the run's first ``run_base`` attempt recorded, not the group branch HEAD that has moved
since, so a branch integrated meanwhile stays out. Every predecessor attempt in the input is recorded, also those
sharing a commit; the merge uses each commit once. A Join whose results conflict starts from the group branch HEAD,
which holds them all (``integration_head``, recorded by a run event). An ended attempt whose integration ended
(``integrated``) loses its tree and branch (``cleanup_ended``; its result stays a parent on the group branch), as an
invalidated one does; the server start removes both (``cleanup_finished``). An active attempt's tree is never removed.

0111 T#1 (D-1a-0): every removal (``cleanup``, which ``cleanup_ended`` and ``cleanup_finished`` go through) first
proves, under the Git locks, that the attempt's source is kept elsewhere; an attempt it cannot prove that for keeps its
tree and branch and is a run event ``cleanup_preserved`` (``dirty`` / ``unpreserved_commits`` / ``unverifiable`` /
``executing``).

0111 T#8 (D-1c): an attempt prepared from now on has no branch. Its commits live on the dedicated ref
``refs/flowgate/attempts/<key>`` and its worktree is added with a detached HEAD (``worktree add --detach``), so no
``refs/heads`` entry -- the branch catalog, an external Git UI's branch list -- ever shows it. The owner marker names
that ``ref``; the tree is valid while its HEAD is detached at the ref or ahead of it on the attempt's own commits; the
attempt commit moves the ref to its result; a removal deletes it (``update-ref -d``) after the same D-1a-0 check, which
counts neither ``graph_attempt_*`` branches nor ``refs/flowgate/attempts/*`` refs as keeping a commit. An attempt
prepared before keeps its ``graph_attempt_<key>`` branch (its marker names ``branch``) and is checked, committed and
removed as it was: nothing is migrated or renamed.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.services import git_service as git, graph_source_hooks as port
from modules.flow_gate.services.git import lock_manager as locks, merge_target, branches
from modules.flow_gate.services.git.worktree import _force_rmtree
from modules.flow_gate.services.git.base_slot import _record_attempt
from modules.flow_gate.storage.paths import project_dir_name
from flowgate.graph_run import execution_store as ledger

log = logging.getLogger(__name__)
EVENT = "graph_attempt_workspace"
# 0061 T#7: ``graph_run_events.event_type`` admits only the run / node status events (migration 144), so the
# attempt's workspace record is a run status event with the status unchanged, told by its cause.
CAUSE = "attempt_workspace"
# 0111 T#8: the two attempt layouts, told by the owner marker (``ref`` or ``branch``). A new preparation takes
# ``NEW_LAYOUT``; the branch layout stays for the attempts prepared before (and the tests that rebuild them).
ATTEMPT_REF_PREFIX = "refs/flowgate/attempts/"
REF_LAYOUT, BRANCH_LAYOUT = "ref", "branch"
NEW_LAYOUT = REF_LAYOUT


def attempt(attempt_id):
    return run_store._one(
        "SELECT a.*, r.group_id FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = ?", [attempt_id])


def location(row, project_id):
    """(directory, tree, name): ``name`` is the attempt's W key in either layout and the branch of a branch-layout
    attempt only (``attempt_head`` is the ref its commits are on)."""
    key = merge_target.workspace_key(row["attempt_id"])
    base = git.get_storage_root() / "graph_attempts"
    directory = base / project_dir_name(project_id) / key
    if base.resolve() not in directory.resolve().parents:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="unsafe_path")
    root = directory / merge_target.WORKSPACE_TREE
    if root.resolve().parent != directory.resolve():
        raise port.GraphSourceError("graph_worktree_unavailable", reason="unsafe_path")
    return directory, root, merge_target.GRAPH_ATTEMPT_BRANCH_PREFIX + key


def _layout(marker):
    return REF_LAYOUT if marker and "ref" in marker else BRANCH_LAYOUT


def _head(marker, name):
    """The ref the attempt's commits are on: the marker's dedicated ref, else the ``graph_attempt_*`` branch."""
    return marker["ref"] if _layout(marker) == REF_LAYOUT else "refs/heads/" + name


def _owner(row, project_id, name, layout):
    owner = {"owner": "graph_node_attempt", "attempt_id": row["attempt_id"],
             "project_id": project_id, "group_id": row["group_id"]}
    if layout == REF_LAYOUT:
        owner["ref"] = ATTEMPT_REF_PREFIX + merge_target.workspace_key(row["attempt_id"])
    else:
        owner["branch"] = name
    return owner


def _marker(row, project_id):
    directory, root, name = location(row, project_id)
    marker = merge_target.read_owner_marker(directory)
    if not marker or any(marker.get(k) != v for k, v in _owner(row, project_id, name, _layout(marker)).items()):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="owner_mismatch")
    return directory, root, name, marker


def attempt_head(row, project_id):
    """0111 T#8: the dedicated ref of a ref-layout attempt (its commit moves it), None for a branch-layout one."""
    marker = merge_target.read_owner_marker(location(row, project_id)[0])
    return marker["ref"] if _layout(marker) == REF_LAYOUT and isinstance(marker.get("ref"), str) else None


def _mode(row):
    # The first successful preparation pins document-only mode even if Git is enabled later.
    for event in run_store._all(
        "SELECT payload_json FROM graph_run_events WHERE run_id = ? AND event_type = 'graph_run_status_changed' "
        "AND cause = ? ORDER BY event_seq", [row["run_id"], CAUSE]):
        data = json.loads(event["payload_json"])
        if data.get("attempt_id") == row["attempt_id"]:
            return data
    return None


def _project(row, project_id=None, group_id=None):
    group = db_groups.get_by_id(row["group_id"]) or {}
    actual = group.get("project_id")
    if not actual or (project_id and project_id != actual) or (group_id and group_id != row["group_id"]):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="owner_scope_mismatch")
    return actual


def _event(row, mode, **extra):
    from modules.flow_gate.graph_workflow.services import run_events
    run = run_store.run_by_id(row["run_id"])
    seq = int(run["last_event_seq"]) + 1
    payload = {"kind": EVENT, "flow_id": run["group_id"], "run_id": run["run_id"],
               "revision_no": run.get("revision_no"), "event_seq": seq, "from": run["status"], "to": run["status"],
               "cause": CAUSE, "reason": mode, "attempt_id": row["attempt_id"], "node_key": row["node_key"],
               "mode": mode, **extra}
    run_store.append_event(
        row["run_id"], seq, run_events.RUN_STATUS, payload, node_key=None, from_status=run["status"],
        to_status=run["status"], cause=CAUSE, result_doc_id=None, canvas_state=None, actor_user_id=None,
        reason=mode)


@contextmanager
def _git_locks(row, project_id, branch):
    ctx, held = locks.new_context("req"), []
    try:
        for domain, extra in (("G", {"group_id": row["group_id"]}),
                              ("W", {"target_key": branch}), ("M", {})):
            outcome = locks.acquire(domain, project_id, holder_kind="worktree_provision", ctx=ctx, **extra)
            if not outcome.ok:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="git_busy")
            held.append(outcome.lock_key)
        yield
    finally:
        for key in reversed(held):
            locks.release(ctx, key)


def _common_dir(root):
    result = git._run_git(["rev-parse", "--git-common-dir"], cwd=root)
    if result.returncode:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="repository_missing")
    return (root / result.stdout.strip()).resolve()


def _validate_detached(root, ref, *, moving=False):
    """0111 T#8: a ref-layout tree's HEAD is detached (``symbolic-ref -q`` answers 1, not a branch) at the attempt's
    ref or ahead of it on commits of its own (made in the tree before the attempt commit records them, as a branch
    would have followed). ``moving``: the Join is about to reset the tree under the Git locks, so where HEAD stands
    is not judged, only that the ref exists."""
    attached = git._run_git(["symbolic-ref", "-q", "HEAD"], cwd=root)
    if attached.returncode != 1:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="ref_mismatch")
    tip = git._rev_parse(root, ref + "^{commit}")
    if not tip:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="ref_missing")
    if moving:
        return
    head = git._rev_parse(root, "HEAD")
    if not head or (head != tip and git._is_ancestor(root, tip, head) is not True):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="ref_mismatch")


def _validate_tree(root, branch, marker, *, moving=False):
    if not root.is_dir() or not git._worktree_link_ok(root):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="worktree_missing")
    if _layout(marker) == REF_LAYOUT:
        _validate_detached(root, marker["ref"], moving=moving)
    else:
        actual = git._run_git(["symbolic-ref", "--short", "HEAD"], cwd=root)
        if actual.returncode or actual.stdout.strip() != branch:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="branch_mismatch")
    if _common_dir(root) != Path(marker["common_dir"]).resolve():
        raise port.GraphSourceError("graph_worktree_unavailable", reason="repository_mismatch")


def source_root(attempt_id, *, project_id=None, group_id=None, locale="ko", document_ok=False):
    row = attempt(attempt_id)
    if not row or row["status"] != "active":
        raise port.GraphSourceError("graph_worktree_unavailable", locale=locale, reason="attempt_not_active")
    project_id = _project(row, project_id, group_id)
    record = _mode(row)
    if record and record["mode"] == "document_only":
        if document_ok:
            return None
        raise port.GraphSourceError("graph_git_required", locale=locale, attempt_id=attempt_id)
    if not record or record["mode"] != "worktree" or not row.get("input_commit"):
        raise port.GraphSourceError("graph_worktree_unavailable", locale=locale, reason="not_prepared")
    _directory, root, branch, marker = _marker(row, project_id)
    if marker.get("input_commit") != row["input_commit"]:
        raise port.GraphSourceError("graph_worktree_unavailable", locale=locale, reason="input_mismatch")
    _validate_tree(root, branch, marker)
    return root.resolve()


@contextmanager
def mutation(attempt_id, *, project_id, group_id):
    from flowgate.graph_run import fencing
    row = attempt(attempt_id)
    if not row:
        raise port.GraphSourceError("graph_worktree_unavailable")
    # T#7: a stopping run's or an inactive attempt's source write / remove is refused (409 graph_attempt_inactive)
    # and recorded before the Git lock (its record takes the Flow guard). The write itself runs under the Stop
    # gate's shared hold (taken before the Git lock), judged again once both are held: ``stopping`` cannot commit
    # between that judgement and the write. A refusal there is recorded after both are released.
    fencing.require(attempt_id, "source_mutation")
    project_id = _project(row, project_id, group_id)
    _directory, _root, branch = location(row, project_id)

    @contextmanager
    def w_lock():
        ctx = locks.new_context("req")
        outcome = locks.acquire("W", project_id, target_key=branch, holder_kind="source_mutation", ctx=ctx)
        if not outcome.ok:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="git_busy")
        try:
            yield
        finally:
            locks.release(ctx, outcome.lock_key)

    with fencing.hold(attempt_id, "source_mutation", before=w_lock):
        yield source_root(attempt_id, project_id=project_id, group_id=group_id)


def _prior(run_id, node_key):
    """The node's current attempt once it is done: its last ended one."""
    return run_store._one("SELECT attempt_id, node_key, result_commit FROM graph_node_attempts WHERE run_id = ? "
                          "AND node_key = ? AND status = 'ended' ORDER BY created_at DESC, attempt_id DESC LIMIT 1",
                          [run_id, node_key])


def _inputs(row):
    """T#10: ``[(node_key, attempt_id, result_commit)]`` the attempt starts from, in node_key order, one per
    predecessor attempt (two may share a commit; each is recorded): each predecessor's ended attempt with a result
    commit; a predecessor with none (a gate, a document-only or never prepared attempt) hands on its own
    predecessors' results. [] -> the run base (``_run_base``)."""
    record = definition_store.revision(row["group_id"], int(row["revision_no"])) or {"nodes": [], "edges": []}
    keys = {n["node_key"] for n in record["nodes"]}
    preds = {}
    for e in record["edges"]:
        if e["source"] in keys and e["target"] in keys:
            preds.setdefault(e["target"], set()).add(e["source"])
    found, seen = [], set()

    def follow(key):
        for pred in sorted(preds.get(key, ())):
            if pred in seen:
                continue
            seen.add(pred)
            prior = _prior(row["run_id"], pred)
            if prior and prior.get("result_commit"):
                found.append((pred, prior["attempt_id"], prior["result_commit"]))
            else:
                follow(pred)
    follow(row["node_key"])
    return sorted(found, key=lambda i: i[0])


def _run_base(row, group_root):
    """T#10, under the Git locks: the run's base — the commit its first ``run_base`` attempt started from (the group
    branch HEAD then). Every attempt of the run with no input commit starts there, so a branch integrated since
    (one done before it under the same root) is not in its input. The first one starts from the group branch HEAD:
    a result of the run is integrated only after an attempt that started from the base recorded it."""
    base = run_store._one("SELECT input_commit FROM graph_node_attempts WHERE run_id = ? AND input_source = 'run_base' "
                          "AND input_commit IS NOT NULL ORDER BY created_at, attempt_id LIMIT 1", [row["run_id"]])
    sha = base["input_commit"] if base else git._rev_parse(group_root, "HEAD^{commit}")
    if not sha or git._rev_parse(group_root, sha + "^{commit}") != sha:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="input_missing")
    return sha


def _start(row, group_root):
    """The marker's input half, under the Git locks: ``input_commit`` (the worktree's first commit), its source, the
    attempts in it, and the Join's other heads still to merge (``join_inputs``)."""
    inputs = _inputs(row)
    if not inputs:
        return {"input_commit": _run_base(row, group_root), "input_source": "run_base", "input_attempt_ids": []}
    # Predecessors sharing a result commit are all recorded; the merge takes the commit once.
    shas = list(dict.fromkeys(sha for _key, _attempt_id, sha in inputs))
    if any(git._rev_parse(group_root, sha + "^{commit}") != sha for sha in shas):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="input_missing")
    # A result another input already holds adds nothing to the merge.
    heads = [sha for sha in shas if not any(other != sha and git._is_ancestor(group_root, sha, other)
                                            for other in shas)]
    return {"input_commit": heads[0], "input_source": "predecessors",
            "input_attempt_ids": [attempt_id for _key, attempt_id, _sha in inputs], "join_inputs": heads[1:]}


def _write_marker(directory, marker):
    temp = directory / (merge_target.OWNER_MARKER + ".tmp")
    temp.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, directory / merge_target.OWNER_MARKER)


def _join(row, directory, root, branch, marker, group_root):
    """T#10, under the Git locks: the Join's other heads merged one by one in the attempt worktree (an interrupted
    join starts again from ``input_commit``). A conflict aborts the merge and the attempt starts from the group
    branch HEAD (every predecessor is done, so it holds them all): ``integration_head``. The marker then records the
    final input and loses ``join_inputs``."""
    def run(args, reason):
        if git._run_git(args, cwd=root).returncode:
            raise port.GraphSourceError("graph_worktree_unavailable", reason=reason)

    def reset(sha):
        if git._rev_parse(root, "MERGE_HEAD") is not None:
            run(["merge", "--abort"], "join_abort_failed")
        run(["reset", "--hard", sha], "join_reset_failed")
        if git._rev_parse(root, "HEAD") != sha or git._rev_parse(root, "MERGE_HEAD") is not None:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="join_reset_failed")

    reset(marker["input_commit"])
    final = {"input_commit": None}
    into = marker["ref"] if _layout(marker) == REF_LAYOUT else branch
    for sha in marker["join_inputs"]:
        message = (f"Merge join input {sha} into {into}\n\n"
                   f"FlowGate-Attempt: {row['attempt_id']}\nFlowGate-Join-Input: {sha}")
        files = branches.merge_no_ff(root, sha, message, sha, into)
        if files:
            head = git._rev_parse(group_root, "HEAD^{commit}")
            if not head:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="input_missing")
            reset(head)
            final = {"input_commit": head, "input_source": "integration_head",
                     "join_conflict": {"conflict_commit": sha, "files": files[:50], "join_inputs":
                                       [marker["input_commit"], *marker["join_inputs"]]}}
            break
    if final["input_commit"] is None:
        final["input_commit"] = git._rev_parse(root, "HEAD")
        if not final["input_commit"]:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="input_missing")
    if _layout(marker) == REF_LAYOUT:
        # T#8: the ref follows the reset / merges as the branch did (before the marker, so a retry starts over).
        run(["update-ref", "-m", "flowgate: attempt join input", marker["ref"], final["input_commit"]],
            "join_ref_update_failed")
    marker = {**{k: v for k, v in marker.items() if k != "join_inputs"}, **final}
    _write_marker(directory, marker)
    return marker


def _materialize(row, project_id):
    directory, root, branch = location(row, project_id)
    module = row["group_id"].split(".")[1]
    # Reuse the group provisioning/recovery contract only to obtain the required input.
    group_root = git.effective_src_root(project_id, row["group_id"])
    if group_root is None:
        # 0085 T#3 (D0007 §7.1): a slot a gate end cleanup let go comes back at its recorded real end HEAD.
        from flowgate.gate_decisions import cleanup as gate_cleanup
        start_point, refused = gate_cleanup.restore_point(project_id, row["group_id"])
        if refused:
            raise port.GraphSourceError("graph_worktree_unavailable", reason=refused)
        restore = {"start_point": start_point} if start_point else {}
        if git.ensure_worktree(project_id, module, row["group_id"], trigger="graph_attempt", **restore) != "ok":
            raise port.GraphSourceError("graph_worktree_unavailable", reason="group_input_missing")
    with _git_locks(row, project_id, branch):
        current = attempt(row["attempt_id"])
        if not current or current["status"] != "active":
            raise ledger.LeaseLost("attempt ended before workspace preparation")
        group_root = git.effective_src_root(project_id, row["group_id"])
        if group_root is None:
            # 0085 T#3 rework: a gate end cleanup let the slot go while this waited for G (its G covers the final
            # check to the last delete): the next preparation restores it at the recorded end HEAD (``git_busy``
            # keeps the attempt and its request).
            from flowgate.gate_decisions import cleanup as gate_cleanup
            if gate_cleanup.slot_released(row["group_id"]):
                raise port.GraphSourceError("graph_worktree_unavailable", reason="git_busy")
            raise port.GraphSourceError("graph_worktree_unavailable", reason="group_input_missing")
        group_root = Path(group_root).resolve()
        marker = merge_target.read_owner_marker(directory)
        if marker is None:
            owner = _owner(row, project_id, branch, NEW_LAYOUT)
            if directory.exists() or git._ref_exists(group_root, "refs/heads/" + branch) \
                    or git._ref_exists(group_root, ATTEMPT_REF_PREFIX + merge_target.workspace_key(row["attempt_id"])):
                raise port.GraphSourceError("graph_worktree_unavailable", reason="workspace_occupied")
            marker = {**owner, **_start(current, group_root),
                      "repository": str(group_root), "common_dir": str(_common_dir(group_root))}
            if not marker.get("join_inputs"):
                marker.pop("join_inputs", None)
            directory.mkdir(parents=True, exist_ok=False)
            # Exclusive owner marker precedes branch/worktree creation, as in merge_target.
            with (directory / merge_target.OWNER_MARKER).open("x", encoding="utf-8") as stream:
                json.dump(marker, stream, ensure_ascii=False)
        else:
            _marker(row, project_id)
            if _common_dir(group_root) != Path(marker["common_dir"]).resolve():
                raise port.GraphSourceError("graph_worktree_unavailable", reason="repository_mismatch")
        sha = marker["input_commit"]
        if not root.exists():
            # Recovery can reuse an owned branch / ref left by an interrupted worktree add (or an interrupted join).
            head = _head(marker, branch)
            exists = git._ref_exists(group_root, head)
            if exists and not marker.get("join_inputs") and git._rev_parse(group_root, head + "^{commit}") != sha:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="branch_occupied")
            if _layout(marker) == REF_LAYOUT:
                # T#8: the ref first (only if absent), then the tree detached at it; no branch is created.
                if not exists and git._run_git(["update-ref", "-m", "flowgate: attempt input", head, sha, ""],
                                               cwd=group_root).returncode:
                    raise port.GraphSourceError("graph_worktree_unavailable", reason="ref_create_failed")
                args = ["worktree", "add", "--detach", str(root), head]
            else:
                args = ["worktree", "add"]
                args += [str(root), branch] if exists else ["-b", branch, str(root), sha]
            proc = git._run_git(args, cwd=group_root)
            if proc.returncode:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="worktree_add_failed")
        _validate_tree(root, branch, marker, moving=bool(marker.get("join_inputs")))
        if marker.get("join_inputs"):
            marker = _join(current, directory, root, branch, marker, group_root)
    return marker["input_commit"]


def _input_record(row, project_id):
    """The input the marker recorded (a marker before T#10 started from the group branch HEAD)."""
    marker = merge_target.read_owner_marker(location(row, project_id)[0]) or {}
    return {"input_source": marker.get("input_source") or "integration_head",
            "input_attempt_ids": list(marker.get("input_attempt_ids") or []),
            "join_conflict": marker.get("join_conflict")}


def _failed(exec_id, cause):
    """The runtime's invalidation of an attempt whose worktree failed. 0061 T#7: committed under the exclusive hold of
    the Flow's Stop gate (``fencing.exclusive``, as the Stop's ``stopping``): a side effect of the attempt's token in
    flight (an inbox registration, a source write) completes before it, and none starts after it — it is judged
    again then and refused. Taken outside every Flow guard; this thread's own shared hold (an approval whose next
    node failed here) is not waited for."""
    from flowgate.graph_run import fencing
    row = ledger.get(exec_id)
    if not row:
        return
    original = attempt(row["attempt_id"])
    if not original:
        return
    def body():
        current = ledger.get(exec_id)
        node_attempt = attempt(row["attempt_id"])
        if not current or current["status"] != "requested" or not node_attempt or node_attempt["status"] != "active":
            return
        ledger.cancel(exec_id, None, "requested")
        run_store._affected(
            "UPDATE graph_node_attempts SET status = 'invalidated', active_node_key = NULL, "
            "invalidated_reason = 'worktree_failed', ended_at = ? WHERE attempt_id = ? AND status = 'active'",
            [ledger.now(), row["attempt_id"]])
        from modules.flow_gate.services import ai_invoke_service
        for token in run_store._all("SELECT token_id FROM graph_run_token_map WHERE attempt_id = ?", [row["attempt_id"]]):
            ai_invoke_service._reclaim_orphan_lease_token(token, "worktree_failed")
        node = next((n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == node_attempt["node_key"]), None)
        run_store._affected(
            "UPDATE graph_node_runs SET status = 'pending', result_doc_id = NULL, opened_at = NULL, "
            "finished_at = NULL, dispatch_token_issued = NULL, dispatch_issued_to = NULL, "
            "dispatch_mention_id = NULL, dispatch_error_code = NULL, stage = NULL, "
            "instruction_doc_id = NULL, result_event_floor = NULL, updated_at = ? "
            "WHERE run_id = ? AND node_key = ? AND status <> 'done'",
            [ledger.now(), row["run_id"], node_attempt["node_key"]])
        _event(attempt(row["attempt_id"]), "worktree_failed", reason=cause)
        if node and node["status"] != "done":
            from modules.flow_gate.graph_workflow.services import run_apply
            ctx = run_apply.load_context(run_store.run_by_id(row["run_id"]), _project(original), None)
            run_apply.emit_node_state(ctx, node_attempt["node_key"], node["status"], "pending", "worktree_failed", None)
            # Do not immediately re-dispatch the same broken workspace.
            from flowgate.graph_run import settle
            if ctx.run["status"] == "running" and not settle.working(ctx, run_store.node_runs(ctx.run_id)):
                run_store.set_run_status(ctx.run_id, "running", "idle", "error")
                run_apply.emit_run_status(ctx, "running", "idle", "worktree_failed", "error")
    with fencing.exclusive(original["group_id"], own_ok=True):
        with_flow_guard(original["group_id"], body)
    cleanup(row["attempt_id"])


def prepare(exec_id, project_id, group_id):
    execution = ledger.get(exec_id)
    if not execution or execution["status"] not in ("requested", "running") or not ledger.eligible(execution):
        raise ledger.LeaseLost("execution is not eligible for workspace preparation")
    row = attempt(execution["attempt_id"])
    project_id = _project(row, project_id, group_id)
    try:
        record = _mode(row)
        if record:
            return source_root(row["attempt_id"], project_id=project_id, group_id=group_id, document_ok=True)
        cfg = git.db_git.get_config(project_id)
        mode = "worktree" if cfg and cfg.get("enabled") else "document_only"
        sha = _materialize(row, project_id) if mode == "worktree" else None
        # T#10: a document-only attempt has no start point (no input rule).
        record = _input_record(row, project_id) if mode == "worktree" else {
            "input_source": "document_only", "input_attempt_ids": [], "join_conflict": None}
        def record_ready():
            current = attempt(row["attempt_id"])
            if not current or current["status"] != "active" or not ledger.eligible(ledger.get(exec_id)):
                raise ledger.LeaseLost("attempt ended while workspace was being prepared")
            if not _mode(current):
                run_store._affected("UPDATE graph_node_attempts SET input_commit = ?, input_source = ?, "
                                    "input_attempt_ids = ? WHERE attempt_id = ? AND status = 'active'",
                                    [sha, record["input_source"], json.dumps(record["input_attempt_ids"]),
                                     row["attempt_id"]])
                _event(current, mode, input_commit=sha, input_source=record["input_source"],
                       input_attempt_ids=record["input_attempt_ids"])
                if record["join_conflict"]:
                    # After the mode record (``_mode`` reads the attempt's first event): the Join's results conflict,
                    # so its input is the group branch HEAD and may hold branches unrelated to it (T#12 reads this).
                    _event(current, "join_input_conflict", input_commit=sha, input_source=record["input_source"],
                           input_attempt_ids=record["input_attempt_ids"], **record["join_conflict"])
        with_flow_guard(group_id, record_ready)
        return source_root(row["attempt_id"], project_id=project_id, group_id=group_id, document_ok=True)
    except ledger.LeaseLost:
        cleanup(row["attempt_id"])
        raise
    except Exception as exc:
        cause = getattr(exc, "details", {}).get("reason") or type(exc).__name__
        if isinstance(exc, port.GraphSourceError) and cause == "git_busy":
            raise  # Transient contention keeps the attempt and durable request intact.
        _record_attempt(project_id, "failed", cause, "graph_attempt", "none")
        _failed(exec_id, cause)
        raise port.GraphSourceError("graph_worktree_unavailable", reason="worktree_failed") from exc


def verify_execution(exec_id):
    execution = ledger.get(exec_id)
    if not execution:
        raise ledger.LeaseLost("execution disappeared")
    try:
        return source_root(execution["attempt_id"], document_ok=True)
    except port.GraphSourceError:
        # The tree can disappear between admission and acquire. Do not strand a
        # requested execution or let a duplicate launcher invalidate a running one.
        if execution["status"] == "requested":
            _failed(exec_id, "launch_workspace_unavailable")
        raise


def _integrated(row):
    return bool(row) and row["status"] == "ended" and row.get("integration_status") == "integrated"


def _removable(row):
    return bool(row) and (row["status"] == "invalidated" or _integrated(row))


PRESERVED = "cleanup_preserved"


class _Preserved(Exception):
    """0111 T#1 (D-1a-0): the attempt's tree and branch stay, told by ``reason`` -- ``dirty`` (an uncommitted or
    untracked change), ``unpreserved_commits`` (a commit no other lasting ref reaches), ``executing`` (a requested /
    running execution) or ``unverifiable`` (the check could not be made: a Git error, a broken worktree link, an
    owner mismatch)."""

    def __init__(self, reason, detail):
        super().__init__(reason, detail)
        self.reason, self.detail = reason, detail


def _unverifiable(exc):
    details = getattr(exc, "details", None)
    return _Preserved("unverifiable", (details.get("reason") if isinstance(details, dict) else None)
                      or type(exc).__name__)


def _tip(repo, head):
    """Under the Git locks: the commit ``head`` (the attempt's branch or ref) points at, "" when it is absent."""
    listed = git._run_git(["for-each-ref", "--format=%(objectname)", head], cwd=repo)
    if listed.returncode:
        raise _Preserved("unverifiable", "branch_unreadable")
    return listed.stdout.strip()


def _unkept_commit(repo, tips):
    """Under the Git locks: whether the attempt's tips hold a commit no lasting ref reaches. Every ref counts
    (group / user branches, tags, remotes, FlowGate's own refs) except the ``graph_attempt_*`` branches and, T#8, the
    ``refs/flowgate/attempts/*`` refs, which go with their attempts; HEADs are not counted (a branch-layout tree's HEAD
    is its own branch; a ref-layout tree's detached HEAD is passed in ``tips``)."""
    tips = [tip for tip in dict.fromkeys(tips) if tip]
    if not tips:
        return False
    result = git._run_git(["rev-list", "--max-count=1", *tips, "--not",
                           "--exclude=refs/heads/" + merge_target.GRAPH_ATTEMPT_BRANCH_PREFIX + "*",
                           "--exclude=" + ATTEMPT_REF_PREFIX + "*", "--glob=refs/*"],
                          cwd=repo)
    if result.returncode:
        raise _Preserved("unverifiable", "rev_list_failed")
    return bool(result.stdout.strip())


def _kept_elsewhere(row, project_id):
    """0111 T#1 (D-1a-0), under the Git locks, before anything is removed: the owner marker, repository, registration
    and branch still match (else ``unverifiable``); the tree has no uncommitted or untracked change (``status
    --porcelain --untracked-files=all`` prints nothing; ignored files are not judged and go with the tree) and the
    branch no commit only it holds. T#8: a ref-layout attempt the same way -- its registered tree detached (no branch),
    its ref and its tree's HEAD the tips judged. Returns what the removal needs; raises ``_Preserved`` otherwise."""
    try:
        directory, root, branch, marker = _marker(row, project_id)
        detached = _layout(marker) == REF_LAYOUT
        repo = Path(marker["repository"])
        if _common_dir(repo) != Path(marker["common_dir"]).resolve():
            raise _Preserved("unverifiable", "repository_mismatch")
        registered = branches._registered_worktrees_for_delete(repo)
        if registered is None:
            raise _Preserved("unverifiable", "worktree_list_failed")
        owned = [entry for entry in registered if Path(entry["path"]).resolve() == root.resolve()]
        if any(entry["branch"] != (None if detached else branch) for entry in owned):
            raise _Preserved("unverifiable", "ref_mismatch" if detached else "branch_mismatch")
        head, own_head = _head(marker, branch), None
        if owned:
            _validate_tree(root, branch, marker)
            status = git._run_git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"],
                                  cwd=root)
            if status.returncode:
                raise _Preserved("unverifiable", "status_failed")
            if status.stdout.strip():
                raise _Preserved("dirty", "status")
            if detached:
                own_head = git._rev_parse(root, "HEAD")
                if not own_head:
                    raise _Preserved("unverifiable", "head_unreadable")
        elif root.exists() and (git._worktree_link_ok(root) or any(root.iterdir())):
            # A tree Git no longer lists (a lost link, an interrupted add): what it holds cannot be told.
            raise _Preserved("unverifiable", "worktree_unregistered")
        tip = _tip(repo, head)
        if _unkept_commit(repo, [tip, own_head]):
            raise _Preserved("unpreserved_commits", "rev_list")
        return directory, root, branch, repo, owned, head, tip
    except port.GraphSourceError as exc:
        raise _unverifiable(exc) from None


def _note_preserved(row, kept):
    """Outside the Git locks: an attempt kept by D-1a-0 is a run event ``cleanup_preserved`` with its reason. Only the
    same check again (the next cleanup call, the next server start) removes it."""
    log.warning("Graph attempt workspace preserved: %s reason=%s detail=%s", row["attempt_id"], kept.reason,
                kept.detail)

    def note():
        current = attempt(row["attempt_id"])
        if _removable(current):
            _event(current, PRESERVED, reason=kept.reason, detail=kept.detail)
    try:
        with_flow_guard(row["group_id"], note)
    except Exception:  # noqa: BLE001 — the record only
        log.exception("Graph attempt workspace preservation not recorded: %s", row["attempt_id"])


def _cleanup(attempt_id):
    """``removed``, ``preserved`` (kept by D-1a-0 and recorded) or None (not to be removed, or the removal failed)."""
    row = attempt(attempt_id)
    if not _removable(row):
        return None
    removing = False
    try:
        project_id = _project(row)
        directory, root, branch = location(row, project_id)
        if not directory.exists():
            return "removed"
        with _git_locks(row, project_id, branch):
            current = attempt(attempt_id)
            if not _removable(current):
                return None
            # Never remove a worktree while an execution could still own a process.
            if run_store._one("SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? "
                              "AND status IN ('requested', 'running')", [attempt_id]):
                raise _Preserved("executing", "execution_active")
            directory, root, branch, repo, owned, head, tip = _kept_elsewhere(current, project_id)
            removing = True
            if owned:
                result = git._run_git(["worktree", "remove", "--force", str(root)], cwd=repo)
                if result.returncode or root.exists():
                    return None
            elif root.exists() and not _force_rmtree(root):
                return None
            if head.startswith(ATTEMPT_REF_PREFIX):
                # T#8: the dedicated ref, only at the tip just judged (W and M are held; nothing else moves it).
                if tip and git._run_git(["update-ref", "-d", head, tip], cwd=repo).returncode:
                    return None
            elif git._ref_exists(repo, head):
                result = git._run_git(["branch", "-D", branch], cwd=repo)
                if result.returncode:
                    return None
            return "removed" if merge_target._remove_workspace(directory, repo) else None
    except _Preserved as kept:
        _note_preserved(row, kept)
        return "preserved"
    except Exception as exc:  # noqa: BLE001
        if not removing:
            # The check itself failed (a Git / lock / ownership error): nothing is removed until it can be made.
            log.exception("Graph attempt workspace cleanup check failed: %s", attempt_id)
            _note_preserved(row, _unverifiable(exc))
            return "preserved"
        log.exception("Graph attempt workspace cleanup deferred: %s", attempt_id)
        return None


def cleanup(attempt_id):
    """Only owned attempts that are invalidated or, T#10, ended with their integration done; an active attempt's
    tree is never removed. 0111 T#1 (D-1a-0): and only once their source is kept elsewhere -- no uncommitted or
    untracked change, no commit only the attempt branch holds, no requested / running execution, the owner marker,
    repository, registered worktree and branch matching, all judged under the Git locks. Anything else keeps the tree
    and branch (False) and records ``cleanup_preserved``; there is no forced removal."""
    return _cleanup(attempt_id) == "removed"


def cleanup_invalidated():
    for row in run_store._all("SELECT attempt_id FROM graph_node_attempts WHERE status = 'invalidated'", []):
        cleanup(row["attempt_id"])


def cleanup_ended(attempt_id):
    """T#10, outside every Flow guard, DB transaction and Git lock: an ended attempt whose integration ended (merged,
    or no source change) loses its worktree and branch — its result commit stays a parent on the group branch. A
    failure is a run event (``cleanup_failed``); the node keeps its state and the server start tries again. 0111 T#1:
    the same check as ``cleanup``; a kept attempt is ``cleanup_preserved``, not a failure."""
    row = attempt(attempt_id)
    if not _integrated(row):
        return False
    record = _mode(row)
    if not record or record.get("mode") != "worktree":
        return True
    outcome = _cleanup(attempt_id)
    if outcome == "removed":
        return True
    if outcome == "preserved":
        return False
    log.warning("Graph attempt workspace cleanup failed: %s", attempt_id)

    def note():
        current = attempt(attempt_id)
        if _integrated(current):
            _event(current, "cleanup_failed")
    try:
        with_flow_guard(row["group_id"], note)
    except Exception:  # noqa: BLE001 — the record only
        log.exception("Graph attempt workspace cleanup failure not recorded: %s", attempt_id)
    return False


def cleanup_finished():
    """T#10, server start, outside every guard: each attempt tree whose owner marker names an attempt that is
    invalidated or ended with its integration done goes through ``cleanup`` (0111 T#1: removed only when its source is
    kept elsewhere, else kept and recorded ``cleanup_preserved`` again). An active attempt's tree is left as it is."""
    base = git.get_storage_root() / "graph_attempts"
    if not base.is_dir():
        return
    for directory in sorted(d for project in base.iterdir() if project.is_dir()
                            for d in project.iterdir() if d.is_dir()):
        try:
            marker = merge_target.read_owner_marker(directory)
            if not marker or marker.get("owner") != "graph_node_attempt" or not marker.get("attempt_id"):
                continue
            row = attempt(marker["attempt_id"])
            if row and row["status"] == "invalidated":
                cleanup(row["attempt_id"])
            elif _integrated(row):
                cleanup_ended(row["attempt_id"])
        except Exception:  # noqa: BLE001 — the next start tries again
            log.exception("Graph attempt workspace start-up cleanup failed: %s", directory)
