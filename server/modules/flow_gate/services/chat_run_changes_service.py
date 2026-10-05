"""FlowGate-computed source changes of one chat AI run (flowgate.default.0670 T0004).

NR0003 §10-§11: ``source_dirty_files`` (``now_paths - baseline``) cannot answer "what
did THIS run change" -- a file that was already dirty and got edited again vanishes
from it, and it carries no line counts. So a chat run snapshots its worktree content
into a Git tree object at admission and again at finalization, and the summary is
``git diff start_tree end_tree``:

* the snapshot is built in an isolated temporary index (``GIT_INDEX_FILE``, the same
  technique ``git_service._build_write_plan_tree`` uses), so the real index, HEAD and
  working tree are never touched and no repository lock is taken;
* ignored files are excluded exactly as Git excludes them, and the same hidden-path
  rule as the group change list applies, so the per-file diff view can open every
  listed path;
* the model never reports these numbers -- whatever its reply says, the card shows
  only what this module measured;
* zero changed files store nothing and draw nothing (NR0003 §11.4).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import ai_run_source_changes as db
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service

logger = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT_SEC = 120


def _git(root: Path, args: list[str], index_file: Optional[str] = None, timeout: int = SNAPSHOT_TIMEOUT_SEC):
    extra = {"GIT_INDEX_FILE": index_file} if index_file else None
    return git_service._run_git(args, cwd=root, timeout=timeout, extra_env=extra)


def capture_tree(root: Optional[Path | str]) -> Optional[str]:
    """Tree oid of the worktree's current content (tracked + untracked, not ignored).

    Never raises: a run must not fail because its change summary could not be prepared.
    """
    if not root:
        return None
    root = Path(root)
    if not root.is_dir():
        return None
    tmpdir = None
    try:
        probe = _git(root, ["rev-parse", "--is-inside-work-tree"], timeout=git_service.GIT_READ_TIMEOUT_SEC)
        if probe.returncode != 0 or (probe.stdout or "").strip() != "true":
            return None
        tmpdir = tempfile.mkdtemp(prefix="flowgate-run-tree-")
        index_file = os.path.join(tmpdir, "index")
        # Seeded from HEAD's tree, NOT from a copy of the real index: read-tree entries
        # carry no stat data, so `git add` hashes every file's content. Reusing the real
        # index's stat cache would let a same-size edit made within the same timestamp
        # tick look unchanged ("racy git") and silently drop it from the summary.
        head = _git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], timeout=git_service.GIT_READ_TIMEOUT_SEC)
        if head.returncode == 0:
            seed = _git(root, ["read-tree", "HEAD"], index_file=index_file)
            if seed.returncode != 0:
                return None
        add = _git(root, ["add", "-A", "--", "."], index_file=index_file)
        if add.returncode != 0:
            logger.warning("run tree snapshot add failed in %s: %s", root, (add.stderr or "")[:200])
            return None
        tree = _git(root, ["write-tree"], index_file=index_file)
        if tree.returncode != 0:
            return None
        oid = (tree.stdout or "").strip()
        return oid or None
    except Exception:
        logger.warning("run tree snapshot failed in %s", root, exc_info=True)
        return None
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def diff_trees(root: Path, start_tree: str, end_tree: str) -> list[dict]:
    """[{path, status, insertions, deletions}] between two snapshots (renames split)."""
    if not start_tree or not end_tree or start_tree == end_tree:
        return []
    names = _git(root, ["diff", "--name-status", "--no-renames", "-z", start_tree, end_tree, "--"],
                 timeout=git_service.GIT_READ_TIMEOUT_SEC)
    if names.returncode != 0:
        raise git_service.GitServiceError(500, "git_error", "run diff failed")
    numstat = _git(root, ["diff", "--numstat", "--no-renames", "-z", start_tree, end_tree, "--"],
                   timeout=git_service.GIT_READ_TIMEOUT_SEC)
    stats: dict[str, tuple[Optional[int], Optional[int]]] = {}
    if numstat.returncode == 0:
        for record in (numstat.stdout or "").split("\0"):
            added, sep, rest = record.partition("\t")
            deleted, sep2, path = rest.partition("\t")
            if sep and sep2 and path:
                # Binary files report "-": unknown, never 0 (same rule as GroupChangeData).
                stats[path] = (int(added) if added.isdigit() else None,
                               int(deleted) if deleted.isdigit() else None)
    fields = (names.stdout or "").split("\0")
    files: list[dict] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index], fields[index + 1]
        if not status or not path or git_service._is_hidden_source_path(path):
            continue
        insertions, deletions = stats.get(path, (None, None))
        files.append({"path": path, "status": status[:1], "insertions": insertions, "deletions": deletions})
    return files


def _totals(files: list[dict]) -> tuple[Optional[int], Optional[int]]:
    known = [f for f in files if f.get("insertions") is not None and f.get("deletions") is not None]
    if not known:
        return None, None
    return sum(f["insertions"] for f in known), sum(f["deletions"] for f in known)


def start_snapshot(action_scope: Optional[str], source_root) -> Optional[str]:
    """Admission hook: only chat runs get a start tree."""
    if action_scope != "chat":
        return None
    return capture_tree(source_root)


def finalize_run(run: dict) -> Optional[dict]:
    """Finalize hook: store and announce the summary when anything changed."""
    if run.get("action_scope") != "chat":
        return None
    start_tree = run.get("source_start_tree")
    root = run.get("source_root")
    if not start_tree or not root or not run.get("doc_ref"):
        return None
    end_tree = capture_tree(root)
    run["source_end_tree"] = end_tree
    if not end_tree or end_tree == start_tree:
        return None
    files = diff_trees(Path(root), start_tree, end_tree)
    if not files:
        return None
    insertions, deletions = _totals(files)
    row = db.upsert(
        run_id=run["run_id"], doc_id=run["doc_ref"], project_id=run["project_id"],
        group_id=run["group_id"], start_tree=start_tree, end_tree=end_tree,
        run_started_at=run.get("started_at"), run_finished_at=run.get("finished_at") or now_iso(),
        files=files, insertions=insertions, deletions=deletions,
    )
    git_service._emit("chat_run_changes", run["project_id"], run["group_id"], {
        "project_id": run["project_id"], "group_id": run["group_id"], "doc_id": run["doc_ref"],
        "run_id": run["run_id"], "files_changed": len(files),
    })
    return row


def public(row: dict) -> dict:
    return {
        "run_id": row["run_id"], "doc_id": row["doc_id"],
        "run_started_at": row.get("run_started_at"), "run_finished_at": row.get("run_finished_at"),
        "files_changed": row["files_changed"], "insertions": row.get("insertions"),
        "deletions": row.get("deletions"), "files": row.get("files") or [],
        "start_tree": row["start_tree"], "end_tree": row["end_tree"],
    }


def list_for_doc(doc_id: str) -> list[dict]:
    return [public(row) for row in db.list_for_doc(doc_id)]


def file_diff(doc_id: str, run_id: str, path: str) -> dict:
    """old (run start) / new (run end) content of one listed path.

    Same payload shape as ``git_service.read_group_file_diff`` so the existing diff
    viewer renders it unchanged. Only paths in the stored summary are readable.
    """
    row = db.get(run_id)
    if row is None or row["doc_id"] != doc_id:
        raise git_service.GitServiceError(404, "not_found", "run change summary not found")
    git_service._validate_blob_path(path)
    normalized = path.replace("\\", "/")
    entry = next((f for f in row.get("files") or [] if f.get("path") == normalized), None)
    if entry is None:
        raise git_service.GitServiceError(404, "not_found", f"path '{path}' is not part of this run's changes")
    root, _reason = git_service.effective_src_root_ex(row["project_id"], row["group_id"])
    if not root:
        raise git_service.GitServiceError(409, "invalid_state", "group worktree is not available")
    root = Path(root)
    old = git_service._diff_side_from_commit(root, row["start_tree"], normalized)
    new = git_service._diff_side_from_commit(root, row["end_tree"], normalized)
    return {"ok": True, "data": {
        "group_id": row["group_id"], "branch": None, "base_branch": None,
        "commit": row["end_tree"], "merge_base": row["start_tree"], "path": normalized,
        "status": git_service._diff_status(old, new, normalized), "old": old, "new": new,
    }}
