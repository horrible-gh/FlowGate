"""Digit-width application, formatting, and parsing.

Formatting and parsing utilities for group, subgroup, and document-type codes.

Default digit widths:
  - Group:     4 -> "0001"
  - Subgroup:  3 -> "001"
  - Document:  4 -> "R0001"  (type_code prefix + 4-digit seq)
"""
from __future__ import annotations

import re


# ── Format ────────────────────────────────────────────────────────────────────

def format_group_code(seq: int, width: int = 4) -> str:
    """Format a group sequence number as a string with the specified width."""
    return str(seq).zfill(width)


def format_subgroup_code(seq: int, width: int = 3) -> str:
    """Format a subgroup sequence number as a string with the specified width."""
    return str(seq).zfill(width)


def format_doc_code(type_code: str, seq: int, width: int = 4) -> str:
    """Format a document-type code. Example: ('R', 1, 4) -> '0001-R'"""
    return f"{str(seq).zfill(width)}-{type_code}"


# ── Parse ─────────────────────────────────────────────────────────────────────

def parse_group_code(code: str) -> int:
    """Parse a group code string into an integer. Example: '0001' -> 1"""
    return int(code)


def parse_subgroup_code(code: str) -> int:
    """Parse a subgroup code string into an integer. Example: '001' -> 1"""
    return int(code)


def parse_doc_code(code: str) -> tuple[str, int]:
    """Parse a document code into a (type_code, seq) tuple.

    Example: '0001-R' -> ('R', 1), '0012-DS' -> ('DS', 12)
    """
    m = re.match(r'^(\d+)-([A-Za-z]+)$', code)
    if not m:
        raise ValueError(f"Invalid document code format: {code!r}")
    return m.group(2), int(m.group(1))


# ── Reformat ──────────────────────────────────────────────────────────────────

def reformat_code(code: str, new_width: int, kind: str) -> str:
    """Reformat an existing code to a new digit width.

    Parameters
    ----------
    code : str
        Existing code string.
    new_width : int
        New digit width.
    kind : str
        'group' | 'subgroup' | 'document'

    Returns
    -------
    str
        Reformatted code string.
    """
    if kind == "group":
        return format_group_code(parse_group_code(code), new_width)
    if kind == "subgroup":
        return format_subgroup_code(parse_subgroup_code(code), new_width)
    if kind == "document":
        type_code, seq = parse_doc_code(code)
        return format_doc_code(type_code, seq, new_width)
    raise ValueError(f"Unknown kind: {kind!r}")


def extract_numeric_suffix(code: str) -> str:
    """Extract the numeric part from a code. '0001' -> '0001', '0001-R' -> '0001'"""
    m = re.match(r'^(\d+)-[A-Za-z]+$', code)
    if m:
        return m.group(1)
    m = re.search(r'\d+$', code)
    return m.group(0) if m else code


def infer_code_width(code: str, kind: str) -> int:
    """Infer the numeric digit width from a code."""
    if kind == "document":
        _, seq = parse_doc_code(code)
        # Return the length of the numeric portion in the original code
        numeric = extract_numeric_suffix(code)
        return len(numeric)
    return len(code)
