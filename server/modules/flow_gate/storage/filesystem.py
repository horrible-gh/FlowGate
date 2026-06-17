"""Safe filesystem operations — rename/move with collision avoidance.

Utilities that handle Windows file locks (PermissionError) and path
collisions.
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional


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
