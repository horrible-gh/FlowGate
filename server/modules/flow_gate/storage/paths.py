"""Storage path pattern definitions.

Calculates the project document root and module/group/subgroup/document
folder paths.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


def _sanitize_part(s: str) -> str:
    """Normalize a filesystem path segment (only Korean/Latin/digits/underscore/hyphen allowed)."""
    return re.sub(r"[^\w\-]", "_", s, flags=re.UNICODE) or "_"


def default_storage_root() -> Path:
    """Return the default storage root relative to the server's working directory."""
    return Path.cwd() / "storage"


def _project_override_root(project_id: Optional[str]) -> Optional[Path]:
    """Read the storage_root_override value from project settings.

    Returns None on failure. Uses lazy import to avoid circular imports with
    config/db modules.
    """
    if not project_id:
        return None
    try:
        from modules.flow_gate.db import projects as _proj  # lazy
    except Exception:
        return None
    try:
        settings = _proj.get_settings(project_id)
    except Exception:
        return None
    if not settings:
        return None
    override = (settings.get("storage_root_override") or "").strip()
    return Path(override) if override else None


def _system_storage_root() -> Optional[Path]:
    """Read the system_settings.storage_root value.

    Returns None on failure. Uses lazy import to avoid circular imports.
    """
    try:
        from modules.flow_gate.db import system_settings as _sys  # lazy
    except Exception:
        return None
    try:
        value = _sys.get_value("storage_root")
    except Exception:
        return None
    if not value:
        return None
    value = value.strip()
    return Path(value) if value else None


def get_storage_root(
    project_id: Optional[str] = None,
    create: bool = False,
) -> Path:
    """Return the storage root path by priority.

    ① FLOWGATE_STORAGE_DIR environment variable
    ② project_settings.storage_root_override (when project_id is given)
    ③ system_settings.storage_root
    ④ default_storage_root()
    """
    env = os.environ.get("FLOWGATE_STORAGE_DIR", "").strip()
    if env:
        root = Path(env)
    else:
        override = _project_override_root(project_id)
        if override:
            root = override
        else:
            system_root = _system_storage_root()
            root = system_root if system_root else default_storage_root()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _slugify_project_name(name: str) -> str:
    """Convert a project name to a directory-safe slug, preserving Korean/Unicode."""
    slug = name.strip()
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"[^\w\-]", "", slug, flags=re.UNICODE)
    slug = slug.strip("_-")
    return slug


def project_dir_name(project_id: str) -> str:
    """Project directory name = sanitized project_id."""
    if not project_id:
        return project_id
    return _sanitize_part(project_id)


def src_root(project_name: str, branch: str = 'main') -> Path:
    """Source exploration root path.

    {storage_root}/src/{project_name}/{branch}
    """
    return get_storage_root() / 'src' / project_name / branch


def project_root(
    project_id: str,
    storage_root: Optional[Path] = None,
    *,
    branch: str = "main",
) -> Path:
    """Project document root path.

    {storage_root}/documents/{project_name_slug}/{branch}/
    """
    root = storage_root or get_storage_root(project_id)
    return root / "documents" / project_dir_name(project_id) / branch


def module_path(
    project_id: str,
    module: str = "none",
    storage_root: Optional[Path] = None,
    *,
    branch: str = "main",
) -> Path:
    """Module folder path.

    {project_root}/{module}/
    """
    return project_root(project_id, storage_root, branch=branch) / (module or "none")


def group_dir_name(group_id: str) -> str:
    """Group directory name = sanitized last segment (group_num) of group_id."""
    if not group_id:
        return group_id
    if "." in group_id:
        return _sanitize_part(group_id.rsplit(".", 1)[-1])
    if "-" in group_id:
        return _sanitize_part(group_id.rsplit("-", 1)[-1])
    return _sanitize_part(group_id)


def group_path(
    project_id: str,
    group_code: str,
    storage_root: Optional[Path] = None,
    module: str = "none",
    *,
    branch: str = "main",
) -> Path:
    """Group folder path.

    {project_root}/{module}/{group_seq}/
    """
    return module_path(project_id, module, storage_root, branch=branch) / group_dir_name(group_code)


def subgroup_path(
    project_id: str,
    group_code: str,
    subgroup_code: str,
    storage_root: Optional[Path] = None,
    module: str = "none",
    *,
    branch: str = "main",
) -> Path:
    """Subgroup folder path.

    {project_root}/{module}/{group_seq}/{subgroup_seq}/
    """
    return group_path(project_id, group_code, storage_root, module, branch=branch) / group_dir_name(subgroup_code)


def document_path(
    project_id: str,
    group_code: str,
    doc_code: str,
    filename: str,
    subgroup_code: Optional[str] = None,
    storage_root: Optional[Path] = None,
    module: str = "none",
    *,
    branch: str = "main",
) -> Path:
    """Document file path.

    With subgroup:    {project_root}/{module}/{group_code}/{subgroup_code}/{doc_code}_{filename}
    Without subgroup: {project_root}/{module}/{group_code}/{doc_code}_{filename}
    """
    if subgroup_code:
        base = subgroup_path(project_id, group_code, subgroup_code, storage_root, module, branch=branch)
    else:
         base = group_path(project_id, group_code, storage_root, module, branch=branch)
    return base / f"{doc_code}_{filename}"


