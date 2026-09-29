"""Shared path-safety helpers for source-root sandboxing (L0006 §4).

A small, single-responsibility module for "is this relative path/pattern safe,
and where does it resolve inside the project root?". Modeled on the path jail
already used by file_transfer_routes (_is_valid_relative_path / _under_root /
os.path.realpath) and on Hivework `http_tools._resolve` (NR0009 §4.3, NR0011 §2):
lexical traversal rejection + realpath root-containment that also defeats
symlink escape.

Two checks (L0006 §4.2):
  • is_safe_relative(value)   — shape and normalisation: rejects empty strings, absolute paths,
                                drive letters and `..` segments; wildcards (`*`/`**`/`?`) are allowed.
  • resolve_in_root(root, rel) — realpath then jail to the root (symlink escapes included) → an absolute Path or None.

`is_safe_relative` covers both plain paths and path-bearing patterns (grep's
`glob`, glob's `pattern`): a `..` component is rejected wherever it appears,
while `*`/`**`/`?` are left untouched (e.g. `../secrets/*` → rejected, `src/**/*.ts` → allowed).
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


class MutationPathUnsafeError(ValueError):
    """The requested mutation path is not a safe source-relative path."""


class MutationPathAliasError(MutationPathUnsafeError):
    """The requested mutation path crosses a symlink/junction/reparse alias."""


def _same_path_identity(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def resolve_mutation_target_no_alias(
    root: Path,
    rel: str,
    *,
    allow_missing_leaf: bool,
) -> Path:
    """Resolve an ordinary source mutation target without following aliases.

    Unlike resolve_in_root(), a symlink/junction/reparse alias is rejected even when
    its resolved target remains inside root. This keeps the lexical source path used
    by TR2 ownership identical to the filesystem object an ordinary mutation touches.
    """
    if not is_safe_relative(rel):
        raise MutationPathUnsafeError(rel)

    root_real = os.path.realpath(str(root))
    normalized = rel.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise MutationPathUnsafeError(rel)

    candidate = os.path.normpath(os.path.join(root_real, *parts))
    if not _under_root(candidate, root_real):
        raise MutationPathUnsafeError(rel)

    resolved = os.path.realpath(candidate)
    if not _under_root(resolved, root_real):
        raise MutationPathAliasError(rel)
    if not _same_path_identity(candidate, resolved):
        raise MutationPathAliasError(rel)

    target = Path(candidate)
    if not allow_missing_leaf and not target.exists():
        raise MutationPathUnsafeError(rel)
    # os.path.realpath(candidate) already resolves every existing ancestor while
    # preserving a missing suffix. The identity comparison above therefore rejects
    # an aliased deepest-existing ancestor without requiring the immediate parent
    # to exist. This keeps safe creation of new nested directories/files possible.
    return target
