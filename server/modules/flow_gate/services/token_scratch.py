"""Ownership and retention for token-owned work/<project>/tok_* directories.

Only a manifest written by this module can authorize a recursive delete.  A
legacy directory without a manifest is deliberately left for manual review.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.storage import filesystem as storage_fs
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.settings import scratch_retention

_log = logging.getLogger(__name__)


class TokenScratchStorageUnsafe(ValueError):
    """Storage root failed the token scratch safety policy."""


def _unsafe_reason(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return "symlink"
        if bool(getattr(path.lstat(), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return "reparse_point"
    except OSError:
        return "lstat_failed"
    return None


def _reject_unsafe(root: Path, reason: str) -> None:
    # The configured path is useful in server logs but must never enter the API response.
    try:
        _, source = storage_paths.resolve_storage_root()
    except Exception:
        source = "unknown"
    _log.error("token scratch storage unsafe: reason=%s effective_source=%s root=%s",
               reason, source, root)
    raise TokenScratchStorageUnsafe("Token scratch storage root is unsafe.")
MANIFEST_NAME = ".flowgate-token-scratch.json"
MANIFEST_SCHEMA = 1
RETENTION_DAYS = 7  # legacy manifest format only; GC reads system settings
JOURNAL_OWNER = "token"  # delete journal owner (storage_fs.JOURNAL_OWNERS)
_TOKEN_ID = re.compile(r"\Atok_[0-9]{8}_[0-9]{6}\Z")
_sweep_lock = threading.Lock()
_last_sweep: dict[str, float] = {}
_sweeping: set[str] = set()


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


def _checked_child(project_id: str, token_id: str, candidate: Path,
                   root: Path | None = None) -> Path | None:
    if not _TOKEN_ID.fullmatch(token_id):
        return None
    try:
        # sweep() passes the root it computed once; every other caller looks it up.
        root = _root(project_id) if root is None else root
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
        _reject_unsafe(storage_root, _unsafe_reason(storage_root) or "unsafe_path")
    storage_root.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    if (_unsafe_link(storage_root) or _unsafe_link(root.parent) or _unsafe_link(root)
            or not _same_path(scratch, (root / token_id).resolve(strict=False))):
        _reject_unsafe(storage_root, "unsafe_scratch_root_or_path")
    # mkdir(exist_ok=False) already excludes other processes; the in-process
    # target lock keeps a delete/resume of the same id out of this window.
    with storage_fs.journal_lock(JOURNAL_OWNER, project_id, token_id, interprocess=False):
        # An id whose earlier delete is still pending never names a new directory.
        if storage_fs.journal_exists(JOURNAL_OWNER, project_id, token_id):
            raise ValueError("token scratch id has a pending delete record")
        scratch.mkdir(exist_ok=False)
        created_at = datetime.now(timezone.utc).isoformat()
        identity = None
        try:
            identity = storage_fs.path_identity(scratch)
            resolved = _checked_child(project_id, token_id, scratch)
            if resolved is None:
                raise ValueError("token scratch is not a managed child")
            manifest = {
                "schema": MANIFEST_SCHEMA,
                "owner": "flowgate.token",
                "project_id": project_id,
                "token_id": token_id,
                "scratch_path": str(resolved),
                "created_at": created_at,
                "policy": {"retention_source": "system_settings"},
            }
            tmp = resolved / (MANIFEST_NAME + ".tmp")
            with tmp.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(manifest, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
            tmp.replace(resolved / MANIFEST_NAME)
        except Exception:
            _rollback_created(project_id, token_id, scratch, root, identity, created_at)
            raise


def _rollback_created(project_id: str, token_id: str, scratch: Path, root: Path,
                      identity: dict | None, created_at: str) -> None:
    """Journal, then remove, the child this create() call made; never raises.

    mkdir(exist_ok=False) plus the directory id taken right after it prove
    this call created the child, so the rollback record needs no manifest.
    """
    try:
        resolved = _checked_child(project_id, token_id, scratch, root)
        if resolved is None or identity is None or not storage_fs.same_identity(resolved, identity):
            raise storage_fs.DeleteJournalError("not_created_child")
        record = storage_fs.new_journal_record(
            JOURNAL_OWNER, "rollback", project_id, token_id,
            managed_root=resolved.parent, target=resolved, reason="create_rollback",
            eligibility={"basis": "planned_created_at", "at": created_at},
            planned={"owner": "flowgate.token", "id": token_id, "created_at": created_at},
            target_identity=identity)
        _delete_journaled(project_id, token_id, storage_fs.write_journal(
            record, extra_roots=_extra_roots()))
    except Exception as exc:
        # The original create() exception is the one the caller sees.
        _log_failure("create_rollback", token_id, exc)


def _manifest(project_id: str, token_id: str, scratch: Path,
              root: Path | None = None) -> dict | None:
    resolved = _checked_child(project_id, token_id, scratch, root)
    if resolved is None:
        return None
    path = resolved / MANIFEST_NAME
    try:
        if _unsafe_link(path) or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if _manifest_fields_ok(data, project_id, token_id, resolved) else None
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def _manifest_fields_ok(data: object, project_id: str, token_id: str, resolved: Path) -> bool:
    return (isinstance(data, dict)
            and data.get("schema") == MANIFEST_SCHEMA
            and data.get("owner") == "flowgate.token"
            and data.get("project_id") == project_id
            and data.get("token_id") == token_id
            and data.get("scratch_path") == str(resolved)
            and data.get("policy") in (
                {"retention_days": RETENTION_DAYS},
                {"retention_source": "system_settings"},
            )
            and _timestamp(data.get("created_at")) is not None)


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


def _eligible(project_id: str, token_id: str, scratch: Path, now: datetime,
              root: Path | None = None) -> dict | None:
    """The retention basis (truthy) when the scratch may go, else None."""
    manifest = _manifest(project_id, token_id, scratch, root)
    if manifest is None:
        return None
    basis = _retention_basis(project_id, token_id, scratch, manifest, now)
    if basis is None or _referenced(project_id, token_id, scratch):
        return None
    return basis


def _retention_basis(project_id: str, token_id: str, scratch: Path, manifest: dict,
                     now: datetime) -> dict | None:
    row = db_tokens.get_by_id(token_id)
    created = _timestamp(manifest["created_at"])
    if created is None or created > now:
        return None
    if row is not None:
        if row.get("project") != project_id or not row.get("scratch_dir"):
            return None
        stored = storage_paths.resolve_storage_dir(row["scratch_dir"], project_id)
        if stored is None or not _same_path(stored, scratch):
            return None
        expires = _timestamp(row.get("expires_at"))
        terminals = [_timestamp(row.get(key)) for key in ("consumed_at", "revoked_at")]
        if expires is not None and expires <= now:
            terminals.append(expires)
        terminals = [stamp for stamp in terminals if stamp is not None]
        if not terminals or any(stamp < created for stamp in terminals):
            return None
        terminal, basis = min(terminals), "row_terminal"
    else:
        # A complete, path-bound manifest is the only allowed proof for an
        # orphan whose INSERT never committed (or whose row was purged earlier).
        terminal, basis = created, "orphan_manifest_created_at"
    if now - terminal < scratch_retention.effective_retention():
        return None
    return {"basis": basis, "at": terminal.isoformat()}


def delete_owned(project_id: str, token_id: str, scratch: Path, *, rollback: bool = False,
                 root: Path | None = None) -> bool:
    """Revalidate immediately before deletion; rollback is for this call's mkdir.

    The owner evidence is journaled outside the scratch before the first file
    is removed.  A failed delete keeps that record for the next sweep.
    """
    try:
        with storage_fs.journal_lock(JOURNAL_OWNER, project_id, token_id, interprocess=False):
            if _manifest(project_id, token_id, scratch, root) is None:
                return False
            basis = None
            if not rollback:
                basis = _eligible(project_id, token_id, scratch,
                                  datetime.now(timezone.utc), root)
                if not basis:
                    return False
            if not rollback and _referenced(project_id, token_id, scratch):
                return False
            with storage_fs.journal_file_lock(JOURNAL_OWNER, project_id, token_id):
                manifest = _manifest(project_id, token_id, scratch, root)
                resolved = _checked_child(project_id, token_id, scratch, root)
                if manifest is None or resolved is None:
                    return False
                record = storage_fs.new_journal_record(
                    JOURNAL_OWNER, "rollback" if rollback else "delete", project_id, token_id,
                    managed_root=resolved.parent, target=resolved, manifest=manifest,
                    reason="issue_rollback" if rollback else "retention_expired",
                    eligibility=basis or {"basis": "issue_rollback", "at": manifest["created_at"]},
                    planned=({"owner": "flowgate.token", "id": token_id,
                              "created_at": manifest["created_at"]} if rollback else None),
                    completed_at=(basis["at"] if basis and basis["basis"] == "row_terminal"
                                  else None))
                # A record that cannot be written and re-read stops here: nothing deleted.
                stored = storage_fs.write_journal(record, extra_roots=_extra_roots())
                return _delete_journaled(project_id, token_id, stored)
    except Exception as exc:
        _log_failure("delete", token_id, exc)
        return False


def _extra_roots() -> list[Path]:
    # The journal must stay outside the source tree too (NR0003 §5.2).
    return [storage_paths.get_storage_root() / "src"]


def _delete_journaled(project_id: str, token_id: str, record: dict) -> bool:
    """Delete the recorded target, then drop the record once it is confirmed gone."""
    target, managed_root = Path(record["target"]), Path(record["managed_root"])
    if storage_fs.path_present(target):  # an unreadable target is never "gone"
        if not (storage_fs.same_identity(managed_root, record["root_identity"])
                and storage_fs.same_identity(target, record["target_identity"])):
            raise storage_fs.DeleteJournalError("identity_mismatch")
        storage_fs.remove_managed_tree(target, managed_root)
    try:
        storage_fs.remove_journal(JOURNAL_OWNER, project_id, token_id)
    except Exception as exc:
        # The target is gone; the record stays pending and the next sweep drops it.
        _log_failure("record_remove", token_id, exc)
    return True


def _log_failure(stage: str, token_id: str | None, exc: BaseException) -> None:
    """owner/id/stage/errno/winerror and a storage-relative path only."""
    _log.warning(
        "token scratch delete failed: owner=%s id=%s stage=%s code=%s errno=%s winerror=%s path=%s",
        JOURNAL_OWNER, token_id, stage, getattr(exc, "code", None) or type(exc).__name__,
        getattr(exc, "errno", None), getattr(exc, "winerror", None),
        _safe_relative(getattr(exc, "filename", None)))


def _safe_relative(filename: object) -> str:
    if not filename:
        return "-"
    try:
        storage_root = os.path.abspath(storage_paths.get_storage_root())
        path = os.path.abspath(str(filename))
        if os.path.normcase(os.path.commonpath([path, storage_root])) == os.path.normcase(storage_root):
            return Path(os.path.relpath(path, storage_root)).as_posix()
    except (OSError, TypeError, ValueError):
        pass
    return "<outside-storage>"


def _resume_pending(project_id: str, root: Path) -> int:
    """Continue journaled deletes before the ordinary candidate scan."""
    try:
        entries = storage_fs.list_journals(JOURNAL_OWNER, project_id)
    except Exception as exc:
        _log_failure("resume_list", None, exc)
        return 0
    deleted = 0
    for entry in entries:
        if entry.error is not None:
            _log.warning("token scratch delete record unusable: owner=%s id=%s code=%s",
                         JOURNAL_OWNER, entry.record_id, entry.error)
            continue
        try:
            with storage_fs.journal_lock(JOURNAL_OWNER, project_id, entry.record_id):
                if _resume_one(project_id, entry.record_id, root):
                    deleted += 1
        except Exception as exc:
            _log_failure("resume", entry.record_id, exc)
    return deleted


def _resume_one(project_id: str, token_id: str, root: Path) -> bool:
    record = storage_fs.read_journal(JOURNAL_OWNER, project_id, token_id)  # reread under lock
    target, managed_root = Path(record["target"]), Path(record["managed_root"])
    if (not _TOKEN_ID.fullmatch(token_id) or target.name != token_id
            or not _same_path(target.parent, managed_root)
            or not _same_path(managed_root, root.resolve(strict=False))):
        raise storage_fs.DeleteJournalError("record_target_mismatch")
    # Ancestors first, then the target.  Only a real ENOENT means "already
    # gone"; any other lstat failure raises PresenceUnknown and keeps the record.
    for part in (root.parent.parent, root.parent, root, target):
        if not storage_fs.path_present(part):
            # Already gone: only the record is left to drop.
            storage_fs.remove_journal(JOURNAL_OWNER, project_id, token_id)
            return False
        if _unsafe_link(part):
            raise storage_fs.DeleteJournalError("unsafe_path")
    if not (storage_fs.same_identity(managed_root, record["root_identity"])
            and storage_fs.same_identity(target, record["target_identity"])):
        raise storage_fs.DeleteJournalError("identity_mismatch")
    _check_remaining_manifest(target, record["manifest"])
    if not _resume_eligible(project_id, token_id, target, record):
        return False
    return _delete_journaled(project_id, token_id, record)


def _check_remaining_manifest(target: Path, recorded: dict | None) -> None:
    path = target / MANIFEST_NAME
    if not storage_fs.path_present(path):
        return
    try:
        if _unsafe_link(path) or not path.is_file():
            raise ValueError("unsafe manifest")
        if json.loads(path.read_text(encoding="utf-8")) == recorded:
            return
    except (OSError, ValueError):
        pass
    raise storage_fs.DeleteJournalError("manifest_conflict")


def _resume_eligible(project_id: str, token_id: str, target: Path, record: dict) -> bool:
    """_eligible/_referenced again; the journaled manifest is resume evidence only."""
    now = datetime.now(timezone.utc)
    manifest = record["manifest"]
    if manifest is not None:
        if (not _manifest_fields_ok(manifest, project_id, token_id, target)
                or _retention_basis(project_id, token_id, target, manifest, now) is None):
            return False
    else:
        # create() rollback: no live owner for the id and retention from its creation.
        planned = record["planned"] or {}
        created = _timestamp(planned.get("created_at"))
        if (record["kind"] != "rollback" or planned.get("id") != token_id
                or created is None or created > now
                or db_tokens.get_by_id(token_id) is not None
                or now - created < scratch_retention.effective_retention()):
            return False
    return not _referenced(project_id, token_id, target)


def sweep(project_id: str) -> int:
    root = _root(project_id)
    # Pending records first: a partial delete may already have removed the
    # manifest the ordinary candidate check below depends on.
    deleted = _resume_pending(project_id, root)
    if (not root.is_dir() or _unsafe_link(root.parent.parent)
            or _unsafe_link(root.parent) or _unsafe_link(root)):
        return deleted
    try:
        for child in root.iterdir():
            if (_TOKEN_ID.fullmatch(child.name)
                    and not storage_fs.journal_exists(JOURNAL_OWNER, project_id, child.name)
                    and delete_owned(project_id, child.name, child, root=root)):
                deleted += 1
    except Exception:
        _log.warning("token scratch sweep failed for project %s", project_id, exc_info=True)
    return deleted


def _sweep_in_background(project_id: str) -> None:
    try:
        sweep(project_id)
    except Exception:
        _log.warning("token scratch issue sweep failed for project %s", project_id, exc_info=True)
    finally:
        with _sweep_lock:
            _sweeping.discard(project_id)


def sweep_on_issue(project_id: str) -> threading.Thread | None:
    """Start at most one hourly sweep per project off the token-issue request path.

    flowgate.v02.0084 T#1: a synchronous sweep over thousands of tok_* folders
    made the issuing /ai-invoke/start request exceed the 30s client timeout.
    """
    now = time.monotonic()
    with _sweep_lock:
        if project_id in _sweeping:
            return None
        if now - _last_sweep.get(project_id, float("-inf")) < 3600:
            return None
        _last_sweep[project_id] = now
        _sweeping.add(project_id)
    thread = threading.Thread(target=_sweep_in_background, args=(project_id,),
                              name="token-scratch-sweep", daemon=True)
    try:
        thread.start()
    except Exception:
        with _sweep_lock:
            _sweeping.discard(project_id)
        _log.warning("token scratch issue sweep failed for project %s", project_id, exc_info=True)
        return None
    return thread


def startup_sweep() -> int:
    projects = db_projects.list_projects()
    for project in projects:
        sweep(project["project_id"])
    return len(projects)


def _delete_records_pending(project_id: str, token_id: str) -> bool:
    """The token's own record, or a record of one of its snapshots, is not finished."""
    return (storage_fs.journal_exists(JOURNAL_OWNER, project_id, token_id)
            or bool(storage_fs.pending_journals_for_parent(
                "snapshot", project_id, JOURNAL_OWNER, token_id)))


def purge_expired_rows() -> int:
    """Purge 30-day-old token rows only after their owned scratch is gone.

    The caller runs the filesystem sweep first. A remaining, linked,
    or mismatched path never authorizes a broad SQL expiry purge, and
    neither does a pending token or snapshot delete record.
    """
    purged = 0
    for row in db_tokens.list_expired_for_purge():
        try:
            project_id, token_id = row["project"], row["token_id"]
            if not _TOKEN_ID.fullmatch(token_id) or not row.get("scratch_dir"):
                continue
            root = _root(project_id)
            if (not root.is_dir() or _unsafe_link(root.parent.parent)
                    or _unsafe_link(root.parent) or _unsafe_link(root)):
                continue
            candidate = root / token_id
            stored = storage_paths.resolve_storage_dir(row["scratch_dir"], project_id)
            if (stored is None or not _same_path(stored, candidate)
                    or storage_fs.path_present(candidate)
                    or _referenced(project_id, token_id, candidate)
                    or _delete_records_pending(project_id, token_id)):
                continue
            db_tokens.delete_expired_one(token_id)
            purged += 1
        except Exception:
            _log.warning("token row purge skipped", exc_info=True)
    return purged