# ── Storage-relative path persistence (B0054.0001 / L0054.0002) ──────────────
# Single source of truth: writers persist storage-root-relative POSIX paths,
# readers resolve them back through resolve_storage_path(). This makes path
# columns invariant across host/OS migration (the B0001 root cause: Windows
# absolute paths like ``E:\...`` were stored verbatim and broke on Linux).
# These two functions absorb the three legacy resolve variants
# (documents._document_file_path, process_service._resolve_storage_path,
# document_routes._fallback_file_path).

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _allowed_roots(project_id: Optional[str] = None) -> list[Path]:
    """Filesystem jail: storage root plus the special storage subtrees.

    Lazy-imports db to avoid a circular import (db/__init__ imports this module
    at load time). Mirrors the allow-list previously inlined in
    documents._document_file_path.
    """
    roots = [get_storage_root(project_id).resolve()]
    try:
        from modules.flow_gate import db  # lazy — avoid import cycle
        for attr in (
            "STORAGE_DIR",
            "TEST_REPORTS_DIR",
            "TEST_REPORTS_ARCHIVE_DIR",
            "DESIGN_REOPEN_DIR",
        ):
            val = getattr(db, attr, None)
            if val:
                roots.append(Path(val).resolve())
    except Exception:
        pass
    return roots


def _within_allowed_roots(path: Path, project_id: Optional[str] = None) -> bool:
    resolved = path.resolve(strict=False)
    return any(
        resolved == root or _is_relative_to(resolved, root)
        for root in _allowed_roots(project_id)
    )


def to_storage_relative(abs_path, project_id: Optional[str] = None) -> str:
    """Write helper → storage-root-relative POSIX path for DB persistence.

    Idempotent: a value that is already relative is returned normalized (so this
    can never double-relativize a path that was loaded from the DB). Absolute
    paths outside the storage root are preserved as POSIX with a warning — this
    should not happen for managed files.
    """
    raw = str(abs_path).replace("\\", "/")
    p_in = Path(abs_path)
    # Already storage-relative (no leading slash, no drive letter) → no-op.
    if not p_in.is_absolute() and not _DRIVE_RE.match(raw):
        return Path(raw).as_posix()
    root = get_storage_root(project_id).resolve()
    p = p_in.resolve(strict=False)
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        _log.warning("path outside storage root: %s (root=%s)", p, root)
        return p.as_posix()


def _apply_branch_segment(stored: str, branch: str) -> Optional[str]:
    """Insert or remove the branch segment after ``/documents/<project>/``.

    Absorbs document_routes._fallback_file_path: handles paths that predate (or
    double-count) the branch directory level.
    """
    if not stored or not branch:
        return None
    norm = stored.replace("\\", "/")
    # Match an optional prefix, the documents/<project>/ segment, then the rest.
    # Works for both canonical relative ("documents/<proj>/...") and legacy
    # absolute ("/home/.../documents/<proj>/...") forms.
    m = re.search(r"(.*?)(documents/[^/]+/)(.*)", norm)
    if not m:
        return None
    prefix, doc_seg, after_project = m.group(1), m.group(2), m.group(3)
    if not after_project:
        return None
    first_seg = after_project.split("/")[0]
    if first_seg == branch:
        rest = "/".join(after_project.split("/")[1:])
        return prefix + doc_seg + rest
    return prefix + doc_seg + branch + "/" + after_project


def resolve_storage_path(
    stored: str,
    project_id: Optional[str] = None,
    branch: str = "main",
) -> Optional[Path]:
    """Read helper → resolve a stored file path to a real, jailed Path, or None.

    Handles, in order:
      ① legacy ``/storage/...`` prefix (old process_service form),
      ② legacy absolute path (POSIX or ``C:`` Windows drive),
      ③ canonical storage-relative path (the new standard),
      ④ branch-segment drift (absorbs _fallback_file_path).
    Returns None when nothing resolves to a file inside the allowed roots.
    """
    if not stored:
        return None
    s = stored.strip().replace("\\", "/")
    root = get_storage_root(project_id).resolve()

    if s.startswith("/storage/"):
        cand = root / s[len("/storage/"):]
    elif Path(s).is_absolute() or _DRIVE_RE.match(s):
        cand = Path(s)
    else:
        cand = root / s

    cand = cand.resolve(strict=False)
    if _within_allowed_roots(cand, project_id) and cand.is_file():
        return cand

    fb = _apply_branch_segment(s, branch)
    if fb:
        fcand = (
            Path(fb) if (Path(fb).is_absolute() or _DRIVE_RE.match(fb)) else root / fb
        ).resolve(strict=False)
        if _within_allowed_roots(fcand, project_id) and fcand.is_file():
            return fcand

    return None


def resolve_storage_dir(stored: str, project_id: Optional[str] = None) -> Optional[Path]:
    """Resolve a stored *directory* path (e.g. tokens.scratch_dir) to an absolute Path.

    Directory analogue of resolve_storage_path: no ``is_file`` check and it
    tolerates a not-yet-created directory. Relative values join the storage root;
    legacy absolute values pass through. Used so consumers that need a usable OS
    path (the doc_path security jail, worker scratch staging) keep working after
    the column is stored relative.
    """
    if not stored:
        return None
    s = stored.strip().replace("\\", "/")
    if Path(s).is_absolute() or _DRIVE_RE.match(s):
        return Path(s).resolve(strict=False)
    root = get_storage_root(project_id).resolve()
    return (root / s).resolve(strict=False)
