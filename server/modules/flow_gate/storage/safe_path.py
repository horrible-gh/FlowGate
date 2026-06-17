"""Shared path-safety helpers for source-root sandboxing (L0006 §4).

A small, single-responsibility module for "is this relative path/pattern safe,
and where does it resolve inside the project root?". Modeled on the path jail
already used by file_transfer_routes (_is_valid_relative_path / _under_root /
os.path.realpath) and on Hivework `http_tools._resolve` (NR0009 §4.3, NR0011 §2):
lexical traversal rejection + realpath root-containment that also defeats
symlink escape.

Two checks (L0006 §4.2):
  • is_safe_relative(value)   — 형식·정규화: 빈 문자열/절대경로/드라이브/`..` 세그먼트 거부.
                                와일드카드(`*`/`**`/`?`)는 정상 입력으로 허용한다.
  • resolve_in_root(root, rel) — realpath 후 루트 봉쇄(심볼릭 탈출 포함) → 절대 Path 또는 None.

`is_safe_relative` covers both plain paths and path-bearing patterns (grep의
`glob`, glob의 `pattern`): a `..` component is rejected wherever it appears,
while `*`/`**`/`?` are left untouched (e.g. `../secrets/*` → 거부, `src/**/*.ts` → 허용).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def is_safe_relative(value: str) -> bool:
    """True if `value` is a non-empty, root-relative path/pattern with no escape.

    Rejects: non-string, empty string, absolute paths (leading `/`), Windows
    drive prefixes (`C:`), and any `..` path segment. Wildcards are allowed
    (they are not path-traversal). Use for both path fields and path-bearing
    patterns; the *absence* of a required field is the caller's (④) concern.
    """
    if not isinstance(value, str) or value == "":
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if _DRIVE_RE.match(normalized):
        return False
    for seg in normalized.split("/"):
        if seg == "..":
            return False
    return True


def _under_root(full_path: str, root: str) -> bool:
    """True if full_path == root or sits under root (os.sep-aware prefix check)."""
    root_prefix = root if root.endswith(os.sep) else root + os.sep
    return full_path == root or full_path.startswith(root_prefix)


def resolve_in_root(root: Path, rel: str) -> Optional[Path]:
    """Resolve a relative path under `root`, or None if it escapes the root.

    Uses os.path.realpath so that symlinks pointing outside the root are caught
    (defense in depth beyond the lexical is_safe_relative check). An empty `rel`
    resolves to the root itself (the project root is a valid base directory).
    """
    root_real = os.path.realpath(str(root))
    full = os.path.realpath(os.path.join(root_real, rel or ""))
    if not _under_root(full, root_real):
        return None
    return Path(full)
