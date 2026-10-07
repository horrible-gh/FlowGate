"""Independent Bundle and AI Scratch cleanup with retryable warnings."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from modules.flow_gate.db import source_bundles as db
from modules.flow_gate.services import source_bundle_materializer as materializer

logger = logging.getLogger(__name__)
_SWEEP_SECONDS = 300
_ORPHAN_GRACE_SECONDS = 180
_stop = threading.Event()
_started = False
_guard = threading.Lock()


def _safe_remove(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if materializer._linked(path.lstat()):
        raise OSError("cleanup target is a link")
    if not path.is_dir():
        raise OSError("cleanup target is not a directory")
    shutil.rmtree(path)


def cleanup_scratch(key: str, *, trigger: str = "explicit") -> bool:
    row = db.scratch_get(key)
    if row is None or row["status"] == "deleted":
        return True
    bundle = db.get(row["bundle_id"])
    if bundle is None or not re.fullmatch(r"[0-9a-f]{64}", key):
        db.scratch_cleanup_warning(key, "invalid scratch ownership")
        return False
    parent = materializer.bundle_path(bundle["project_id"], bundle["bundle_id"]).parent / "scratch"
    target = parent / key
    lock = parent / ("." + key + ".lock")
    try:
        # A build in progress retains ownership until its lock is removed.
        if lock.exists():
            if trigger in {"startup_orphan", "ttl"} and time.time() - lock.stat().st_mtime > _ORPHAN_GRACE_SECONDS:
                _safe_remove(lock)
            else:
                raise OSError("scratch build in progress")
        _safe_remove(target)
        db.scratch_deleted(key)
        return True
    except OSError as exc:
        db.scratch_cleanup_warning(key, f"{trigger}: {type(exc).__name__}")
        logger.warning("Source Bundle Scratch cleanup failed key=%s trigger=%s", key, trigger, exc_info=True)
        return False


def cleanup_for_run(run_id: str) -> dict:
    rows = db.scratch_candidates(run_id=run_id)
    return {"matched": len(rows), "deleted": sum(cleanup_scratch(r["scratch_key"], trigger="run_finished") for r in rows)}


def cleanup_for_token(token_id: str) -> dict:
    rows = db.scratch_candidates(token_id=token_id)
    return {"matched": len(rows), "deleted": sum(cleanup_scratch(r["scratch_key"], trigger="token_cleanup") for r in rows)}


def cleanup_bundle(bundle_id: str, *, trigger: str = "explicit") -> bool:
    row = db.get(bundle_id)
    if row is None or row["status"] == "deleted":
        return True
    if row["status"] != "created":
        return False
    # A live Test Basis executes from a pinned Bundle (0682 D#1 3.7); only Group cleanup,
    # which releases the Group's Pins first, may remove it.
    if trigger != "group_finished" and db.is_pinned(bundle_id):
        return False
    try:
        _safe_remove(materializer.bundle_path(row["project_id"], bundle_id))
        db.bundle_cleanup_success(bundle_id)
        db.deleted(bundle_id)
        return True
    except OSError as exc:
        db.bundle_cleanup_warning(bundle_id, f"{trigger}: {type(exc).__name__}")
        logger.warning("Source Bundle cleanup failed bundle=%s trigger=%s", bundle_id, trigger, exc_info=True)
        return False


def cleanup_for_group(group_id: str) -> dict:
    db.pin_release_group(group_id)
    scratch = db.scratch_candidates(group_id=group_id)
    scratch_deleted = sum(cleanup_scratch(row["scratch_key"], trigger="group_finished") for row in scratch)
    bundles = db.list_created(group_id=group_id)
    bundle_deleted = sum(cleanup_bundle(row["bundle_id"], trigger="group_finished") for row in bundles)
    return {"scratch_matched": len(scratch), "scratch_deleted": scratch_deleted,
            "bundle_matched": len(bundles), "bundle_deleted": bundle_deleted}


def sweep_expired(now: datetime | None = None) -> dict:
    point = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    scratch = db.scratch_candidates(expired_before=point)
    scratch_deleted = sum(cleanup_scratch(row["scratch_key"], trigger="ttl") for row in scratch)
    bundles = db.list_created(expired_before=point, exclude_pinned=True)
    bundle_deleted = sum(cleanup_bundle(row["bundle_id"], trigger="ttl") for row in bundles)
    return {"scratch_matched": len(scratch), "scratch_deleted": scratch_deleted,
            "bundle_matched": len(bundles), "bundle_deleted": bundle_deleted}


def cleanup_orphans() -> dict:
    """At startup, no run from the prior server process can own its Scratch."""
    scratch = db.scratch_candidates()
    scratch_deleted = sum(cleanup_scratch(row["scratch_key"], trigger="startup_orphan") for row in scratch)
    recovered_builds = 0
    now = datetime.now(timezone.utc)
    for row in db.list_building():
        slot = db.slot(row["project_id"], row["group_id"])
        try:
            final = materializer.bundle_path(row["project_id"], row["bundle_id"])
            _safe_remove(final)
            stage = final.parent / ("." + row["bundle_id"] + ".building")
            _safe_remove(stage)
            db.failed(row["bundle_id"], slot["owner_id"] if slot else "", "orphan_build_recovered",
                      "interrupted Bundle build recovered at startup")
            recovered_builds += 1
        except OSError as exc:
            db.bundle_cleanup_warning(row["bundle_id"], f"startup_orphan: {type(exc).__name__}")
    for row in db.list_created():
        final = materializer.bundle_path(row["project_id"], row["bundle_id"])
        if not final.exists():
            db.bundle_cleanup_success(row["bundle_id"])
            db.deleted(row["bundle_id"])
        stage = final.parent / ("." + row["bundle_id"] + ".building")
        if stage.exists() and time.time() - stage.stat().st_mtime > _ORPHAN_GRACE_SECONDS:
            try:
                _safe_remove(stage)
            except OSError as exc:
                db.bundle_cleanup_warning(row["bundle_id"], f"startup_stage: {type(exc).__name__}")
    # Recover interrupted Scratch staging directories that were never published
    # or registered in the Scratch ledger.
    staging_removed = 0
    parents = {materializer.bundle_path(row["project_id"], row["bundle_id"]).parent
               for row in db.list_created() + db.list_building()}
    for parent in parents:
        scratch_parent = parent / "scratch"
        if not scratch_parent.is_dir() or materializer._linked(scratch_parent.lstat()):
            continue
        for child in scratch_parent.iterdir():
            staging = bool(re.fullmatch(r"\.[0-9a-f]{64}\.(?:lock|[0-9]+)", child.name))
            orphan_final = bool(re.fullmatch(r"[0-9a-f]{64}", child.name)) and (
                (record := db.scratch_get(child.name)) is None or record["status"] == "deleted")
            if not staging and not orphan_final:
                continue
            try:
                if time.time() - child.stat().st_mtime > _ORPHAN_GRACE_SECONDS:
                    _safe_remove(child)
                    staging_removed += 1
            except OSError:
                logger.warning("Source Bundle Scratch orphan staging cleanup failed", exc_info=True)
    return {"scratch_deleted": scratch_deleted, "builds_recovered": recovered_builds,
            "staging_removed": staging_removed}


def startup() -> None:
    global _started
    with _guard:
        if _started:
            return
        _started = True
        _stop.clear()
    cleanup_orphans()
    sweep_expired()

    def loop():
        while not _stop.wait(_SWEEP_SECONDS):
            try:
                sweep_expired()
            except Exception:
                logger.warning("Source Bundle TTL sweep failed", exc_info=True)

    threading.Thread(target=loop, name="source-bundle-cleanup", daemon=True).start()


def shutdown() -> None:
    global _started
    _stop.set()
    with _guard:
        _started = False
