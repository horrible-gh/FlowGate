"""FlowGate DB package.

Expose FlowGateStore/get_store/now_iso from connection.py.

Compatibility facade that re-exports path constants and CRUD functions so
process_service.py and existing tests continue to work. All data access is
delegated to get_store()-based db submodules. Legacy store.py and db.py were
removed by migration on 2026-06-09.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path

import sqlite3

from . import connection as _conn_mod
from . import projects as _db_projects
from . import events as _db_events
from . import group_events as _db_group_events
from . import documents as _db_documents
from . import groups as _db_groups
from . import tv_scenarios as _db_tv
from .connection import FlowGateStore, get_store, now_iso  # noqa: F401
from ..storage.paths import get_storage_root


# ── Migration bridge for retiring store.py ─────────────────────────────────────
# During migration, keep the legacy shim (_store/store.py) and the new db
# submodules (get_store()/connection.FlowGateStore) on the same database.
# Production is untouched because config.get_db_instance() populates connection.STORE.
# This bridge runs only with TESTING=1 and injects a SQLite backend connected
# directly to DB_PATH, which tests may monkeypatch.
class _SqliteDbAdapter:
    """Minimal single-file SQLite implementation of the _db interface expected by connection.FlowGateStore.

    Implements execute, fetch_one, fetch_all, and begin_transaction.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute(self, sql: str, params=None) -> None:
        conn = self._c()
        try:
            conn.execute(sql, params or [])
            conn.commit()
        finally:
            conn.close()

    def fetch_one(self, sql: str, params=None):
        conn = self._c()
        try:
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def fetch_all(self, sql: str, params=None):
        conn = self._c()
        try:
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
        finally:
            conn.close()

    @contextmanager
    def begin_transaction(self):
        conn = self._c()
        try:
            yield _SqliteTxnAdapter(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class _SqliteTxnAdapter:
    """Cursor wrapper used inside the begin_transaction() context."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cur = None

    def execute(self, sql: str, params=None) -> None:
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        return self._cur.fetchone() if self._cur is not None else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur is not None else []


def _bridge_connection_store() -> None:
    """Inject a current-DB_PATH adapter into connection.STORE when TESTING=1.

    Do nothing in production when TESTING != 1.
    """
    if os.environ.get("TESTING") != "1":
        return
    store = _conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(os.path.normpath(DB_PATH))
    _conn_mod.STORE = store

# ── Path calculation ──────────────────────────────────────────────────────────
# db/__init__.py is at server/modules/flow_gate/db/__init__.py
# 4 levels up → server/
_SERVER_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent.parent)  # repo root

STORAGE_DIR: str = os.path.normpath(str(get_storage_root()))
DB_PATH: str = os.path.normpath(os.path.join(_SERVER_DIR, "flowgate.db"))
INBOX_DIR: str = os.path.join(STORAGE_DIR, "inbox")
PROCESSED_DIR: str = os.path.join(STORAGE_DIR, "processed")
ERROR_DIR: str = os.path.join(STORAGE_DIR, "error")
CONFLICT_DIR: str = os.path.join(STORAGE_DIR, "conflict")
OUTBOX_DIR: str = os.path.join(STORAGE_DIR, "outbox")
ACCEPT_DIR: str = os.path.join(STORAGE_DIR, "accept")
REJECT_DIR: str = os.path.join(STORAGE_DIR, "reject")
CANCELLED_DIR: str = os.path.join(STORAGE_DIR, "cancelled")

TEST_REPORTS_DIR: str = os.environ.get("FLOWGATE_TEST_REPORTS_DIR") or os.path.join(
    _BASE_DIR, "_documents", "FlowGate", "90_test_reports"
)
TEST_REPORTS_ARCHIVE_DIR: str = os.environ.get("FLOWGATE_TEST_REPORTS_ARCHIVE_DIR") or os.path.join(
    _BASE_DIR, "_documents", "FlowGate", "98_archive", "90_test_reports"
)
DESIGN_REOPEN_DIR: str = os.environ.get("FLOWGATE_DESIGN_REOPEN_DIR") or os.path.join(
    _BASE_DIR, "_documents", "FlowGate", "10_requirements"
)

# ── Non-path constants ────────────────────────────────────────────────────────
VALID_STATUS_TRANSITIONS = {
    "open": {"closed", "rejected"},
    "accepted": {"closed", "cancelled"},
}
VALID_GROUP_STATUSES = {"OPEN", "CLOSED", "DISCARDED"}
# group 0022 §7.3: Q/A/V removed (doc types retired). Past Q/A/V docs remain viewable via
# direct document/group lookup; they are no longer routed through inbox/outbox classifiers.
OUTBOX_TYPES = {"R", "B", "AC", "RJ"}
INBOX_PROCESS_TYPES = {"AR", "DS", "D", "DB", "P", "L", "DC", "N", "NR", "T", "TR", "TV", "TVR", "VR", "M", "CH"}

# ── Schema ────────────────────────────────────────────────────────────────────
_SCHEMA_SQL = Path(_SERVER_DIR) / "sql" / "migrations" / "sqlite" / "001_flowgate_schema.sql"


def _reset_legacy_store() -> None:
    """After retiring store.py, only reinject the connection.STORE bridge for tests."""
    _bridge_connection_store()


def _legacy_schema_sql() -> str:
    schema_sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    return schema_sql.replace(
        "'draft','open','in_review','approved','rejected',\n                         'cancelled','closed','archived','answered'",
        "'draft','open','in_review','approved','accepted','rejected',\n                         'cancelled','closed','archived','answered','monitoring','done'",
    )


def _ensure_legacy_schema() -> None:
    conn = sqlite3.connect(os.path.normpath(DB_PATH))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'groups'"
        ).fetchone()
        if row is None and _SCHEMA_SQL.is_file():
            conn.executescript(_legacy_schema_sql())
            conn.commit()
    finally:
        conn.close()


# ── Storage path helpers ──────────────────────────────────────────────────────

def _sub_dir(project_id: str | None, name: str) -> str:
    root = os.path.normpath(str(get_storage_root(project_id)))
    return os.path.join(root, name)


def storage_dir(project_id: str | None = None) -> str:
    return os.path.normpath(str(get_storage_root(project_id)))


def inbox_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "inbox")


def outbox_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "outbox")


def processed_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "processed")


def error_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "error")


def conflict_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "conflict")


def accept_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "accept")


def reject_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "reject")


def cancelled_dir(project_id: str | None = None) -> str:
    return _sub_dir(project_id, "cancelled")


# ── Connection / initialization ───────────────────────────────────────────────

def get_connection():
    """Return the main DB connection (raw sqlite3 on DB_PATH)."""
    conn = sqlite3.connect(os.path.normpath(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the storage directories and the legacy sqlite store."""
    _reset_legacy_store()
    for d in [
        STORAGE_DIR, INBOX_DIR, PROCESSED_DIR, ERROR_DIR,
        CONFLICT_DIR, OUTBOX_DIR, ACCEPT_DIR, REJECT_DIR, CANCELLED_DIR,
    ]:
        os.makedirs(d, exist_ok=True)
    _ensure_legacy_schema()
    _bridge_connection_store()


# ── Numbering ─────────────────────────────────────────────────────────────────

def get_next_number(project: str, module: str, doc_type: str) -> int:
    return _db_documents.get_next_number(project, module, doc_type)


def get_next_doc_id(project: str, module: str, doc_type: str) -> str:
    return _db_documents.get_next_doc_id(project, module, doc_type)


def issue_group_id(project: str, module: str) -> str:
    return _db_groups.issue_group_id(project, module)


# ── Allowed Projects ─────────────────────────────────────────────────────

def add_allowed_project(project: str, module: str = "") -> None:
    _db_projects.add_allowed_project(project, module)


def remove_allowed_project(project: str, module: str = "") -> None:
    _db_projects.remove_allowed_project(project, module)


def get_allowed_projects() -> list:
    return _db_projects.get_allowed_projects()


def get_allowed_project_names() -> set:
    return _db_projects.get_allowed_project_names()


def get_project_modules(project_id: str) -> list:
    return _db_projects.get_project_modules(project_id)


# ── Document CRUD ────────────────────────────────────────────────────────

def insert_document(doc_id, doc_type, project, module, title, **kw):
    return _db_documents.insert_document(doc_id, doc_type, project, module, title, **kw)


def insert_event(doc_id, event_type, **kw):
    return _db_events.insert_event(doc_id, event_type, **kw)


def get_created_memo_file(doc_id):
    return _db_events.get_created_memo_file(doc_id)


def get_recent_events_by_doc_id(doc_id, limit: int = 5):
    return _db_events.get_recent_events_by_doc_id(doc_id, limit)


def get_recent_events(limit: int = 5):
    return _db_events.get_recent_events(limit)


def get_latest_events_map(doc_ids):
    return _db_events.get_latest_events_map(doc_ids)


def get_conflict_events(limit: int = 50):
    return _db_events.get_conflict_events(limit)


def is_file_processed(memo_file):
    return _db_events.is_file_processed(memo_file)


def is_hash_processed(file_hash):
    return _db_events.is_hash_processed(file_hash)


def get_document_by_pk(pk: int):
    return _db_documents.get_document_by_pk(pk)


def update_document_status_by_pk(pk: int, new_status: str) -> None:
    _db_documents.update_document_status_by_pk(pk, new_status)


def get_documents_by_group_id(group_id, exclude_types=None, exclude_statuses=None):
    return _db_documents.get_documents_by_group_id(group_id, exclude_types, exclude_statuses)


def get_document_by_id(doc_id):
    return _db_documents.get_document_by_id(doc_id)


def get_documents_by_target_id(target_id, types=None, statuses=None):
    return _db_documents.get_documents_by_target_id(target_id, types, statuses)


def get_all_documents():
    return _db_documents.get_all_documents()


def get_documents_filtered(
    project=None, doc_type=None, status=None,
    owner=None, priority=None, query=None,
):
    return _db_documents.get_documents_filtered(project, doc_type, status, owner, priority, query)


def get_documents_by_status(status):
    return _db_documents.get_documents_by_status(status)


def get_documents_by_status_and_types(status, types):
    return _db_documents.get_documents_by_status_and_types(status, types)


def get_documents_grouped_by_status():
    return _db_documents.get_documents_grouped_by_status()


def get_open_documents():
    return _db_documents.get_open_documents()


def get_pending_nr_tr_documents():
    return _db_documents.get_pending_nr_tr_documents()


def get_recently_closed_or_rejected_documents(limit: int = 10):
    return _db_documents.get_recently_closed_or_rejected_documents(limit)


def get_rejected_documents_with_reasons():
    return _db_documents.get_rejected_documents_with_reasons()


def get_next_outbox_seq(group_id, doc_type):
    return _db_documents.get_next_outbox_seq(group_id, doc_type)


def update_document_fields(doc_id, **fields):
    return _db_documents.update_document_fields(doc_id, **fields)


def set_review_required(doc_id, flag):
    return _db_documents.set_review_required(doc_id, flag)


def set_superseded_by(doc_id, superseded_by):
    return _db_documents.set_superseded_by(doc_id, superseded_by)


def set_triggered_by(doc_id, triggered_by):
    return _db_documents.set_triggered_by(doc_id, triggered_by)


def get_latest_t_number_in_group(group_id):
    return _db_documents.get_latest_t_number_in_group(group_id)


def get_latest_ds_in_group(group_id):
    return _db_documents.get_latest_ds_in_group(group_id)


def get_latest_d_in_group(group_id):
    return _db_documents.get_latest_d_in_group(group_id)


def get_linked_result_documents(target_id):
    return _db_documents.get_linked_result_documents(target_id)


def has_open_result_for_target(target_id):
    return _db_documents.has_open_result_for_target(target_id)


def update_document_status(doc_id: str, new_status: str) -> tuple[bool, str]:
    """Change the document status. Returns (success, message)."""
    if new_status not in ("closed", "rejected", "cancelled"):
        return False, f"Invalid status: {new_status}"
    doc = _db_documents.get_document_by_id(doc_id)
    if doc is None:
        return False, f"Document not found: {doc_id}"
    current = doc["status"]
    allowed = VALID_STATUS_TRANSITIONS.get(current)
    if allowed is None or new_status not in allowed:
        return False, f"Transition from '{current}' to '{new_status}' not allowed"
    _db_documents.update_document_status(doc_id, new_status)
    return True, f"{doc_id}: {current} → {new_status}"


def update_document_metadata(
    doc_id: str,
    owner,
    priority,
    due_date,
) -> tuple[bool, str]:
    """Update document operational metadata (owner/priority/due_date)."""
    doc = _db_documents.get_document_by_id(doc_id)
    if doc is None:
        return False, f"Document not found: {doc_id}"
    _db_documents.update_document_metadata(doc_id, owner, priority, due_date)
    return True, f"{doc_id}: metadata updated"


# ── TV scenarios / status / clear-scope / chain ────────────────────────────────

def get_tv_scenarios(tv_doc_id):
    return _db_tv.get_tv_scenarios(tv_doc_id)


def insert_tv_scenarios_bulk(tv_doc_id, titles, source="worker"):
    return _db_tv.insert_tv_scenarios_bulk(tv_doc_id, titles, source)


def append_tv_scenario(tv_doc_id, title, source="user"):
    return _db_tv.append_tv_scenario(tv_doc_id, title, source)


def update_tv_scenario_result(tv_doc_id, scenario_idx, result, note=None):
    return _db_tv.update_tv_scenario_result(tv_doc_id, scenario_idx, result, note)


def update_tv_scenario_hold_to_skip(tv_doc_id, scenario_idx, reason):
    return _db_tv.update_tv_scenario_hold_to_skip(tv_doc_id, scenario_idx, reason)


def disable_tv_scenario(tv_doc_id, scenario_idx, reason):
    return _db_tv.disable_tv_scenario(tv_doc_id, scenario_idx, reason)


def insert_tv_status(tv_doc_id, tv_status="Open", progress_done=0, progress_total=0):
    return _db_tv.insert_tv_status(tv_doc_id, tv_status, progress_done, progress_total)


def get_tv_status(tv_doc_id):
    return _db_tv.get_tv_status(tv_doc_id)


def update_tv_status(tv_doc_id, tv_status, progress_done=None, progress_total=None):
    return _db_tv.update_tv_status(tv_doc_id, tv_status, progress_done, progress_total)


def insert_tv_clear_scope(tv_doc_id, clear_db=1, clear_fs=1, clear_cache=0, clear_logs=0):
    return _db_tv.insert_tv_clear_scope(tv_doc_id, clear_db, clear_fs, clear_cache, clear_logs)


def get_tv_clear_scope(tv_doc_id):
    return _db_tv.get_tv_clear_scope(tv_doc_id)


def update_tv_clear_scope(tv_doc_id, clear_db=None, clear_fs=None, clear_cache=None, clear_logs=None):
    return _db_tv.update_tv_clear_scope(tv_doc_id, clear_db, clear_fs, clear_cache, clear_logs)


def get_active_tv_for_t(t_doc_id):
    return _db_tv.get_active_tv_for_t(t_doc_id)


def get_active_tvs_by_statuses(statuses):
    return _db_tv.get_active_tvs_by_statuses(statuses)


def get_tv_tvr_chain(t_doc_id):
    return _db_tv.get_tv_tvr_chain(t_doc_id)


def get_running_tv_in_env(project, module=None, env=None):
    return _db_tv.get_running_tv_in_env(project, module, env)


def get_previous_active_tv(target_id, exclude_tv_doc_id):
    return _db_tv.get_previous_active_tv(target_id, exclude_tv_doc_id)


# ── Event lookup ──────────────────────────────────────────────────────────────

def get_events_by_doc_id(doc_id):
    return _db_events.get_events_by_doc_id(doc_id)


# ── Group-level event lookup (group_events table, migration 048) ───────────────

def insert_group_event(group_id, event_type, reason=None, note=None):
    return _db_group_events.insert_group_event(group_id, event_type, reason=reason, note=note)


def get_group_events(group_id):
    return _db_group_events.get_group_events(group_id)


# ── Project Settings ─────────────────────────────────────────────────────

def get_project_settings():
    return _db_projects.get_project_settings()


def get_project_settings_by_project(project):
    return _db_projects.get_project_settings_by_project(project)


def upsert_project_settings(project, docs_root, project_root) -> None:
    _db_projects.upsert_project_settings(project, docs_root, project_root)


def remove_project_settings(project) -> None:
    _db_projects.remove_project_settings(project)


# ── Groups CRUD ───────────────────────────────────────────────────────────────

def insert_group(group_id, project, module, title, priority=None) -> None:
    _db_groups.insert_group(group_id, project, module, title, priority)


def get_group(group_id):
    return _db_groups.get_group(group_id)


def get_all_groups(status=None):
    return _db_groups.get_all_groups(status)


def get_groups_by_projects(project_ids):
    return _db_groups.get_groups_by_projects(project_ids)


def update_group_status(group_id: str, new_status: str) -> tuple[bool, str]:
    """Change the group status."""
    target_status = (new_status or "").strip().upper()
    if target_status not in VALID_GROUP_STATUSES:
        return False, f"Invalid group status: {new_status}"
    group = _db_groups.get_group(group_id)
    if group is None:
        return False, f"Group not found: {group_id}"
    current_status = (group["status"] or "OPEN").upper()
    if current_status == target_status:
        return False, f"Already in {target_status} status: {group_id}"
    if current_status in {"CLOSED", "DISCARDED"}:
        return False, f"Terminal-state groups cannot be modified further: {group_id} ({current_status})"
    _db_groups.update_group_status(group_id, target_status)
    return True, f"{group_id}: {current_status} → {target_status}"


def close_group(group_id):
    return update_group_status(group_id, "CLOSED")


def update_group_updated_at(group_id: str) -> None:
    _db_groups.update_group_updated_at(group_id)


# ── OutBox/InBox lookup ───────────────────────────────────────────────────────

def get_outbox_documents(group_id=None):
    return _db_documents.get_outbox_documents(group_id)


def get_inbox_process_documents(group_id=None, doc_type=None):
    return _db_documents.get_inbox_process_documents(group_id, doc_type)


def get_docs_for_tree_by_group(group_id: str) -> list:
    """List documents by group_id (for get_group_tree only)."""
    return _db_documents.get_docs_for_tree_by_group(group_id)


def get_orphan_docs_for_tree(project_id: str, known_group_ids: list) -> list:
    """List orphan documents in the project (for get_group_tree only)."""
    return _db_documents.get_orphan_docs_for_tree(project_id, known_group_ids)


# ── TV doc_id helpers (pure logic) ────────────────────────────────────────────

def derive_tv_doc_id(t_doc_id: str) -> str | None:
    """Generate the corresponding TV document ID from a T document ID."""
    m = re.match(r"^(.+\.)(\d+)-T$", t_doc_id)
    if m is None:
        return None
    prefix, num_str = m.group(1), m.group(2)
    num = int(num_str)
    fmt = f"{num:03d}" if num <= 999 else str(num)
    return f"{prefix}{fmt}-TV"


def derive_tvr_doc_id(tv_doc_id: str) -> str | None:
    """Generate the corresponding TVR document ID from a TV document ID."""
    m = re.match(r"^(.+\.)(\d+)-TV$", tv_doc_id)
    if m is None:
        return None
    prefix, num_str = m.group(1), m.group(2)
    num = int(num_str)
    fmt = f"{num:03d}" if num <= 999 else str(num)
    return f"{prefix}{fmt}-TVR"


def get_doc_seq_num(doc_id: str, type_prefix: str) -> int | None:
    """Return the seq numeric part of doc_id."""
    pattern = re.compile(rf"^.+\.(\d+)-{re.escape(type_prefix)}$")
    m = pattern.match(doc_id)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ── Sealed fallback ────────────────────────────────────────────────────────────
# With store.py retired, delegation to legacy _store is no longer allowed.
# Access to attributes absent from explicit wrappers/db submodules now raises
# AttributeError immediately; the old __getattr__ -> _store backdoor is gone.

def __getattr__(name: str):
    raise AttributeError(f"module 'flow_gate.db' has no attribute {name!r}")


__all__ = [
    "FlowGateStore", "get_store", "now_iso",
    # Path constants
    "STORAGE_DIR", "DB_PATH", "INBOX_DIR", "PROCESSED_DIR", "ERROR_DIR",
    "CONFLICT_DIR", "OUTBOX_DIR", "ACCEPT_DIR", "REJECT_DIR", "CANCELLED_DIR",
    "TEST_REPORTS_DIR", "TEST_REPORTS_ARCHIVE_DIR", "DESIGN_REOPEN_DIR",
    # Non-path constants
    "VALID_STATUS_TRANSITIONS", "VALID_GROUP_STATUSES",
    "OUTBOX_TYPES", "INBOX_PROCESS_TYPES",
    # Storage path helpers
    "storage_dir", "inbox_dir", "outbox_dir", "processed_dir", "error_dir",
    "conflict_dir", "accept_dir", "reject_dir", "cancelled_dir",
    # Functions
    "get_connection", "init_db",
    "add_allowed_project", "remove_allowed_project",
    "insert_document", "insert_event", "insert_group",
    "get_document_by_pk", "update_document_status_by_pk",
    "get_documents_by_group_id", "get_next_number", "get_next_doc_id",
    "issue_group_id", "get_project_settings", "upsert_project_settings",
    "remove_project_settings", "get_created_memo_file",
    "get_allowed_projects", "get_allowed_project_names",
    "update_group_updated_at", "get_document_by_id",
    "get_documents_by_target_id", "get_events_by_doc_id",
    "update_document_status", "update_document_metadata",
    "get_all_documents", "get_documents_filtered",
    "get_documents_by_status", "get_all_groups", "get_group",
    "update_group_status", "close_group",
    "get_outbox_documents", "get_inbox_process_documents",
    "get_docs_for_tree_by_group", "get_orphan_docs_for_tree",
    "derive_tv_doc_id", "derive_tvr_doc_id", "get_doc_seq_num",
]
