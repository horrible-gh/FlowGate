"""Content guards for the remote ``write`` / ``patch`` tools (v02gui 0015 T5).

0006-NR §6-2 traced a 0005 incident where a worker read source through a layer that
replaced values with ``***`` and wrote the masked text back, damaging dozens of files.
The server cannot see what the client did to a read, so this module only judges what
the server *can* see: the bytes on disk and the bytes it is asked to store.

(a) ``base_sha256``   -- optimistic concurrency. The caller passes the sha256 of the
                         bytes it read (``read`` now returns ``source_sha256``); a
                         mismatch means someone else wrote in between. It does NOT
                         detect masking.
(c) masked substitution -- the prefix rule (0006-NR §6-2 (c), rev 4). In a changed
                         hunk, an added line ``a`` and a removed line ``r`` are a
                         suspect pair when, for a ``***`` at index ``i`` of ``a``:
                         ``a[:i] == r[:i]`` (same head) and ``r[i:i+3] != "***"``
                         (real text stood there). Only the head is compared, so an
                         edit to the tail (a comment) cannot hide the substitution,
                         and a line that already held ``***`` at that spot is not
                         flagged. Two narrowings keep obvious non-masks out:
                         the head must hold a non-blank character (a bare ``***`` /
                         markdown rule line is not a masked value), and a ``***``
                         *inserted* in front of the original text (``r[i]`` equals
                         the first character after the ``***`` in ``a``, e.g. markdown
                         emphasis ``- item`` -> ``- ***item***``) is not a substitution.
                         Pure additions (no removed line in the hunk) are not judged.
(e) U+FFFD           -- ``read`` decodes with ``errors="replace"``, so a non-UTF-8 byte
                         comes back as U+FFFD. Writing that text back silently replaces
                         the original bytes. A hunk that adds more U+FFFD than it
                         removes is refused.
(f) post-write check -- syntax check of the stored result (``.py`` via ``compile`` to an
                         AST, ``.json`` via ``json.loads``). Reported, never blocking:
                         blocking a broken result belongs to the integration gate.

Known misses (documented, not guaranteed): a line whose head was edited together with
the masked value, a change that reshapes lines, masked text copied into a new file or
from another file, masking that is not line-shaped. The override flags
``allow_mask_substitution`` / ``allow_replacement_char`` + ``reason`` exist because an
intentional ``"Total"`` -> ``"***"`` edit cannot be told apart from masking by content.
"""
from __future__ import annotations

import ast
import codecs
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

MASK = "***"
REPLACEMENT_CHAR = "�"

# sha256 of the whole file is computed for read/write/patch only up to this size. A
# byte-window walk over a huge file must not re-hash the whole file for every window.
HASH_LIMIT_BYTES = 16 * 1024 * 1024
# The post-write syntax check is skipped above this size.
CHECK_LIMIT_BYTES = 2 * 1024 * 1024
# At most this many line numbers are echoed back in an error/report.
MAX_REPORTED_LINES = 20

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

REASON_BASE_CHANGED = "base_changed"
REASON_MASKED = "masked_substitution_suspect"
REASON_REPLACEMENT_CHAR = "replacement_char_introduced"
REASON_OVERRIDE_REASON_REQUIRED = "override_reason_required"
REASON_BASE_NOT_APPLICABLE = "base_sha256_not_applicable"

GUARD_FIELDS = frozenset({"base_sha256", "allow_mask_substitution", "allow_replacement_char", "reason"})


# ── hashing / decoding ─────────────────────────────────────────────────────────

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path, size: Optional[int] = None) -> Optional[str]:
    """Streaming sha256 of a file, or None above HASH_LIMIT_BYTES / on OSError."""
    try:
        if size is None:
            size = path.stat().st_size
        if size > HASH_LIMIT_BYTES:
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def is_valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def is_transformed(raw: bytes, encoding: str) -> bool:
    """True when decoding ``raw`` with errors="replace" changed something."""
    try:
        raw.decode(encoding)
        return False
    except (UnicodeDecodeError, ValueError):
        return True


