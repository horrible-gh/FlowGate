"""Source-capture utilities shared by the source fingerprint and legacy Snapshot (0672 T0004 stage 3).

These used to live in ``snapshot_materialization_service``. They belong to no single
feature: capture exclusion policy, worker-facing locator redaction, and the path-only
guard that keeps legacy Snapshot copies from being promoted into the live worktree
through the canonical mutation tools. (0684 T#4 removed Source Bundle, the other
original user, and its Bundle/AI Scratch promotion guard with it.)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from modules.flow_gate.services import git_service, token_service
from modules.flow_gate.storage import paths as storage_paths

# The legacy Snapshot scratch directory name; kept out of every capture.
LEGACY_SNAPSHOT_NAMESPACE = "source-snapshots"

# Capture copy policy (source fingerprint and legacy Snapshot) is deliberately narrower than the TR
# change-list debris policy: dot-prefixed source such as .github remains source, while known
# VCS/tool/build trees do not.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env", "site-packages",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".cache", ".parcel-cache", ".turbo",
    "build", "dist", "htmlcov", "coverage", "coverage_html_report",
    "tmp", "temp", LEGACY_SNAPSHOT_NAMESPACE,
})
EXCLUDED_FILE_NAMES = frozenset({".coverage"})
EXCLUDED_DIR_PREFIXES = (".test-tmp", "pytest-cache-files-")

# Worker-facing text (API tool results, CLI HTTP bodies, and the durable failure_reason /
# cleanup_last_error columns they re-surface) must never carry a server-internal absolute
# path. Known locator roots are replaced by a marker that keeps the relative suffix for
# diagnosis; error text is additionally scrubbed of any remaining absolute path.
SCRATCH_REDACTION = "<flowgate-snapshot-scratch>"
WORKTREE_REDACTION = "<flowgate-worktree>"
STORAGE_REDACTION = "<flowgate-storage>"
SERVER_REDACTION = "<flowgate-server>"
PATH_REDACTION = "<redacted-path>"
_SERVER_ROOT = Path(__file__).resolve().parents[3]
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s'\"<>|]*"          # C:\x, C:/x, and repr C:\\x
    r"|(?<![^\s'\"(\[=,])(?:\\){2,4}[^\\/\s'\"<>|]+[\\/]+[^\s'\"<>|]*"  # UNC and \\?\ forms, at a token start
    r"|(?<![\w.~:/\\>-])/[^\s'\"<>/\\]+/[^\s'\"<>]*"      # /abs/path (two or more segments)
)


def locator_roots(row: dict | None) -> tuple[tuple[str, str], ...]:
    """(absolute root, marker) pairs whose prefixes must never reach an AI worker.

    Covers the token scratch root (snapshot final/source/.flowgate-tmp all nest under it),
    the live group worktree the snapshot copies from, the storage root that holds every
    scratch and worktree, and the server install root. Raw and resolved spellings are both
    kept (8.3 short names, junctioned roots). Best-effort: a lookup that fails only drops
    that root, and the error-text path still falls back to the generic absolute-path scrub.
    """
    roots: list[tuple[str, str]] = []

    def add(value: object, marker: str) -> None:
        if not value:
            return
        path = Path(str(value))
        candidates = [path]
        try:
            candidates.append(path.resolve())
        except (OSError, RuntimeError):
            pass
        for candidate in candidates:
            # Never redact a bare drive or a top-level directory such as /data.
            if candidate.is_absolute() and len(candidate.parts) >= 3:
                roots.append((str(candidate), marker))

    row = row or {}
    project_id = str(row.get("project_id") or "")
    token_id = str(row.get("token_id") or "")
    group_id = str(row.get("group_id") or "")
    if project_id and token_id:
        try:
            add(token_service.scratch_dir_path(project_id, token_id), SCRATCH_REDACTION)
        except Exception:
            pass
    if project_id and group_id:
        try:
            worktree, _reason = git_service.effective_src_root_ex(project_id, group_id)
            add(worktree, WORKTREE_REDACTION)
        except Exception:
            pass
    try:
        add(storage_paths.get_storage_root(project_id or None), STORAGE_REDACTION)
    except Exception:
        pass
    add(_SERVER_ROOT, SERVER_REDACTION)
    return tuple(dict.fromkeys(roots))


def redact_locators(text: str, roots: Iterable[tuple[str, str]]) -> str:
    """Replace every spelling of each root prefix: native, '/', '\\', and the doubled
    backslashes an OSError/repr() rendering produces. Longest root wins, so the scratch
    marker survives even though the scratch lives under the storage root."""
    pairs: dict[str, str] = {}
    for root, marker in roots:
        base = str(root or "")
        if not base:
            continue
        windows = base.replace("/", "\\")
        for needle in (base, base.replace("\\", "/"), windows, windows.replace("\\", "\\\\")):
            pairs.setdefault(needle, marker)
    flags = re.IGNORECASE if os.name == "nt" else 0
    for needle in sorted(pairs, key=len, reverse=True):
        marker = pairs[needle]
        text = re.sub(re.escape(needle) + r"(?![\w-])", lambda _m, marker=marker: marker, text, flags=flags)
    return text


def redact_error_text(text: str, roots: Iterable[tuple[str, str]]) -> str:
    """Error/diagnostic text: known roots get markers, any other absolute path is removed."""
    return _ABSOLUTE_PATH_RE.sub(PATH_REDACTION, redact_locators(str(text), roots))


MUTATION_TOOL_NAMES = frozenset({"write_source_file", "patch_source_file", "remove_source_file"})
_SNAPSHOT_ARTIFACT_RE = re.compile(r"(?:^|/)source-snapshots/(snap_[A-Za-z0-9_-]+)(?:/|$)")


class PromotionBlocked(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def payload(self, operation: str) -> dict:
        return {"ok": False, "op": operation, "error": {"code": self.code, "message": self.message}}


def guard_artifact_promotion(tool_name: str, tool_input: dict) -> None:
    """Refuse a live mutation whose path names a legacy Snapshot copy."""
    if tool_name not in MUTATION_TOOL_NAMES:
        return
    raw = str(tool_input.get("path") or "").replace("\\", "/")
    if _SNAPSHOT_ARTIFACT_RE.search(raw):
        raise PromotionBlocked(
            403, "snapshot_promotion_blocked",
            "snapshot files cannot be promoted, uploaded, committed, merged, or synced to source; "
            "make persistent edits through canonical FlowGate mutation paths using worktree-relative paths",
        )
