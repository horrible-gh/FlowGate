"""Filename sanitizing, extension policy, same-name dedupe, Content-Disposition.

flowgate.default.0060 L0012 §2-2 (S1~S9), §2-3 (E1~E4, U1~U3), §2-4 (D1~D6), §2-7.

What this replaces: ``documents.py`` used to sanitize with a single ``Path(name).name``.
On Linux that lets ``..\\..\\etc\\passwd`` through as ONE filename component — the exact
defect ``tree_routes.py:151`` already had to fix by normalizing a backslash to ``/`` first.
S3 applies the same correction here, and it has to run before S4 or a ``C:`` prefix turns
into ``C_`` and the reasoning gets muddy.
"""
from __future__ import annotations

import mimetypes
import os
import time
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .constants import (
    ATTACH_DEDUPE_MAX_ATTEMPTS,
    ATTACH_DEFAULT_CONTENT_TYPE,
    ATTACH_EXECUTABLE_EXTENSIONS,
    ATTACH_MAX_FILENAME_BYTES,
    ATTACH_MAX_REQUEST_BYTES,
    WINDOWS_RESERVED_NAMES,
)
from .errors import AttachmentError

_CONTROL_CHARS = {chr(c) for c in range(0x00, 0x20)} | {chr(0x7F)}
# Bidi overrides: a right-to-left mark can make `photo_gnp.exe` render as `photo_exe.png`.
_BIDI_CONTROLS = {chr(c) for c in range(0x202A, 0x202F)} | {chr(c) for c in range(0x2066, 0x206A)}
_WINDOWS_ILLEGAL = ('<', '>', ':', '"', '|', '?', '*')


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Cut *text* to at most *max_bytes* UTF-8 bytes, on a character boundary.

    L0012 §2-2 note 4: cutting on a raw byte index splits a Hangul syllable in half and the
    name on disk stops matching the name in the registry.
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _strip_controls(name: str) -> str:
    return "".join(ch for ch in name if ch not in _CONTROL_CHARS and ch not in _BIDI_CONTROLS)


def _apply_s3_to_s7(name: str) -> str:
    """S3~S7 — the part shared by ``sanitize_attachment_name`` and ``sanitize_path_component``."""
    # S3 — backslash first, then keep only the last path component.
    name = name.replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    # S4 — control / bidi characters out, Windows-illegal characters to '_'.
    name = _strip_controls(name)
    for ch in _WINDOWS_ILLEGAL:
        name = name.replace(ch, "_")
    # S5 — Windows silently drops trailing dots and spaces, so drop them ourselves.
    #      Must run BEFORE S6 or `CON.` never matches the reserved-name list.
    name = name.rstrip(" .").lstrip(" ")
    # S6 — device names.
    if Path(name).stem.upper() in WINDOWS_RESERVED_NAMES:
        name = "_" + name
    # S7 — nothing usable survived.
    if not name or name in (".", ".."):
        name = "upload"
    return name


def sanitize_path_component(raw: str) -> str:
    """S3~S7 only — used for the attachment room name (the document code)."""
    return _apply_s3_to_s7(raw or "")


def sanitize_attachment_name(raw: Optional[str]) -> str:
    """S1~S9 — the name a part is actually stored under."""
    # S1
    name = raw or ""
    if not name.strip():
        name = "upload"
    # S2 — macOS sends NFD Hangul; fold to NFC so the registry and the disk agree.
    name = unicodedata.normalize("NFC", name)
    # S3~S7
    name = _apply_s3_to_s7(name)
    # S8 — truncate LAST, on a character boundary, because earlier steps shorten the name.
    if len(name.encode("utf-8")) > ATTACH_MAX_FILENAME_BYTES:
        ext = Path(name).suffix
        if len(ext.encode("utf-8")) > 32:
            ext = _truncate_utf8(ext, 32)
        budget = ATTACH_MAX_FILENAME_BYTES - len(ext.encode("utf-8"))
        stem = _truncate_utf8(Path(name).stem, budget)
        name = stem + ext
        if not Path(name).stem.strip():
            name = "upload" + ext
    # S9
    return name


def original_display_name(raw: Optional[str]) -> str:
    """``original_filename`` — S2 only (P0011 §1-3 / L0012 §2-2 last paragraph).

    The user's own name is shown back to them verbatim; only characters that would corrupt
    a log line or spoof an extension are removed.
    """
    name = raw or "upload"
    return _strip_controls(unicodedata.normalize("NFC", name)) or "upload"


# ── §2-3 size and extension checks ──────────────────────────────────────────────

def check_request_size(content_length: Optional[str]) -> None:
    """U1 — the cheap pre-check on the declared body size.

    A missing or lying header is expected; U3 counts the real bytes as they arrive.
    """
    if not content_length:
        return
    try:
        declared = int(content_length)
    except (TypeError, ValueError):
        return
    if declared > ATTACH_MAX_REQUEST_BYTES:
        raise AttachmentError(
            413,
            "ATTACHMENT_TOO_LARGE",
            "Attachment request exceeds the total size limit.",
            limit_bytes=ATTACH_MAX_REQUEST_BYTES,
        )