def decode_for_compare(raw: bytes, encoding: str) -> str:
    """Decode the on-disk original for diffing.

    Undecodable bytes become lone surrogates (surrogateescape), never U+FFFD, so a
    line that came back from ``read`` with U+FFFD never compares equal to the
    original line it replaced -- and is therefore judged as an added U+FFFD line.
    """
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, ValueError):
        return raw.decode(encoding, errors="surrogateescape")


def _is_utf8(encoding: str) -> bool:
    try:
        return codecs.lookup(encoding).name in ("utf-8", "utf-8-sig")
    except LookupError:
        return False


# ── diff ───────────────────────────────────────────────────────────────────────

def changed_hunks(old_text: Optional[str], new_text: str) -> list[tuple[list[str], list[str], int]]:
    """Line hunks between two texts as (removed_lines, added_lines, added_start_1based).

    Lines are compared without their terminators, so a CRLF file rewritten with the
    same CRLF content (or an LF copy of it) yields no hunk. ``old_text=None`` means
    "no original": every new line is an addition.
    """
    new_lines = new_text.splitlines()
    if old_text is None:
        return [([], new_lines, 1)] if new_lines else []
    old_lines = old_text.splitlines()
    # Trim the common head/tail first: patch-sized edits on big files stay cheap.
    head = 0
    limit = min(len(old_lines), len(new_lines))
    while head < limit and old_lines[head] == new_lines[head]:
        head += 1
    tail = 0
    while (tail < limit - head
           and old_lines[len(old_lines) - 1 - tail] == new_lines[len(new_lines) - 1 - tail]):
        tail += 1
    old_mid = old_lines[head:len(old_lines) - tail]
    new_mid = new_lines[head:len(new_lines) - tail]
    if not old_mid and not new_mid:
        return []
    hunks: list[tuple[list[str], list[str], int]] = []
    matcher = difflib.SequenceMatcher(None, old_mid, new_mid, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "delete"):
            continue
        hunks.append((old_mid[i1:i2], new_mid[j1:j2], head + j1 + 1))
    return hunks


# ── (c) masked substitution, prefix rule ─────────────────────────────────────────

def _mask_positions(line: str) -> list[int]:
    out = []
    i = line.find(MASK)
    while i != -1:
        out.append(i)
        i = line.find(MASK, i + 1)
    return out


def is_masked_substitution(added: str, removed: str) -> bool:
    """The 0006-NR §6-2 (c) prefix rule for one (added, removed) line pair."""
    for i in _mask_positions(added):
        head = added[:i]
        if not head.strip():
            continue                      # no key/context before the *** -> not a masked value
        if not removed.startswith(head) or len(removed) <= i:
            continue                      # heads differ, or the original ended here
        if removed[i:i + len(MASK)] == MASK:
            continue                      # the original already had *** at this spot
        after = added[i + len(MASK):]
        if after and removed[i] == after[0]:
            continue                      # *** inserted before the original text, nothing replaced
        return True
    return False


def masked_substitution_lines(hunks) -> list[int]:
    """1-based new-content line numbers of added lines that look like masked substitutions."""
    lines: list[int] = []
    for removed, added, start in hunks:
        if not removed:
            continue
        for offset, a in enumerate(added):
            if MASK not in a:
                continue
            if any(is_masked_substitution(a, r) for r in removed):
                lines.append(start + offset)
    return lines


# ── (e) U+FFFD ──────────────────────────────────────────────────────────────────

def replacement_char_lines(hunks) -> list[int]:
    """Added lines carrying U+FFFD in hunks that add more U+FFFD than they remove.

    Comparing counts per hunk (not "any U+FFFD") keeps an edit to a line that really
    holds U+FFFD on disk from being refused.
    """
    lines: list[int] = []
    for removed, added, start in hunks:
        added_count = sum(a.count(REPLACEMENT_CHAR) for a in added)
        if not added_count:
            continue
        removed_count = sum(r.count(REPLACEMENT_CHAR) for r in removed)
        if added_count > removed_count:
            lines.extend(start + k for k, a in enumerate(added) if REPLACEMENT_CHAR in a)
    return lines


