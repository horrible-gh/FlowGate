"""FlowGate DB migration script.

Three steps: backup -> transform -> validate.
Idempotency guaranteed (safe to run again).
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

_SERVER_DIR = Path(__file__).resolve().parents[3]  # server/
_SCHEMA_SQL = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "001_flowgate_schema.sql"


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def backup_db(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_name(f"{db_path.stem}_bak_{ts}{db_path.suffix}")
    shutil.copy2(db_path, bak)
    print(f"[backup] {db_path} → {bak}")
    return bak


def get_existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def apply_schema(conn: sqlite3.Connection) -> None:
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)
    print("[schema] Schema applied")


def migrate_legacy_data(
    legacy_conn: sqlite3.Connection, new_conn: sqlite3.Connection
) -> None:
    """Attempt to preserve data from the legacy DB in the new schema."""
    legacy_tables = get_existing_tables(legacy_conn)
    now = now_iso()

    # documents migration
    if "documents" in legacy_tables:
        rows = legacy_conn.execute("SELECT * FROM documents").fetchall()
        cols_info = legacy_conn.execute("PRAGMA table_info(documents)").fetchall()
        cols = [c[1] for c in cols_info]
        migrated = 0
        for row in rows:
            d = dict(zip(cols, row))
            try:
                project_id = d.get("project") or "LEGACY"
                new_conn.execute(
                    "INSERT OR IGNORE INTO projects "
                    "(project_id, project_name, is_active, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    [project_id, project_id, now, now],
                )
                new_conn.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(doc_id, project_id, module, type_code, seq, title, status, "
                    "owner_id, priority, due_date, direction, review_required, "
                    "tv_type, pass_criteria, worker_tier, target_id, triggered_by, "
                    "superseded_by, previous_tv, previous_t, previous_ds, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        d.get("doc_id"), project_id,
                        d.get("module") or "none",
                        d.get("type") or "R",
                        d.get("seq_num") or 0,
                        d.get("title") or "(migration)",
                        d.get("status") or "draft",
                        d.get("owner"), d.get("priority"), d.get("due_date"),
                        d.get("direction"), d.get("review_required") or 0,
                        d.get("tv_type"), d.get("pass_criteria") or "all",
                        d.get("worker_tier"), d.get("target_id"),
                        d.get("triggered_by"), d.get("superseded_by"),
                        d.get("previous_tv"), d.get("previous_t"), d.get("previous_ds"),
                        d.get("created_at") or now, d.get("updated_at") or now,
                    ],
                )
                migrated += 1
            except Exception as e:
                print(f"[warn] documents migration failed: {e}", file=sys.stderr)
        print(f"[migrate] documents: {migrated}/{len(rows)} rows migrated")

    # events migration
    if "events" in legacy_tables:
        rows = legacy_conn.execute("SELECT * FROM events").fetchall()
        cols_info = legacy_conn.execute("PRAGMA table_info(events)").fetchall()
        cols = [c[1] for c in cols_info]
        migrated = 0
        for row in rows:
            d = dict(zip(cols, row))
            try:
                new_conn.execute(
                    "INSERT OR IGNORE INTO events "
                    "(doc_id, event_type, memo_file, file_hash, reason, "
                    "related_doc_id, related_target_id, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        d.get("doc_id"), d.get("event_type") or "legacy",
                        d.get("memo_file"), d.get("file_hash"), d.get("reason"),
                        d.get("related_doc_id"), d.get("related_target_id"),
                        d.get("note"), d.get("created_at") or now,
                    ],
                )
                migrated += 1
            except Exception as e:
                print(f"[warn] events migration failed: {e}", file=sys.stderr)
        print(f"[migrate] events: {migrated}/{len(rows)} rows migrated")

    # tv_scenarios migration
    if "tv_scenarios" in legacy_tables:
        rows = legacy_conn.execute("SELECT * FROM tv_scenarios").fetchall()
        cols_info = legacy_conn.execute("PRAGMA table_info(tv_scenarios)").fetchall()
        cols = [c[1] for c in cols_info]
        migrated = 0
        for row in rows:
            d = dict(zip(cols, row))
            try:
                new_conn.execute(
                    "INSERT OR IGNORE INTO tv_scenarios "
                    "(tv_doc_id, scenario_idx, source, title, result, note, "
                    "disabled, disabled_reason, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        d.get("tv_doc_id"), d.get("scenario_idx") or 0,
                        d.get("source") or "worker",
                        d.get("title") or "(migration)",
                        d.get("result"), d.get("note"),
                        d.get("disabled") or 0, d.get("disabled_reason"),
                        d.get("updated_at") or now,
                    ],
                )
                migrated += 1
            except Exception as e:
                print(f"[warn] tv_scenarios migration failed: {e}", file=sys.stderr)
        print(f"[migrate] tv_scenarios: {migrated}/{len(rows)} rows migrated")

    # groups migration
    if "groups" in legacy_tables:
        rows = legacy_conn.execute("SELECT * FROM groups").fetchall()
        cols_info = legacy_conn.execute("PRAGMA table_info(groups)").fetchall()
        cols = [c[1] for c in cols_info]
        migrated = 0
        for row in rows:
            d = dict(zip(cols, row))
            try:
                project_id = d.get("project") or "LEGACY"
                new_conn.execute(
                    "INSERT OR IGNORE INTO projects "
                    "(project_id, project_name, is_active, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    [project_id, project_id, now, now],
                )
                module = d.get("module") or "none"
                if module == "":
                    module = "none"
                new_conn.execute(
                    "INSERT OR IGNORE INTO groups "
                    "(group_id, project_id, module, title, priority, status, "
                    "created_at, updated_at, closed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        d.get("group_id"), project_id, module,
                        d.get("title") or "(migration)",
                        d.get("priority"), d.get("status") or "OPEN",
                        d.get("created_at") or now, d.get("updated_at") or now,
                        d.get("closed_at"),
                    ],
                )
                migrated += 1
            except Exception as e:
                print(f"[warn] groups migration failed: {e}", file=sys.stderr)
        print(f"[migrate] groups: {migrated}/{len(rows)} rows migrated")


def verify(conn: sqlite3.Connection) -> bool:
    required_tables = {
        "users", "roles", "permissions", "role_permissions",
        "projects", "project_settings", "groups", "sub_groups",
        "user_project_roles", "documents", "events", "tv_scenarios",
        "document_types", "document_type_templates", "id_counter",
        "token_blacklist", "refresh_tokens", "workflow_events",
        "numbering_jobs", "system_settings",
    }
    existing = get_existing_tables(conn)
    missing = required_tables - existing
    if missing:
        print(f"[verify] ❌ Missing tables: {missing}", file=sys.stderr)
        return False
    role_count = conn.execute(
        "SELECT COUNT(*) FROM roles WHERE is_system=1"
    ).fetchone()[0]
    perm_count = conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
    dt_count = conn.execute(
        "SELECT COUNT(*) FROM document_types WHERE project_id IS NULL"
    ).fetchone()[0]
    print(
        f"[verify] {len(existing)} tables ✓, "
        f"system roles {role_count}, permissions {perm_count}, doc types {dt_count}"
    )
    return True


def run(db_path: str | None = None, legacy_db_path: str | None = None) -> None:
    """
    db_path: path to the new / migration target DB
    legacy_db_path: path to the legacy DB (if missing, only create a new DB)
    """
    if db_path is None:
        server_dir = Path(__file__).resolve().parents[3]
        db_path_obj = server_dir / "storage" / "flow_gate_new.db"
    else:
        db_path_obj = Path(db_path)

    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    # 1. Backup
    backup_db(db_path_obj)

    # 2. Apply the new schema
    new_conn = sqlite3.connect(str(db_path_obj))
    new_conn.execute("PRAGMA foreign_keys = ON")
    new_conn.execute("PRAGMA journal_mode = WAL")
    try:
        apply_schema(new_conn)

        # 3. Migrate legacy data
        if legacy_db_path and Path(legacy_db_path).exists():
            legacy_conn = sqlite3.connect(legacy_db_path)
            try:
                migrate_legacy_data(legacy_conn, new_conn)
            except Exception as e:
                new_conn.rollback()
                print(f"[error] Migration failed, rollback: {e}", file=sys.stderr)
                raise
            finally:
                legacy_conn.close()

        new_conn.commit()

        # 4. Validation
        if not verify(new_conn):
            raise RuntimeError("Verification failed")
        print("[done] Migration complete")
    except Exception:
        new_conn.rollback()
        raise
    finally:
        new_conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FlowGate DB migration")
    parser.add_argument("--db", default=None, help="target DB path")
    parser.add_argument("--legacy", default=None, help="legacy DB path")
    args = parser.parse_args()
    run(args.db, args.legacy)
