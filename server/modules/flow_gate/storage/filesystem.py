"""Safe filesystem operations — rename/move with collision avoidance.

Utilities that handle Windows file locks (PermissionError) and path
collisions.  Also the managed recursive delete and its persistent delete
journal (flowgate.v02.0090 T#1, NR0003 §5.1/§5.2) shared by the Token, AI-run
and snapshot scratch owners.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NamedTuple, Optional


def ensure_dir(path: Path) -> None:
    """Create the directory if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)


def _tmp_name(path: Path) -> Path:
    """Generate a temporary name for collision avoidance."""
    return path.parent / f"{path.name}.__tmp_{uuid.uuid4().hex[:8]}"


def safe_rename(
    src: Path,
    dst: Path,
    retries: int = 3,
    delay: float = 0.3,
) -> None:
    """Safely rename src to dst.

    - If dst already exists, back it up under a temporary name first.
    - Retries on Windows PermissionError.
    """
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    ensure_dir(dst.parent)

    # Handle dst collision
    tmp_backup: Optional[Path] = None
    if dst.exists():
        tmp_backup = _tmp_name(dst)
        dst.rename(tmp_backup)

    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            src.rename(dst)
            # Delete the collision backup
            if tmp_backup and tmp_backup.exists():
                if tmp_backup.is_dir():
                    shutil.rmtree(tmp_backup, ignore_errors=True)
                else:
                    tmp_backup.unlink(missing_ok=True)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay * (attempt + 1))
        except OSError as exc:
            # Cross-device move → copy + delete
            if exc.errno == errno_EXDEV():
                _cross_device_move(src, dst)
                if tmp_backup and tmp_backup.exists():
                    if tmp_backup.is_dir():
                        shutil.rmtree(tmp_backup, ignore_errors=True)
                    else:
                        tmp_backup.unlink(missing_ok=True)
                return
            last_exc = exc
            break

    # Failed — restore backup
    if tmp_backup and tmp_backup.exists():
        try:
            tmp_backup.rename(dst)
        except Exception:
            pass
    raise OSError(f"Failed to rename: {src} → {dst}") from last_exc


def errno_EXDEV() -> int:
    """Return the cross-device errno value."""
    import errno
    return errno.EXDEV


def _cross_device_move(src: Path, dst: Path) -> None:
    """Cross-device move: copy → delete."""
    if src.is_dir():
        shutil.copytree(str(src), str(dst))
        shutil.rmtree(str(src))
    else:
        shutil.copy2(str(src), str(dst))
        src.unlink()


def move_subtree(
    src_dir: Path,
    dst_dir: Path,
    retries: int = 3,
    delay: float = 0.3,
) -> None:
    """Move all files/folders under src_dir into dst_dir.

    Creates dst_dir if it does not exist. Overwrites existing files.
    """
    if not src_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {src_dir}")

    ensure_dir(dst_dir)

    for item in src_dir.iterdir():
        dst_item = dst_dir / item.name
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                if item.is_dir():
                    if dst_item.exists():
                        move_subtree(item, dst_item, retries=retries, delay=delay)
                    else:
                        safe_rename(item, dst_item, retries=retries, delay=delay)
                else:
                    safe_rename(item, dst_item, retries=retries, delay=delay)
                last_exc = None
                break
            except PermissionError as exc:
                last_exc = exc
                time.sleep(delay * (attempt + 1))

        if last_exc:
            raise OSError(f"Failed to move subtree: {item}") from last_exc

    # Remove src_dir when empty
    try:
        src_dir.rmdir()
    except OSError:
        pass  # ignore if not empty


# ── Managed recursive delete (flowgate.v02.0090 T#1, NR0003 §5.1) ───────────
#
# Only for service-managed scratch paths whose owner already proved ownership.
# The single repair is a Windows ReadOnly regular file (Git loose objects,
# B0001): its write bit is added and the same unlink retried exactly once.
# Sharing violations, ACL denials, scandir failures and every non-Windows
# permission error propagate untouched.

_FILE_ATTRIBUTE_READONLY = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINERROR_IN_USE = (32, 33)  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION


