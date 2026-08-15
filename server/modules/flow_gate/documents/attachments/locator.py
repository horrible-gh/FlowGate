"""The single gate every attachment path goes through.

flowgate.default.0060 L0012 §2-1 (``resolve_attach_dir``) and §2-6
(``resolve_registered_attachment``).

D0010 §2-4 required that path assembly live in exactly one place, because the moment each
route assembles its own path one of them ends up returning an absolute path or missing a
guard — that is literally how NR0003 G4 happened. Both functions here are that one place.

NR0015 §5 measured that ``tree_routes``' download guard cannot be reused wholesale: its
resolver is built on the project source root and git worktrees and has no concept of a
storage room, a registry row, or per-document ownership. Nothing in this module imports
``tree_routes``. What IS shared is the per-component symlink check idea from
``_seal_under_root`` (J6), rewritten here against the storage jail.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from modules.flow_gate.storage import paths as storage_paths

from .errors import AttachmentError
from .naming import has_forbidden_name_chars, sanitize_path_component
from .registry import registry_get

_DRIVE_RE = storage_paths._DRIVE_RE  # same drive-letter test the storage layer already uses


def within_storage_jail(path: Path, project_id: Optional[str]) -> bool:
    """The storage-jail test, bound at call time instead of at import time.

    0060 TR0017 rev2. NR0015 §7 지침 5 told this package to reuse the storage jail through a
    PUBLIC name, so 0060 added the wrapper ``within_allowed_roots()`` to ``storage/paths.py``
    and called it from here. That is still the binding we want — it names the reuse boundary
    instead of reaching into ``_within_allowed_roots``.

    What it cost: the wrapper is a NEW symbol in a shared file this group does not own, while
    this whole package is new. A deployment that carries this package next to an OLDER
    ``paths.py`` — which is exactly what the dev preview was running — has no such attribute,
    and ``storage_paths.within_allowed_roots(...)`` raised ``AttributeError`` on the very
    first upload::

        2026-08-15 19:43:00 [ERROR][attachments] upload failed: AttributeError
          File ".../attachments/locator.py", line 90, in resolve_attach_dir
            if not storage_paths.within_allowed_roots(room, project_id):
        AttributeError: module 'modules.flow_gate.storage.paths' has no attribute
                        'within_allowed_roots'

    That is the bare ``500`` this document was rejected for. Resolution order below: public
    wrapper first, then the long-standing private function, and if NEITHER exists deny — this
    is a jail, so an unresolvable check has to fail closed rather than wave the path through.
    """
    checker = getattr(storage_paths, "within_allowed_roots", None)
    if checker is None:
        checker = getattr(storage_paths, "_within_allowed_roots", None)
    if checker is None:
        return False
    return bool(checker(path, project_id))


def doc_code_of(doc_id: str) -> str:
    """``flowgate.default.0060.0001-R`` → ``0001-R``."""
    return (doc_id or "").rsplit(".", 1)[-1]


def project_branch(project_id: Optional[str]) -> str:
    """project_settings.branch, defaulting to 'main' — mirrors documents._get_project_branch."""
    try:
        from modules.flow_gate.db import projects as _proj

        settings = _proj.get_settings(project_id) if project_id else None
        if settings:
            return (settings.get("branch") or "main").strip() or "main"
    except Exception:
        pass
    return "main"


def resolve_attach_dir(doc: dict) -> Path:
    """§2-1 — the attachment room for one document, as a sibling of its body file.

    ① the body's REAL location wins. Where the body actually sits is the truth; trusting the
       calculation alone puts the room somewhere else than the body for any document whose
       stored path carries the branch-segment drift ``_apply_branch_segment`` absorbs.
    ② no body row, or it does not resolve → fall back to the calculated group/subgroup path.

    Read paths never create the room. Only upload and the legacy migration mkdir it, so a
    document with no attachments answers "none" (empty list / 404) instead of leaving an
    empty directory behind for the numbering scan to trip over.
    """
    doc_id = doc.get("doc_id") or ""
    project_id = doc.get("project_id")
    room_name = sanitize_path_component(doc_code_of(doc_id))
    if not room_name or room_name == "upload":
        raise AttachmentError(
            500, "ATTACHMENT_STORE_FAILED", "Document code is unusable.", doc_id=doc_id
        )

    branch = project_branch(project_id)
    body_abs = None
    stored = doc.get("file_path")
    if stored:
        body_abs = storage_paths.resolve_storage_path(stored, project_id, branch)

    if body_abs is not None:
        room = body_abs.parent / room_name
    else:
        module = doc.get("module") or "none"
        group_code = doc.get("group_id") or ""
        subgroup_code = doc.get("sub_group_id") or ""
        if subgroup_code:
            base = storage_paths.subgroup_path(
                project_id, group_code, subgroup_code, module=module, branch=branch
            )
        else:
            base = storage_paths.group_path(
                project_id, group_code, module=module, branch=branch
            )
        room = base / room_name

    if not within_storage_jail(room, project_id):
        raise AttachmentError(
            403,
            "STORAGE_PATH_OUTSIDE_ROOT",
            "The attachment location resolves outside the storage root.",
            doc_id=doc_id,
        )
    return room


def resolve_registered_attachment(
    doc: dict, name_from_url: str, *, require_file: bool = True
) -> tuple[dict, Path]:
    """§2-6 J1~J8 — shared by download, delete, read and copy.

    The name in the request is a registry lookup key and NEVER a path fragment. Whatever
    becomes a path came from the registry. That is the first line of defence; J3~J6 are the
    second, for the case where the registry row itself is wrong.

    ``require_file=False`` skips J7 ONLY — every jail check still runs. Delete is the one
    caller that needs it: §2-8 X4 treats "the file is already gone" as the target state, and
    §3-2 makes the next identical delete the way a ``ghost_row`` heals. With J7 mandatory that
    row could never be cleared, and the half-finished state would be permanent.
    """
    doc_id = doc.get("doc_id") or ""
    project_id = doc.get("project_id")

    # J1 — the URL segment must be a bare filename. FastAPI has already percent-decoded it.
    if has_forbidden_name_chars(name_from_url):
        raise AttachmentError(
            400, "INVALID_FILENAME", "Attachment name is not a valid file name.",
            doc_id=doc_id, filename=name_from_url,
        )

    # J2
    row = registry_get(doc_id, name_from_url)
    if row is None:
        raise AttachmentError(
            404, "ATTACHMENT_NOT_FOUND", "Attachment was not found.",
            doc_id=doc_id, filename=name_from_url,
        )

    # J3 — a poisoned row: absolute, drive-prefixed, UNC, or carrying '..'.
    stored = (row.get("file_path") or "").replace("\\", "/")
    outside = AttachmentError(
        403,
        "STORAGE_PATH_OUTSIDE_ROOT",
        "The registered attachment path resolves outside the storage root.",
        doc_id=doc_id,
        filename=name_from_url,
    )
    if (
        not stored
        or stored.startswith("/")
        or stored.startswith("//")
        or _DRIVE_RE.match(stored)
        or ".." in stored.split("/")
    ):
        raise outside

    # J4 — inside the storage jail?
    #      We do NOT call resolve_storage_path(): it collapses "outside the jail" and
    #      "file missing" into a single None, and P0011 needs 403 for the first and 404 for
    #      the second. Same ingredients, two branches.
    root = storage_paths.get_storage_root(project_id).resolve()
    candidate = (root / stored).resolve(strict=False)
    if not within_storage_jail(candidate, project_id):
        raise outside

    # J5 — the key defence. Being inside the storage root is not enough; the row must point
    #      into THIS document's room (D0010 §1).
    room = resolve_attach_dir(doc).resolve(strict=False)
    if candidate.parent != room:
        raise outside

    # J6 — per component, because checking only the leaf lets an intermediate directory
    #      link escape (the tree_routes._seal_under_root pattern).
    current = room
    try:
        relative_parts = candidate.relative_to(room).parts
    except ValueError:
        raise outside
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise outside

    # J7 — the row exists but the file does not. Externally a 404 (P0011 §4); a ghost_row
    #      internally (L0012 §3-1), which the next delete self-heals.
    if require_file and not candidate.is_file():
        raise AttachmentError(
            404, "ATTACHMENT_NOT_FOUND", "Attachment was not found.",
            doc_id=doc_id, filename=name_from_url,
        )

    return row, candidate


def storage_relative(path: Path, project_id: Optional[str]) -> str:
    """The only form of a path that leaves this module (P0011 §9)."""
    return storage_paths.to_storage_relative(path, project_id)
