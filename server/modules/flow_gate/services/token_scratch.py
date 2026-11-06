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


# ── flowgate.v02.0137 T0004 §3 (A-2): copy unsubmitted drafts into the NEXT token's scratch ──
# A worker may only submit `doc_path` files from its OWN token scratch (inbox
# `validate_doc_path`). When a run changes token (review-loop stage switch, reissue) or a
# follow-up run starts, the previous drafts sit in a directory the new token cannot submit
# from. This copies them — never moves, never deletes the source — into
# `<new token scratch>/carryover/<label>/`, where the new worker can read, edit and
# resubmit them through the ordinary checks. Nothing is submitted on the worker's behalf.
CARRYOVER_DIR = "carryover"
CARRYOVER_MANIFEST = "CARRYOVER.json"
CARRYOVER_SCHEMA = 1
CARRYOVER_MAX_FILE_BYTES = 10 * 1024 * 1024
CARRYOVER_MAX_TOTAL_BYTES = 50 * 1024 * 1024
CARRYOVER_MAX_FILES = 200
CARRYOVER_MAX_DEPTH = 8
_CARRYOVER_LABEL = re.compile(r"[^A-Za-z0-9_\-]")
_CARRYOVER_SECRET_NAMES = re.compile(r"(?i)\A(\.env(\..*)?|.*\.(pem|key|pfx|p12)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?)\Z")
_COPY_CHUNK = 1024 * 1024
# TR0005 rev1 (T0004 §3-5): a file is refused on its CONTENT, not only on the raw tokens
# this run handed out. A follow-up run's sources were written under an EARLIER run's token,
# which the new run never saw. Two layers, no raw token stored anywhere new:
#   * credential shapes — an Authorization/Bearer header value, a FLOWGATE_TOKEN=... line;
#   * token-shaped strings (43 url-safe chars, secrets.token_urlsafe(32)) that the token
#     table recognizes (hash lookup through the caller's `token_probe`), whatever their
#     state — a spent token is still a credential in a file someone may share.
_CREDENTIAL_PATTERNS = (
    re.compile(rb"(?i)authorization[\"'\s]*[:=][\"'\s]*bearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9_\-]{24,}"),
    re.compile(rb"(?i)flowgate_token[\"'\s]*[:=][\"'\s]*[A-Za-z0-9_\-]{16,}"),
)
_TOKEN_SHAPE = re.compile(rb"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])")
_TOKEN_PROBES_PER_FILE = 256
_SCAN_OVERLAP = 512


def _carryover_part_ok(part: str) -> bool:
    return bool(part) and part not in (".", "..") and ":" not in part and "\\" not in part \
        and "/" not in part and not part.endswith((" ", "."))


def _carryover_candidates(source: dict, skipped: list[dict], top: Path | None = None,
                          shown_prefix: str = ""):
    """Yield `(relative_posix, path, size, mtime)` for one validated tree, depth-first, sorted.

    Symlinks / reparse points (files and directories) are refused, as are names that
    could re-resolve (`..`, ADS `:`, trailing dot/space) and anything deeper than
    CARRYOVER_MAX_DEPTH. Top-level names in `exclude_top` and every FlowGate manifest are
    infrastructure, not drafts.
    """
    top = Path(source["path"]) if top is None else top
    exclude_top = set() if shown_prefix else {name.lower() for name in source.get("exclude_top") or ()}
    for current, dirs, files in os.walk(top, topdown=True, followlinks=False):
        here = Path(current)
        rel_dir = here.relative_to(top)
        depth = len(rel_dir.parts)
        kept = []
        for name in sorted(dirs):
            rel = (rel_dir / name).as_posix()
            if depth == 0 and name.lower() in exclude_top:
                continue
            if not _carryover_part_ok(name) or _unsafe_link(here / name):
                skipped.append({"path": shown_prefix + rel, "reason": "unsafe_directory"})
                continue
            if depth + 1 >= CARRYOVER_MAX_DEPTH:
                skipped.append({"path": shown_prefix + rel, "reason": "too_deep"})
                continue
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            rel = (rel_dir / name).as_posix()
            lowered = name.lower()
            if depth == 0 and lowered in exclude_top:
                continue
            if lowered.startswith(".flowgate-") or lowered == CARRYOVER_MANIFEST.lower() \
                    or lowered.endswith(".carry-tmp"):
                continue
            if _CARRYOVER_SECRET_NAMES.fullmatch(name):
                skipped.append({"path": shown_prefix + rel, "reason": "secret_like_name"})
                continue
            path = here / name
            if not _carryover_part_ok(name) or _unsafe_link(path):
                skipped.append({"path": shown_prefix + rel, "reason": "unsafe_file"})
                continue
            try:
                info = path.lstat()
            except OSError:
                skipped.append({"path": shown_prefix + rel, "reason": "unreadable"})
                continue
            if not stat.S_ISREG(info.st_mode):
                skipped.append({"path": shown_prefix + rel, "reason": "not_regular_file"})
                continue
            yield rel, path, int(info.st_size), float(info.st_mtime)