def resolve_content_type(safe_name: str) -> str:
    """E1~E2 — the media type this attachment is stored under. Refuses nothing.

    This was ``check_extension``, and it raised ``400 UNSUPPORTED_EXTENSION`` for every
    kind in §1-3. TR0017 rev3 was rejected for that wall, so the extension no longer decides
    whether a file is accepted — only how it is served (see ``constants`` §1-3).

    The part's own ``Content-Type`` header is deliberately ignored either way: keeping it
    would let the uploader pick the media type the download response later serves.
    """
    ext = Path(safe_name).suffix.lstrip(".").lower()
    if not ext:
        return ATTACH_DEFAULT_CONTENT_TYPE
    if ext in ATTACH_EXECUTABLE_EXTENSIONS:
        # ``.js`` guesses as ``text/javascript``, ``.jar`` as ``application/java-archive``.
        # Writing that into the registry would mean every later reader of the row has to
        # remember to undo it; one row that nobody undid is one inline-rendered script.
        return ATTACH_DEFAULT_CONTENT_TYPE
    guessed, _ = mimetypes.guess_type(safe_name)
    return guessed or ATTACH_DEFAULT_CONTENT_TYPE


def is_executable_extension(filename: str) -> bool:
    """§1-3 — "a kind the receiver would click and run".

    Answers a serving question, not an admission one: download forces these back to
    octet-stream even when the stored row says otherwise (a legacy-migrated row can, since
    it never went through :func:`resolve_content_type`).
    """
    return Path(filename).suffix.lstrip(".").lower() in ATTACH_EXECUTABLE_EXTENSIONS


# ── §2-4 same-name dedupe ───────────────────────────────────────────────────────

_O_BINARY = getattr(os, "O_BINARY", 0)


def _exists_case_insensitive(room: Path, candidate: str) -> bool:
    """D3 — compare case-folded.

    ``A.txt`` and ``a.txt`` are the same file on Windows and two files on Linux. Without
    this the identical request produces different results per host.
    """
    folded = candidate.casefold()
    try:
        return any(entry.name.casefold() == folded for entry in room.iterdir())
    except OSError:
        return False


def resolve_unique_name(
    room: Path,
    doc_id: str,
    safe_name: str,
    reserved_in_request: set,
    registry_has,
) -> tuple[str, int]:
    """D1~D6 — pick a free name and atomically reserve it. Returns (name, open fd).

    The old rule appended ``_{epoch}`` exactly once, so two parts with the same name in one
    request both computed the same candidate in the same second and the second silently
    overwrote the first. D2 (in-request reservation) and D5 (``O_CREAT|O_EXCL``) close that;
    D5 is the only one that also closes the race between two concurrent requests, because an
    ``exists()`` followed by a ``write()`` always has a gap.
    """
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    epoch = int(time.time())

    for attempt in range(ATTACH_DEDUPE_MAX_ATTEMPTS + 1):
        if attempt == 0:
            candidate = safe_name
        elif attempt == 1:
            candidate = f"{stem}_{epoch}{suffix}"
        else:
            candidate = f"{stem}_{epoch}_{attempt}{suffix}"

        if candidate in reserved_in_request:          # D2
            continue
        if _exists_case_insensitive(room, candidate):  # D3
            continue
        if registry_has(doc_id, candidate):            # D4
            continue
        try:                                           # D5 — the real reservation
            fd = os.open(
                str(room / candidate),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise AttachmentError(
                500,
                "ATTACHMENT_STORE_FAILED",
                "Could not create the attachment file.",
                filename=safe_name,
            ) from exc
        reserved_in_request.add(candidate)
        return candidate, fd

    # D6 — stop inventing names.
    raise AttachmentError(
        409,
        "ATTACHMENT_EXISTS",
        "An attachment with this name already exists.",
        doc_id=doc_id,
        filename=safe_name,
    )


# ── §2-7 Content-Disposition ────────────────────────────────────────────────────

def rfc5987_encode(name: str) -> str:
    """RFC 5987 UTF-8 percent-encoding — same rule as ``tree_routes._rfc5987_encode``."""
    return "UTF-8''" + quote(name, safe="")


def rfc5987_disposition(name: str) -> str:
    """``filename*`` always, plus the ASCII ``filename`` fallback when the name is ASCII.

    NR0015 §5 measured that ``tree_routes`` has the encoder as a standalone function but
    leaves the fallback inline in ``download_file:400-407``. This is that pair lifted into
    one shared helper, per NR0015 §7 확정 지침 4·5 — nothing imports the route.
    """
    encoded = rfc5987_encode(name)
    try:
        ascii_name = name.encode("ascii").decode("ascii")
    except UnicodeEncodeError:
        return f"attachment; filename*={encoded}"
    # A quote or backslash inside the ASCII fallback would end the quoted-string early.
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f'attachment; filename="{ascii_name}"; filename*={encoded}'


def has_forbidden_name_chars(name: str) -> bool:
    """J1 — the URL segment must be a bare filename, never a path."""
    if not name or name in (".", ".."):
        return True
    if "/" in name or "\\" in name:
        return True
    return any(ch in _CONTROL_CHARS for ch in name)