# ── request validation & verdict ─────────────────────────────────────────────────

def validate_fields(body: dict) -> Optional[dict]:
    """Shape check of the guard fields. Returns 422 details or None."""
    base = body.get("base_sha256")
    if base is not None and not is_valid_sha256(base):
        return {"reason": "invalid_base_sha256"}
    for key in ("allow_mask_substitution", "allow_replacement_char"):
        if body.get(key) is not None and not isinstance(body[key], bool):
            return {"reason": "invalid_field", "field": key}
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        return {"reason": "invalid_field", "field": "reason"}
    if (body.get("allow_mask_substitution") or body.get("allow_replacement_char")) and not (reason or "").strip():
        return {"reason": REASON_OVERRIDE_REASON_REQUIRED}
    return None


def base_mismatch(body: dict, current_sha256: Optional[str]) -> Optional[dict]:
    """409 details when ``base_sha256`` was sent and the disk no longer matches."""
    base = body.get("base_sha256")
    if base is None:
        return None
    if current_sha256 is not None and current_sha256 == base.lower():
        return None
    return {"reason": REASON_BASE_CHANGED, "current_sha256": current_sha256}


def _clip(lines: list[int]) -> list[int]:
    return lines[:MAX_REPORTED_LINES]


def judge(body: dict, old_text: Optional[str], new_text: str) -> tuple[Optional[dict], Optional[dict]]:
    """Run (c) and (e) on a proposed change.

    Returns ``(refusal_details, override_report)``: refusal_details is the 422
    details dict when the change must be refused, override_report describes the
    findings an override flag let through (for the response and the audit log).
    The refusal never echoes line *text*: the removed line may be the secret.
    """
    hunks = changed_hunks(old_text, new_text)
    masked = masked_substitution_lines(hunks)
    replaced = replacement_char_lines(hunks)
    overridden: dict = {}
    if masked:
        if not body.get("allow_mask_substitution"):
            return ({"reason": REASON_MASKED, "lines": _clip(masked), "line_count": len(masked),
                     "override": "allow_mask_substitution+reason"}, None)
        overridden[REASON_MASKED] = _clip(masked)
    if replaced:
        if not body.get("allow_replacement_char"):
            return ({"reason": REASON_REPLACEMENT_CHAR, "lines": _clip(replaced), "line_count": len(replaced),
                     "override": "allow_replacement_char+reason"}, None)
        overridden[REASON_REPLACEMENT_CHAR] = _clip(replaced)
    if not overridden:
        return None, None
    return None, {"overridden": overridden, "reason": body.get("reason")}


# ── (f) post-write syntax check ─────────────────────────────────────────────────

def post_write_check(path: str, data: bytes, encoding: str) -> Optional[dict]:
    """Syntax check of the stored bytes; None for file kinds that are not checked."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        checker = "python_compile"
    elif suffix == ".json":
        checker = "json_parse"
    else:
        return None
    if len(data) > CHECK_LIMIT_BYTES:
        return {"checker": checker, "status": "skipped", "reason": "too_large"}
    try:
        if checker == "python_compile":
            # bytes keep the BOM / coding-cookie handling of the real interpreter.
            source = data if _is_utf8(encoding) else data.decode(encoding)
            compile(source, path, "exec", flags=ast.PyCF_ONLY_AST, dont_inherit=True)
        else:
            json.loads(data.decode(encoding))
    except SyntaxError as exc:
        return {"checker": checker, "status": "syntax_error", "line": exc.lineno,
                "offset": exc.offset, "message": exc.msg}
    except json.JSONDecodeError as exc:
        return {"checker": checker, "status": "syntax_error", "line": exc.lineno,
                "offset": exc.colno, "message": exc.msg}
    except (ValueError, UnicodeDecodeError) as exc:
        return {"checker": checker, "status": "syntax_error", "line": None,
                "offset": None, "message": str(exc)[:200]}
    return {"checker": checker, "status": "ok"}
