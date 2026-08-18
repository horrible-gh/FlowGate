"""Tool-debris detection — the single rule shared by the screens and the check (0382 NR0003 proposal 3).

The 0382 B0001 incident happened because there were two rules. The screens (file explorer,
change list, final approval) used "hide it if any path segment starts with a dot", while the
submission check (tr_scope_service) used "only look at whether the first segment is a dot".
So 261 ``server/.test-tmp-0313/...`` files ended up **invisible on every screen yet blocking
submission**, and rode the finalize commit into main without anyone seeing them.

So the decision code is gathered here. The NR's conclusion that "tr_scope_service.is_excluded_path
is canonical" holds by name — that name merely re-exports this module, and git_service's
screen filter calls the same function.

There are four categories (inherited from 0299 D0004 §3.3, with one added in 0382).

1) Top-level entries starting with a dot — ``.git/``, ``.venv/``, ``.env`` and so on.
2) **Dot directories mid-path** (added in 0382) — this is what catches ``server/.test-tmp-0313/...``.
   The final segment (the filename) is deliberately exempt, so a genuinely edited config file
   like ``client/src/.eslintrc.json`` cannot vanish silently (0299's original judgement stands).
3) Directory names and prefixes tools create — ``node_modules``, ``.pytest_cache``,
   ``.test-tmp-0313`` and so on.
4) File extensions produced by running things — ``*.db``, ``*.pyc``, ``*.log`` and so on.

There is no per-project configuration. This is a list of "traces tools leave behind", and
varying it per project would vary the baseline of the check itself, making verdicts
incomparable across projects. Anything that needs adding is added here.
"""
from __future__ import annotations

from typing import Iterable, Optional

# ── Reasons (the classification that lets a screen say "why it was excluded") ──
REASON_DOT_TOPLEVEL = "dot_toplevel"
REASON_DOT_DIRECTORY = "dot_directory"
REASON_TOOL_DIRECTORY = "tool_directory"
REASON_GENERATED_FILE = "generated_file"
REASON_EMPTY = "empty_path"

EXCLUSION_REASONS = (
    REASON_DOT_TOPLEVEL,
    REASON_DOT_DIRECTORY,
    REASON_TOOL_DIRECTORY,
    REASON_GENERATED_FILE,
    REASON_EMPTY,
)

# 0382: name prefixes of scratch directories tests used to create inside the repository. The
# test-side default moved outside the repo (proposal 2-a), but they are kept here too so that
# already-created ones, and ones drifting in from other checkouts, are recognised identically by screens, checks and finalize.
TEST_SCRATCH_PREFIX = ".test-tmp"

_EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".log")
_EXCLUDED_DIR_SEGMENTS = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", ".tox", "site-packages",
})
_EXCLUDED_DIR_PREFIXES = ("pytest-cache-files-", TEST_SCRATCH_PREFIX)


def normalize_repo_path(path: str) -> str:
    """Repo-relative path with empty and current-directory (``.``) segments removed before comparison."""
    return "/".join(
        segment
        for segment in path.replace("\\", "/").split("/")
        if segment not in ("", ".")
    )


def exclusion_reason(path: str) -> Optional[str]:
    """The reason, if this path is debris left by a tool or the environment rather than work output; else None."""
    normalized = normalize_repo_path(path)
    if not normalized:
        return REASON_EMPTY
    segments = normalized.split("/")
    if segments[0].startswith("."):
        return REASON_DOT_TOPLEVEL
    # Drop the last segment since it may be a filename — only directory segments are examined.
    if any(seg.startswith(".") for seg in segments[:-1]):
        return REASON_DOT_DIRECTORY
    if any(seg in _EXCLUDED_DIR_SEGMENTS for seg in segments):
        return REASON_TOOL_DIRECTORY
    if any(seg.startswith(_EXCLUDED_DIR_PREFIXES) for seg in segments):
        return REASON_TOOL_DIRECTORY
    if segments[-1].lower().endswith(_EXCLUDED_SUFFIXES):
        return REASON_GENERATED_FILE
    return None


def is_excluded_path(path: str) -> bool:
    """Is this debris left by a tool or the environment rather than work output (0299 D0004 §3.3 + 0382)?"""
    return exclusion_reason(path) is not None


def partition_paths(paths: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(work output, tool debris)`` — input order is preserved.

    The shape the finalize-commit gate uses. The key point is that debris is **returned
    separately rather than discarded**. 0382's prevention principle is "never exclude
    silently", so callers put the second list into the result and the event and show it on screen.
    """
    kept: list[str] = []
    artifacts: list[str] = []
    for path in paths:
        (artifacts if is_excluded_path(path) else kept).append(path)
    return kept, artifacts