class TreeDeleteRefused(OSError):
    """The delete was refused before anything was removed; ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DeleteJournalError(OSError):
    """A record could not be written, read or trusted; ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PresenceUnknown(DeleteJournalError):
    """lstat failed for a reason other than ENOENT (access denied, sharing
    violation, ...): the path may still exist, so it is never treated as gone."""

    def __init__(self, cause: OSError) -> None:
        super().__init__("presence_unknown")
        self.errno = cause.errno
        self.winerror = getattr(cause, "winerror", None)
        self.filename = cause.filename


def path_present(path) -> bool:
    """lstat-based existence: False only for a real ENOENT, else ``PresenceUnknown``.

    ``os.path.lexists`` also answers False when the lookup itself fails, which
    must never count as "already deleted" (the record would be dropped while
    the target remains).
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PresenceUnknown(exc) from None
    return True


def _is_link_or_reparse(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _strictly_inside(path, root) -> bool:
    child, parent = _norm(path), _norm(root)
    if child == parent:
        return False
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def _plain_chain(path: Path, top: Path) -> bool:
    """True when *path*, *top* and every directory between them is no link/reparse."""
    current = Path(os.path.abspath(str(path)))
    top_norm = _norm(top)
    while True:
        try:
            if _is_link_or_reparse(os.lstat(current)):
                return False
        except OSError:
            return False
        if _norm(current) == top_norm:
            return True
        if current.parent == current:
            return False
        current = current.parent


def _readonly_retry(target: Path):
    def handle(func, path, exc):
        if (os.name != "nt" or not isinstance(exc, PermissionError)
                or func not in (os.unlink, os.remove)
                or getattr(exc, "winerror", None) in _WINERROR_IN_USE):
            raise exc
        try:
            st = os.lstat(path)
        except OSError:
            raise exc from None
        if (not stat.S_ISREG(st.st_mode) or _is_link_or_reparse(st)
                or not getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_READONLY
                or not _strictly_inside(path, target)
                or not _plain_chain(Path(path).parent, target)):
            raise exc
        os.chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IWRITE)
        func(path)  # the one retry; its failure propagates
    return handle


def remove_managed_tree(target, allowed_root, *, missing_ok: bool = False) -> None:
    """Recursively delete exactly *target*, a directory strictly inside *allowed_root*.

    The caller decides ownership.  This refuses the root itself, an escape and
    a linked/reparse root, target or intermediate directory — rechecked here,
    right before the delete.  Success means *target* is gone; otherwise an
    OSError propagates (``TreeDeleteRefused`` when nothing was touched,
    ``PresenceUnknown`` when lstat before or after the delete fails other
    than ENOENT).
    """
    target = Path(os.path.abspath(str(target)))
    root = Path(os.path.abspath(str(allowed_root)))
    if not _strictly_inside(target, root):
        raise TreeDeleteRefused("outside_allowed_root")
    if not path_present(target):
        if missing_ok:
            return
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
    try:
        root_st, target_st = os.lstat(root), os.lstat(target)
    except OSError:
        raise TreeDeleteRefused("lstat_failed") from None
    if not stat.S_ISDIR(root_st.st_mode) or _is_link_or_reparse(root_st):
        raise TreeDeleteRefused("unsafe_root")
    if not stat.S_ISDIR(target_st.st_mode) or _is_link_or_reparse(target_st):
        raise TreeDeleteRefused("unsafe_target")
    if (not _plain_chain(target.parent, root)
            or not _strictly_inside(os.path.realpath(target), os.path.realpath(root))):
        raise TreeDeleteRefused("unsafe_parent")
    handler = _readonly_retry(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(target, onexc=handler)
    else:
        shutil.rmtree(target, onerror=lambda func, path, info: handler(func, path, info[1]))
    if path_present(target):
        raise OSError(errno.ENOTEMPTY, "delete target is still present")


# ── Delete journal (flowgate.v02.0090 T#1, NR0003 §5.2) ─────────────────────
#
# A delete starts only after its owner evidence is persisted *outside* the
# deleted tree, so a partial delete or a crash that already removed the
# original manifest is resumed by the owner's existing sweep.
#   G = <S0>/.flowgate-gc,  record = G/<owner>/<sha256(project_id)>/<id>.json
# where S0 = storage_paths.get_storage_root() without a project override.
# Owners: token (wired here), ai-run and snapshot (T#2/T#3).  A snapshot
# record must name its token in ``parent`` ({"owner": "token", "id": ...}) so
# the token row purge can wait for it; one that does not is a damaged record.

GC_DIR_NAME = ".flowgate-gc"
JOURNAL_SCHEMA = 1
JOURNAL_OWNERS = ("token", "ai-run", "snapshot")
JOURNAL_KINDS = ("delete", "rollback")
_JOURNAL_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_JOURNAL_FIELDS = frozenset({
    "schema", "owner", "kind", "project_id", "project_key", "record_id",
    "storage_root", "managed_root", "target", "root_identity", "target_identity",
    "manifest", "planned", "created_at", "completed_at", "reason", "eligibility",
    "parent",
})
_JOURNAL_MAX_BYTES = 1 << 20
_O_BINARY = getattr(os, "O_BINARY", 0)
# Records of these owners must name the parent whose row purge waits for them.
_REQUIRED_PARENT = {"snapshot": "token"}


class JournalEntry(NamedTuple):
    record_id: str
    record: Optional[dict]
    error: Optional[str]  # set instead of ``record`` for a damaged/untrusted file


def project_key(project_id: str) -> str:
    return hashlib.sha256(project_id.encode("utf-8")).hexdigest()


def journal_storage_root() -> Path:
    """S0: the global storage root (no project override), absolute."""
    from modules.flow_gate.storage import paths as storage_paths  # same layer, lazy
    return Path(os.path.abspath(str(storage_paths.get_storage_root())))


def _check_owner(owner: str, record_id: Optional[str] = None) -> None:
    if owner not in JOURNAL_OWNERS:
        raise ValueError("unknown delete journal owner")
    if record_id is not None and not _JOURNAL_ID.fullmatch(str(record_id)):
        raise ValueError("invalid delete journal id")


def journal_path(owner: str, project_id: str, record_id: str) -> Path:
    _check_owner(owner, record_id)
    return (journal_storage_root() / GC_DIR_NAME / owner / project_key(project_id)
            / f"{record_id}.json")


def journal_exists(owner: str, project_id: str, record_id: str) -> bool:
    """True while any record file — valid or not — is pending for this id.

    Raises ``PresenceUnknown`` when the record file cannot be looked up.
    """
    return path_present(journal_path(owner, project_id, record_id))


def _ancestry(directory: Path) -> Iterator[Path]:
    """*directory* and every ancestor, on the logical and on the resolved path."""
    seen = set()
    for start in (directory, Path(os.path.realpath(directory))):
        for current in (start, *start.parents):
            if _norm(current) not in seen:
                seen.add(_norm(current))
                yield current


def _check_outside_repository(directory: Path) -> None:
    # A link/junction above S0 must not hide a repository: the resolved chain
    # is checked too.  Hiding G with .gitignore is not an option (NR0003 §5.2).
    try:
        inside = any(path_present(current / ".git") for current in _ancestry(directory))
    except PresenceUnknown:
        raise DeleteJournalError("journal_location_unverified") from None
    if inside:
        raise DeleteJournalError("journal_in_repository")


def _journal_dir(owner: str, project_id: str, *, create: bool) -> Path:
    """G/<owner>/<project-key>, refusing a G that Git or a link could expose."""
    storage_root = journal_storage_root()
    _check_outside_repository(storage_root / GC_DIR_NAME / owner / project_key(project_id))
    try:
        root_st = os.lstat(storage_root)
    except OSError:
        raise DeleteJournalError("journal_dir_failed") from None
    if _is_link_or_reparse(root_st):
        raise DeleteJournalError("journal_dir_unsafe")
    gc_root = storage_root / GC_DIR_NAME
    directory = gc_root / owner / project_key(project_id)
    for part in (gc_root, gc_root / owner, directory):
        try:
            if create:
                part.mkdir(exist_ok=True)
            st = os.lstat(part)
        except FileNotFoundError:
            if create:
                raise DeleteJournalError("journal_dir_failed") from None
            return directory  # nothing recorded yet
        except OSError:
            raise DeleteJournalError("journal_dir_failed") from None
        if _is_link_or_reparse(st) or not stat.S_ISDIR(st.st_mode):
            raise DeleteJournalError("journal_dir_unsafe")
    return directory


def path_identity(path) -> dict:
    """``{"dev", "ino"}`` of *path* itself (not followed); a zero id is untrusted."""
    try:
        st = os.lstat(path)
    except OSError:
        raise DeleteJournalError("identity_unavailable") from None
    if not st.st_ino:
        raise DeleteJournalError("identity_unavailable")
    return {"dev": int(st.st_dev), "ino": int(st.st_ino)}


def same_identity(path, identity) -> bool:
    try:
        return path_identity(path) == {"dev": identity["dev"], "ino": identity["ino"]}
    except (DeleteJournalError, KeyError, TypeError):
        return False


def new_journal_record(owner: str, kind: str, project_id: str, record_id: str, *,
                       managed_root, target, reason: str, eligibility: dict,
                       manifest: Optional[dict] = None, planned: Optional[dict] = None,
                       completed_at: Optional[str] = None, parent: Optional[dict] = None,
                       target_identity: Optional[dict] = None) -> dict:
    """Build the record for an approved delete of *target* (a child of *managed_root*).

    ``manifest`` is the original owner manifest; a rollback carries
    ``planned`` = {"owner", "id", "created_at"}.  ``eligibility`` keeps the
    original eligibility basis/time.  A snapshot record needs
    ``parent`` = {"owner": "token", "id": <token id>}.  Never put prompts,
    commands, credentials or file contents here.
    """
    _check_owner(owner, record_id)
    managed_root = os.path.abspath(str(managed_root))
    target = os.path.abspath(str(target))
    return {
        "schema": JOURNAL_SCHEMA, "owner": owner, "kind": kind,
        "project_id": project_id, "project_key": project_key(project_id),
        "record_id": record_id, "storage_root": str(journal_storage_root()),
        "managed_root": managed_root, "target": target,
        "root_identity": path_identity(managed_root),
        "target_identity": target_identity or path_identity(target),
        "manifest": manifest, "planned": planned,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": completed_at, "reason": reason,
        "eligibility": eligibility, "parent": parent,
    }


def _aware(value) -> bool:
    try:
        return datetime.fromisoformat(str(value)).tzinfo is not None
    except (TypeError, ValueError):
        return False


def _identity_ok(value) -> bool:
    return (isinstance(value, dict) and set(value) == {"dev", "ino"}
            and all(type(value[key]) is int for key in value) and value["ino"] != 0)


def _record_problem(data, owner: str, project_id: str, record_id: str) -> Optional[str]:
    if not isinstance(data, dict) or set(data) != _JOURNAL_FIELDS:
        return "journal_invalid"
    if data["schema"] != JOURNAL_SCHEMA:
        return "journal_schema"
    if (data["owner"] != owner or data["project_id"] != project_id
            or data["project_key"] != project_key(project_id)
            or data["record_id"] != record_id):
        return "journal_mismatch"
    if not isinstance(data["storage_root"], str) or _norm(data["storage_root"]) != _norm(
            journal_storage_root()):
        # A record never authorizes a delete under another storage root (NR0003 §5.2).
        return "journal_storage_root_changed"
    if (data["kind"] not in JOURNAL_KINDS
            or not all(isinstance(data[key], str) and os.path.isabs(data[key])
                       for key in ("managed_root", "target"))
            or not _strictly_inside(data["target"], data["managed_root"])
            or not (_identity_ok(data["root_identity"]) and _identity_ok(data["target_identity"]))
            or not _aware(data["created_at"])
            or not (data["completed_at"] is None or _aware(data["completed_at"]))
            or not isinstance(data["reason"], str) or not isinstance(data["eligibility"], dict)
            or any(data[key] is not None and not isinstance(data[key], dict)
                   for key in ("manifest", "planned", "parent"))):
        return "journal_invalid"
    planned = data["planned"]
    if data["kind"] == "rollback" and not (
            planned is not None and planned.get("id") == record_id
            and isinstance(planned.get("owner"), str) and _aware(planned.get("created_at"))):
        return "journal_invalid"
    if not _parent_ok(owner, data["parent"]):
        # A snapshot whose token cannot be named would slip past the token row purge.
        return "journal_parent_invalid"
    return None


def _parent_ok(owner: str, parent) -> bool:
    required = _REQUIRED_PARENT.get(owner)
    if parent is None:
        return required is None
    return (isinstance(parent, dict) and set(parent) == {"owner", "id"}
            and parent["owner"] in JOURNAL_OWNERS and parent["owner"] != owner
            and (required is None or parent["owner"] == required)
            and isinstance(parent["id"], str) and bool(_JOURNAL_ID.fullmatch(parent["id"])))


def _load(path: Path, owner: str, project_id: str, record_id: str) -> dict:
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or _is_link_or_reparse(st):
            raise DeleteJournalError("journal_unsafe")
        if st.st_size > _JOURNAL_MAX_BYTES:
            raise DeleteJournalError("journal_invalid")
        with open(path, "rb") as handle:
            data = json.loads(handle.read())
    except DeleteJournalError:
        raise
    except FileNotFoundError:
        raise DeleteJournalError("journal_missing") from None
    except (OSError, ValueError):
        raise DeleteJournalError("journal_unreadable") from None
    problem = _record_problem(data, owner, project_id, record_id)
    if problem:
        raise DeleteJournalError(problem)
    return data


def read_journal(owner: str, project_id: str, record_id: str) -> dict:
    """The validated record, or ``DeleteJournalError`` with the reason code."""
    _check_owner(owner, record_id)
    directory = _journal_dir(owner, project_id, create=False)
    return _load(directory / f"{record_id}.json", owner, project_id, record_id)


def _check_separation(gc_root: Path, roots) -> None:
    """G must neither contain nor sit inside a delete/source/snapshot/attempt root."""
    gc_identity = path_identity(gc_root)
    gc_real = os.path.realpath(gc_root)
    for root in roots:
        real = os.path.realpath(str(root))
        if (_norm(root) == _norm(gc_root) or _strictly_inside(root, gc_root)
                or _strictly_inside(gc_root, root) or _norm(real) == _norm(gc_real)
                or _strictly_inside(real, gc_real) or _strictly_inside(gc_real, real)
                or (path_present(root) and same_identity(root, gc_identity))):
            raise DeleteJournalError("journal_overlap")


def write_journal(record: dict, *, extra_roots=()) -> dict:
    """Persist *record* atomically and return the stored, re-read and verified record.

    Exclusive temp file → write → flush/fsync → atomic replace → re-read.  An
    existing valid record for the same target is returned unchanged (its
    times are not refreshed).  Every failure raises ``DeleteJournalError``
    before the caller deletes anything.  ``extra_roots`` adds source,
    snapshot or attempt roots to the overlap check.
    """
    owner, project_id, record_id = (record.get("owner"), record.get("project_id"),
                                    record.get("record_id"))
    _check_owner(owner, record_id)
    problem = _record_problem(record, owner, project_id, record_id)
    if problem:
        raise DeleteJournalError(problem)
    directory = _journal_dir(owner, project_id, create=True)
    _check_separation(directory.parent.parent,
                      [record["managed_root"], record["target"], *extra_roots])
    path = directory / f"{record_id}.json"
    if path_present(path):  # an unreadable slot is never overwritten
        existing = _load(path, owner, project_id, record_id)
        if (existing["kind"] != record["kind"]
                or _norm(existing["target"]) != _norm(record["target"])
                or existing["target_identity"] != record["target_identity"]):
            raise DeleteJournalError("journal_conflict")
        return existing
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    tmp = directory / f".{record_id}.{uuid.uuid4().hex[:12]}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise DeleteJournalError("journal_write_failed") from None
    stored = _load(path, owner, project_id, record_id)
    if stored != record:
        raise DeleteJournalError("journal_verify_failed")
    return stored


def remove_journal(owner: str, project_id: str, record_id: str) -> None:
    """Drop a record after its owner confirmed the target is gone."""
    _check_owner(owner, record_id)
    path = _journal_dir(owner, project_id, create=False) / f"{record_id}.json"
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        raise DeleteJournalError("journal_remove_failed") from None
    if path_present(path):
        raise DeleteJournalError("journal_remove_failed")


def list_journals(owner: str, project_id: str) -> list[JournalEntry]:
    """Pending ``<id>.json`` records of one owner/project-key, in name order.

    Damaged or untrusted files are reported with ``error`` instead of being
    skipped.  ``.tmp`` and ``.lock`` files are never records.
    """
    _check_owner(owner)
    directory = _journal_dir(owner, project_id, create=False)
    try:
        names = sorted(entry.name for entry in os.scandir(directory))
    except FileNotFoundError:
        return []
    except OSError:
        raise DeleteJournalError("journal_list_failed") from None
    entries = []
    for name in names:
        if not name.endswith(".json"):
            continue
        record_id = name[:-len(".json")]
        if not _JOURNAL_ID.fullmatch(record_id):
            entries.append(JournalEntry(record_id, None, "journal_bad_name"))
            continue
        try:
            entries.append(JournalEntry(
                record_id, _load(directory / name, owner, project_id, record_id), None))
        except DeleteJournalError as exc:
            entries.append(JournalEntry(record_id, None, exc.code))
    return entries


def pending_journals_for_parent(owner: str, project_id: str, parent_owner: str,
                                parent_id: str) -> list[JournalEntry]:
    """Records of *owner* whose ``parent`` is *parent_owner*/*parent_id* (a token's snapshots).

    Damaged records are included: their parent cannot be ruled out, so a
    caller waiting for "nothing pending" fails closed.
    """
    return [entry for entry in list_journals(owner, project_id)
            if entry.error is not None
            or (entry.record.get("parent") or {}) == {"owner": parent_owner, "id": parent_id}]


# Per-target exclusion for create / delete / resume.  Never waits: a busy
# target raises ``journal_busy`` and is left to the owner's next existing sweep.
_held_lock = threading.Lock()
_held: set[tuple[str, str, str]] = set()


def _lock_file(path: Path) -> int:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o600)
    except OSError:
        raise DeleteJournalError("journal_lock_failed") from None
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held, named = os.fstat(fd), os.lstat(path)
            if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
                raise OSError("lock file was replaced")
    except OSError:
        os.close(fd)
        raise DeleteJournalError("journal_busy") from None
    return fd


def _unlock_file(fd: int, path: Path) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            os.unlink(path)  # while still locked; a waiter rechecks the inode
    except OSError:
        pass
    finally:
        os.close(fd)
    if os.name == "nt":
        try:
            os.unlink(path)  # refused harmlessly while another process has it open
        except OSError:
            pass


@contextmanager
def journal_file_lock(owner: str, project_id: str, record_id: str) -> Iterator[None]:
    """Cross-process lock on ``G/<owner>/<project-key>/<id>.lock`` for journal writers."""
    _check_owner(owner, record_id)
    path = _journal_dir(owner, project_id, create=True) / f"{record_id}.lock"
    fd = _lock_file(path)
    try:
        yield
    finally:
        _unlock_file(fd, path)


@contextmanager
def journal_lock(owner: str, project_id: str, record_id: str, *,
                 interprocess: bool = True) -> Iterator[None]:
    """In-process per-target lock, plus ``journal_file_lock`` when *interprocess*."""
    _check_owner(owner, record_id)
    key = (owner, project_key(project_id), record_id)
    with _held_lock:
        if key in _held:
            raise DeleteJournalError("journal_busy")
        _held.add(key)
    try:
        if interprocess:
            with journal_file_lock(owner, project_id, record_id):
                yield
        else:
            yield
    finally:
        with _held_lock:
            _held.discard(key)
