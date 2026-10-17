"""Attachments of a root converted by number swap (0064 L0005 2.7.1, DB0007 §2.5 / §4 Q9·Q10).

A number swap changes the document id (``…0001-R`` → ``…0001-B``) and the attachment room next to the
body (``0001-R/`` → ``0001-B/``), so the rows and the files move together:

1. ``require_target_empty``: the target id has no attachment row at all (an orphan of an earlier swap
   included) — checked after the target id is known, when staging starts and before the first row write.
2. ``stage_attachment_room``: every row passes J1~J6 first (one unsafe row refuses the whole conversion,
   nothing copied), then the files are copied into the new room (removed again on rollback) and the rows
   are kept as an order-free snapshot keyed by ``filename``.
3. ``reassign_attachment_rows``: before the old document row is deleted (``attachments.doc_id`` cascades),
   every row — ghosts too — takes the new ``doc_id`` and its new-room ``file_path``; no row may stay on the
   old id and the new id must hold exactly the moved rows.
4. ``finish_attachment_room``: only queues the cleanup of the old files and the empty old room after the
   outermost commit; another row pointing at the same file keeps it.

A succession (L0005 2.9.3, 0064 T#4) stages the same way (1, 2) and then ``copy_attachments`` registers the
copies as the new document's rows; the original rows, files and room stay and nothing is queued for them.

Every cleanup — the old files after the commit, the copies and a room made here after a rollback — removes
only what it owns (the file identity taken when the file was read or made, a room only while empty) and runs
under the durable lock of the guard the next writer of those paths takes (``flow_guard.run_cleanup``, 0064 T#3).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modules.flow_gate.db.connection import after_commit, after_rollback, get_store
from modules.flow_gate.documents.conversion_errors import ConversionRefused
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.storage.files import (
    cleanup_runner, file_identity, referenced_by_any_document, remove_owned_file, same_file, write_new_file,
)

from .errors import AttachmentError
from .locator import resolve_attach_dir, resolve_registered_attachment, storage_relative, within_storage_jail
from .registry import registry_list

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Move:
    filename: str
    row: dict
    src: Optional[Path]          # None for a ghost (a safe row whose file is missing)
    dst: Path
    src_identity: Optional[tuple] = None    # the old file as copied: the only file its cleanup removes

    @property
    def ghost(self) -> bool:
        return self.src is None


@dataclass(frozen=True)
class AttachStage:
    snapshot: dict
    moves: tuple
    old_room: Optional[Path]
    new_room: Optional[Path]
    project_id: Optional[str]

    @property
    def reassigned_count(self) -> int:
        return len(self.moves)

    @property
    def ghost_count(self) -> int:
        return sum(1 for m in self.moves if m.ghost)


def attachment_snapshot(rows: list, doc_id: str) -> dict:
    """``filename -> every persisted column``: compared as a map, so the list order never matters."""
    out: dict = {}
    for row in rows or []:
        name = row.get("filename")
        if name in out:
            raise ConversionRefused("attachments_changed", doc_id=doc_id)
        out[name] = dict(row)
    return out


def require_target_empty(doc_id: str, new_id: str, mode: str, stage: str = "") -> None:
    rows = registry_list(new_id)
    if rows:
        _log.warning("conversion refused: attachment rows exist for target mode=%s target=%s rows=%d stage=%s",
                     mode, new_id, len(rows), stage)
        raise ConversionRefused("attachment_target_rows_exist", doc_id=doc_id, target_doc_id=new_id, mode=mode)


def _unsafe(doc_id: str, filename: Optional[str], reason: str) -> ConversionRefused:
    return ConversionRefused("attachment_path_unsafe", doc_id=doc_id, filename=filename, reason=reason)


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _prepare_room(room: Path, doc_id: str) -> None:
    """The new room; a room made here is removed again on rollback (after its files, LIFO)."""
    if os.path.lexists(room):
        if room.is_symlink() or not room.is_dir():
            raise _unsafe(doc_id, None, "STORAGE_PATH_OUTSIDE_ROOT")
        return
    room.mkdir(parents=True)

    def remove() -> None:
        _rmdir_if_empty(room)

    under = cleanup_runner(f"rollback new room {room}")
    after_rollback(remove if under is None else (lambda: under(remove)))


def stage_attachment_room(doc: dict, new_id: str, new_file_path: Optional[str], mode: str) -> AttachStage:
    doc_id, project_id = doc["doc_id"], doc.get("project_id")
    require_target_empty(doc_id, new_id, mode, "attachment_stage")
    rows = registry_list(doc_id)
    snapshot = attachment_snapshot(rows, doc_id)
    if not rows:
        return AttachStage(snapshot, (), None, None, project_id)
    new_doc = {**doc, "doc_id": new_id, "file_path": new_file_path}
    try:
        old_room = resolve_attach_dir(doc)
        new_room = resolve_attach_dir(new_doc)
    except AttachmentError as exc:
        raise _unsafe(doc_id, None, exc.code)
    room_key = new_room.resolve(strict=False)
    moves = []
    for listed in rows:                                   # every row is checked before anything is copied
        name = listed.get("filename")
        try:
            row, src = resolve_registered_attachment(doc, name, require_file=False)
        except AttachmentError as exc:
            if exc.code == "ATTACHMENT_NOT_FOUND":
                raise ConversionRefused("attachments_changed", doc_id=doc_id)
            raise _unsafe(doc_id, name, exc.code)
        if dict(row) != snapshot.get(name):
            raise ConversionRefused("attachments_changed", doc_id=doc_id)
        dst = new_room / row["filename"]
        if os.path.islink(dst) and not os.path.islink(new_room):  # a link already at the target (L0005 2.9.3)
            raise ConversionRefused("attachment_target_exists", doc_id=doc_id, filename=name)
        resolved = dst.resolve(strict=False)
        if resolved.parent != room_key or not within_storage_jail(resolved, project_id):
            raise _unsafe(doc_id, name, "STORAGE_PATH_OUTSIDE_ROOT")
        if os.path.lexists(dst):
            raise ConversionRefused("attachment_target_exists", doc_id=doc_id, filename=name)
        if src.is_file():
            moves.append(Move(name, dict(row), src, dst, file_identity(src)))
        elif not os.path.lexists(src):
            moves.append(Move(name, dict(row), None, dst))
        else:
            raise _unsafe(doc_id, name, "not_a_file")
    if any(not m.ghost for m in moves):
        _prepare_room(new_room, doc_id)
    for m in moves:
        if m.ghost:
            continue
        write_new_file(m.dst, m.src.read_bytes())         # bytes as they are; removed again on rollback
        if m.dst.stat().st_size != m.src.stat().st_size:
            raise ConversionRefused("attachment_copy_mismatch", doc_id=doc_id, filename=m.filename)
    return AttachStage(snapshot, tuple(moves), old_room, new_room, project_id)


def reassign_attachment_rows(doc: dict, new_id: str, stage: AttachStage, mode: str) -> None:
    """Q10, before the old document row is deleted, in the same transaction."""
    doc_id = doc["doc_id"]
    require_target_empty(doc_id, new_id, mode, "row_write")
    if attachment_snapshot(registry_list(doc_id), doc_id) != stage.snapshot:
        raise ConversionRefused("attachments_changed", doc_id=doc_id)
    store = get_store()
    expected = {}
    for m in stage.moves:
        path_value = storage_relative(m.dst, stage.project_id)
        affected = store._execute_affected(
            "UPDATE attachments SET doc_id = ?, file_path = ? WHERE doc_id = ? AND filename = ?",
            [new_id, path_value, doc_id, m.filename],
        )
        if affected != 1:
            raise ConversionRefused("attachments_changed", doc_id=doc_id)
        expected[m.filename] = {**m.row, "doc_id": new_id, "file_path": path_value}
    if registry_list(doc_id):
        raise ConversionRefused("attachments_changed", doc_id=doc_id)
    if attachment_snapshot(registry_list(new_id), doc_id) != expected:
        raise ConversionRefused("attachments_changed", doc_id=doc_id)


def finish_attachment_room(stage: Optional[AttachStage], doc_id: str, cleanup_key) -> None:
    """Queue the old room's cleanup for after the outermost commit; never delete inline.

    ``cleanup_key`` is the guard of the document as committed — the one the next writer of the old room takes."""
    from modules.flow_gate.graph_workflow.services.flow_guard import run_cleanup

    if stage is None or stage.old_room is None:
        return
    sources = tuple((m.src, m.src_identity) for m in stage.moves if m.src is not None)
    old_room, project_id = stage.old_room, stage.project_id
    what = f"old attachment room cleanup {old_room}"

    def cleanup() -> None:
        try:
            run_cleanup(cleanup_key, lambda: cleanup_old_attachment_room(sources, old_room, project_id), what)
        except Exception:  # noqa: BLE001 — committed already; files left behind are unreferenced orphans
            _log.warning("%s failed", what, exc_info=True)

    if not after_commit(cleanup):
        raise ConversionRefused("transaction_required", doc_id=doc_id)


def cleanup_old_attachment_room(sources, old_room: Path, project_id: Optional[str]) -> None:
    """``sources``: ``(path, identity)`` of the files copied out. A path another row uses, or that holds another
    file by now, is kept; the room goes only when empty."""
    for src, identity in sources:
        try:
            if referenced_by_any_attachment(src) or referenced_by_any_document(src):
                _log.info("old attachment kept, still referenced: %s", src)
                continue
            remove_owned_file(src, identity)
        except Exception:  # noqa: BLE001 — the conversion is committed; cleanup is best-effort
            _log.warning("old attachment cleanup failed: %s", src, exc_info=True)
    try:
        if old_room.is_dir() and not any(old_room.iterdir()):
            old_room.rmdir()
    except Exception:  # noqa: BLE001
        _log.warning("old attachment room cleanup failed: %s", old_room, exc_info=True)


def referenced_by_any_attachment(abs_path) -> bool:
    """Whether an attachment row resolves to this very file (each row on its document's storage root)."""
    path = Path(abs_path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        return True
    name = path.name.casefold()
    rows = get_store()._fetch_all(
        "SELECT a.file_path AS file_path, d.project_id AS project_id FROM attachments a "
        "LEFT JOIN documents d ON d.doc_id = a.doc_id")
    for row in rows or []:
        stored = (row.get("file_path") or "").strip().replace("\\", "/")
        if not stored or stored.rsplit("/", 1)[-1].casefold() != name:
            continue
        root = storage_paths.get_storage_root(row.get("project_id")).resolve()
        if same_file((root / stored).resolve(strict=False), path):
            return True
    return False


def copy_attachments(doc: dict, new_id: str, stage: AttachStage, mode: str) -> None:
    """L0005 2.9.3, a succession: register the copies ``stage_attachment_room`` made as the new document's rows.

    No file is copied here. Before the first INSERT the target id is empty again (an empty ``moves`` too) and
    the source rows are still the snapshot (order free); every row — ghosts too — becomes a new row with its
    new-room ``file_path`` and the rest of its metadata, and the new id holds exactly those rows. The original
    rows, files and room are kept: nothing of theirs is removed or queued for removal."""
    from .registry import registry_insert

    doc_id = doc["doc_id"]
    require_target_empty(doc_id, new_id, mode, "row_write")
    if attachment_snapshot(registry_list(doc_id), doc_id) != stage.snapshot:
        raise ConversionRefused("attachments_changed", doc_id=doc_id)
    expected = {}
    for m in stage.moves:
        r = m.row
        path_value = storage_relative(m.dst, stage.project_id)
        registry_insert(doc_id=new_id, original_filename=r["original_filename"], filename=r["filename"],
                        file_path=path_value, size=r["size"], content_type=r["content_type"],
                        content_sha256=r["content_sha256"], uploaded_by=r.get("uploaded_by"),
                        uploaded_at=r.get("uploaded_at"), is_legacy_migrated=bool(r.get("is_legacy_migrated")))
        expected[m.filename] = path_value
    if {r.get("filename"): r.get("file_path") for r in registry_list(new_id)} != expected:
        raise ConversionRefused("attachments_changed", doc_id=doc_id)