def _relayed_carryovers(source: dict, skipped: list[dict]):
    """TR0005 rev1: what an earlier carry-over left in a SOURCE token scratch, flattened.

    A worker edits a carried draft where it was put (`carryover/<label>/<origin>/...`). The
    next token must receive that newest content, but never `carryover/<l1>/<origin>/
    carryover/<l2>/...` growth. So only folders the server itself wrote for THIS source
    token (their CARRYOVER.json names it as target) are read, and each file is re-keyed to
    `<origin>/<rest>` — the origin prefix it was first carried under. A file a worker put
    straight into the label folder becomes `carried_<label>/<name>`. Yields
    `(dest_relative, path, size, mtime, via)`.
    """
    root = Path(source["path"]) / CARRYOVER_DIR
    if not root.exists():
        return
    if _unsafe_link(root) or not root.is_dir():
        skipped.append({"path": CARRYOVER_DIR, "reason": "carryover_root_unsafe"})
        return
    for label_dir in sorted(root.iterdir(), key=lambda p: p.name):
        name = label_dir.name
        shown = f"{CARRYOVER_DIR}/{name}"
        if not _carryover_part_ok(name) or _unsafe_link(label_dir) or not label_dir.is_dir():
            skipped.append({"path": shown, "reason": "carryover_unverified"})
            continue
        manifest = label_dir / CARRYOVER_MANIFEST
        try:
            record = json.loads(manifest.read_text(encoding="utf-8")) \
                if manifest.is_file() and not _unsafe_link(manifest) else None
        except (OSError, ValueError):
            record = None
        if not isinstance(record, dict) or record.get("target_token_id") != source.get("id") \
                or record.get("schema") != CARRYOVER_SCHEMA:
            skipped.append({"path": shown, "reason": "carryover_unverified"})
            continue
        for rel, path, size, mtime in _carryover_candidates(source, skipped, top=label_dir,
                                                           shown_prefix=shown + "/"):
            parts = rel.split("/")
            if len(parts) >= 2:
                dest = "/".join(parts)
            else:
                dest = f"carried_{_CARRYOVER_LABEL.sub('_', name)[:120]}/{parts[0]}"
            yield dest, path, size, mtime, shown


def _scan_refusal(window: bytes, secrets: tuple[bytes, ...], token_probe, probed: set) -> str | None:
    if any(secret in window for secret in secrets):
        return "contains_token"
    if any(pattern.search(window) for pattern in _CREDENTIAL_PATTERNS):
        return "contains_credential"
    if token_probe is not None:
        for match in _TOKEN_SHAPE.finditer(window):
            candidate = match.group(0)
            if candidate in probed:
                continue
            if len(probed) >= _TOKEN_PROBES_PER_FILE:
                return "secret_check_incomplete"
            probed.add(candidate)
            try:
                if token_probe(candidate.decode("ascii")):
                    return "contains_token"
            except Exception:
                return "secret_check_failed"
    return None


