"""What one chat AI run changed: start/end tree snapshots, diff, file detail (D0006 §3.8).

Run changes are not Group changes. Group changes compare the group's scope floor with
the current worktree; a run change compares the worktree content when ONE chat run
started with the content when it ended:

* the snapshot is a Git tree written from an isolated temporary index
  (``GIT_INDEX_FILE``) seeded from HEAD, so the real index, HEAD and working tree are
  never touched and no repository lock is taken. Tracked and untracked files are
  included, ignored files are not;
* at most one row per run, written at finalization when the trees differ -- also for a
  failed, cancelled or timed-out run. Changes made after the run ended belong to the
  next run's start tree, never to this run;
* a group without a managed worktree, or a snapshot that fails, never fails the chat
  run: the run change is simply not recorded, a warning is logged and the run record
  carries ``source_change_capture = unavailable`` with the reason (D0006 §3.8.1).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from flowgate.conversation import activity
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter
from flowgate.conversation import repository as repo

logger = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT_SEC = 120

CAPTURE_REASON_NO_WORKTREE = "worktree_unavailable"
CAPTURE_REASON_ROOT_MISMATCH = "worktree_mismatch"
CAPTURE_REASON_START_FAILED = "start_snapshot_failed"
CAPTURE_REASON_END_FAILED = "end_snapshot_failed"
CAPTURE_REASON_DIFF_FAILED = "diff_failed"


class SnapshotError(RuntimeError):
    pass


def _git(root: Path, args: list[str], index_file: Optional[str] = None, timeout: int = SNAPSHOT_TIMEOUT_SEC):
    return legacy_adapter.run_git(root, args, timeout=timeout, index_file=index_file)


def capture_tree(root: Path | str) -> str:
    """Tree oid of the worktree's current content (tracked + untracked, not ignored).

    Raises :class:`SnapshotError` when the tree cannot be written.
    """
    root = Path(root)
    if not root.is_dir():
        raise SnapshotError(f"worktree {root} is not a directory")
    read_timeout = legacy_adapter.git_read_timeout_sec()
    probe = _git(root, ["rev-parse", "--is-inside-work-tree"], timeout=read_timeout)
    if probe.returncode != 0 or (probe.stdout or "").strip() != "true":
        raise SnapshotError("not a git work tree")
    tmpdir = tempfile.mkdtemp(prefix="flowgate-run-tree-")
    try:
        index_file = os.path.join(tmpdir, "index")
        # Seeded from HEAD's tree, NOT from a copy of the real index: read-tree entries
        # carry no stat data, so `git add` hashes every file's content and a same-size
        # edit within one timestamp tick cannot look unchanged ("racy git").
        head = _git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], timeout=read_timeout)
        if head.returncode == 0:
            seed = _git(root, ["read-tree", "HEAD"], index_file=index_file)
            if seed.returncode != 0:
                raise SnapshotError(f"read-tree failed: {(seed.stderr or '')[:200]}")
        add = _git(root, ["add", "-A", "--", "."], index_file=index_file)
        if add.returncode != 0:
            raise SnapshotError(f"add failed: {(add.stderr or '')[:200]}")
        tree = _git(root, ["write-tree"], index_file=index_file)
        oid = (tree.stdout or "").strip()
        if tree.returncode != 0 or not oid:
            raise SnapshotError(f"write-tree failed: {(tree.stderr or '')[:200]}")
        return oid
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def diff_trees(root: Path, start_tree: str, end_tree: str) -> list[dict]:
    """[{path, status, insertions, deletions}] between two snapshots, renames split.

    Binary files have no line counts (``None``, never 0).
    """
    if not start_tree or not end_tree or start_tree == end_tree:
        return []
    read_timeout = legacy_adapter.git_read_timeout_sec()
    names = _git(root, ["diff", "--name-status", "--no-renames", "-z", start_tree, end_tree, "--"],
                 timeout=read_timeout)
    if names.returncode != 0:
        raise SnapshotError(f"diff failed: {(names.stderr or '')[:200]}")
    numstat = _git(root, ["diff", "--numstat", "--no-renames", "-z", start_tree, end_tree, "--"],
                   timeout=read_timeout)
    stats: dict[str, tuple[Optional[int], Optional[int]]] = {}
    if numstat.returncode == 0:
        for record in (numstat.stdout or "").split("\0"):
            added, sep, rest = record.partition("\t")
            deleted, sep2, path = rest.partition("\t")
            if sep and sep2 and path:
                stats[path] = (int(added) if added.isdigit() else None,
                               int(deleted) if deleted.isdigit() else None)
    fields = (names.stdout or "").split("\0")
    files: list[dict] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index], fields[index + 1]
        if not status or not path or legacy_adapter.is_hidden_source_path(path):
            continue
        insertions, deletions = stats.get(path, (None, None))
        files.append({"path": path, "status": status[:1], "insertions": insertions, "deletions": deletions})
    return files


def totals(files: list[dict]) -> tuple[Optional[int], Optional[int]]:
    known = [f for f in files if f.get("insertions") is not None and f.get("deletions") is not None]
    if not known:
        return None, None
    return sum(f["insertions"] for f in known), sum(f["deletions"] for f in known)


def _mark_unavailable(run: dict, reason: str, detail: Optional[str] = None) -> None:
    run["source_change_capture"] = c.CAPTURE_UNAVAILABLE
    run["source_change_capture_reason"] = reason if not detail else f"{reason}: {detail}"[:300]


def managed_worktree(run: dict) -> Optional[Path]:
    """The group's managed worktree when it is the root the run works in, else None."""
    root, reason = legacy_adapter.effective_src_root_ex(run.get("project_id"), run.get("group_id"))
    if reason != legacy_adapter.WORKTREE_REASON or not root:
        return None
    source_root = run.get("source_root")
    if source_root:
        try:
            if Path(source_root).resolve() != Path(root).resolve():
                return None
        except OSError:
            return None
    return Path(root)


