"""Attempt-owned linked worktrees (0061 T#5).

Git changes precede journal changes. Git locks are never acquired inside a Flow
guard/transaction and are released before admission or any provider is started.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.services import git_service as git, graph_source_hooks as port
from modules.flow_gate.services.git import lock_manager as locks, merge_target, branches
from modules.flow_gate.services.git.worktree import _force_rmtree
from modules.flow_gate.services.git.base_slot import _record_attempt
from modules.flow_gate.storage.paths import project_dir_name
from flowgate.graph_run import execution_store as ledger

log = logging.getLogger(__name__)
EVENT = "graph_attempt_workspace"


def attempt(attempt_id):
    return run_store._one(
        "SELECT a.*, r.group_id FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = ?", [attempt_id])


def location(row, project_id):
    key = merge_target.workspace_key(row["attempt_id"])
    base = git.get_storage_root() / "graph_attempts"
    directory = base / project_dir_name(project_id) / key
    if base.resolve() not in directory.resolve().parents:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="unsafe_path")
    root = directory / merge_target.WORKSPACE_TREE
    if root.resolve().parent != directory.resolve():
        raise port.GraphSourceError("graph_worktree_unavailable", reason="unsafe_path")
    return directory, root, "graph_attempt_" + key


def _owner(row, project_id, branch):
    return {"owner": "graph_node_attempt", "attempt_id": row["attempt_id"],
            "project_id": project_id, "group_id": row["group_id"], "branch": branch}


def _marker(row, project_id):
    directory, root, branch = location(row, project_id)
    marker = merge_target.read_owner_marker(directory)
    if not marker or any(marker.get(k) != v for k, v in _owner(row, project_id, branch).items()):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="owner_mismatch")
    return directory, root, branch, marker


def _mode(row):
    # The first successful preparation pins document-only mode even if Git is enabled later.
    for event in run_store._all(
        "SELECT payload_json FROM graph_run_events WHERE run_id = ? AND event_type = ? "
        "ORDER BY event_seq", [row["run_id"], EVENT]):
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
    run = run_store.run_by_id(row["run_id"])
    seq = int(run["last_event_seq"]) + 1
    run_store.append_event(
        row["run_id"], seq, EVENT, {"attempt_id": row["attempt_id"], "mode": mode, **extra},
        node_key=row["node_key"], from_status=None, to_status=row["status"],
        cause=mode, result_doc_id=None, canvas_state=None, actor_user_id=None, reason=mode)


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


def _validate_tree(root, branch, marker):
    if not root.is_dir() or not git._worktree_link_ok(root):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="worktree_missing")
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
    row = attempt(attempt_id)
    if not row:
        raise port.GraphSourceError("graph_worktree_unavailable")
    project_id = _project(row, project_id, group_id)
    _directory, _root, branch = location(row, project_id)
    ctx = locks.new_context("req")
    outcome = locks.acquire("W", project_id, target_key=branch, holder_kind="source_mutation", ctx=ctx)
    if not outcome.ok:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="git_busy")
    try:
        yield source_root(attempt_id, project_id=project_id, group_id=group_id)
    finally:
        locks.release(ctx, outcome.lock_key)


def _materialize(row, project_id):
    directory, root, branch = location(row, project_id)
    module = row["group_id"].split(".")[1]
    # Reuse the group provisioning/recovery contract only to obtain the required input.
    group_root = git.effective_src_root(project_id, row["group_id"])
    if group_root is None:
        if git.ensure_worktree(project_id, module, row["group_id"], trigger="graph_attempt") != "ok":
            raise port.GraphSourceError("graph_worktree_unavailable", reason="group_input_missing")
    with _git_locks(row, project_id, branch):
        current = attempt(row["attempt_id"])
        if not current or current["status"] != "active":
            raise ledger.LeaseLost("attempt ended before workspace preparation")
        group_root = git.effective_src_root(project_id, row["group_id"])
        if group_root is None:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="group_input_missing")
        group_root = Path(group_root).resolve()
        marker = merge_target.read_owner_marker(directory)
        if marker is None:
            if directory.exists() or git._ref_exists(group_root, "refs/heads/" + branch):
                raise port.GraphSourceError("graph_worktree_unavailable", reason="workspace_occupied")
            sha = git._rev_parse(group_root, "HEAD^{commit}")
            if not sha:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="input_missing")
            marker = {**_owner(row, project_id, branch), "input_commit": sha,
                      "repository": str(group_root), "common_dir": str(_common_dir(group_root))}
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
            # Recovery can reuse an owned branch left by an interrupted worktree add.
            exists = git._ref_exists(group_root, "refs/heads/" + branch)
            if exists and git._rev_parse(group_root, branch + "^{commit}") != sha:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="branch_occupied")
            args = ["worktree", "add"]
            args += [str(root), branch] if exists else ["-b", branch, str(root), sha]
            proc = git._run_git(args, cwd=group_root)
            if proc.returncode:
                raise port.GraphSourceError("graph_worktree_unavailable", reason="worktree_add_failed")
        _validate_tree(root, branch, marker)
    return sha


def _failed(exec_id, cause):
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
            if ctx.run["status"] == "running" and not ledger.working(ctx.run_id):
                run_store.set_run_status(ctx.run_id, "running", "idle", "error")
                run_apply.emit_run_status(ctx, "running", "idle", "worktree_failed", "error")
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
        def record_ready():
            current = attempt(row["attempt_id"])
            if not current or current["status"] != "active" or not ledger.eligible(ledger.get(exec_id)):
                raise ledger.LeaseLost("attempt ended while workspace was being prepared")
            if not _mode(current):
                run_store._affected("UPDATE graph_node_attempts SET input_commit = ? "
                                    "WHERE attempt_id = ? AND status = 'active'", [sha, row["attempt_id"]])
                _event(current, mode, input_commit=sha)
        with_flow_guard(group_id, record_ready)
        return source_root(row["attempt_id"], project_id=project_id, group_id=group_id, document_ok=True)
    except ledger.LeaseLost:
        cleanup(row["attempt_id"])
        raise
    except Exception as exc:
        cause = getattr(exc, "details", {}).get("reason") or type(exc).__name__
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


def cleanup(attempt_id):
    """Only invalidated, owned attempts; ended trees remain until integration."""
    row = attempt(attempt_id)
    if not row or row["status"] != "invalidated":
        return False
    try:
        project_id = _project(row)
        directory, root, branch = location(row, project_id)
        if not directory.exists():
            return True
        with _git_locks(row, project_id, branch):
            current = attempt(attempt_id)
            if not current or current["status"] != "invalidated":
                return False
            # Never remove a worktree while an execution could still own a process.
            if run_store._one("SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? "
                              "AND status IN ('requested', 'running')", [attempt_id]):
                return False
            directory, root, branch, marker = _marker(current, project_id)
            repo = Path(marker["repository"])
            if _common_dir(repo) != Path(marker["common_dir"]).resolve():
                return False
            registered = branches._registered_worktrees_for_delete(repo)
            if registered is None:
                return False
            owned = [entry for entry in registered if Path(entry["path"]).resolve() == root.resolve()]
            if any(entry["branch"] != branch for entry in owned):
                return False
            if owned:
                if root.exists() and git._worktree_link_ok(root):
                    _validate_tree(root, branch, marker)
                result = git._run_git(["worktree", "remove", "--force", str(root)], cwd=repo)
                if result.returncode or root.exists():
                    return False
            elif root.exists():
                # An interrupted add may leave only a partial checkout. Its
                # exclusive marker owns it, but another repository never does.
                if (root / ".git").exists() or not _force_rmtree(root):
                    return False
            if git._ref_exists(repo, "refs/heads/" + branch):
                result = git._run_git(["branch", "-D", branch], cwd=repo)
                if result.returncode:
                    return False
            return merge_target._remove_workspace(directory, repo)
    except Exception:
        log.exception("Graph attempt workspace cleanup deferred: %s", attempt_id)
        return False


def cleanup_invalidated():
    for row in run_store._all("SELECT attempt_id FROM graph_node_attempts WHERE status = 'invalidated'", []):
        cleanup(row["attempt_id"])
