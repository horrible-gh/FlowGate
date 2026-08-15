"""Document attachments — storage, registry and the six request paths.

flowgate.default.0060: D0010 (design) → P0011 (contract) → L0012 (logic) → DB0013 (schema)
→ T0016 (this implementation).

Layout, in dependency order:

  constants  §1     — every number and policy table, one copy of each
  errors     §1-4   — the P0011 error envelope as an exception
  naming     §2-2~4 — sanitize / extension policy / dedupe / Content-Disposition
  registry   DB §4  — the `attachments` table, exactly the queries DB0013 lists
  locator    §2-1,6 — the single gate: where the room is, and is this row really ours
  service    §2-5~10— upload, list, download, delete, read, copy
  legacy     §2-11  — the one-off move out of the old side tree
"""
from __future__ import annotations

from .errors import AttachmentError, error_response, unexpected  # noqa: F401
from .service import (  # noqa: F401
    acopy_to_source,
    adelete_attachment,
    alist_attachments,
    aread_attachment,
    aresolve_download,
    copy_to_source,
    delete_attachment,
    list_attachments,
    read_attachment,
    resolve_download,
    upload_attachments,
)

__all__ = [
    "AttachmentError",
    "unexpected",
    "error_response",
    "upload_attachments",
    "list_attachments",
    "alist_attachments",
    "resolve_download",
    "aresolve_download",
    "delete_attachment",
    "adelete_attachment",
    "read_attachment",
    "aread_attachment",
    "copy_to_source",
    "acopy_to_source",
]
