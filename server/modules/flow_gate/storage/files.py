"""New document files and shared identity checks (0064 T-B1a, L0005 2.2/2.3).

0064 T#3 (L0005 2.6 / 2.7.1): a file is removed by the transaction that made it (rollback) or that let it go
(commit) only while its path still holds that very file (``file_identity``), and only under the durable lock the
next writer of that path takes (``cleanup_runner``). The transaction's own lock is gone by the time its callbacks
run, so another server process may already have moved the file aside and staged its own at the same path.
"""
from __future__ import annotations

import logging
import os
import stat
import threading
import time
from pathlib import Path
from typing import Optional

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
    return document_using_file(abs_path, exclude_doc_ids) is not None


def document_using_file(abs_path, exclude_doc_ids=()) -> Optional[dict]:
    """``{doc_id, project_id}`` of the row that uses this very file (L0005 2.3.1), or None.

    A link is in use without a row to name (0064 T-B1d, P0006 §2.7 ``used_by_doc_id``): both values are None.
    """
    path = Path(abs_path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        return {"doc_id": None, "project_id": None}
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
                return {"doc_id": row["doc_id"], "project_id": row["project_id"]}
    return None


def file_identity(abs_path) -> Optional[tuple]:
    """``(st_dev, st_ino, st_size, st_mtime_ns)`` of the regular file at the path; None when absent or not a file.

    A writer that moves a file aside (``write_new_file``) keeps its inode, and the file it stages has a new one, so
    the identity tells "the file I made / read" from "a file someone put at the same path later".
    """
    try:
        st = os.lstat(abs_path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def is_same_file_as(abs_path, identity: Optional[tuple]) -> bool:
    """Whether the path still holds the file ``identity`` was taken of (a shorter identity compares its prefix)."""
    current = file_identity(abs_path)
    return identity is not None and current is not None and current[: len(identity)] == tuple(identity)


def remove_owned_file(abs_path, identity: Optional[tuple]) -> bool:
    """Unlink the path only while it is still the very file of ``identity``; anything else stays. True: removed."""
    path = Path(abs_path)
    if not is_same_file_as(path, identity):
        if os.path.lexists(path):
            _log.info("file kept, no longer the one this transaction owned: %s", path)
        return False
    path.unlink(missing_ok=True)
    return True


def cleanup_runner(what: str):
    """How a cleanup queued now must run later: under the durable lock of the guard this thread holds.

    Returns ``None`` outside a guard (the cleanup runs inline, identity-checked). See
    ``flow_guard.run_cleanup``: a fresh transaction whose first statement takes that guard's durable lock again.
    """
    from modules.flow_gate.graph_workflow.services import flow_guard

    key = flow_guard.held_guard()
    if key is None:
        return None
    return lambda body: flow_guard.run_cleanup(key, body, what)


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


def write_new_file(abs_path, data: bytes, exclude_doc_ids=()) -> None:
    """Preserve unreferenced orphans, write bytes atomically, and track the new file for rollback.

    Call inside the outer transaction, never inside a savepoint. Existing referenced
    files (including links) are refused without any movement or byte changes. The rollback
    removes the file only while the path still holds it, under the guard's durable lock
    (0064 T#3): a retry in another process that moved it aside keeps its own file.
    ``exclude_doc_ids``: the rows of this very write, already pointed at the path in the same
    transaction (0064 T-B1d recovery updates the row first, L0005 2.12); they do not make it in use.
    A caller that names its rows so has every other row checked even while the path holds no file: a
    row pointing at a missing file still uses that path. Without them (a caller whose row may point at
    the path already) only an existing file is checked, as before.
    """
    path, tmp = Path(abs_path), None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            if referenced_by_any_document(path, exclude_doc_ids):
                raise StorageUnwritable("target_in_use")
            orphan = _side_path(path, ORPHAN_SUFFIX)
            path.rename(orphan)
            _log.warning("orphan moved aside: %s -> %s", path, orphan)
        elif exclude_doc_ids and referenced_by_any_document(path, exclude_doc_ids):
            raise StorageUnwritable("target_in_use")
        tmp = _side_path(path, TEMP_SUFFIX)
        with tmp.open("xb") as stream:
            stream.write(data)
        tmp.replace(path)
        identity = file_identity(path)
        track_new_file(path, still_ours=lambda p: is_same_file_as(p, identity),
                       under=cleanup_runner(f"rollback new file {path}"))
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


# ── restorable replacement (flowgate.v02.0074 T#4, D0006 §3.6.5) ─────────────

KEEP_SUFFIX = ".keep-{pid}-{utc_ms}"


class FileChanged(GraphApiError):
    """The path no longer holds the file a write judged (someone replaced it in between)."""

    def __init__(self, path):
        super().__init__(409, "file_changed", {})
        self.path = Path(path)


class ReplacedFile:
    """An existing file replaced by ``replace_restorably``: its original is kept aside until the write commits."""

    def __init__(self, path: Path, keep: Path, original: tuple, replaced: Optional[tuple]):
        self.path, self.keep, self.original, self.replaced = path, keep, original, replaced
        self.restored = False

    def restore(self) -> bool:
        """Put the original back while the path still holds the file this replacement wrote. True: the path holds
        the original again (also when it already did). False: the path holds something else now; the kept original
        stays where it is (``keep``) for an operator."""
        if self.restored:
            return True
        if not is_same_file_as(self.path, self.replaced):
            _log.warning("replaced file not restored, the path changed again: %s (original kept at %s)",
                         self.path, self.keep)
            return False
        os.replace(self.keep, self.path)
        self.restored = True
        return True

    def release(self) -> None:
        """The write committed: the kept original is no longer needed."""
        if self.restored:
            return
        try:
            self.keep.unlink(missing_ok=True)
        except OSError:
            _log.warning("kept original not removed: %s", self.keep, exc_info=True)


def replace_restorably(abs_path, data: bytes, expected_identity: Optional[tuple]) -> ReplacedFile:
    """Replace the existing file at ``abs_path`` with ``data`` so that the replacement can be undone byte for byte.

    The path must still hold the very file ``expected_identity`` was taken of (``file_identity``), else
    ``FileChanged`` and nothing moves. The original is kept beside it first — a second name of the same file (a hard
    link, so the original keeps its identity) or, where links are not possible, a copy — and the new bytes are
    written to a temporary file that then takes the path. ``ReplacedFile.restore`` puts the original back (identity
    checked), ``release`` drops the kept original after the commit. Nothing here is registered with the transaction:
    the caller decides when to restore and under which lock (``cleanup_runner``).
    """
    path = Path(abs_path)
    if expected_identity is None or not is_same_file_as(path, expected_identity):
        raise FileChanged(path)
    keep = _side_path(path, KEEP_SUFFIX)
    try:
        os.link(path, keep)
    except OSError:
        import shutil

        shutil.copy2(path, keep)
    original = file_identity(keep)
    tmp = _side_path(path, TEMP_SUFFIX)
    try:
        if not is_same_file_as(path, expected_identity):
            keep.unlink(missing_ok=True)
            raise FileChanged(path)
        with tmp.open("xb") as stream:
            stream.write(data)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        if is_same_file_as(path, expected_identity):
            keep.unlink(missing_ok=True)     # the original never left the path: nothing to keep
        raise
    return ReplacedFile(path, keep, original, file_identity(path))
