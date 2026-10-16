"""New document files and shared identity checks (0064 T-B1a, L0005 2.2/2.3)."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from modules.flow_gate.db.connection import get_store, track_new_file
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from .paths import storage_path_candidates

_log = logging.getLogger(__name__)
CLEANUP_RETRY_MAX = 0
ORPHAN_SUFFIX = ".orphan-{utc_ms}"
TEMP_SUFFIX = ".tmp-{pid}-{utc_ms}"
_name_lock = threading.Lock()
_last_ms = 0


class StorageUnwritable(GraphApiError):
    def __init__(self, cause="write_failed", exception=None):
        super().__init__(503, "document_storage_unwritable", {"cause": cause})
        self.exception = exception or type(self).__name__


def same_file(a, b) -> bool:
    """Compare file identities, falling back to normalized resolved paths when missing."""
    a, b = Path(a), Path(b)
    if a.exists() and b.exists():
        return os.path.samefile(a, b)
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def referenced_by_any_document(abs_path, exclude_doc_ids=()) -> bool:
    """All projects/statuses, using each row's project and branch, including outside-jail paths."""
    path = Path(abs_path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        return True
    name = path.name.casefold()
    excluded = set(exclude_doc_ids)
    rows = get_store()._fetch_all(
        "SELECT doc_id, project_id, branch, file_path FROM documents "
        "WHERE file_path IS NOT NULL AND TRIM(file_path) <> ''")
    for row in rows:
        stored = row["file_path"].strip().replace("\\", "/")
        if row["doc_id"] in excluded or stored.rsplit("/", 1)[-1].casefold() != name:
            continue
        for candidate, _inside in storage_path_candidates(stored, row["project_id"], row.get("branch") or "main"):
            if same_file(candidate, path):
                return True
    return False


def _side_path(path: Path, suffix: str) -> Path:
    global _last_ms
    with _name_lock:
        stamp = max(time.time_ns() // 1_000_000, _last_ms + 1)
        candidate = Path(str(path) + suffix.format(pid=os.getpid(), utc_ms=stamp))
        while os.path.lexists(candidate):
            stamp += 1
            candidate = Path(str(path) + suffix.format(pid=os.getpid(), utc_ms=stamp))
        _last_ms = stamp
        return candidate


def write_new_file(abs_path, data: bytes) -> None:
    """Preserve unreferenced orphans, write bytes atomically, and track the new file for rollback.

    Call inside the outer transaction, never inside a savepoint. Existing referenced
    files (including links) are refused without any movement or byte changes.
    """
    path, tmp = Path(abs_path), None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            if referenced_by_any_document(path):
                raise StorageUnwritable("target_in_use")
            orphan = _side_path(path, ORPHAN_SUFFIX)
            path.rename(orphan)
            _log.warning("orphan moved aside: %s -> %s", path, orphan)
        tmp = _side_path(path, TEMP_SUFFIX)
        with tmp.open("xb") as stream:
            stream.write(data)
        tmp.replace(path)
        track_new_file(path)
    except OSError as exc:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                _log.warning("temporary file cleanup failed: %s", tmp, exc_info=True)
        raise StorageUnwritable(exception=type(exc).__name__) from exc


def overwrite_file(abs_path, data: bytes) -> None:
    """Replace an existing document atomically, preserving submitted bytes exactly."""
    path = Path(abs_path)
    tmp = _side_path(path, TEMP_SUFFIX)
    try:
        with tmp.open("xb") as stream:
            stream.write(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
