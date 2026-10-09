"""Ownership and retention for token-owned work/<project>/tok_* directories.

Only a manifest written by this module can authorize a recursive delete.  A
legacy directory without a manifest is deliberately left for manual review.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.storage import paths as storage_paths

_log = logging.getLogger(__name__)
MANIFEST_NAME = ".flowgate-token-scratch.json"
MANIFEST_SCHEMA = 1
RETENTION_DAYS = 7
_TOKEN_ID = re.compile(r"\Atok_[0-9]{8}_[0-9]{6}\Z")
_sweep_lock = threading.Lock()
_last_sweep: dict[str, float] = {}


def _unsafe_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(path.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return True


def _root(project_id: str) -> Path:
    project = db_projects.get_by_id(project_id)
    name = project["project_name"] if project else project_id
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "_"
    return storage_paths.get_storage_root() / "work" / safe


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _checked_child(project_id: str, token_id: str, candidate: Path) -> Path | None:
    if not _TOKEN_ID.fullmatch(token_id):
        return None
    try:
        root = _root(project_id)
        if (_unsafe_link(root.parent.parent) or _unsafe_link(root.parent)
                or _unsafe_link(root) or _unsafe_link(candidate)):
            return None
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if (not _same_path(candidate.parent.resolve(strict=True), resolved_root)
                or not _same_path(resolved.parent, resolved_root)
                or resolved.name != token_id or not resolved.is_dir()):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def create(project_id: str, token_id: str, scratch: Path) -> None:
    """Create a fresh direct child and atomically record its owner."""
    if not _TOKEN_ID.fullmatch(token_id):
        raise ValueError("invalid token scratch id")
    root = _root(project_id)
    storage_root = root.parent.parent
    if storage_root.exists() and _unsafe_link(storage_root):
        raise ValueError("token scratch storage root is unsafe")
    storage_root.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    if (_unsafe_link(storage_root) or _unsafe_link(root.parent) or _unsafe_link(root)
            or not _same_path(scratch, (root / token_id).resolve(strict=False))):
        raise ValueError("token scratch root is unsafe")
    scratch.mkdir(exist_ok=False)
    try:
        resolved = _checked_child(project_id, token_id, scratch)
        if resolved is None:
            raise ValueError("token scratch is not a managed child")
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "owner": "flowgate.token",
            "project_id": project_id,
            "token_id": token_id,
            "scratch_path": str(resolved),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "policy": {"retention_days": RETENTION_DAYS},
        }
        tmp = resolved / (MANIFEST_NAME + ".tmp")
        with tmp.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
        tmp.replace(resolved / MANIFEST_NAME)
    except Exception:
        # mkdir(exist_ok=False) proves this call created the child.  The path is
        # rechecked before removing a partially written manifest.
        if _checked_child(project_id, token_id, scratch) is not None:
            shutil.rmtree(scratch)
        raise


def _manifest(project_id: str, token_id: str, scratch: Path) -> dict | None:
    resolved = _checked_child(project_id, token_id, scratch)
    if resolved is None:
        return None
    path = resolved / MANIFEST_NAME
    try:
        if _unsafe_link(path) or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema") != MANIFEST_SCHEMA
                or data.get("owner") != "flowgate.token"
                or data.get("project_id") != project_id
                or data.get("token_id") != token_id
                or data.get("scratch_path") != str(resolved)
                or data.get("policy") != {"retention_days": RETENTION_DAYS}):
            return None
        created = _timestamp(data.get("created_at"))
        return data if created is not None else None
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _referenced(project_id: str, token_id: str, scratch: Path) -> bool:
    """Fail closed if either live AI surface cannot be checked."""
    if db_leases.get_by_token_id(token_id) is not None:
        return True
    from modules.flow_gate.services import ai_invoke_service as ai
    with ai._runs_lock:
        runs = list(ai._runs.values())
    for run in runs:
        if run.get("status") == "finished" or run.get("project_id") != project_id:
            continue
        stored = run.get("token_scratch_dir")
        if stored and _same_path(Path(stored).resolve(strict=False), scratch):
            return True
    return False


def _eligible(project_id: str, token_id: str, scratch: Path, now: datetime) -> bool:
    manifest = _manifest(project_id, token_id, scratch)
    if manifest is None:
        return False
    row = db_tokens.get_by_id(token_id)
    created = _timestamp(manifest["created_at"])
    if created is None or created > now:
        return False
    if row is not None:
        if row.get("project") != project_id or not row.get("scratch_dir"):
            return False
        stored = storage_paths.resolve_storage_dir(row["scratch_dir"], project_id)
        if stored is None or not _same_path(stored, scratch):
            return False
        expires = _timestamp(row.get("expires_at"))
        terminals = [_timestamp(row.get(key)) for key in ("consumed_at", "revoked_at")]
        if expires is not None and expires <= now:
            terminals.append(expires)
        terminals = [stamp for stamp in terminals if stamp is not None]
        if not terminals or any(stamp < created for stamp in terminals):
            return False
        terminal = min(terminals)
    else:
        # A complete, path-bound manifest is the only allowed proof for an
        # orphan whose INSERT never committed (or whose row was purged earlier).
        terminal = created
    if now - terminal < timedelta(days=RETENTION_DAYS):
        return False
    return not _referenced(project_id, token_id, scratch)


def delete_owned(project_id: str, token_id: str, scratch: Path, *, rollback: bool = False) -> bool:
    """Revalidate immediately before deletion; rollback is for this call's mkdir."""
    try:
        if _manifest(project_id, token_id, scratch) is None:
            return False
        if not rollback and not _eligible(project_id, token_id, scratch, datetime.now(timezone.utc)):
            return False
        if not rollback and _referenced(project_id, token_id, scratch):
            return False
        if _manifest(project_id, token_id, scratch) is None:
            return False
        shutil.rmtree(scratch)
        return not (scratch.exists() or scratch.is_symlink())
    except Exception:
        _log.warning("token scratch deletion failed for %s", token_id, exc_info=True)
        return False


def sweep(project_id: str) -> int:
    root = _root(project_id)
    if (not root.is_dir() or _unsafe_link(root.parent.parent)
            or _unsafe_link(root.parent) or _unsafe_link(root)):
        return 0
    deleted = 0
    try:
        for child in root.iterdir():
            if _TOKEN_ID.fullmatch(child.name) and delete_owned(project_id, child.name, child):
                deleted += 1
    except Exception:
        _log.warning("token scratch sweep failed for project %s", project_id, exc_info=True)
    return deleted


def sweep_on_issue(project_id: str) -> None:
    now = time.monotonic()
    with _sweep_lock:
        if now - _last_sweep.get(project_id, float("-inf")) < 3600:
            return
        _last_sweep[project_id] = now
    try:
        sweep(project_id)
    except Exception:
        _log.warning("token scratch issue sweep failed for project %s", project_id, exc_info=True)


def startup_sweep() -> int:
    projects = db_projects.list_projects()
    for project in projects:
        sweep(project["project_id"])
    return len(projects)
