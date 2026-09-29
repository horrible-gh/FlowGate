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


class Tr2FilePolicyError(RuntimeError):
    def __init__(self, code: str, path: str | None = None, *, details: dict | None = None):
        self.code = code
        self.path = path
        self.details = {"code": code, **(details or {})}
        if path is not None:
            self.details["path"] = path
        super().__init__(f"{code}: {path or ''}".rstrip(": "))


class Tr2OwnershipInvariantError(RuntimeError):
    pass


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
    return [_canonical(path) for path in paths]


def managed_paths(group_id: str) -> set[str]:
    """Authoritative, uncached ownership set derived from succeeded approval attempts."""
    result: set[str] = set()
    for row in db_attempts.successful_by_group(group_id):
        result.update(_commit_paths(row))
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
