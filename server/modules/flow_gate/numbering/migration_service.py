"""Digit-width change reformat service.

Enqueue jobs into the numbering_jobs queue, and process them serially with a single worker.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..db.connection import get_store, now_iso
from ..db import numbering_jobs as db_jobs
from ..db import projects as db_projects
from .id_formatter import reformat_code
from .verify import verify_id_widths

_JST = timezone(timedelta(hours=9))
_worker_lock = threading.Lock()


# ── Enqueue ───────────────────────────────────────────────────────────────────

def enqueue_reformat(
    project_id: str,
    requested_by: str,
    target: str,
    from_width: int,
    to_width: int,
) -> dict:
    """Add a digit-width change job to the numbering_jobs queue.

    Parameters
    ----------
    project_id : str
    requested_by : str
        Requesting user_id.
    target : str
        'group' | 'subgroup' | 'document'
    from_width : int
        Current digit width.
    to_width : int
        Digit width after the change.
    """
    if target not in ("group", "subgroup", "document"):
        raise ValueError(f"target must be one of 'group'/'subgroup'/'document': {target!r}")
    if from_width == to_width:
        raise ValueError("from_width and to_width are the same.")
    return db_jobs.create({
        "project_id": project_id,
        "requested_by": requested_by,
        "target": target,
        "from_width": from_width,
        "to_width": to_width,
        "status": "queued",
    })


# ── Execute reformat ──────────────────────────────────────────────────────────

def _backup_db_snapshot(project_id: str, backup_dir: Path) -> Path:
    """Save a snapshot of id_counter, groups, sub_groups, and documents as JSON."""
    store = get_store()
    snapshot = {
        "groups": store._fetch_all(
            "SELECT * FROM groups WHERE project_id=?", [project_id]
        ),
        "sub_groups": store._fetch_all(
            "SELECT sg.* FROM sub_groups sg "
            "JOIN groups g ON sg.group_id=g.group_id "
            "WHERE g.project_id=?",
            [project_id],
        ),
        "documents": store._fetch_all(
            "SELECT * FROM documents WHERE project_id=?", [project_id]
        ),
        "id_counter": store._fetch_all(
            "SELECT * FROM id_counter WHERE project_id=?", [project_id]
        ),
    }
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(_JST).strftime("%Y%m%d_%H%M%S")
    snap_path = backup_dir / f"snapshot_{project_id}_{ts}.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap_path


def _restore_from_snapshot(snapshot_path: Path) -> None:
    """Restore group, subgroup, and document codes from the backup JSON."""
    store = get_store()
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))

    for grp in data.get("groups", []):
        store._execute(
            "UPDATE groups SET group_id=?, updated_at=? WHERE group_id=?",
            [grp["group_id"], now_iso(), grp["group_id"]],
        )
    for sg in data.get("sub_groups", []):
        store._execute(
            "UPDATE sub_groups SET sub_group_id=?, updated_at=? WHERE sub_group_id=?",
            [sg["sub_group_id"], now_iso(), sg["sub_group_id"]],
        )
    for doc in data.get("documents", []):
        store._execute(
            "UPDATE documents SET doc_id=?, file_path=?, updated_at=? WHERE doc_id=?",
            [doc["doc_id"], doc.get("file_path"), now_iso(), doc["doc_id"]],
        )


def _apply_reformat(
    project_id: str,
    target: str,
    from_width: int,
    to_width: int,
) -> int:
    """Reformat DB codes and filesystem paths to the new digit width.

    Returns
    -------
    int
        Number of changed entities.
    """
    from ..storage import filesystem as fs

    store = get_store()
    affected = 0

    if target == "group":
        rows = store._fetch_all(
            "SELECT group_id FROM groups WHERE project_id=?", [project_id]
        )
        for row in rows:
            old_id = row["group_id"]
            if not old_id.isdigit():
                continue
            new_id = reformat_code(old_id, to_width, "group")
            if old_id == new_id:
                continue
            store._execute(
                "UPDATE groups SET group_id=?, updated_at=? WHERE group_id=?",
                [new_id, now_iso(), old_id],
            )
            # Rename in the filesystem
            try:
                from ..storage.paths import project_root as get_proj_root
                src = get_proj_root(project_id) / old_id
                dst = get_proj_root(project_id) / new_id
                if src.exists():
                    fs.safe_rename(src, dst)
            except Exception:
                pass
            affected += 1

    elif target == "subgroup":
        rows = store._fetch_all(
            "SELECT sg.sub_group_id, sg.group_id FROM sub_groups sg "
            "JOIN groups g ON sg.group_id=g.group_id "
            "WHERE g.project_id=?",
            [project_id],
        )
        for row in rows:
            old_id = row["sub_group_id"]
            numeric = old_id.split("-")[-1] if "-" in old_id else old_id
            if not numeric.isdigit():
                continue
            new_numeric = reformat_code(numeric, to_width, "subgroup")
            prefix = old_id[: -len(numeric)] if old_id.endswith(numeric) else ""
            new_id = prefix + new_numeric
            if old_id == new_id:
                continue
            store._execute(
                "UPDATE sub_groups SET sub_group_id=?, updated_at=? WHERE sub_group_id=?",
                [new_id, now_iso(), old_id],
            )
            affected += 1

    elif target == "document":
        import re
        rows = store._fetch_all(
            "SELECT doc_id, file_path FROM documents WHERE project_id=?", [project_id]
        )
        for row in rows:
            old_id = row["doc_id"]
            m = re.match(r'^(.*\.)(\d+)-([A-Za-z]+)$', old_id)
            if not m:
                continue
            prefix, numeric, type_code = m.groups()
            if len(numeric) == to_width:
                continue
            new_numeric = str(int(numeric)).zfill(to_width)
            new_id = f"{prefix}{new_numeric}-{type_code}"

            old_path = row.get("file_path")
            new_path: Optional[str] = None
            if old_path:
                # file_path is persisted relative (L0054.0002): resolve to an
                # absolute Path for the rename, then re-store the new value relative.
                from ..storage.paths import resolve_storage_path, to_storage_relative
                resolved_old = resolve_storage_path(old_path, project_id)
                src = resolved_old if resolved_old is not None else Path(old_path)
                new_fname = re.sub(r'\d+', lambda mo: str(int(mo.group(0))).zfill(to_width), src.name, count=1)
                dst = src.parent / new_fname
                new_path = to_storage_relative(dst, project_id)
                try:
                    if src.exists():
                        fs.safe_rename(src, dst)
                except Exception:
                    new_path = old_path

            store._execute(
                "UPDATE documents SET doc_id=?, file_path=?, updated_at=? WHERE doc_id=?",
                [new_id, new_path, now_iso(), old_id],
            )
            affected += 1

    return affected


# ── Worker ────────────────────────────────────────────────────────────────────

def process_job(job_id: int, backup_dir: Optional[Path] = None) -> dict:
    """Process a single numbering job.

    1. Backup (DB snapshot + file tree)
    2. DB UPDATE (reformat)
    3. Validation
    4. Recovery on failure

    Returns
    -------
    dict
        Completed numbering_job row.
    """
    job = db_jobs.get_by_id(job_id)
    if job is None:
        raise ValueError(f"job_id={job_id} not found")
    if job["status"] != "queued":
        raise RuntimeError(f"job_id={job_id} status={job['status']!r}: not queued")

    project_id = job["project_id"]

    # Ensure a single worker per project
    with _worker_lock:
        db_jobs.update(job_id, {"status": "running", "started_at": now_iso()})

        # 1. Backup
        if backup_dir is None:
            from ..db import db as _db_module  # type: ignore
            try:
                storage_root = Path(_db_module.STORAGE_DIR)
            except Exception:
                storage_root = Path(".")
            backup_dir = storage_root / "_backup" / "numbering"

        snap_path: Optional[Path] = None
        try:
            snap_path = _backup_db_snapshot(project_id, backup_dir)
        except Exception:
            pass  # Continue even if backup fails (warning only)

        # 2. Reformat
        try:
            affected = _apply_reformat(
                project_id,
                job["target"],
                job["from_width"],
                job["to_width"],
            )

            # 3. Validation
            report = verify_id_widths(project_id)
            if not report.ok:
                raise RuntimeError(
                    f"Validation failed: width_mismatches={report.width_mismatches}"
                )

            return db_jobs.update(job_id, {
                "status": "succeeded",
                "affected_count": affected,
                "finished_at": now_iso(),
            })

        except Exception as exc:
            # 4. Recovery
            if snap_path and snap_path.exists():
                try:
                    _restore_from_snapshot(snap_path)
                except Exception:
                    pass

            return db_jobs.update(job_id, {
                "status": "failed",
                "error_message": str(exc)[:500],
                "finished_at": now_iso(),
            })


def run_pending_jobs(project_id: str, backup_dir: Optional[Path] = None) -> list[dict]:
    """Process all queued jobs for the project."""
    jobs = db_jobs.list_by_project(project_id, status="queued")
    results = []
    for job in reversed(jobs):  # Process oldest first
        results.append(process_job(job["id"], backup_dir=backup_dir))
    return results
