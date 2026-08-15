"""Move attachments left in the old side tree into the document's own room.

flowgate.default.0060 L0012 §2-11 (M1~M8), DB0013 §3-4.

The old location is ``{storage_root}/projects/{project_dir}/attachments/{doc_id}/`` and it
was written without a registry, so the ONLY clue about which document a file belongs to is
the directory name (D0010 §3-8).

This is an operational procedure, not part of server start-up. DB0013 §3-4 gives two
reasons: the source cannot be expressed as ``INSERT ... SELECT`` (it is a directory listing),
and the copy is heavy I/O that would block boot. Run it from
``server/tools/migrate_legacy_attachments.py`` after 080 is applied.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from modules.flow_gate.storage import paths as storage_paths

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
    """M1~M8. Returns ``{moved, skipped, unresolved}``.

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
    """
    from modules.flow_gate.documents import document_service

    report: dict[str, list] = {"moved": [], "skipped": [], "unresolved": []}
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
            try:
                with open(entry, "rb") as src:
                    while True:
                        chunk = src.read(1048576)
                        if not chunk:
                            break
                        os.write(fd, chunk)
                os.fsync(fd)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

            # A legacy executable keeps its row and reads back like any other binary; the
            # extension only decides that it is stored and served as octet-stream (§1-3).
            content_type = resolve_content_type(final_name)

            registry_insert(                                   # M6
                doc_id=doc_id,
                original_filename=entry.name,
                filename=final_name,
                file_path=storage_relative(dest, doc.get("project_id")),
                size=dest.stat().st_size,
                content_type=content_type,
                content_sha256=sha,
                uploaded_by=None,                              # no record of who; do not invent one
                uploaded_at=_mtime_rfc3339(entry),             # mtime, an estimate
                is_legacy_migrated=True,
            )

            keep = root / MIGRATED_DIR_NAME / directory.name   # M7 — preserve the original
            keep.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(keep / entry.name))
            report["moved"].append({
                "doc_id": doc_id, "filename": entry.name, "stored_as": final_name,
            })

    return report                                              # M8
