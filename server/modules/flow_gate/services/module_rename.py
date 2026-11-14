"""Rename a Group's module key everywhere (flowgate.v02gui.0012 0001-B item B).

``rename_module(project_id, old, new)`` is what ``PATCH /projects/{pid}/modules/{name}`` runs when
the identifier changes. Every group of the module ``old`` becomes a group of ``new``:

- database: every stored form of the key (``db.module_rename.rewrite_database``) — group, sub-group
  and document ids, ``module`` columns, ``id_counter``, ``project_modules``, storage paths, branch
  names, ids inside JSON and text;
- files: the module directories under ``documents/{project}/{branch}/``, revision snapshots named
  after the doc id, front matter and the ids inside bodies, the legacy per-doc-id stores;
- Git: each group worktree is moved and its branch renamed (``flowgate_old_0012`` ->
  ``flowgate_new_0012``).

One transaction. While it is open every Flow of the module is guarded (no run, card work or
document save can start) and the database's path namespace is held. The new file trees are written
next to the old ones first; the old ones go only after the commit, and a failure anywhere removes
the new trees and moves Git back, so nothing is half renamed.

Refused (409, nothing changed) when ``new`` is already a key of the project
(``module_identifier_taken``, the details name that group), while anything is running in one of
the module's groups (``module_rename_busy``, the details list what and where), or when a branch /
worktree / directory of the new name is already there.

Not rewritten, because it cannot be: Git commit messages, text outside FlowGate (links, copies)
and branches already pushed to the remote (they keep the old name on origin).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import connection
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db.module_rename import (
    KeyRewriter, ModuleRenameRefused, defer_foreign_keys, module_in_use, restore_foreign_keys,
    rewrite_database,
)

_log = logging.getLogger(__name__)

__all__ = ["rename_module", "ModuleRenameRefused", "busy_reasons"]


# ── running work ───────────────────────────────────────────────────────────────────────────────

_RUN_BUSY = ("running", "paused", "stopped", "stopping")


def busy_reasons(project_id: str, group_ids: list[str]) -> list[dict]:
    """What runs in these groups now; empty when the rename may go ahead.

    Called again inside the guard, where nothing new can start in these Flows."""
    from modules.flow_gate.db import (
        ai_invoke_paused_chains, git_integration, group_ai_leases, operation_job, test_runs,
        tr_self_check_runs,
    )
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services.ai_invoke.chain import has_active_run

    store = get_store()
    reasons: list[dict] = []
    has_agent_group = store.table_exists("agent_jobs") and store.column_exists("agent_jobs", "ai_group_id")
    terminal = list(operation_job.TERMINAL_STATUSES)
    for gid in group_ids:
        run = run_store.active_run(gid)
        if run and str(run.get("status") or "") in _RUN_BUSY:
            reasons.append({"group_id": gid, "reason": "flow_run_active", "status": run.get("status")})
        if group_ai_leases.get_active(gid):
            reasons.append({"group_id": gid, "reason": "ai_lease_active"})
        if has_active_run(gid):
            reasons.append({"group_id": gid, "reason": "ai_run_active"})
        if ai_invoke_paused_chains.exists(gid):
            reasons.append({"group_id": gid, "reason": "ai_chain_paused"})
        if git_integration.get_open_session_by_group(gid):
            reasons.append({"group_id": gid, "reason": "merge_session_open"})
        if test_runs.get_active_by_group(gid):
            reasons.append({"group_id": gid, "reason": "test_run_active"})
        if tr_self_check_runs.get_active(project_id, gid):
            reasons.append({"group_id": gid, "reason": "self_check_active"})
        if store._fetch_one(
            "SELECT 1 AS hit FROM operation_job WHERE group_id = ? AND status NOT IN ("
            + ",".join("?" for _ in terminal) + ") LIMIT 1", [gid, *terminal]):
            reasons.append({"group_id": gid, "reason": "git_job_active"})
        if store._fetch_one(
            "SELECT 1 AS hit FROM chat_command_requests WHERE group_id = ?"
            " AND status IN ('pending_approval', 'approved', 'running') LIMIT 1", [gid]):
            reasons.append({"group_id": gid, "reason": "chat_command_active"})
        if has_agent_group and store._fetch_one(
            "SELECT 1 AS hit FROM agent_jobs WHERE ai_group_id = ? AND status IN ('NEW', 'CLAIMED', 'STARTED')"
            " LIMIT 1", [gid]):
            reasons.append({"group_id": gid, "reason": "agent_job_active"})
    return reasons


def _refuse_busy(reasons: list[dict]) -> None:
    if reasons:
        where = ", ".join(sorted({r["group_id"] for r in reasons}))
        raise ModuleRenameRefused(
            "module_rename_busy",
            f"Work is running in {where}. Wait until it ends (or stop it) and try again.",
            details={"busy": reasons})

# ── files ──────────────────────────────────────────────────────────────────────────────────────
# Copy-then-swap: the new trees are written first (renamed names, rewritten front matter/bodies)
# while the old trees stay untouched; the old trees are removed only after the database commit.
# A failure before the commit removes the new trees instead, so disk and database never disagree.

_TEXT_SUFFIXES = frozenset({".md", ".json", ".txt", ".yaml", ".yml"})


@dataclass
class _TreeMove:
    old: Path
    new: Path
    rewrite_content: bool  # document bodies/revisions yes; attachments (user uploads) never


def _is_document_text(rel: Path) -> bool:
    """A body or revision snapshot (rewritten) rather than an attachment (copied byte for byte).

    Inside a module directory bodies sit in ``{group}/[{subgroup}/]`` and snapshots in ``revisions/``;
    attachments sit in a ``{doc_code}/`` directory next to the body (attachments/locator.py)."""
    if rel.suffix.lower() not in _TEXT_SUFFIXES:
        return False
    return all(part.isdigit() or part == "revisions" for part in rel.parts[:-1])


def _plan_trees(project_id: str, rw: KeyRewriter, group_ids: list[str]) -> list[_TreeMove]:
    from modules.flow_gate import db as fg_db
    from modules.flow_gate.storage import paths as storage_paths

    moves: list[_TreeMove] = []
    root = storage_paths.get_storage_root(project_id)
    project_docs = root / "documents" / storage_paths.project_dir_name(project_id)
    if project_docs.is_dir():
        for branch_dir in sorted(p for p in project_docs.iterdir() if p.is_dir()):
            old_dir = branch_dir / rw.old
            if old_dir.is_dir():
                moves.append(_TreeMove(old_dir, branch_dir / rw.new, True))
            # Legacy layout without the branch segment (paths._apply_branch_segment reads it too).
        legacy_module = project_docs / rw.old
        if legacy_module.is_dir() and any(c.is_dir() and c.name.isdigit() for c in legacy_module.iterdir()):
            moves.append(_TreeMove(legacy_module, project_docs / rw.new, True))
    # Legacy attachment tree: one directory per full doc id (documents/attachments/legacy.py).
    legacy_attach = root / "projects" / storage_paths.project_dir_name(project_id) / "attachments"
    for parent in (legacy_attach, legacy_attach / "_migrated"):
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                renamed = rw.text(child.name)
                if renamed != child.name:
                    moves.append(_TreeMove(child, parent / renamed, False))
    # Legacy single-file stores whose file names carry the full doc id (process_service).
    for attr in ("TEST_REPORTS_DIR", "TEST_REPORTS_ARCHIVE_DIR", "DESIGN_REOPEN_DIR", "OUTBOX_DIR"):
        base = getattr(fg_db, attr, None)
        if base and Path(base).is_dir():
            for child in sorted(Path(base).iterdir()):
                renamed = rw.text(child.name)
                if child.is_file() and renamed != child.name:
                    moves.append(_TreeMove(child, Path(base) / renamed, True))
    return moves


def _rewrite_attempt_markers(project_id: str, rw: KeyRewriter, group_ids: list[str],
                             changed: list[tuple[Path, str]]) -> None:
    """Rewrite the ``owner.json`` of each Graph attempt workspace of these groups in place; returns (path, old text).

    An attempt workspace (``graph_attempts/{project}/{key}``, not keyed by module) records its owner's ``group_id``,
    and graph_run.workspace refuses a workspace whose marker disagrees with the run row (``owner_mismatch``). The
    caller restores the old texts on rollback; each one is recorded in ``changed`` before its file is written."""
    from modules.flow_gate.storage import paths as storage_paths

    base = storage_paths.get_storage_root(project_id) / "graph_attempts" / storage_paths.project_dir_name(project_id)
    wanted = set(group_ids)
    if not base.is_dir():
        return
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        marker = directory / "owner.json"
        if not marker.is_file():
            continue
        try:
            text = marker.read_text(encoding="utf-8")
            owner = json.loads(text)
        except (OSError, ValueError):
            continue
        if not isinstance(owner, dict) or owner.get("group_id") not in wanted:
            continue
        new_text = rw.text(text)
        if new_text != text:
            changed.append((marker, text))
            marker.write_text(new_text, encoding="utf-8")


def _restore_texts(items: list[tuple[Path, str]]) -> None:
    for path, text in reversed(items):
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            _log.error("module rename: could not restore %s", path, exc_info=True)


def _copy_rewritten(move: _TreeMove, rw: KeyRewriter) -> None:
    """Write ``move.new`` from ``move.old`` (names and, for document text, content rewritten)."""
    if move.old.is_file():
        _copy_file(move.old, move.new, rw, move.rewrite_content)
        return
    for current, dirs, files in os.walk(move.old):
        current_path = Path(current)
        rel_dir = current_path.relative_to(move.old)
        target_dir = move.new / Path(*[rw.text(part) for part in rel_dir.parts]) if rel_dir.parts else move.new
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            rel = rel_dir / name
            _copy_file(current_path / name, target_dir / rw.text(name), rw,
                       move.rewrite_content and _is_document_text(rel))


def _copy_file(src: Path, dst: Path, rw: KeyRewriter, rewrite: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if rewrite:
        raw = src.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            new_text = rw.text(text)
            if new_text != text:
                with open(dst, "xb") as fh:  # never overwrite: the target tree is ours alone
                    fh.write(new_text.encode("utf-8"))
                shutil.copystat(src, dst)
                return
    shutil.copy2(src, dst)


def _remove_tree(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        _log.warning("module rename: could not remove %s", path, exc_info=True)


# ── git ────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class _BranchMove:
    group_id: str
    old_branch: str
    new_branch: str
    old_path: Optional[Path]
    new_path: Optional[Path]
    moved_worktree: bool = False
    renamed_branch: bool = False


def _git_plan(project_id: str, rw: KeyRewriter, groups: list[dict]) -> tuple[Optional[Path], list[_BranchMove]]:
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git.worktree import worktree_branch_name
    from modules.flow_gate.services.git.config import _base_root_of
    from modules.flow_gate.services import git_service
    from modules.flow_gate.storage.paths import src_root

    plans: list[_BranchMove] = []
    base = _base_root_of(project_id)
    project_name = git_service._project_name(project_id)
    for group in groups:
        old_gid = group["group_id"]
        state = db_git.get_state(old_gid) or {}
        branch = (state.get("branch") or "").strip()
        try:
            derived_old = worktree_branch_name(project_id, rw.old, old_gid)
        except Exception:  # noqa: BLE001 — an underivable name has no branch to move
            continue
        if not branch:
            branch = derived_old
        elif branch != derived_old:
            continue  # a branch not named after the module does not carry the key
        new_branch = worktree_branch_name(project_id, rw.new, rw.text(old_gid))
        if new_branch == branch:
            continue
        old_path = src_root(project_name, branch) if project_name else None
        new_path = src_root(project_name, new_branch) if project_name else None
        plans.append(_BranchMove(old_gid, branch, new_branch, old_path, new_path))
    return base, plans


def _git(base: Path, args: list[str]):
    from modules.flow_gate.services.git.command import _run_git
    return _run_git(args, cwd=base)


def _ref_exists(base: Path, ref: str) -> bool:
    return _git(base, ["show-ref", "--verify", "--quiet", ref]).returncode == 0


def _check_git(base: Optional[Path], plans: list[_BranchMove]) -> None:
    if not plans:
        return
    blocked = []
    for plan in plans:
        if base is not None and base.is_dir():
            if _ref_exists(base, f"refs/heads/{plan.new_branch}"):
                blocked.append({"branch": plan.new_branch, "where": "local"})
            # A remote branch of that name would be adopted by the next provisioning
            # (worktree add --track -b {branch} origin/{branch}): the new group would start from the
            # old group's history.
            if _ref_exists(base, f"refs/remotes/origin/{plan.new_branch}"):
                blocked.append({"branch": plan.new_branch, "where": "origin"})
        if plan.new_path is not None and plan.new_path.exists():
            blocked.append({"branch": plan.new_branch, "where": "worktree", "path": str(plan.new_path)})
    if blocked:
        names = ", ".join(sorted({b["branch"] for b in blocked}))
        raise ModuleRenameRefused(
            "module_rename_branch_exists",
            f"Git branch or worktree already exists: {names}. Remove it first.",
            details={"branches": blocked})


def _apply_git(base: Optional[Path], plans: list[_BranchMove]) -> None:
    """Move each worktree, then rename its branch (git updates the worktree HEAD). Raises on failure."""
    if base is None or not base.is_dir():
        return
    for plan in plans:
        if plan.old_path is not None and plan.old_path.is_dir() and (plan.old_path / ".git").exists():
            plan.new_path.parent.mkdir(parents=True, exist_ok=True)
            proc = _git(base, ["worktree", "move", str(plan.old_path), str(plan.new_path)])
            if proc.returncode != 0:
                raise ModuleRenameRefused(
                    "module_rename_git_failed",
                    f"git worktree move failed for {plan.old_branch}: {(proc.stderr or '').strip()[:300]}",
                    status=500, details={"branch": plan.old_branch})
            plan.moved_worktree = True
        if _ref_exists(base, f"refs/heads/{plan.old_branch}"):
            proc = _git(base, ["branch", "-m", plan.old_branch, plan.new_branch])
            if proc.returncode != 0:
                raise ModuleRenameRefused(
                    "module_rename_git_failed",
                    f"git branch rename failed for {plan.old_branch}: {(proc.stderr or '').strip()[:300]}",
                    status=500, details={"branch": plan.old_branch})
            plan.renamed_branch = True
            # The upstream still names the old remote branch; the next push names the new one.
            _git(base, ["branch", "--unset-upstream", plan.new_branch])


def _revert_git(base: Optional[Path], plans: list[_BranchMove]) -> None:
    if base is None:
        return
    for plan in reversed(plans):
        try:
            if plan.renamed_branch:
                _git(base, ["branch", "-m", plan.new_branch, plan.old_branch])
            if plan.moved_worktree:
                _git(base, ["worktree", "move", str(plan.new_path), str(plan.old_path)])
        except Exception:  # noqa: BLE001 — best effort; logged for the operator
            _log.error("module rename: git revert failed for %s", plan.group_id, exc_info=True)


# ── the operation ──────────────────────────────────────────────────────────────────────────────

def rename_module(project_id: str, old: str, new: str, actor: Optional[str] = None) -> dict:
    """Rename module ``old`` of ``project_id`` to ``new``. Returns ``{"groups": {old_id: new_id}, ...}``."""
    from modules.flow_gate.db.module_identifiers import validate_identifier_format, ModuleIdentifierError
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from modules.flow_gate.storage import paths as storage_paths

    store = get_store()
    old = (old or "").strip()
    try:
        new = validate_identifier_format(new)
    except ModuleIdentifierError as exc:
        raise ModuleRenameRefused(exc.code, exc.message, exc.status) from exc
    if old in ("", "none", "__ALL__"):
        raise ModuleRenameRefused("module_identifier_reserved", "The 'All' group's identifier cannot be changed.", 422)
    if new == old:
        return {"groups": {}, "tables": {}, "files": 0, "branches": []}
    _refuse_taken(store, project_id, new)

    groups = store._fetch_all(
        "SELECT * FROM groups WHERE project_id = ? AND module = ? ORDER BY group_id", [project_id, old])
    group_ids = [g["group_id"] for g in groups]
    _refuse_busy(busy_reasons(project_id, group_ids))

    base, branch_plans = _git_plan(project_id, KeyRewriter(project_id, old, new, ""), groups)
    exact = {p.old_branch: p.new_branch for p in branch_plans}
    rw = KeyRewriter(project_id, old, new, storage_paths.project_dir_name(project_id), exact)
    renamed = {gid: rw.text(gid) for gid in group_ids}
    docs = store._fetch_all(
        "SELECT * FROM documents WHERE project_id = ? AND (module = ? OR group_id IN ("
        + (",".join("?" for _ in group_ids) or "NULL") + "))", [project_id, old, *group_ids])

    def body() -> dict:
        # Inside every Flow guard of the module, one transaction, the path namespace held.
        _refuse_taken(store, project_id, new)
        _refuse_busy(busy_reasons(project_id, group_ids))
        _check_git(base, branch_plans)
        moves = _plan_trees(project_id, rw, group_ids)
        occupied = [str(m.new) for m in moves if m.new.exists() or m.new.is_symlink()]
        if occupied:
            raise ModuleRenameRefused(
                "module_rename_path_exists",
                f"'{occupied[0]}' already exists; the new identifier would overwrite it.",
                details={"paths": occupied})
        written: list[Path] = []  # only paths this call created (checked free just above)
        connection.after_rollback(lambda: [_remove_tree(p) for p in reversed(written)])
        for move in moves:
            written.append(move.new)
            _copy_rewritten(move, rw)
        markers: list[tuple[Path, str]] = []
        connection.after_rollback(lambda: _restore_texts(markers))
        _rewrite_attempt_markers(project_id, rw, group_ids, markers)
        defer_foreign_keys(store)
        try:
            result = rewrite_database(store, rw)
        finally:
            restore_foreign_keys(store)
        connection.after_rollback(lambda: _revert_git(base, branch_plans))
        _apply_git(base, branch_plans)
        connection.after_commit(lambda: [_remove_tree(m.old) for m in moves])
        connection.after_commit(lambda: _forget_caches(project_id, group_ids))
        return {"groups": renamed, "tables": result["tables"], "files": len(moves),
                "branches": [{"from": p.old_branch, "to": p.new_branch} for p in branch_plans]}

    result = card_document_ports.write_gate("module_rename", group_ids, docs, body)
    _log.info("module rename %s: %s -> %s by %s (%d groups, %d tables)", project_id, old, new, actor,
              len(group_ids), len(result.get("tables") or {}))
    return result


def _refuse_taken(store, project_id: str, new: str) -> None:
    holder = module_in_use(store, project_id, new)
    if holder:
        title = holder.get("title") or new
        raise ModuleRenameRefused(
            "module_identifier_taken",
            f"Identifier '{new}' is used by the group '{title}'.",
            details={"holder": holder})


def _forget_caches(project_id: str, group_ids: list[str]) -> None:
    try:
        from modules.flow_gate.services.git.merge_target import invalidate_check_targets
        for gid in group_ids:
            invalidate_check_targets(project_id, gid)
    except Exception:  # noqa: BLE001 — a cache miss later is harmless
        _log.debug("module rename: cache invalidation skipped", exc_info=True)