def start_snapshot(run: dict) -> Optional[str]:
    """Admission: record the run's start tree. Never raises and never refuses the run."""
    if run.get("action_scope") != "chat":
        return None
    try:
        root = managed_worktree(run)
    except Exception as exc:
        logger.warning("chat run %s: worktree lookup failed; run change disabled", run.get("run_id"),
                       exc_info=True)
        _mark_unavailable(run, CAPTURE_REASON_NO_WORKTREE, str(exc)[:200])
        return None
    if root is None:
        _mark_unavailable(run, CAPTURE_REASON_NO_WORKTREE)
        return None
    try:
        tree = capture_tree(root)
    except Exception as exc:
        logger.warning("chat run %s: start snapshot failed; run change disabled: %s",
                       run.get("run_id"), exc)
        _mark_unavailable(run, CAPTURE_REASON_START_FAILED, str(exc)[:200])
        return None
    run["source_start_tree"] = tree
    run["source_change_root"] = str(root)
    run["source_change_capture"] = c.CAPTURE_READY
    run["source_change_capture_reason"] = None
    return tree


def finalize_run(run: dict) -> Optional[dict]:
    """Finalization: store (and announce) the run change when anything changed."""
    if run.get("action_scope") != "chat":
        return None
    start_tree = run.get("source_start_tree")
    root = run.get("source_change_root")
    if not start_tree or not root or not run.get("doc_ref"):
        return None
    try:
        end_tree = capture_tree(root)
    except Exception as exc:
        logger.warning("chat run %s: end snapshot failed; run change skipped: %s", run.get("run_id"), exc)
        _mark_unavailable(run, CAPTURE_REASON_END_FAILED, str(exc)[:200])
        return None
    run["source_end_tree"] = end_tree
    if end_tree == start_tree:
        return None
    try:
        files = diff_trees(Path(root), start_tree, end_tree)
    except Exception as exc:
        logger.warning("chat run %s: run diff failed; run change skipped: %s", run.get("run_id"), exc)
        _mark_unavailable(run, CAPTURE_REASON_DIFF_FAILED, str(exc)[:200])
        return None
    if not files:
        return None
    insertions, deletions = totals(files)
    row = repo.upsert_run_change(
        run_id=run["run_id"], doc_id=run["doc_ref"], project_id=run["project_id"],
        group_id=run["group_id"], start_tree=start_tree, end_tree=end_tree,
        run_started_at=run.get("started_at"), run_finished_at=run.get("finished_at") or _now_iso(),
        files=files, insertions=insertions, deletions=deletions,
        run_start_seq=run.get("chat_start_seq"),
    )
    try:
        activity.resolve_change(run["doc_ref"], run["run_id"])
        row = repo.get_run_change(run["run_id"]) or row
    except Exception:
        logger.warning("chat run change anchor resolve failed for %s", run.get("run_id"), exc_info=True)
    legacy_adapter.emit_event(c.EVENT_RUN_CHANGES, run["project_id"], run["group_id"], {
        "project_id": run["project_id"], "group_id": run["group_id"], "doc_id": run["doc_ref"],
        "run_id": run["run_id"], "files_changed": len(files),
    })
    return row


