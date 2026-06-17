"""ID format validation utilities (T261 §1 — canonical regex, same as T260 §5).

Functions:
    validate_project_id(value) -> str
    validate_group_id(value)   -> str
    validate_doc_id(value)     -> str

Each function returns the value unchanged on success and raises ValueError on failure.
The router catches these and converts them to HTTPException(422).
"""
from __future__ import annotations

import re

SLUG_CHARS = r'[a-z0-9_\-]+'

PROJECT_ID = re.compile(rf'^{SLUG_CHARS}$')

GROUP_ID = re.compile(
    rf'^{SLUG_CHARS}\.(?:none|[a-z0-9_]+)\.\d{{4}}$'
)

DOC_ID = re.compile(
    rf'^{SLUG_CHARS}\.(?:none|[a-z0-9_]+)\.\d{{4}}\.\d{{4}}-[A-Z]+$'
)


def validate_project_id(value: str) -> str:
    """Validate canonical project_id format."""
    if value is None:
        raise ValueError("project_id cannot be None")
    value = str(value)
    if not PROJECT_ID.match(value):
        raise ValueError(f"project_id format is invalid: {value!r}")
    return value


def validate_group_id(value: str) -> str:
    """Validate canonical group_id format."""
    if value is None:
        raise ValueError("group_id cannot be None")
    value = str(value)
    if not GROUP_ID.match(value):
        raise ValueError(f"group_id format is invalid: {value!r}")
    return value


def validate_doc_id(value: str) -> str:
    """Validate canonical doc_id format."""
    if value is None:
        raise ValueError("doc_id cannot be None")
    value = str(value)
    if not DOC_ID.match(value):
        raise ValueError(f"doc_id format is invalid: {value!r}")
    return value
