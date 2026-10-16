"""Move attachments left in the old side tree into the document's own room.

flowgate.default.0060 L0012 §2-11 (M1~M8), DB0013 §3-4.

The old location is ``{storage_root}/projects/{project_dir}/attachments/{doc_id}/`` and it
was written without a registry, so the ONLY clue about which document a file belongs to is
the directory name (D0010 §3-8).

This is an operational procedure, not part of server start-up. DB0013 §3-4 gives two
reasons: the source cannot be expressed as ``INSERT ... SELECT`` (it is a directory listing),
and the copy is heavy I/O that would block boot. Run it from
``server/tools/migrate_legacy_attachments.py`` after 080 is applied.

0064 L0005 2.7.1: each registration commits under the guard an R↔B conversion of the document holds
and re-checks the document there (``_register_copy``).
"""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from modules.flow_gate.storage import paths as storage_paths

from .errors import AttachmentError
from .locator import resolve_attach_dir, storage_relative
from .naming import resolve_content_type, resolve_unique_name, sanitize_attachment_name
from .registry import registry_has, registry_has_same, registry_insert

MIGRATED_DIR_NAME = "_migrated"


def legacy_root(project_id: str) -> Path:
    """Where ``documents.py:2763`` used to put things."""
    return (
        storage_paths.get_storage_root(project_id)
        / "projects"
        / storage_paths.project_dir_name(project_id)
        / "attachments"
    )


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime_rfc3339(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def migrate_legacy_attachments(project_id: str, dry_run: bool = True) -> dict:
    """M1~M8. Returns ``{moved, skipped, unresolved, refused}``.

    Two rules that are not negotiable:

    * A file whose document cannot be found (M2) is not touched — not deleted, not moved.
      The directory name is the only clue to its owner, and the document may still be
      restored later; erasing the clue loses the file for good.
    * The original is MOVED to ``_migrated/``, never deleted (M7). If anything about the
      migration turns out to be wrong there is still something to go back to. This product
      does not do destructive deletes.

    Re-running is safe: M4 skips any file whose sha256 is already registered for that
    document. The extension deny-list is deliberately NOT applied — the goal is not to erase
    material that is already uploaded; ``read`` blocks those at §2-9 R3 instead.

    0064 L0005 2.7.1: the registration (M6) commits under the guard an R↔B conversion of the
    document holds (``_register_copy``). The copy is written first, like an upload's stream; under
    the guard the document is read again and must still exist, be mutable and own the room the copy
    went to. Otherwise the file lands in ``refused``: only this run's copy is removed and the
    original stays where it is, unmoved.
    """
    from modules.flow_gate.documents import document_service

    report: dict[str, list] = {"moved": [], "skipped": [], "unresolved": [], "refused": []}
    root = legacy_root(project_id)
    if not root.is_dir():
        return report

    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if directory.name.startswith("_"):        # M1 — _migrated and friends
            continue
        doc = document_service.get_document(directory.name)   # dir name == old doc_id
        if doc is None:                                        # M2
            report["unresolved"].append(directory.name)
            continue

        doc_id = doc.get("doc_id")
        room = resolve_attach_dir(doc)                         # M3
        created_room = not room.exists()
        if not dry_run:
            room.mkdir(parents=True, exist_ok=True)

        reserved: set = set()
        for entry in sorted(p for p in directory.iterdir() if p.is_file()):
            safe = sanitize_attachment_name(entry.name)        # §2-2 re-applied
            sha = _sha256_of(entry)
            if registry_has_same(doc_id, sha):                 # M4 — idempotent
                report["skipped"].append({"doc_id": doc_id, "filename": entry.name})
                continue
            if dry_run:
                report["moved"].append({
                    "doc_id": doc_id, "filename": entry.name, "planned_name": safe,
                })
                continue

            final_name, fd = resolve_unique_name(              # M5
                room, doc_id, safe, reserved, registry_has
            )
            dest = room / final_name
            owned = _identity_of(dest, created=True)           # O_EXCL: this run's file until removed
            try:
                with open(entry, "rb") as src:
                    while True:
                        chunk = src.read(1048576)
                        if not chunk:
                            break
                        os.write(fd, chunk)
                os.fsync(fd)
            except BaseException:
                os.close(fd)
                _remove_copy(dest, owned)
                raise
            os.close(fd)
            owned = _identity_of(dest) or owned

            # A legacy executable keeps its row and reads back like any other binary; the
            # extension only decides that it is stored and served as octet-stream (§1-3).
            content_type = resolve_content_type(final_name)

            try:
                registered = _register_copy(                   # M6, under the document's guard
                    doc_id, room, entry, dest, final_name, sha, content_type
                )
            except AttachmentError as exc:
                _remove_copy(dest, owned)                      # this run's copy only; the original stays
                report["refused"].append({
                    "doc_id": doc_id, "filename": entry.name, "code": exc.code,
                    "reason": exc.details.get("reason"),
                })
                continue
            except BaseException:
                _remove_copy(dest, owned)
                raise
            if not registered:                                 # registered meanwhile — M4 again
                _remove_copy(dest, owned)
                report["skipped"].append({"doc_id": doc_id, "filename": entry.name})
                continue

            keep = root / MIGRATED_DIR_NAME / directory.name   # M7 — preserve the original
            keep.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(keep / entry.name))
            report["moved"].append({
                "doc_id": doc_id, "filename": entry.name, "stored_as": final_name,
            })

        if created_room and not dry_run:
            try:
                room.rmdir()                                   # only a room this run made and left empty
            except OSError:
                pass

    return report                                              # M8


