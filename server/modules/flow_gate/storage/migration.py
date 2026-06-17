"""Storage/DB migration logic.

Move an old-schema DB (`project`, `type`, etc.) and old storage directories
to a new-schema DB (`project_id`, `type_code`, etc.) and new storage directories.

Used by both the router and the CLI. The CLI entry point is
`server/_tools/storage_migrate.py`.

Key functions:
    migrate(...)        — migrate data (DB INSERT + file copy)
    verify(...)         — self-verify migration results (row count, file count, sample hash)
    delete_legacy(...)  — permanently delete old DB/files after verification passes
    run_full(...)       — run all 3 steps together, including skipping deletion on verification failure
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional


COPY_SUBDIRS = (
    "inbox",
    "outbox",
    "processed",
    "accept",
    "reject",
    "cancelled",
    "error",
    "conflict",
)


def _connect(path: str, ro: bool = False) -> sqlite3.Connection:
    if ro:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────
# Data migration
# ─────────────────────────────────────────────────────────────

def _migrate_groups(old: sqlite3.Connection, new: sqlite3.Connection,
                    dry_run: bool) -> dict:
    rows = old.execute("SELECT * FROM groups").fetchall()
    project_ids = {r["project_id"] for r in new.execute(
        "SELECT project_id FROM projects").fetchall()}
    new_cols = _columns(new, "groups")
    inserted = 0
    skipped = 0
    unmatched: list[str] = []
    for r in rows:
        proj = r["project"] if "project" in r.keys() else r["project_id"]
        if proj not in project_ids:
            unmatched.append(f"{r['group_id']} (project={proj})")
            continue
        exists = new.execute(
            "SELECT 1 FROM groups WHERE group_id = ?", (r["group_id"],)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        values = {
            "group_id": r["group_id"],
            "project_id": proj,
            "module": r["module"] if "module" in r.keys() else "none",
            "parent_id": None,
            "title": r["title"],
            "priority": r["priority"] if "priority" in r.keys() else None,
            "status": (r["status"] or "OPEN") if "status" in r.keys() else "OPEN",
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "closed_at": r["closed_at"] if "closed_at" in r.keys() else None,
        }
        if values["status"] == "DISCARDED":
            values["status"] = "CANCELLED"
        cols = [c for c in values.keys() if c in new_cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO groups ({','.join(cols)}) VALUES ({placeholders})"
        if not dry_run:
            new.execute(sql, [values[c] for c in cols])
        inserted += 1
    return {"inserted": inserted, "skipped": skipped, "unmatched": unmatched}


def _migrate_documents(old: sqlite3.Connection, new: sqlite3.Connection,
                       dry_run: bool) -> dict:
    rows = old.execute("SELECT * FROM documents").fetchall()
    project_ids = {r["project_id"] for r in new.execute(
        "SELECT project_id FROM projects").fetchall()}
    new_cols = _columns(new, "documents")
    inserted = 0
    skipped = 0
    unmatched: list[str] = []
    for r in rows:
        keys = set(r.keys())
        proj = r["project"] if "project" in keys else r["project_id"]
        if proj not in project_ids:
            unmatched.append(f"{r['doc_id']} (project={proj})")
            continue
        exists = new.execute(
            "SELECT 1 FROM documents WHERE doc_id = ?", (r["doc_id"],)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        values = {
            "doc_id": r["doc_id"],
            "project_id": proj,
            "module": r["module"] if "module" in keys else "none",
            "group_id": r["group_id"] if "group_id" in keys else None,
            "sub_group_id": None,
            "type_code": r["type"] if "type" in keys else None,
            "seq": r["seq_num"] if "seq_num" in keys else None,
            "title": r["title"],
            "file_path": None,
            "status": r["status"] if "status" in keys else "draft",
            "owner_id": r["owner"] if "owner" in keys else None,
            "priority": r["priority"] if "priority" in keys else None,
            "due_date": r["due_date"] if "due_date" in keys else None,
            "direction": r["direction"] if "direction" in keys else None,
            "review_required": r["review_required"] if "review_required" in keys else 0,
            "tv_type": r["tv_type"] if "tv_type" in keys else None,
            "pass_criteria": r["pass_criteria"] if "pass_criteria" in keys else None,
            "worker_tier": r["worker_tier"] if "worker_tier" in keys else None,
            "target_id": r["target_id"] if "target_id" in keys else None,
            "triggered_by": r["triggered_by"] if "triggered_by" in keys else None,
            "superseded_by": r["superseded_by"] if "superseded_by" in keys else None,
            "previous_tv": r["previous_tv"] if "previous_tv" in keys else None,
            "previous_t": r["previous_t"] if "previous_t" in keys else None,
            "previous_ds": r["previous_ds"] if "previous_ds" in keys else None,
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        cols = [c for c in values.keys() if c in new_cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO documents ({','.join(cols)}) VALUES ({placeholders})"
        if not dry_run:
            new.execute(sql, [values[c] for c in cols])
        inserted += 1
    return {"inserted": inserted, "skipped": skipped, "unmatched": unmatched}


def _migrate_events(old: sqlite3.Connection, new: sqlite3.Connection,
                    dry_run: bool) -> dict:
    new_doc_ids = {r["doc_id"] for r in new.execute(
        "SELECT doc_id FROM documents").fetchall()}
    new_cols = _columns(new, "events")
    rows = old.execute("SELECT * FROM events").fetchall()
    inserted = 0
    skipped = 0
    for r in rows:
        if r["doc_id"] not in new_doc_ids:
            skipped += 1
            continue
        values = {
            "doc_id": r["doc_id"],
            "event_type": r["event_type"],
            "actor_user_id": None,
            "memo_file": r["memo_file"] if "memo_file" in r.keys() else None,
            "file_hash": r["file_hash"] if "file_hash" in r.keys() else None,
            "reason": r["reason"] if "reason" in r.keys() else None,
            "related_doc_id": r["related_doc_id"] if "related_doc_id" in r.keys() else None,
            "related_target_id": r["related_target_id"] if "related_target_id" in r.keys() else None,
            "note": r["note"] if "note" in r.keys() else None,
            "created_at": r["created_at"],
        }
        cols = [c for c in values.keys() if c in new_cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO events ({','.join(cols)}) VALUES ({placeholders})"
        if not dry_run:
            new.execute(sql, [values[c] for c in cols])
        inserted += 1
    return {"inserted": inserted, "skipped": skipped, "unmatched": []}


def _copy_storage(src_root: str, dst_root: str, dry_run: bool) -> dict:
    src = Path(src_root)
    dst = Path(dst_root)
    if not src.exists():
        return {"copied_dirs": 0, "copied_files": 0,
                "error": f"src missing: {src}"}
    copied_dirs = 0
    copied_files = 0
    for sub in COPY_SUBDIRS:
        s = src / sub
        if not s.is_dir():
            continue
        d = dst / sub
        if dry_run:
            copied_files += sum(1 for _ in s.rglob("*") if _.is_file())
            copied_dirs += 1
            continue
        d.mkdir(parents=True, exist_ok=True)
        for item in s.rglob("*"):
            if item.is_file():
                rel = item.relative_to(s)
                target = d / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied_files += 1
        copied_dirs += 1
    return {"copied_dirs": copied_dirs, "copied_files": copied_files}


def migrate(src: str, dst: str, db_old: str, db_new: str,
            dry_run: bool = False) -> dict:
    """Migrate old DB/storage → new DB/storage. Return the result dict."""
    if not os.path.exists(db_old):
        return {"ok": False, "error": f"Legacy DB not found: {db_old}"}
    if not os.path.exists(db_new):
        return {"ok": False, "error": f"New DB not found: {db_new}"}

    old = _connect(db_old, ro=True)
    new = _connect(db_new, ro=False)
    try:
        if not dry_run:
            new.execute("BEGIN")
        try:
            g = _migrate_groups(old, new, dry_run)
            d = _migrate_documents(old, new, dry_run)
            e = _migrate_events(old, new, dry_run)
            if not dry_run:
                new.commit()
        except Exception:
            if not dry_run:
                new.rollback()
            raise
        f = _copy_storage(src, dst, dry_run)
    finally:
        old.close()
        new.close()

    return {
        "ok": "error" not in f,
        "dry_run": dry_run,
        "groups": g,
        "documents": d,
        "events": e,
        "files": f,
    }


# ─────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────

def verify(src: str, dst: str, db_old: str, db_new: str,
           migrate_result: Optional[dict] = None) -> dict:
    """Self-verify migration results. Only ok=True is a safe signal to delete legacy data."""
    checks: list[dict] = []
    failures: list[str] = []

    if not os.path.exists(db_old):
        return {"ok": False, "checks": [], "failures": [f"Legacy DB not found: {db_old}"]}
    if not os.path.exists(db_new):
        return {"ok": False, "checks": [], "failures": [f"New DB not found: {db_new}"]}

    old = _connect(db_old, ro=True)
    new = _connect(db_new, ro=True)
    try:
        # Compare row counts (excluding unmatched)
        for table, mr_key in (("groups", "groups"),
                              ("documents", "documents"),
                              ("events", "events")):
            try:
                old_n = old.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                old_n = 0
            new_n = new.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            unmatched = 0
            if migrate_result and mr_key in migrate_result:
                unmatched = len(migrate_result[mr_key].get("unmatched", []))
            expected_min = old_n - unmatched
            ok = new_n >= expected_min
            checks.append({
                "name": f"row_count[{table}]",
                "old": old_n, "new": new_n, "unmatched": unmatched,
                "expected_min": expected_min, "ok": ok,
            })
            if not ok:
                failures.append(
                    f"{table}: new={new_n} < expected_min={expected_min}")
    finally:
        old.close()
        new.close()

    # Verify file hashes
    src_p = Path(src)
    dst_p = Path(dst)
    file_total = 0
    file_match = 0
    sample_failures: list[str] = []
    for sub in COPY_SUBDIRS:
        s = src_p / sub
        if not s.is_dir():
            continue
        for item in s.rglob("*"):
            if not item.is_file():
                continue
            file_total += 1
            rel = item.relative_to(s)
            target = dst_p / sub / rel
            if not target.exists():
                sample_failures.append(f"missing: {sub}/{rel}")
                continue
            try:
                if _file_sha256(item) == _file_sha256(target):
                    file_match += 1
                else:
                    sample_failures.append(f"hash mismatch: {sub}/{rel}")
            except OSError as exc:
                sample_failures.append(f"read err {sub}/{rel}: {exc}")
    files_ok = (file_total == 0 and not sample_failures) or (
        file_match == file_total and not sample_failures)
    checks.append({
        "name": "files",
        "total": file_total, "matched": file_match,
        "failures": sample_failures[:10], "ok": files_ok,
    })
    if not files_ok:
        failures.extend(sample_failures[:10])

    ok = not failures
    return {"ok": ok, "checks": checks, "failures": failures}


# ─────────────────────────────────────────────────────────────
# Permanently delete old data
# ─────────────────────────────────────────────────────────────

def delete_legacy(src: str, db_old: str,
                  protect_paths: Optional[list[str]] = None) -> dict:
    """Call after verification passes. Permanently delete the old DB and old storage
    subdirectories (COPY_SUBDIRS).

    Never touch paths listed in `protect_paths` (to prevent mistakes).
    """
    protect = {os.path.normpath(p) for p in (protect_paths or [])}
    deleted_files = 0
    deleted_dirs = 0
    removed_db = None
    errors: list[str] = []

    src_p = Path(src)
    for sub in COPY_SUBDIRS:
        s = src_p / sub
        if not s.is_dir():
            continue
        if os.path.normpath(str(s)) in protect:
            continue
        for item in s.rglob("*"):
            if item.is_file():
                try:
                    item.unlink()
                    deleted_files += 1
                except OSError as exc:
                    errors.append(f"{item}: {exc}")
        try:
            shutil.rmtree(s)
            deleted_dirs += 1
        except OSError as exc:
            errors.append(f"{s}: {exc}")

    if os.path.exists(db_old) and os.path.normpath(db_old) not in protect:
        try:
            os.remove(db_old)
            removed_db = db_old
        except OSError as exc:
            errors.append(f"{db_old}: {exc}")

    return {
        "ok": not errors,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "removed_db": removed_db,
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────
# Full flow
# ─────────────────────────────────────────────────────────────

def run_full(src: str, dst: str, db_old: str, db_new: str,
             delete_legacy_after: bool = True) -> dict:
    """Run migration → verification → legacy deletion on verification success in one pass.

    Skip legacy deletion when verification fails. The result dict includes details for each stage.
    """
    # When the paths are identical (src==dst), file copy is a no-op.
    # Legacy deletion must be skipped because the new data is there.
    same_root = os.path.normpath(src) == os.path.normpath(dst)

    m = migrate(src, dst, db_old, db_new, dry_run=False)
    if not m.get("ok"):
        return {"ok": False, "stage": "migrate", "migrate": m}

    v = verify(src, dst, db_old, db_new, migrate_result=m)
    result: dict[str, Any] = {
        "ok": v["ok"],
        "stage": "verify",
        "migrate": m,
        "verify": v,
    }

    if v["ok"] and delete_legacy_after and not same_root:
        d = delete_legacy(src, db_old, protect_paths=[dst, db_new])
        result["delete"] = d
        result["stage"] = "delete"
        result["ok"] = d["ok"]
    elif v["ok"] and same_root:
        result["delete"] = {
            "ok": True, "deleted_files": 0, "deleted_dirs": 0,
            "removed_db": None, "errors": [],
            "note": "src==dst: file deletion skipped (legacy DB preserved)",
        }

    return result


# ─────────────────────────────────────────────────────────────
# File-only verification (for environments without a legacy DB)
# ─────────────────────────────────────────────────────────────

def _verify_files_only(src: str, dst: str) -> dict:
    """Verify file hashes only, without a DB. ok=True means every file in src was copied correctly to dst."""
    src_p = Path(src)
    dst_p = Path(dst)
    file_total = 0
    file_match = 0
    sample_failures: list[str] = []
    for sub in COPY_SUBDIRS:
        s = src_p / sub
        if not s.is_dir():
            continue
        for item in s.rglob("*"):
            if not item.is_file():
                continue
            file_total += 1
            rel = item.relative_to(s)
            target = dst_p / sub / rel
            if not target.exists():
                sample_failures.append(f"missing: {sub}/{rel}")
                continue
            try:
                if _file_sha256(item) == _file_sha256(target):
                    file_match += 1
                else:
                    sample_failures.append(f"hash mismatch: {sub}/{rel}")
            except OSError as exc:
                sample_failures.append(f"read err {sub}/{rel}: {exc}")
    files_ok = (file_total == 0 and not sample_failures) or (
        file_match == file_total and not sample_failures)
    checks = [{
        "name": "files",
        "total": file_total, "matched": file_match,
        "failures": sample_failures[:10], "ok": files_ok,
    }]
    failures = sample_failures[:10] if not files_ok else []
    return {"ok": files_ok, "checks": checks, "failures": failures}


def _cleanup_dst(dst: str) -> None:
    """Clean up partially copied target directories on verification failure."""
    dst_p = Path(dst)
    for sub in COPY_SUBDIRS:
        d = dst_p / sub
        if d.is_dir():
            try:
                shutil.rmtree(d)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────
# Project settings change trigger (called from the router)
# ─────────────────────────────────────────────────────────────

def _server_dir() -> Path:
    """server/ directory. Uses the same location calculation as paths.py."""
    return Path(__file__).resolve().parent.parent.parent.parent


def apply_storage_change(project_id: str, new_root: str) -> dict:
    """Change project-settings storage_root_override to new_root, then migrate old
    data to the new location → verify → permanently delete old data.

    Old root: the current project_settings.storage_root_override or the default
    (`<server>/storage`).
    If a legacy DB (`<old_root>/flow_gate.db`) exists, migrate both DB + files.
    If there is no legacy DB, copy files only → verify → delete the source.
    Update DB storage paths only after verification succeeds.
    On verification failure, clean the target path and preserve legacy data.
    """
    from modules.flow_gate.db import projects as _proj

    new_root_p = Path(new_root).resolve()
    new_root_p.mkdir(parents=True, exist_ok=True)

    cur = _proj.get_settings(project_id) or {}
    old_root_str = (cur.get("storage_root_override") or "").strip()
    if not old_root_str:
        old_root_str = str(_server_dir() / "storage")

    db_new = str(_server_dir() / "flowgate.db")
    db_old = str(Path(old_root_str) / "flow_gate.db")

    if Path(old_root_str).resolve() == new_root_p:
        # No-op: same path
        if cur.get("storage_root_override") != str(new_root_p):
            _proj.upsert_settings(
                project_id, {**cur, "storage_root_override": str(new_root_p)}
            )
        return {
            "ok": True,
            "stage": "noop",
            "message": "Same path — migration skipped",
            "old_root": old_root_str,
            "new_root": str(new_root_p),
        }

    if os.path.exists(db_old):
        # Legacy-schema DB exists → migrate both DB + files
        result = run_full(
            old_root_str, str(new_root_p), db_old, db_new,
            delete_legacy_after=True,
        )
    else:
        # Already using the new schema → copy files only → verify → delete the source
        f = _copy_storage(old_root_str, str(new_root_p), dry_run=False)
        if "error" in f:
            return {
                "ok": False,
                "stage": "migrate",
                "migrate": {"files": f},
                "old_root": old_root_str,
                "new_root": str(new_root_p),
            }
        v = _verify_files_only(old_root_str, str(new_root_p))
        result = {
            "ok": v["ok"],
            "stage": "verify",
            "migrate": {"files": f},
            "verify": v,
        }
        if v["ok"]:
            d = delete_legacy(old_root_str, db_old, protect_paths=[str(new_root_p), db_new])
            result["delete"] = d
            result["stage"] = "delete"
            result["ok"] = d["ok"]
        else:
            # Verification failed — clean the target and preserve legacy data
            _cleanup_dst(str(new_root_p))
            result["message"] = "Verification failed — source preserved, target cleaned"

    result["old_root"] = old_root_str
    result["new_root"] = str(new_root_p)

    if result.get("ok"):
        _proj.upsert_settings(
            project_id, {**cur, "storage_root_override": str(new_root_p)}
        )

    return result
