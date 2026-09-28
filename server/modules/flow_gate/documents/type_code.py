"""Canonical document type and document-code grammar (flowgate.default.0565)."""
from __future__ import annotations

import re

TYPE_CODE_MAX_LEN = 4
TYPE_CODE_PATTERN = r"[A-Z][A-Z0-9]{0,3}"
TYPE_CODE_RE = re.compile(rf"^{TYPE_CODE_PATTERN}$")
DOC_CODE_RE = re.compile(rf"^(?P<seq>\d+)-(?P<type>{TYPE_CODE_PATTERN})$")
DOC_ID_TAIL_RE = re.compile(rf"\.(?P<seq>\d+)-(?P<type>{TYPE_CODE_PATTERN})$")
STEP_KEY_RE = re.compile(rf"^(?P<type>{TYPE_CODE_PATTERN})#(?P<ordinal>[1-9][0-9]*)$")


def is_valid_type_code(value: object) -> bool:
    return isinstance(value, str) and TYPE_CODE_RE.fullmatch(value) is not None


def parse_doc_code(code: str, *, allow_lowercase: bool = True) -> tuple[str, int]:
    if not isinstance(code, str):
        raise ValueError(f"Invalid document code format: {code!r}")
    candidate = code.upper() if allow_lowercase else code
    match = DOC_CODE_RE.fullmatch(candidate)
    if not match:
        raise ValueError(f"Invalid document code format: {code!r}")
    return match["type"], int(match["seq"])


def doc_code_type(code: str) -> str | None:
    try:
        return parse_doc_code(code)[0]
    except ValueError:
        return None


def doc_code_seq_text(code: str) -> str | None:
    if not isinstance(code, str):
        return None
    match = DOC_CODE_RE.fullmatch(code.upper())
    return match["seq"] if match else None


def doc_id_type_code(doc_id: str) -> str | None:
    if not isinstance(doc_id, str):
        return None
    match = DOC_ID_TAIL_RE.search(doc_id)
    return match["type"] if match else None


def parse_step_key(key: str) -> tuple[str, int] | None:
    if not isinstance(key, str):
        return None
    match = STEP_KEY_RE.fullmatch(key)
    return (match["type"], int(match["ordinal"])) if match else None
