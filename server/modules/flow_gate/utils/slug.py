"""Convert project name to slug (D009 §3-6 / D013 §3-1)."""
from __future__ import annotations

import re
from unidecode import unidecode

# Allowed characters: a-z, 0-9, -, _
ALLOWED = re.compile(r'[a-z0-9\-_]')
DANGEROUS = re.compile(r'[/\\:*?"<>|]')

_RESERVED = frozenset({"__SYSTEM__", "__ALL__", ".", ".."})

_MAX_LEN = 50


def project_name_to_slug(name: str) -> str:
    """Convert a user-provided project_name into a PK-safe slug.

    Allowed characters: a-z, 0-9, -, _
    Conversion rules:
      - Uppercase ASCII letters → lowercase
      - Spaces → hyphens
      - Consecutive hyphens/underscores → single
      - Trim leading/trailing - and _
      - Reject URL-dangerous characters (/, \\, :, *, ?, ", <, >, |, whitespace other than spaces) with ValueError
      - Maximum length 50 characters
      - Empty result → ValueError
      - Reserved names (__SYSTEM__, __ALL__, '.', '..') → ValueError
    """
    if DANGEROUS.search(name):
        raise ValueError(f"Project name contains invalid characters: {name!r}")

    # Reject whitespace other than spaces
    if re.search(r'[\t\n\r\f\v]', name):
        raise ValueError(f"Project name may not contain whitespace other than spaces: {name!r}")

    # Reserved name check (applies to original input)
    if name in _RESERVED:
        raise ValueError(f"Project name is reserved: {name!r}")

    slug = name.lower()
    slug = slug.replace(" ", "-")

    # Keep only allowed characters
    slug = "".join(ch for ch in slug if ALLOWED.match(ch))

    # Collapse consecutive hyphens/underscores into single
    slug = re.sub(r'-{2,}', '-', slug)
    slug = re.sub(r'_{2,}', '_', slug)

    # Strip leading/trailing - and _
    slug = slug.strip("-_")

    if not slug:
        raise ValueError(f"Slug is empty after conversion: {name!r}")

    # Reserved name check (applies to converted slug)
    if slug in {r.lower() for r in _RESERVED}:
        raise ValueError(f"Slug is a reserved name: {slug!r}")

    if len(slug) > _MAX_LEN:
        raise ValueError(f"Slug exceeds maximum length of {_MAX_LEN} characters: {slug!r}")

    return slug


def romanize_to_slug(text: str) -> str:
    """
    Transliterate non-ASCII text (Korean, Japanese, Chinese, etc.) into a slug per M031 policy (`[a-z0-9_-]`).
    Raises ValueError on empty input or if transliteration yields an empty slug.
    Intended for cases where the user can edit the result.
    """
    if not text:
        raise ValueError("text is empty")
    romanized = unidecode(text)
    # Reuse existing slug conversion pipeline (lowercase, spaces→hyphens, allowed-character filter)
    return project_name_to_slug(romanized)