def _copy_one(src: Path, dst: Path, size_limit: int, secrets: tuple[bytes, ...],
              token_probe=None) -> tuple[str, int, str | None]:
    """Copy with a running sha256; `(sha256, bytes, refusal)` — refusal leaves no file."""
    import hashlib

    digest = hashlib.sha256()
    total = 0
    tail = b""
    keep = max([len(s) for s in secrets] + [_SCAN_OVERLAP])
    probed: set = set()
    tmp = dst.with_name(dst.name + ".carry-tmp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    refusal = None
    try:
        with src.open("rb") as reader, tmp.open("xb") as writer:
            while True:
                chunk = reader.read(_COPY_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > size_limit:
                    refusal = "file_too_large"
                    break
                window = tail + chunk
                refusal = _scan_refusal(window, secrets, token_probe, probed)
                if refusal is not None:
                    break
                tail = window[-keep:]
                digest.update(chunk)
                writer.write(chunk)
        if refusal is None:
            os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return digest.hexdigest(), total, refusal


def carry_over(project_id: str, *, target_token_id: str, target_scratch: Path, label: str,
               sources: list[dict], meta: dict | None = None,
               secrets: tuple[str, ...] = (), token_probe=None) -> dict:
    """Copy validated draft sources into `<target>/carryover/<label>/`. Never raises.

    `sources`: `[{"kind", "id", "path", "exclude_top", "relay_carryover"}]`, each ALREADY
    ownership-checked by the caller (token scratches are re-checked here against their own
    manifest). The target must be this project's managed `tok_*` child with a valid
    manifest. A label that already exists is left untouched (same hop asked twice).
    Limits: per file CARRYOVER_MAX_FILE_BYTES, per carry CARRYOVER_MAX_TOTAL_BYTES /
    CARRYOVER_MAX_FILES; anything refused is listed under `skipped` with its reason, both
    in the returned summary and in CARRYOVER.json, and is logged at WARNING (paths and
    reasons only). A file is refused when it contains one of the raw `secrets`, a
    credential shape, or a string `token_probe` recognizes as a FlowGate token.

    TR0005 rev1: a token source with `relay_carryover` also hands on what earlier
    carry-overs put into it (`_relayed_carryovers`), flattened to `<origin>/<rest>`, so a
    chain of reissues / failed runs keeps the newest draft without nesting. When two
    candidates land on the same destination the newest file wins; the other is listed
    as `superseded_by_newer`.
    """
    summary: dict = {"status": "skipped", "label": label, "files": [], "skipped": [],
                     "bytes": 0}
    try:
        target = _checked_child(project_id, target_token_id, Path(target_scratch))
        if target is None or _manifest(project_id, target_token_id, target) is None:
            summary["reason"] = "target_scratch_unowned"
            return summary
        safe_label = _CARRYOVER_LABEL.sub("_", label)[:120] or "carry"
        carry_root = target / CARRYOVER_DIR
        if carry_root.exists() and (_unsafe_link(carry_root) or not carry_root.is_dir()):
            summary["reason"] = "carryover_root_unsafe"
            return summary
        dest = carry_root / safe_label
        if dest.exists():
            summary.update(status="already_present", dest=str(dest))
            return summary
        usable = []
        for source in sources:
            path = Path(source["path"])
            if source.get("kind") == "token_scratch":
                if _manifest(project_id, str(source.get("id")), path) is None:
                    summary["skipped"].append({"path": str(source.get("id")),
                                               "reason": "source_unowned"})
                    continue
            if not path.is_dir() or _unsafe_link(path):
                summary["skipped"].append({"path": str(source.get("id")), "reason": "source_missing"})
                continue
            if _same_path(path.resolve(strict=False), target):
                continue  # the same token scratch: its files are already submittable
            usable.append(source)
        secret_bytes = tuple(s.encode("utf-8") for s in secrets if s)
        # Gather first, then de-duplicate by destination (newest wins), then copy.
        chosen: dict[str, dict] = {}
        for source in usable:
            prefix = _CARRYOVER_LABEL.sub("_", f"{source.get('kind')}_{source.get('id')}")[:120]
            found = [(f"{prefix}/{rel}", path, size, mtime, None)
                     for rel, path, size, mtime in _carryover_candidates(source, summary["skipped"])]
            if source.get("kind") == "token_scratch" and source.get("relay_carryover"):
                found.extend(_relayed_carryovers(source, summary["skipped"]))
            for dest_rel, path, size, mtime, via in found:
                entry = {"dest": dest_rel, "path": path, "size": size, "mtime": mtime,
                         "via": via, "source_kind": source.get("kind"),
                         "source_id": source.get("id")}
                key = dest_rel.lower()
                previous = chosen.get(key)
                if previous is None:
                    chosen[key] = entry
                    continue
                newer, older = (entry, previous) if mtime > previous["mtime"] else (previous, entry)
                chosen[key] = newer
                summary["skipped"].append({"path": older["dest"], "reason": "superseded_by_newer",
                                           "via": older["via"] or older["source_id"]})
        files: list[dict] = summary["files"]
        total = 0
        for key in sorted(chosen):
            entry = chosen[key]
            shown, path, size = entry["dest"], entry["path"], entry["size"]
            if size > CARRYOVER_MAX_FILE_BYTES:
                summary["skipped"].append({"path": shown, "bytes": size, "reason": "file_too_large"})
                continue
            if len(files) >= CARRYOVER_MAX_FILES:
                summary["skipped"].append({"path": shown, "bytes": size, "reason": "file_count_limit"})
                continue
            if total + size > CARRYOVER_MAX_TOTAL_BYTES:
                summary["skipped"].append({"path": shown, "bytes": size, "reason": "total_size_limit"})
                continue
            dst = dest.joinpath(*shown.split("/"))
            try:
                sha, copied, refusal = _copy_one(
                    path, dst, min(CARRYOVER_MAX_FILE_BYTES, CARRYOVER_MAX_TOTAL_BYTES - total),
                    secret_bytes, token_probe)
            except OSError as exc:
                summary["skipped"].append({"path": shown, "reason": f"copy_failed:{type(exc).__name__}"})
                continue
            if refusal is not None:
                summary["skipped"].append({"path": shown, "bytes": size, "reason": refusal})
                continue
            total += copied
            files.append({"path": shown, "bytes": copied, "sha256": sha,
                          "source_kind": entry["source_kind"], "source_id": entry["source_id"],
                          "origin": shown.split("/", 1)[0], "via": entry["via"]})
        summary["bytes"] = total
        summary["relayed"] = sum(1 for f in files if f.get("via"))
        if not files and not dest.exists():
            summary["status"] = "nothing_to_copy"
        else:
            dest.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": CARRYOVER_SCHEMA,
                "copied_at": datetime.now(timezone.utc).isoformat(),
                "target_token_id": target_token_id,
                "label": safe_label,
                "sources": [{"kind": s.get("kind"), "id": s.get("id")} for s in usable],
                "files": files,
                "skipped": summary["skipped"],
                "limits": {"file_bytes": CARRYOVER_MAX_FILE_BYTES,
                           "total_bytes": CARRYOVER_MAX_TOTAL_BYTES,
                           "files": CARRYOVER_MAX_FILES},
                **(meta or {}),
            }
            manifest_tmp = dest / (CARRYOVER_MANIFEST + ".tmp")
            with manifest_tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=1, sort_keys=True)
            manifest_tmp.replace(dest / CARRYOVER_MANIFEST)
            summary.update(status="copied" if files else "nothing_to_copy", dest=str(dest))
    except Exception as exc:  # noqa: BLE001 — a carry-over is an aid, never a run failure
        summary.update(status="failed", reason=type(exc).__name__)
        _log.warning("token scratch carry-over failed: target=%s label=%s", target_token_id,
                     label, exc_info=True)
    if summary["skipped"]:
        _log.warning("token scratch carry-over skipped entries: target=%s label=%s skipped=%s",
                     target_token_id, label,
                     json.dumps(summary["skipped"][:50], ensure_ascii=True, default=str))
    return summary


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