def _now_iso() -> str:
    from modules.flow_gate.db.connection import now_iso

    return now_iso()


def public(row: dict) -> dict:
    return {
        "run_id": row["run_id"], "doc_id": row["doc_id"],
        "run_started_at": row.get("run_started_at"), "run_finished_at": row.get("run_finished_at"),
        "files_changed": row["files_changed"], "insertions": row.get("insertions"),
        "deletions": row.get("deletions"), "files": row.get("files") or [],
        "start_tree": row["start_tree"], "end_tree": row["end_tree"],
        "created_at": row.get("created_at"),
        "anchor_seq": row.get("anchor_seq"), "anchor_position": row.get("anchor_position"),
        "anchor_state": row.get("anchor_state"),
    }


def file_diff(doc_id: str, run_id: str, path: str) -> dict:
    """Run-start / run-end content of one listed path, from the STORED trees (D0006 §3.8.3).

    Same payload shape as the Group changes file diff so the existing viewer renders it.
    Only paths in the stored summary are readable; the current worktree is never used to
    reconstruct a past result.
    """
    row = repo.get_run_change(run_id)
    if row is None or row["doc_id"] != doc_id:
        raise c.ConversationError(404, c.ERR_RUN_CHANGE_NOT_FOUND, "run change not found")
    try:
        legacy_adapter.validate_blob_path(path)
    except legacy_adapter.git_service_error_type() as exc:
        raise c.ConversationError(getattr(exc, "status", 400), getattr(exc, "code", "invalid_path"),
                                  getattr(exc, "message", str(exc))) from exc
    normalized = path.replace("\\", "/")
    if not any(f.get("path") == normalized for f in row.get("files") or []):
        raise c.ConversationError(404, c.ERR_RUN_CHANGE_PATH_NOT_FOUND,
                                  f"path '{path}' is not part of this run's changes")
    root, reason = legacy_adapter.effective_src_root_ex(row["project_id"], row["group_id"])
    if reason != legacy_adapter.WORKTREE_REASON or not root:
        raise c.ConversationError(409, c.ERR_WORKTREE_UNAVAILABLE, "group worktree is not available")
    try:
        old = legacy_adapter.diff_side_from_tree(Path(root), row["start_tree"], normalized)
        new = legacy_adapter.diff_side_from_tree(Path(root), row["end_tree"], normalized)
        status = legacy_adapter.diff_status(old, new, normalized)
    except legacy_adapter.git_service_error_type() as exc:
        raise c.ConversationError(getattr(exc, "status", 500), getattr(exc, "code", "git_error"),
                                  getattr(exc, "message", str(exc))) from exc
    return {"ok": True, "data": {
        "group_id": row["group_id"], "run_id": row["run_id"], "branch": None, "base_branch": None,
        "commit": row["end_tree"], "merge_base": row["start_tree"], "path": normalized,
        "status": status, "old": old, "new": new,
    }}
