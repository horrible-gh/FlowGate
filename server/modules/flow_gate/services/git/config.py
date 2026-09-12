"""Git per-project config CRUD, validation, and base-branch resolution.

Extracted from git_service.py (flowgate.default.0550 T0007, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import projects as db_projects

from .credentials import (
    GIT_AUTHOR_EMAIL_MAX,
    GIT_AUTHOR_NAME_MAX,
    GitServiceError,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)

_log = logging.getLogger(__name__)

PROVIDER_VALUES = ("github", "gitlab", "gitea", "gitbucket", "generic")
DEFAULT_FINALIZE_ACTION_VALUES = ("merge", "push", "wait")


def _base_root_of(project_id: str) -> Optional[Path]:
    """The project's base-checkout path, or None when unresolvable (0205 §2.5)."""
    cfg = db_git.get_config(project_id)
    # lazy — import cycle safety; also keeps `src_root` reachable as git_service.src_root,
    # which existing tests monkeypatch (e.g. test_base_file_explorer_branch_0319.py).
    from modules.flow_gate.services import git_service as _gs
    project_name = _gs._project_name(project_id)
    if not cfg or not project_name:
        return None
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    return _gs.src_root(project_name, base_branch)


# ── Config CRUD (P0005 §1·§2) ────────────────────────────────────────────────

_URL_HTTP_RE = re.compile(r"^https?://\S+$")
_URL_SSH_RE = re.compile(r"^(ssh://\S+|[\w.-]+@[\w.-]+:\S+)$")
# file:// mirrors are accepted for same-host repositories (and the test harness).
_URL_FILE_RE = re.compile(r"^file:///\S+$")


def _validate_repo_url(repo_url: str) -> None:
    url = (repo_url or "").strip()
    if not url or not (
        _URL_HTTP_RE.match(url) or _URL_SSH_RE.match(url) or _URL_FILE_RE.match(url)
    ):
        raise GitServiceError(
            422, "invalid_request",
            f"repo_url must be an http(s):// or ssh (git@host:path) URL: {repo_url!r}",
        )
    if "@" in url and _URL_HTTP_RE.match(url):
        # http(s) URLs must not smuggle credentials in userinfo (L0006 §2.3 invariant d).
        raise GitServiceError(
            422, "invalid_request",
            "repo_url must not embed credentials; store the token separately",
        )