def _identity_of(dest: Path, created: bool = False):
    from modules.flow_gate.storage.files import file_identity

    identity = file_identity(dest)
    return identity[:2] if identity is not None and created else identity


def _remove_copy(dest: Path, owned=None) -> None:
    """Remove a copy this run wrote and did not register (nothing lists or reads it).

    0064 T#3: only while the path still holds that copy (``owned``: its file identity)."""
    from modules.flow_gate.storage.files import remove_owned_file

    try:
        if owned is None:
            dest.unlink(missing_ok=True)
        else:
            remove_owned_file(dest, owned)
    except OSError:
        pass


def _register_copy(
    doc_id: str, room: Path, entry: Path, dest: Path, final_name: str, sha: str, content_type: str
) -> bool:
    """M6 under the guard an R↔B conversion of ``doc_id`` holds (0064 L0005 2.7.1).

    False: the same content was registered meanwhile (M4). Raises ``AttachmentError`` when the
    document is gone (404 ``DOCUMENT_NOT_FOUND``), not mutable (409 ``DOCUMENT_NOT_MUTABLE``) or
    no longer owns ``room`` (409 ``DOCUMENT_NOT_MUTABLE``, reason ``attachment_room_changed``).
    """
    from modules.flow_gate.graph_workflow.services.flow_guard import GuardNotHeld, run_in_document_guard

    from .service import assert_mutable, load_document

    def register() -> bool:
        fresh = load_document(doc_id)
        assert_mutable(fresh, None, "legacy attachment migration")
        if resolve_attach_dir(fresh).resolve(strict=False) != room.resolve(strict=False):
            raise AttachmentError(
                409, "DOCUMENT_NOT_MUTABLE",
                "The attachment folder of this document changed; migrate again.",
                doc_id=doc_id, reason="attachment_room_changed",
            )
        if registry_has_same(doc_id, sha):
            return False
        registry_insert(
            doc_id=doc_id,
            original_filename=entry.name,
            filename=final_name,
            file_path=storage_relative(dest, fresh.get("project_id")),
            size=dest.stat().st_size,
            content_type=content_type,
            content_sha256=sha,
            uploaded_by=None,                                  # no record of who; do not invent one
            uploaded_at=_mtime_rfc3339(entry),                 # mtime, an estimate
            is_legacy_migrated=True,
        )
        return True

    try:
        return run_in_document_guard(doc_id, register)
    except GuardNotHeld as exc:
        raise AttachmentError(
            409, "DOCUMENT_NOT_MUTABLE", "Legacy attachments are registered only under the document's guard.",
            doc_id=doc_id, reason="transaction_required",
        ) from exc
