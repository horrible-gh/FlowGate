"""Durable per-group work-base contract.

``project_git_config.base_branch`` remains the project default.  A non-NULL
``groups.work_base_ref`` pins the branch/ref selected for one group; legacy rows
fall back to the project default.  Finalize targets and recovery ``start_point``
are intentionally outside this module.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import dialect as db_dialect
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import operation_job as db_jobs
from modules.flow_gate.db.connection import get_store, in_transaction

from .credentials import GitServiceError

_log = logging.getLogger(__name__)


def _normalized_ref(value: str) -> str:
    ref = value.strip() if isinstance(value, str) else ""
    if not ref or ref != value:
        raise GitServiceError(
            422,
            "group_work_base_invalid",
            "group work base must be a non-blank branch name without surrounding whitespace",
        )
    if ref == "HEAD" or ref.startswith("-"):
        raise GitServiceError(422, "group_work_base_invalid", "invalid group work base")
    return ref


def validate_group_work_base_ref(project_id: str, value: str) -> str:
    """Validate and return the exact branch name stored on a new group.

    Only ordinary local branches or their ``origin`` tracking counterparts are
    accepted.  Arbitrary revision expressions, commits, FlowGate-owned group
    branches, and a checkout-less/disabled integration fail explicitly.
    """
    from modules.flow_gate.services import git_service as _gs

    _gs._require_enabled_config(project_id)
    ref = _normalized_ref(value)
    base_root = _gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(
            409,
            "group_work_base_checkout_unavailable",
            "project Git checkout is not available for group work-base validation",
        )

    checked = _gs._run_git(["check-ref-format", "--branch", ref], cwd=base_root)
    if checked.returncode != 0:
        raise GitServiceError(
            422,
            "group_work_base_invalid",
            "group work base is not a valid Git branch name",
        )

    owner = _gs.db_git.get_state_by_branch(project_id, ref)
    if owner is not None:
        raise GitServiceError(
            409,
            "group_work_base_internal",
            "a FlowGate group worktree branch cannot be a group work base",
            {"connected_group_id": owner.get("group_id")},
        )

    local_ref = f"refs/heads/{ref}"
    remote_ref = f"refs/remotes/origin/{ref}"
    if not _gs._ref_exists(base_root, local_ref):
        if _gs._ref_exists(base_root, remote_ref):
            raise GitServiceError(
                409,
                "group_work_base_remote_only",
                "a remote-only branch cannot be a group work base",
                {"work_base_ref": ref},
            )
        raise GitServiceError(
            404,
            "group_work_base_not_found",
            "group work base does not resolve to an available repository branch",
            {"work_base_ref": ref},
        )

    resolved = _gs._run_git(
        ["rev-parse", "--verify", "--quiet", f"{local_ref}^{{commit}}"],
        cwd=base_root,
    )
    if resolved.returncode != 0:
        raise GitServiceError(
            422,
            "group_work_base_not_commit",
            "group work base does not resolve to a commit",
            {"work_base_ref": ref},
        )
    return ref


def default_work_base_ref_for_new_group(project_id: str) -> Optional[str]:
    """The work base a NEW group pins when none was requested (0665 T0004, NR0003 §7.3).

    A Git-active project stores its base branch name AT CREATION TIME, so a later
    project base change can no longer move an existing group's work base.  Non-Git
    (or unreadable-config) projects keep NULL: they have no Git work base at all.
    """
    from modules.flow_gate.services import git_service as _gs

    try:
        cfg = _gs.db_git.get_config(project_id)
    except Exception:
        return None
    if cfg is None or not cfg.get("enabled"):
        return None
    return (cfg.get("base_branch") or "main").strip() or "main"


def local_branch_refs(base_root) -> dict:
    """``{name: (tip object id, tip object type)}`` of every local branch (``refs/heads``), unfiltered.

    flowgate.v02.0098 T#1 (D0004 §3.2): the name listing of
    :func:`list_group_work_base_options`, shared with the check card's merge
    target candidates (``merge_target.check_target_view``), which judge every
    name themselves (the tip and its type spare one Git call per name). Raises
    ``group_work_base_options_failed`` when Git cannot list them.

    The name is the full refname less its exact ``refs/heads/`` prefix: ``%(refname:short)``
    answers ``heads/main`` once a tag ``main`` exists, which is no branch name."""
    from modules.flow_gate.services import git_service as _gs

    proc = _gs._run_git(
        ["for-each-ref", "--format=%(objectname)%09%(objecttype)%09%(refname)", "refs/heads"],
        cwd=base_root,
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500,
            "group_work_base_options_failed",
            "Git could not list branches",
            diagnostic=" ".join((proc.stderr or "").split()),
        )
    refs = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("	", 2)
        refname = parts[2].strip() if len(parts) == 3 else ""
        if refname.startswith("refs/heads/") and refname != "refs/heads/":
            refs[refname[len("refs/heads/"):]] = (parts[0].strip(), parts[1].strip())
    return refs


def list_group_work_base_options(project_id: str) -> dict:
    """Return only what a requirement author needs to pick a group work base.

    flowgate.default.0613 T0013: the project branch catalog
    (``GET /projects/{id}/git/branches``) is a Branch Manager surface gated by
    ``project.settings.read`` and carries delete/merge metadata.  Requirement
    creation needs neither, so this read-only view lists exactly the names
    :func:`validate_group_work_base_ref` would accept -- ordinary local branches
    that no FlowGate group worktree owns -- plus the project base used as the
    default selection.  A disabled or absent integration answers
    ``git_enabled: False`` with no branches (the legacy, UI-hidden path).
    """
    from modules.flow_gate.services import git_service as _gs

    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": True, "git_enabled": False, "base_branch": None, "branches": []}
    base_root = _gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(
            409,
            "group_work_base_checkout_unavailable",
            "project Git checkout is not available for group work-base validation",
        )
    names = sorted(local_branch_refs(base_root))
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    # Include historical/unregistered failure rows, just like get_state_by_branch.
    owned_branches = {
        row.get("branch") for row in _gs.db_git.list_states_of_project_any(project_id)
    }
    branches = [
        {"name": name, "is_project_base": name == base_branch}
        for name in names
        if name not in owned_branches
    ]
    return {
        "ok": True,
        "git_enabled": True,
        "base_branch": base_branch,
        "branches": branches,
    }


def groups_pinning_work_base(project_id: str, branch: str) -> list[str]:
    """Return the non-terminal groups whose durable work base is ``branch``.

    The branch delete guard consults this so an ordinary local branch cannot be
    removed while a group may still provision, retry, reprovision or reopen its
    worktree from it.  Legacy (NULL) groups follow the project base, which the
    guard already protects as ``branch_is_base``.
    """
    return [
        row["group_id"]
        for row in db_groups.list_open_groups_by_work_base(project_id, branch)
    ]


def _locked_by_state(state: Optional[dict]) -> bool:
    """True once ``state`` shows real Git work already started from a work base.

    flowgate.default.0613 TR0014 rev2: ``worktree_registered`` covers the
    ordinary case (the worktree exists right now, created via
    ``worktree.py``'s ``... _worktree_start_point(base_root, work_base_ref)``
    fork). But ``worktree.unregister_worktree`` clears that flag on slot
    cleanup while leaving ``status``/``initial_source_sync_at`` as permanent
    group history (0182 NR0003 §5) -- and a later terminal reopen resolves its
    C1 ancestry against ``resolve_group_work_base_ref`` again
    (``worktree._ensure_worktree_locked``'s ``terminal_base_diverged`` check).
    So a stale ``worktree_registered = 0`` must not reopen the field: once the
    group ever synced from its base (``initial_source_sync_at``) or reached any
    non-'none' ledger status, the base is locked for good. A row that only
    records provisioning *failures* (``upsert_provision_failure``, worktree
    never actually created) leaves all three false and stays unlocked.
    """
    if not state:
        return False
    if state.get("worktree_registered"):
        return True
    if state.get("initial_source_sync_at"):
        return True
    status = state.get("status")
    return bool(status) and status != "none"


def group_work_base_locked(project_id: str, group_id: str) -> bool:
    """Whether ``group_id``'s stored work base can still be changed."""
    from modules.flow_gate.services import git_service as _gs

    return _locked_by_state(_gs.db_git.get_state(group_id))


def locked_group_ids(project_id: str) -> set[str]:
    """Batch form of :func:`group_work_base_locked` for one project's groups list."""
    from modules.flow_gate.services import git_service as _gs

    return {
        row["group_id"]
        for row in _gs.db_git.list_states_of_project_any(project_id)
        if _locked_by_state(row)
    }


# ── existing-group change boundary (flowgate.v02.0104 D0004 §3.2–§3.4) ────────
#
# The extended lock judgment, the group row lock and the one change path that the
# PATCH /groups/{id}/work-base route and the v0.1 existing-group root document both
# use. ``group_work_base_locked`` / ``locked_group_ids`` above stay the ledger-only
# batch judgment of ``GET /groups`` (§3.2.3).

PROVISION_KIND = "worktree_provision"

# §3.2.2, in this order wherever a list of them is reported.
LOCK_EVIDENCE = ("ledger", "work_base_record", "provision_succeeded", "group_branch_exists")

_EXPECTED_UNSET = object()
_GROUP_ROW_COLUMNS = "group_id, project_id, module, work_base_ref, updated_at"


def lock_group_row(group_id: str) -> Optional[dict]:
    """Lock ``group_id``'s ``groups`` row and read it; None when there is no row (§3.3.1).

    The first statement of the caller's transaction, shared by the change path and the
    provision start recheck. PostgreSQL / MySQL: ``SELECT … FOR UPDATE`` waits for a
    writer holding the row and then reads the committed row, so the stored ref is the
    latest even under repeatable read. SQLite has no row lock: an UPDATE that changes no
    value (``updated_at`` included) takes the database write lock, then the row is read
    in the same transaction. Only DB work belongs in that transaction -- no Git, fetch,
    file work or job registration (``job_store.create_or_get_job``, D 8.4).
    """
    if not in_transaction():
        raise RuntimeError("lock_group_row must run inside the transaction it locks for")
    store = get_store()
    if getattr(store, "dialect", db_dialect.SQLITE) == db_dialect.SQLITE:
        locked = store._execute_affected(
            "UPDATE groups SET work_base_ref = work_base_ref WHERE group_id = ?", [group_id])
        if locked == 0:
            return None
        return store._fetch_one(
            f"SELECT {_GROUP_ROW_COLUMNS} FROM groups WHERE group_id = ?", [group_id])
    return store._fetch_one(
        f"SELECT {_GROUP_ROW_COLUMNS} FROM groups WHERE group_id = ? FOR UPDATE", [group_id])


def _active_provision_jobs(group_id: str) -> list[dict]:
    """Non-terminal ``worktree_provision`` jobs of the group: pending, blocked,
    retry_wait, running, freezing, recovery_required ... (§3.3.2)."""
    return db_jobs.active_jobs_of_group(group_id, PROVISION_KIND)


def _db_lock_evidence(group_id: str) -> list[str]:
    """The §3.2.2 evidence a DB read can prove: ``ledger``, ``work_base_record``,
    ``provision_succeeded``. Not evidence: failed/cancelled provision jobs only, a
    provision-failure-only ledger row, a backfill row without ``work_base_sha``."""
    from modules.flow_gate.services import git_service as _gs

    state = _gs.db_git.get_state(group_id)
    evidence = []
    if _locked_by_state(state):
        evidence.append("ledger")
    if str((state or {}).get("work_base_sha") or "").strip():
        evidence.append("work_base_record")
    if db_jobs.jobs_of_group(group_id, PROVISION_KIND, ("succeeded",)):
        evidence.append("provision_succeeded")
    return evidence


def _group_branch_names(project_id: str, group: dict) -> set[str]:
    """The group's worktree branch: the ledger's name and the derived one
    (``worktree_branch_name``, the name provisioning creates)."""
    from modules.flow_gate.services import git_service as _gs

    group_id = group["group_id"]
    names = set()
    recorded = str((_gs.db_git.get_state(group_id) or {}).get("branch") or "").strip()
    if recorded:
        names.add(recorded)
    module = (group.get("module") or "").strip() or _gs._module_of(group_id)
    try:
        names.add(_gs.worktree_branch_name(project_id, module, group_id))
    except GitServiceError:
        pass
    return names


def _group_branch_exists(project_id: str, group: dict, base_root: Path) -> bool:
    """``group_branch_exists``: a local or ``origin`` tracking branch of the group's
    name in the base checkout. Local reads only, no fetch. A failed read fails closed."""
    from modules.flow_gate.services import git_service as _gs

    for name in sorted(_group_branch_names(project_id, group)):
        wanted = {f"refs/heads/{name}", f"refs/remotes/origin/{name}"}
        proc = _gs._run_git(["for-each-ref", "--format=%(refname)", *sorted(wanted)],
                            cwd=base_root)
        if proc.returncode != 0:
            raise GitServiceError(
                500,
                "group_work_base_update_failed",
                "Git could not read the group branches",
                diagnostic=" ".join((proc.stderr or "").split()),
            )
        if wanted & {line.strip() for line in (proc.stdout or "").splitlines()}:
            return True
    return False


def _lock_evidence(project_id: str, group: dict, base_root: Optional[Path]) -> list[str]:
    """Every §3.2.2 evidence that applies; the branch is only read with a checkout."""
    found = set(_db_lock_evidence(group["group_id"]))
    if base_root is not None and _group_branch_exists(project_id, group, base_root):
        found.add("group_branch_exists")
    return [code for code in LOCK_EVIDENCE if code in found]


def _checkout_of(project_id: str) -> Optional[Path]:
    from modules.flow_gate.services import git_service as _gs

    base_root = _gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        return None
    return base_root


def _group_disposed(group_id: str) -> bool:
    from modules.flow_gate import process_service

    return bool(process_service.is_group_disposed(group_id))


def _git_enabled(config: Optional[dict]) -> bool:
    return bool(config is not None and config.get("enabled"))


def _group_not_found(group_id: str) -> GitServiceError:
    return GitServiceError(404, "group_not_found", "group not found", {"group_id": group_id})


def _disposed_error() -> GitServiceError:
    return GitServiceError(409, "group_disposed", "a disposed group's work base cannot change")


def _git_disabled_error() -> GitServiceError:
    return GitServiceError(409, "group_work_base_git_disabled",
                           "the project's Git integration is not enabled")


def _checkout_error() -> GitServiceError:
    return GitServiceError(
        409,
        "group_work_base_checkout_unavailable",
        "project Git checkout is not available for group work-base validation",
    )


def _locked_error(evidence: list[str]) -> GitServiceError:
    return GitServiceError(
        409,
        "group_work_base_locked",
        "group work base cannot change once its Git worktree has started",
        {"lock_evidence": list(evidence)},
    )


def _changed_error() -> GitServiceError:
    return GitServiceError(409, "group_work_base_changed",
                           "group work base changed since it was read")


def _busy_error() -> GitServiceError:
    return GitServiceError(409, "group_work_base_busy",
                           "group worktree provisioning is in progress")


def _save_work_base_ref(group_id: str, stored: Optional[str], ref: str) -> int:
    """Conditional save: only while the stored value (NULL included) is ``stored``."""
    now = datetime.now().isoformat()
    if stored is None:
        return get_store()._execute_affected(
            "UPDATE groups SET work_base_ref = ?, updated_at = ? "
            "WHERE group_id = ? AND work_base_ref IS NULL",
            [ref, now, group_id],
        )
    return get_store()._execute_affected(
        "UPDATE groups SET work_base_ref = ?, updated_at = ? "
        "WHERE group_id = ? AND work_base_ref = ?",
        [ref, now, group_id, stored],
    )


def group_work_base_view(group_id: str) -> dict:
    """The ``GET /groups/{id}/work-base`` read (§5.3) without ``can_edit``.

    Display only: the final judgment is the change path's (§3.4). ``reason`` is the
    first of disposed / git_disabled / checkout_unavailable / history_locked /
    provision_busy that applies.
    """
    from modules.flow_gate.services import git_service as _gs

    group = db_groups.get_by_id(group_id)
    if group is None:
        raise _group_not_found(group_id)
    project_id = group.get("project_id")
    config = _gs.db_git.get_config(project_id)
    git_enabled = _git_enabled(config)
    base_root = _checkout_of(project_id) if git_enabled else None
    evidence = _lock_evidence(project_id, group, base_root)
    reasons = (
        ("disposed", lambda: _group_disposed(group_id)),
        ("git_disabled", lambda: not git_enabled),
        ("checkout_unavailable", lambda: base_root is None),
        ("history_locked", lambda: bool(evidence)),
        ("provision_busy", lambda: bool(_active_provision_jobs(group_id))),
    )
    reason = next((code for code, applies in reasons if applies()), None)
    return {
        "group_id": group_id,
        "project_id": project_id,
        "git_enabled": git_enabled,
        "work_base_ref": group.get("work_base_ref"),
        "effective_work_base_ref": _gs.resolve_group_work_base_ref(
            project_id, group_id, group=group, config=config),
        "work_base_locked": bool(evidence),
        "lock_evidence": evidence,
        "work_base_editable": reason is None,
        "reason": reason,
    }


# A provision job that settles (failed / cancelled) after the change's step-1 group
# branch read may have created that branch first and left no DB evidence (§3.2.2):
# the change then rolls back and runs steps 1→2 again, at most this many times.
_CHANGE_ATTEMPTS = 3


class _StaleBranchRead(Exception):
    """Rolls the change transaction back: a provision job settled after the branch read."""


def _settled_provision_jobs(group_id: str) -> frozenset:
    """Fingerprint of the group's failed / cancelled provision jobs.

    Taken before the step-1 group branch read and again inside the row lock. A job that
    settled in between -- active before, or registered after, the read -- is a new
    member, so the branch the read saw no longer proves anything. Active jobs are
    ``group_work_base_busy`` and succeeded ones ``provision_succeeded`` already."""
    rows = db_jobs.jobs_of_group(group_id, PROVISION_KIND, ("failed", "cancelled"))
    return frozenset(
        (row.get("job_id"), row.get("status"), row.get("touch_seq")) for row in rows)


def change_group_work_base_ref(
    group_id: str,
    value: str,
    *,
    expected=_EXPECTED_UNSET,
    project_id: Optional[str] = None,
) -> dict:
    """The one change path of an existing group's stored work base (§3.4).

    ``expected`` is the PATCH's ``expected_work_base_ref`` (None = stored NULL); the
    v0.1 root document path leaves it out and skips only that comparison. The order is
    fixed and is the error order:

    1. outside a transaction, read only: group (and ``project_id`` when given) →
       disposed → Git enabled → base checkout → §3.2.2 lock (DB evidence + group
       branch) → ``validate_group_work_base_ref``;
    2. one group row lock transaction: stored value → expected → DB lock evidence
       again → no active provision job → no provision job settled since the step-1
       branch read (else roll back and run 1→2 again; still settling after
       ``_CHANGE_ATTEMPTS`` runs → busy) → conditional save → commit.

    Any failure changes nothing. No Git, fetch or job registration inside the
    transaction; the checkout is never switched. A repository error in either step
    is the 500 ``group_work_base_update_failed``; GitServiceErrors pass unchanged.
    """
    try:
        for _attempt in range(_CHANGE_ATTEMPTS):
            try:
                return _change_once(group_id, value, expected, project_id)
            except _StaleBranchRead:
                _log.info("provision job settled during the work base change of %s; "
                          "reading the group branch again", group_id)
        raise _busy_error()
    except GitServiceError:
        raise
    except Exception as exc:
        _log.warning("group work base change failed for %s", group_id, exc_info=True)
        raise GitServiceError(
            500,
            "group_work_base_update_failed",
            "group work base could not be saved; nothing changed",
            diagnostic=type(exc).__name__,
        ) from exc


def _change_once(group_id: str, value: str, expected, project_id: Optional[str]) -> dict:
    """Steps 1→2 of :func:`change_group_work_base_ref`, once."""
    from modules.flow_gate.services import git_service as _gs

    group = db_groups.get_by_id(group_id)
    if group is None or (project_id is not None and group.get("project_id") != project_id):
        raise _group_not_found(group_id)
    project_id = group.get("project_id")
    if _group_disposed(group_id):
        raise _disposed_error()
    if not _git_enabled(_gs.db_git.get_config(project_id)):
        raise _git_disabled_error()
    base_root = _checkout_of(project_id)
    if base_root is None:
        raise _checkout_error()
    settled = _settled_provision_jobs(group_id)
    evidence = _lock_evidence(project_id, group, base_root)
    if evidence:
        raise _locked_error(evidence)
    ref = validate_group_work_base_ref(project_id, value)

    with get_store().transaction():
        row = lock_group_row(group_id)
        if row is None:
            raise _group_not_found(group_id)
        stored = row.get("work_base_ref")
        if expected is not _EXPECTED_UNSET and expected != stored:
            raise _changed_error()
        evidence = _db_lock_evidence(group_id)
        if evidence:
            raise _locked_error(evidence)
        if _active_provision_jobs(group_id):
            raise _busy_error()
        if _settled_provision_jobs(group_id) != settled:
            raise _StaleBranchRead()
        if _save_work_base_ref(group_id, stored, ref) != 1:
            raise _changed_error()
        saved = get_store()._fetch_one(
            f"SELECT {_GROUP_ROW_COLUMNS} FROM groups WHERE group_id = ?", [group_id])
    return {
        "group_id": group_id,
        "project_id": project_id,
        "work_base_ref": ref,
        "effective_work_base_ref": ref,
        "work_base_locked": False,
        "work_base_editable": True,
        "reason": None,
        "updated_at": (saved or {}).get("updated_at"),
    }


def apply_group_work_base_ref(project_id: str, group_id: str, value: str) -> str:
    """Change an existing group's stored work base without the expected comparison.

    flowgate.v02.0104 D0004 §3.4: a thin wrapper of :func:`change_group_work_base_ref`
    -- lock, busy and the conditional save apply as for the PATCH route. There is no
    unconditional write path any more.
    """
    return change_group_work_base_ref(group_id, value, project_id=project_id)["work_base_ref"]


def resolve_group_work_base_ref(
    project_id: str,
    group_id: str,
    *,
    group: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Optional[str]:
    """Return the authoritative effective work base for a group.

    Resolution is deliberately small and stable: stored group value first,
    otherwise the project's ``base_branch``.  Non-Git projects return ``None``.
    No finalize target, read-only ref, or recovery ``start_point`` participates.

    The project-base fallback now only serves legacy NULL rows: new Git groups
    store their base at creation (:func:`default_work_base_ref_for_new_group`),
    the work-base backfill pins existing rows, and a project base change is
    refused while a locked NULL row remains (0665 T0004).
    """
    from modules.flow_gate.services import git_service as _gs

    cfg = config if config is not None else _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return None
    row = group if group is not None else db_groups.get_by_id(group_id)
    stored = ((row or {}).get("work_base_ref") or "").strip()
    if stored:
        return stored
    return (cfg.get("base_branch") or "main").strip() or "main"