def _resolve_author(
    body: dict, existing: Optional[dict]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the commit-author override to store (0237 — R0001/NR0003 §5.3).

    Same exclude_unset protocol as secret/translate_url: field omitted → keep the
    stored value; sent → trim, "" → NULL (= revert to the FlowGate default).

    Validated as a PAIR: a half-set identity would splice a configured name onto the
    default email (or vice versa), and an empty ident makes every commit for the
    project fail with "Author identity unknown" — so a bad value is rejected at the
    door (422) rather than at finalize time, where it would strand the workflow.
    """
    def _field(key: str) -> Optional[str]:
        if key in body:
            return (body.get(key) or "").strip() or None
        return (existing.get(key) or None) if existing else None

    name = _field("author_name")
    email = _field("author_email")

    if (name is None) != (email is None):
        raise GitServiceError(
            422, "invalid_request",
            "author_name and author_email must be set together (send both, or "
            "clear both with \"\" to commit as the default FlowGate identity)",
        )
    if name is None:
        return None, None
    if len(name) > GIT_AUTHOR_NAME_MAX:
        raise GitServiceError(
            422, "invalid_request",
            f"author_name must be at most {GIT_AUTHOR_NAME_MAX} characters",
        )
    if len(email) > GIT_AUTHOR_EMAIL_MAX:
        raise GitServiceError(
            422, "invalid_request",
            f"author_email must be at most {GIT_AUTHOR_EMAIL_MAX} characters",
        )
    # git strips "<", ">" and newlines out of an ident itself (so this can never be
    # an argv/config injection — NR0003 §4); reject them anyway so the operator gets
    # the identity they typed instead of a silently mangled one.
    if any(ch in name for ch in "<>\n\r"):
        raise GitServiceError(
            422, "invalid_request", "author_name must not contain '<', '>' or newlines",
        )
    if any(ch in email for ch in "<>\n\r ") or "@" not in email:
        raise GitServiceError(
            422, "invalid_request",
            f"author_email must be an email address without spaces: {email!r}",
        )
    return name, email


def _config_view(row: dict) -> dict:
    """Row → response config object (P0005 §1-1) with the secret masked."""
    has_secret = bool(row.get("secret_enc"))
    masked: Optional[str] = None
    if has_secret:
        try:
            masked = mask_secret(decrypt_secret(row["secret_enc"]))
        except GitServiceError:
            masked = "********"  # unreadable (E2) — keep has_secret=true
    return {
        "project_id": row["project_id"],
        "repo_url": row["repo_url"],
        "provider": row.get("provider") or "generic",
        "username": row.get("username"),
        "secret_masked": masked,
        "has_secret": has_secret,
        "base_branch": row.get("base_branch") or "main",
        "default_finalize_action": row.get("default_finalize_action") or "wait",
        "enabled": bool(row.get("enabled")),
        "translate_url": row.get("translate_url") or None,
        # null = not overridden → server commits as the FlowGate default (0237).
        "author_name": row.get("author_name") or None,
        "author_email": row.get("author_email") or None,
        # TR work-scope check enforcement stage (0299 D0004 §3.6). An existing row with a NULL
        # column predates migration 071, so it is read with the default 'observe'.
        "tr_scope_stage": row.get("tr_scope_stage") or "observe",
        "updated_at": row.get("updated_at"),
    }


def get_config_view(project_id: str) -> dict:
    row = db_git.get_config(project_id)
    if row is None:
        return {"ok": True, "configured": False, "config": None}
    return {"ok": True, "configured": True, "config": _config_view(row)}


def save_config(project_id: str, body: dict) -> dict:
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    _validate_repo_url(body.get("repo_url") or "")
    provider = body.get("provider") or "generic"
    if provider not in PROVIDER_VALUES:
        raise GitServiceError(422, "invalid_request", f"invalid provider: {provider!r}")
    action = body.get("default_finalize_action") or "wait"
    if action not in DEFAULT_FINALIZE_ACTION_VALUES:
        raise GitServiceError(
            422, "invalid_request", f"invalid default_finalize_action: {action!r}"
        )

    existing = db_git.get_config(project_id)
    secret = body.get("secret", None)
    if secret is None:
        secret_enc = existing.get("secret_enc") if existing else None  # keep (P0005 §2-1)
    elif secret == "":
        secret_enc = None  # clear
    else:
        secret_enc = encrypt_secret(str(secret))

    # translate_url (P0003 §4-1): field omitted → keep stored value; sent → trim,
    # empty string stored as NULL (= disabled). Same exclude_unset "keep" protocol
    # as secret above.
    if "translate_url" in body:
        translate_url = (body.get("translate_url") or "").strip() or None
    else:
        translate_url = existing.get("translate_url") if existing else None

    author_name, author_email = _resolve_author(body, existing)

    # tr_scope_stage (0299 D0004 §3.6): omitted → keep stored (or 'observe' on a new
    # row). Same exclude_unset "keep" protocol as translate_url/secret above, so an
    # older client that does not know the field cannot silently reset the stage.
    if "tr_scope_stage" in body:
        tr_scope_stage = (body.get("tr_scope_stage") or "observe").strip() or "observe"
        if tr_scope_stage not in db_git.TR_SCOPE_STAGE_VALUES:
            raise GitServiceError(
                422, "invalid_request", f"invalid tr_scope_stage: {tr_scope_stage!r}"
            )
    else:
        tr_scope_stage = (existing.get("tr_scope_stage") if existing else None) or "observe"

    row = db_git.upsert_config(project_id, {
        "repo_url": (body.get("repo_url") or "").strip(),
        "provider": provider,
        "username": (body.get("username") or None),
        "secret_enc": secret_enc,
        "base_branch": (body.get("base_branch") or "main").strip() or "main",
        "default_finalize_action": action,
        "enabled": bool(body.get("enabled")),
        "translate_url": translate_url,
        "author_name": author_name,
        "author_email": author_email,
        "tr_scope_stage": tr_scope_stage,
    })
    return {"ok": True, "configured": True, "config": _config_view(row)}


def delete_config(project_id: str) -> dict:
    deleted = db_git.delete_config(project_id)
    # Existing worktrees are intentionally left untouched (P0005 §2-3);
    # source resolution falls back immediately via the enabled/config check (E13).
    return {"ok": True, "deleted": deleted}


def _require_enabled_config(project_id: str) -> dict:
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(
            409, "invalid_state", f"git integration is not enabled for project '{project_id}'"
        )
    return cfg


# ── Base source-root resolution for the file explorer (0319 B0001) ────────────
# The base file explorer and the editable base-checkout APIs resolved their source
# directory from project_settings.branch (default "main"), while Git provisioning
# clones/adopts the connected repo into src/{project}/{base_branch} (the git
# integration config). When a connected repo's base branch is not "main" these two
# paths diverge: provisioning lands the existing source under base_branch, but the
# explorer walks an empty src/{project}/main and shows nothing — the B0001 report
# ("the branch name is even the same, yet the file explorer is empty"). The source
# is never actually "not fetched"; the read layer just looks at the wrong branch
# folder. Resolve the base tree from the Git base_branch whenever integration is
# ENABLED (mirroring the effective_src_root gate); a non-integrated or disabled
# project keeps its project_settings.branch folder, so its behaviour never changes
# (fallback-first, L0006 §2.2).
def base_branch_for(project_id: Optional[str]) -> Optional[str]:
    """Git base_branch when integration is enabled for the project, else None.

    Never raises: any lookup failure reads as "not integrated", so the caller
    falls back to the ordinary project-settings branch.
    """
    if not project_id:
        return None
    try:
        cfg = db_git.get_config(project_id)
    except Exception:
        _log.warning("base_branch_for lookup failed for %s", project_id, exc_info=True)
        return None
    if cfg is None or not cfg.get("enabled"):
        return None
    return (cfg.get("base_branch") or "main").strip() or "main"


def base_src_root(
    project_id: Optional[str], project_name: str, fallback_branch: str = "main"
) -> Path:
    """Base source-checkout root for base file-explorer reads/edits (0319 B0001).

    Git-integrated (enabled) → ``src_root(project_name, base_branch)``; otherwise
    ``src_root(project_name, fallback_branch)``. ``fallback_branch`` is the value
    the caller already derived from project_settings, so a non-integrated project
    resolves byte-for-byte the same path as before. The returned Path is NOT
    ``.resolve()``-d — callers that need a resolved path do so themselves, exactly
    as they did with the raw ``src_root`` call this replaces.
    """
    # lazy — see _base_root_of: keeps `src_root` reachable as git_service.src_root
    # for existing monkeypatches.
    from modules.flow_gate.services import git_service as _gs
    branch = base_branch_for(project_id) or (fallback_branch or "main").strip() or "main"
    return _gs.src_root(project_name, branch)
