"""TR2-managed source ownership and ordinary-mutation guard (flowgate.default.0641)."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.db import tr_commit_ledger as db_ledger
from modules.flow_gate.documents import tr2_service
from modules.flow_gate.services import git_service
from modules.flow_gate.storage.safe_path import (
    MutationPathAliasError,
    MutationPathUnsafeError,
    resolve_mutation_target_no_alias,
)

TR2_MANAGED_FILE = "TR2_MANAGED_FILE"
SOURCE_PATH_ALIAS_NOT_ALLOWED = "SOURCE_PATH_ALIAS_NOT_ALLOWED"
SOURCE_PATH_INVALID = "SOURCE_PATH_INVALID"
SOURCE_WORKTREE_UNAVAILABLE = "SOURCE_WORKTREE_UNAVAILABLE"
SOURCE_MUTATION_BUSY = "SOURCE_MUTATION_BUSY"
TR2_OWNERSHIP_INVARIANT = "TR2_OWNERSHIP_INVARIANT"


class Tr2FilePolicyError(RuntimeError):
    def __init__(self, code: str, path: str | None = None, *, details: dict | None = None):
        self.code = code
        self.path = path
        self.details = {"code": code, **(details or {})}
        if path is not None:
            self.details["path"] = path
        super().__init__(f"{code}: {path or ''}".rstrip(": "))


class Tr2OwnershipInvariantError(RuntimeError):
    code = TR2_OWNERSHIP_INVARIANT

    def __init__(self, message: str, *, details: dict | None = None):
        self.details = {"code": self.code, "message": message, **(details or {})}
        super().__init__(message)


def _canonical(path: str) -> str:
    try:
        return tr2_service.normalized_target_path({"file": path})
    except tr2_service.Tr2ValidationError as exc:
        raise Tr2FilePolicyError(SOURCE_PATH_INVALID, str(path)) from exc


def _commit_paths(row: dict) -> list[str]:
    raw = row.get("commit_json")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise Tr2OwnershipInvariantError(
            f"malformed succeeded TR2 commit_json for attempt {row.get('attempt_id')}"
        ) from exc
    paths = payload.get("paths") if isinstance(payload, dict) else None
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise Tr2OwnershipInvariantError(
            f"missing succeeded TR2 commit paths for attempt {row.get('attempt_id')}"
        )
    canonical: list[str] = []
    for path in paths:
        try:
            canonical.append(_canonical(path))
        except Tr2FilePolicyError as exc:
            raise Tr2OwnershipInvariantError(
                "persisted TR2 ownership path is invalid",
                details={
                    "attempt_id": row.get("attempt_id"),
                    "path": path,
                    "cause_code": exc.code,
                },
            ) from exc
    return canonical


def _lineage_root(row: dict, rows_by_id: dict[int, dict]) -> dict:
    current = row
    seen: set[int] = set()
    while True:
        try:
            row_id = int(current["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Tr2OwnershipInvariantError(
                "ledger row has no valid id", details={"row": current}
            ) from exc
        if row_id in seen:
            raise Tr2OwnershipInvariantError(
                "restored_from_id cycle in TR2 ownership lineage",
                details={"ledger_row_id": row_id},
            )
        seen.add(row_id)
        parent_id = current.get("restored_from_id")
        if parent_id is None:
            return current
        try:
            parent_key = int(parent_id)
        except (TypeError, ValueError) as exc:
            raise Tr2OwnershipInvariantError(
                "restored_from_id is invalid",
                details={"ledger_row_id": row_id, "restored_from_id": parent_id},
            ) from exc
        parent = rows_by_id.get(parent_key)
        if parent is None:
            raise Tr2OwnershipInvariantError(
                "restored_from_id target is missing",
                details={"ledger_row_id": row_id, "restored_from_id": parent_key},
            )
        if parent.get("group_id") != current.get("group_id") or parent.get("doc_id") != current.get("doc_id"):
            raise Tr2OwnershipInvariantError(
                "TR2 ownership lineage crosses group/document boundary",
                details={"ledger_row_id": row_id, "restored_from_id": parent_key},
            )
        current = parent


def managed_paths(group_id: str) -> set[str]:
    """Authoritative active ownership derived from live ledger lineages + durable attempts."""
    rows = db_ledger.ownership_rows(group_id)
    rows_by_id: dict[int, dict] = {}
    for row in rows:
        try:
            rows_by_id[int(row["id"])] = row
        except (KeyError, TypeError, ValueError) as exc:
            raise Tr2OwnershipInvariantError(
                "ledger row has no valid id", details={"group_id": group_id}
            ) from exc

    roots: dict[int, dict] = {}
    for attempt in db_attempts.successful_by_group(group_id):
        ledger_row_id = attempt.get("ledger_row_id")
        if ledger_row_id is None:
            continue
        try:
            roots[int(ledger_row_id)] = attempt
        except (TypeError, ValueError) as exc:
            raise Tr2OwnershipInvariantError(
                "succeeded TR2 attempt has invalid ledger_row_id",
                details={"attempt_id": attempt.get("attempt_id")},
            ) from exc

    result: set[str] = set()
    for row in rows:
        if row.get("state") != "live":
            continue
        root = _lineage_root(row, rows_by_id)
        try:
            root_id = int(root["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Tr2OwnershipInvariantError("TR2 ownership root has no valid id") from exc
        attempt = roots.get(root_id)
        if attempt is None:
            # tr_commit_ledger also stores ordinary TR commits. Only a live TR2 row
            # without its succeeded approval provenance is an ownership invariant break.
            if str(row.get("doc_type_code") or "").upper() == tr2_service.TR2_TYPE_CODE:
                raise Tr2OwnershipInvariantError(
                    "live TR2 lineage has no succeeded approval root",
                    details={"ledger_row_id": row.get("id"), "root_ledger_row_id": root_id},
                )
            continue
        result.update(_commit_paths(attempt))
    return result


def is_managed(group_id: str, path: str) -> bool:
    return _canonical(path) in managed_paths(group_id)


def managed_descendant(group_id: str, path: str) -> str | None:
    prefix = _canonical(path)
    child_prefix = prefix.rstrip("/") + "/"
    for owned in sorted(managed_paths(group_id), key=lambda item: item.encode("utf-8")):
        if owned == prefix or owned.startswith(child_prefix):
            return owned
    return None


def _group_root(project_id: str, group_id: str) -> Path:
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if (
        not cfg
        or not cfg.get("enabled")
        or not state
        or not state.get("worktree_registered")
        or not state.get("branch")
        or state.get("project_id") != project_id
    ):
        raise Tr2FilePolicyError(
            SOURCE_WORKTREE_UNAVAILABLE,
            details={"project_id": project_id, "group_id": group_id},
        )
    root = tr2_service.resolve_source_root(project_id, group_id).resolve()
    if not root.is_dir():
        raise Tr2FilePolicyError(
            SOURCE_WORKTREE_UNAVAILABLE,
            details={"project_id": project_id, "group_id": group_id},
        )
    return root


def _resolve(root: Path, path: str, *, allow_missing_leaf: bool) -> tuple[str, Path]:
    canonical = _canonical(path)
    try:
        resolved = resolve_mutation_target_no_alias(
            root, canonical, allow_missing_leaf=allow_missing_leaf
        )
    except MutationPathAliasError as exc:
        raise Tr2FilePolicyError(SOURCE_PATH_ALIAS_NOT_ALLOWED, canonical) from exc
    except MutationPathUnsafeError as exc:
        raise Tr2FilePolicyError(SOURCE_PATH_INVALID, canonical) from exc
    return canonical, resolved


@dataclass(frozen=True)
class GeneralSourceMutation:
    project_id: str
    group_id: str
    root: Path
    holder: str
    exact_targets: tuple[tuple[str, Path], ...]
    recursive_targets: tuple[tuple[str, Path], ...]


@contextmanager
def general_source_mutation(
    project_id: str,
    group_id: str,
    *,
    exact_paths: Iterable[str] = (),
    recursive_paths: Iterable[str] = (),
    allow_missing_leaf: bool = False,
):
    """Serialize an ordinary source mutation with TR2 approval and check ownership under lock.

    The critical ordering is fixed:
      LOCK -> authoritative worktree resolve -> NO_ALIAS -> managed re-read -> ACT -> UNLOCK.
    Callers must perform the actual mutation inside the yielded context.
    """
    holder = f"tr2-file-policy:{group_id}:{uuid.uuid4().hex}"
    if not git_service._acquire_lock(project_id, holder):
        raise Tr2FilePolicyError(
            SOURCE_MUTATION_BUSY,
            details={"project_id": project_id, "group_id": group_id},
        )
    try:
        root = _group_root(project_id, group_id)
        exact_targets = tuple(
            _resolve(root, path, allow_missing_leaf=allow_missing_leaf)
            for path in exact_paths
        )
        recursive_targets = tuple(
            _resolve(root, path, allow_missing_leaf=allow_missing_leaf)
            for path in recursive_paths
        )
        owned = managed_paths(group_id)
        for path, _target in exact_targets:
            if path in owned:
                raise Tr2FilePolicyError(
                    TR2_MANAGED_FILE, path, details={"group_id": group_id, "required_mutation": "TR2"}
                )
        for path, _target in recursive_targets:
            prefix = path.rstrip("/") + "/"
            hit = next(
                (owned_path for owned_path in sorted(owned, key=lambda item: item.encode("utf-8")) if owned_path == path or owned_path.startswith(prefix)),
                None,
            )
            if hit is not None:
                raise Tr2FilePolicyError(
                    TR2_MANAGED_FILE, hit, details={"group_id": group_id, "requested_path": path,
                                                   "required_mutation": "TR2"}
                )
        yield GeneralSourceMutation(
            project_id=project_id,
            group_id=group_id,
            root=root,
            holder=holder,
            exact_targets=exact_targets,
            recursive_targets=recursive_targets,
        )
    finally:
        db_git.release_lock(project_id, holder)
