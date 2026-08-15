"""Attachment limits and policy tables — flowgate.default.0060 L0012 §1.

L0012 §1 calls itself "the single source of truth for every number", and this module is
that section transcribed. Implementation, tests and screen copy all read from here; none
of these values is re-derived anywhere else.

One trap worth naming: P0011 §2 shows ``limit_bytes: 10485760`` in its
[failure — upload too large] example. That is an *example* written while P had already
delegated the number to L, and L0012 §1-1 explicitly warns not to copy it into the
implementation. The real ceiling is ``ATTACH_MAX_UPLOAD_BYTES`` (20 MiB), taken from the
approved deck's own caption ("파일당 최대 20MB").
"""
from __future__ import annotations

# ── §1-1 size / count limits ────────────────────────────────────────────────────
ATTACH_MAX_UPLOAD_BYTES = 20971520          # 20 MiB — one uploaded file
ATTACH_MAX_REQUEST_BYTES = 104857600        # 100 MiB — one upload request body
ATTACH_MAX_FILES_PER_REQUEST = 20           # `file` parts accepted per request
ATTACH_MAX_COUNT_PER_DOC = 200              # registered attachments per document
ATTACH_READ_MAX_BYTES = 1048576             # 1 MiB — read() content ceiling
ATTACH_MAX_FILENAME_BYTES = 200             # UTF-8 bytes of the sanitized filename
ATTACH_MAX_TARGET_PATH_CHARS = 1024         # copy target_path total length
ATTACH_MAX_PATH_SEGMENT_BYTES = 255         # UTF-8 bytes of one path segment

# ── §1-2 processing parameters ──────────────────────────────────────────────────
ATTACH_STREAM_CHUNK_BYTES = 1048576         # upload receive / hash chunk
ATTACH_COPY_CHUNK_BYTES = 65536             # copy chunk (same as tree_routes ZIP streaming)
ATTACH_DEDUPE_MAX_ATTEMPTS = 100            # same-name avoidance candidates
ATTACH_DELETE_RETRY = 1                     # registry-row removal retries on delete
ATTACH_TEXT_SNIFF_BYTES = 8192              # bytes inspected for BOM / NUL in read(auto)
ATTACH_DEFAULT_CONTENT_TYPE = "application/octet-stream"

# Media types a browser would execute if it rendered them inline. Downloads force these
# back to octet-stream (§1-2).
ATTACH_DOWNLOAD_FORCED_OCTET_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
})

# ── §1-3 extension policy — accepted at the door, neutralized on the way out ────
# This list used to be a DENY list: an upload whose last extension was on it was refused
# with 400 UNSUPPORTED_EXTENSION. TR0017 rev3 was rejected for exactly that wall — a person
# picked their own work file and the screen answered "첨부로 받을 수 없는 확장자 입니다.".
# Attachments are work material whose kinds cannot be enumerated in advance (the deck
# caption itself says "엑셀, 이미지, PDF 등", and 등 is doing real work there: a .ps1, a .js,
# a shortcut and an installer are all things this project's people hand each other).
# Nothing on this list is refused any more.
#
# What replaces the refusal is the property the refusal was actually after — an attachment
# must never come back in a shape the receiving browser will run. That is enforced where the
# bytes LEAVE the server, which is the only place it can be enforced anyway (files that
# predate the policy, and files that arrive through the legacy migration, never passed
# through an upload check):
#   * upload records `application/octet-stream` for these kinds instead of the guessed
#     media type (`naming.resolve_content_type`), so the registry never carries a runnable
#     media type in the first place;
#   * download serves them as octet-stream with `Content-Disposition: attachment` and
#     `X-Content-Type-Options: nosniff` (`service.resolve_download`).
# So the table still decides something — how a file is *served*, not whether it is
# *accepted*. Note it does not apply to a copy target path either — see L0012 §2-10 C4.
ATTACH_EXECUTABLE_EXTENSIONS = frozenset({
    "exe", "com", "scr", "pif", "bat", "cmd", "msi", "msp", "cpl", "dll", "sys", "drv",
    "vbs", "vbe", "js", "jse", "wsf", "wsh", "ps1", "psm1", "ps1xml", "reg", "hta",
    "lnk", "url", "scf", "inf", "jar", "apk", "app", "gadget",
})

# Windows device names. A file called `CON.txt` is not openable on Windows, so the
# sanitizer prefixes it (§2-2 S6) and a copy target segment carrying one is refused
# (§2-10 C2).
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
