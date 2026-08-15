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


def _group_worktree_on_disk(
    project_id: Optional[str], group_id: Optional[str]
) -> Optional[Path]:
    """The group's branch worktree path when it physically exists, else None.

    0284 T0005 (structural fix for B0001): recovery used by
    resolve_project_src_root / classify_src_root when the git ledger's
    worktree_registered flag has been cleared — merge/push cleanup resets it to
    0 (git_integration.unregister_worktree), which is what silently dropped every
    post-merge / in-flight re-run to the base(main) tree (NR0003 §4). The flag gates
    slot *accounting*, not which tree the runner must read: while the group's branch
    worktree is still on disk it stays authoritative, so a fix-verification suite is
    checked against the tree that actually holds the fix. Mirrors the gates of
    git_service.effective_src_root_ex (integration on, state, branch, project
    name, directory present) EXCEPT the registration flag. Pure lookup — never
    raises; a genuinely pruned directory returns None so the caller still falls back.

    0287 NR0004 §5: "present" additionally requires the worktree's `.git` link. A
    teardown interrupted mid-delete leaves the directory standing with its link and
    most of its files gone, and that corpse must not be recovered as authoritative.
    """
    if not project_id or not group_id:
        return None
    try:
        from modules.flow_gate.services import git_service  # lazy — import cycle

        cfg = git_service.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return None
        state = git_service.db_git.get_state(group_id)
        if not state:
            return None
        branch = (state.get("branch") or "").strip()
        if not branch:
            return None
        project_name = git_service._project_name(project_id)
        if not project_name:
            return None
        wt = src_root(project_name, branch)
        # 0287 NR0004 §5: "still on disk" has to mean a real tree, not a directory
        # that happens to exist. An interrupted `worktree remove` leaves the path
        # in place minus its `.git` link and most of its content, and recovering
        # ONTO that corpse is worse than the fallback this guard was written to
        # avoid — the suite runs against a tree missing the modules under test.
        if wt.is_dir() and git_service._worktree_link_ok(wt):
            return wt.resolve()
    except Exception:
        _log.warning(
            "on-disk worktree recovery failed for group %s", group_id, exc_info=True
        )
    return None


def resolve_project_src_root(
    project_id: Optional[str],
    fallback_branch: str = "main",
    group_id: Optional[str] = None,
) -> Optional[Path]:
    """Resolve a project's source mirror root from its *project_id*.

    ``src_root()`` takes the human-facing project_name, so callers holding only
    a project_id must translate through the projects table and take the branch
    from project settings (falling back to *fallback_branch*). Passing a
    project_id straight into ``src_root()`` resolves a nonexistent directory
    whenever project_id != project_name (the 0152 test-runner outage).
    Returns None when the project row or its name is missing.

    0115: when *group_id* is given and the project is git-integrated with a
    registered worktree for that group, the group worktree path wins (L0006
    §2.2). Every other case — no group, no config, disabled, missing worktree —
    falls back to the ordinary project-branch folder below (fallback first).
    """
    if not project_id:
        return None
    if group_id:
        try:
            from modules.flow_gate.services import git_service  # lazy — import cycle

            wt = git_service.effective_src_root(project_id, group_id)
            if wt is not None:
                return wt
        except Exception:
            # 0280 NR0003 §4-B: worktree resolution must never break the fallback
            # path — but swallowing it silently made a git-integrated group run in
            # base(main) with no trace anywhere. Fall back loudly.
            _log.warning(
                "worktree resolution failed for group %s (project %s) — falling back "
                "to the base project-branch tree",
                group_id,
                project_id,
                exc_info=True,
            )
        # 0284 T0005 (structural fix for B0001 / NR0003 §6-1): effective_src_root()
        # returns None once the ledger's worktree_registered flag is cleared (merge/
        # push cleanup), which used to drop the run to the base(main) tree even while
        # the group's branch worktree was still on disk — so a fix-verification suite
        # ran against a tree lacking the very fix it verifies. Recover the on-disk
        # branch worktree before falling back; a pruned directory — or one left
        # broken by an interrupted teardown (0287 NR0004) — falls through.
        recovered = _group_worktree_on_disk(project_id, group_id)
        if recovered is not None:
            _log.info(
                "resolve_project_src_root: group %s using on-disk worktree %s despite "
                "cleared registration ledger (post-merge recovery)",
                group_id,
                recovered,
            )
            return recovered
    try:
        from modules.flow_gate.db import projects as _proj  # lazy — import cycle
    except Exception:
        return None
    try:
        row = _proj.get_by_id(project_id)
    except Exception:
        return None
    project_name = (row.get("project_name") or "").strip() if row else ""
    if not project_name:
        return None
    branch = (fallback_branch or "main").strip() or "main"
    try:
        settings = _proj.get_settings(project_id)
    except Exception:
        settings = None
    if settings:
        configured = (settings.get("branch") or "").strip()
        if configured:
            branch = configured
    return src_root(project_name, branch).resolve()


def classify_src_root(
    project_id: Optional[str],
    group_id: Optional[str],
    root: Optional[Path],
) -> str:
    """Classify an *already resolved* src root into a ``SRC_ROOT_*`` kind.

    0280 NR0003 §6-2: callers persist and display the root they actually used, so
    a "the tests ran in main" report becomes checkable instead of arguable. Kind is
    ``git_service.SRC_ROOT_WORKTREE`` when *root* is the group's worktree, else the
    ``SRC_ROOT_*`` reason the worktree was skipped.

    Deliberately classifies the root it is *given* rather than re-deriving it: when
    the two disagree (a test double, a caller that resolved elsewhere) the answer is
    ``"unknown"``, never a confident lie. Never raises — bookkeeping must not be able
    to fail a run.
    """
    if root is None:
        return "unknown"
    try:
        from modules.flow_gate.services import git_service  # lazy — import cycle

        wt, reason = git_service.effective_src_root_ex(project_id, group_id)
        if wt is not None:
            return reason if Path(root).resolve(strict=False) == wt else "unknown"
        # 0284 T0005: effective_src_root_ex still reports the ledger fallback reason
        # (e.g. worktree_unregistered after merge/cleanup) even though the run may have
        # executed in the group's on-disk branch worktree via the recovery above.
        # Classify by the root ACTUALLY used so the TSR reads "worktree", not the
        # stale ledger reason.
        recovered = _group_worktree_on_disk(project_id, group_id)
        if recovered is not None and Path(root).resolve(strict=False) == recovered:
            return git_service.SRC_ROOT_WORKTREE
        return reason
    except Exception:
        _log.warning(
            "src_root classification failed for group %s", group_id, exc_info=True
        )
        return "unknown"


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


def within_allowed_roots(path: Path, project_id: Optional[str] = None) -> bool:
    """Public wrapper over ``_within_allowed_roots`` — the storage filesystem jail.

    flowgate.default.0060 NR0015 §7 확정 지침 5: the predicate itself is private, and the
    document-attachment paths (L0012 §2-1, §2-6 J4) need exactly this judgement. Importing a
    private name across modules would make the reuse boundary invisible, so the boundary is
    named here instead. Same behaviour, no second copy of the rule.
    """
    return _within_allowed_roots(path, project_id)


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
