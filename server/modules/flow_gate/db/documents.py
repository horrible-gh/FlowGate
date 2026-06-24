"""Document CRUD."""
from __future__ import annotations
import json
import os
import re
from datetime import datetime
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_id(doc_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM documents WHERE doc_id = ?", [doc_id]
    )


def get_by_rowid(row_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM documents WHERE id = ?", [row_id]
    )


def list_documents(
    project_id: str,
    module: str | None = None,
    group_id: str | None = None,
    type_code: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    store = get_store()
    sql = "SELECT * FROM documents WHERE project_id = ?"
    params: list = [project_id]
    if module is not None:
        sql += " AND module = ?"
        params.append(module)
    if group_id is not None:
        sql += " AND group_id = ?"
        params.append(group_id)
    if type_code is not None:
        sql += " AND type_code = ?"
        params.append(type_code)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return store._fetch_all(sql, params)


def find_by_content_fingerprint(
    content_sha256: str, exclude_group_id: Optional[str] = None
) -> Optional[dict]:
    """Return one document whose body fingerprint matches, optionally excluding a group.

    Backs the inbox duplicate-body guard (B0106 / NR0003): a substantial body
    byte-identical to an existing document in a *different* group is the
    submission-layer contamination signature (correct title, stale/reused body).

    The fingerprint is stored inside the meta JSON ("content_sha256") rather than a
    dedicated column, so this matches a stable substring with LIKE — no schema
    migration and dialect-portable (the same literal lands in sqlite/mysql/postgres).
    Returns the earliest-created match so the guard's error names the original.
    """
    if not content_sha256:
        return None
    needle = f'"content_sha256": "{content_sha256}"'
    sql = "SELECT * FROM documents WHERE meta LIKE ?"
    params: list = [f"%{needle}%"]
    if exclude_group_id is not None:
        sql += " AND group_id != ?"
        params.append(exclude_group_id)
    sql += " ORDER BY created_at ASC LIMIT 1"
    return get_store()._fetch_one(sql, params)


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    cols = [
        "doc_id", "project_id", "branch", "module", "group_id", "sub_group_id",
        "type_code", "seq", "title", "file_path", "filename", "status", "owner_id",
        "priority", "due_date", "direction", "review_required",
        "tv_type", "pass_criteria", "worker_tier",
        "target_id", "triggered_by", "superseded_by",
        "previous_tv", "previous_t", "previous_ds",
        "created_at", "meta", "updated_at",
    ]
    fp = data.get("file_path")
    derived_filename = data.get("filename") or (os.path.basename(fp) if fp else None)
    vals = [
        data["doc_id"], data["project_id"], data.get("branch", "main"), data.get("module", "none"),
        data.get("group_id"), data.get("sub_group_id"),
        data["type_code"], data["seq"], data["title"],
        fp, derived_filename, data.get("status", "draft"), data.get("owner_id"),
        data.get("priority"), data.get("due_date"), data.get("direction"),
        data.get("review_required", 0),
        data.get("tv_type"), data.get("pass_criteria", "all"), data.get("worker_tier"),
        data.get("target_id"), data.get("triggered_by"), data.get("superseded_by"),
        data.get("previous_tv"), data.get("previous_t"), data.get("previous_ds"),
        data.get("created_at", now), data.get("meta"), data.get("updated_at", now),
    ]
    placeholders = ", ".join(["?"] * len(cols))
    store._execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    return get_by_id(data["doc_id"])  # type: ignore[return-value]


def update(doc_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("doc_id", "id", "created_at")}
    # auto-derive filename from file_path if file_path is updated and filename not explicitly set
    if "file_path" in updates and "filename" not in updates:
        fp = updates["file_path"]
        updates["filename"] = os.path.basename(fp) if fp else None
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE documents SET {set_clause} WHERE doc_id = ?",
        [*updates.values(), doc_id],
    )
    return get_by_id(doc_id)


def delete(doc_id: str) -> None:
    store = get_store()
    doc = get_by_id(doc_id)
    # Wrap in a transaction so the SQLite backend has FK enforcement ON for the
    # DELETE (connection.transaction() enables `PRAGMA foreign_keys = ON`). Outside a
    # transaction the live sqlite backend leaves FKs OFF, so declared ON DELETE
    # SET NULL / CASCADE actions would not fire and could orphan dependent rows
    # (NR0122 §5 / B0001 rec #3). transaction() is reentrant, so callers that already
    # opened a transaction (e.g. document_service.delete_document) pass straight
    # through without nesting.
    with store.transaction():
        if doc is not None:
            # Preserve workflow history while releasing the FK to documents.id. This
            # mirrors document_service.delete_document and protects internal cleanup
            # paths that intentionally call the low-level delete helper.
            try:
                store._execute(
                    "UPDATE workflow_events SET document_id = NULL WHERE document_id = ?",
                    [doc["id"]],
                )
            except Exception:
                pass
        store._execute("DELETE FROM documents WHERE doc_id = ?", [doc_id])


def fetch_recent_group_docs(
    group_id: str,
    before_seq: int,
    limit: int = 5,
) -> list[dict]:
    """Retrieve up to limit records in the group with seq less than or equal to the reference value (seq DESC).

    Returned items: {"doc_id": <canonical>, "doc_type": str, "seq": int, "title": str, "status": str}
    """
    if not group_id or not before_seq:
        return []
    rows = get_store()._fetch_all(
        "SELECT doc_id, type_code, seq, title, status FROM documents "
        "WHERE group_id = ? AND seq <= ? ORDER BY seq DESC LIMIT ?",
        [group_id, before_seq, limit],
    )
    return [
        {
            "doc_id": r["doc_id"],
            "doc_type": r.get("type_code", ""),
            "seq": r.get("seq"),
            "title": r.get("title", ""),
            "status": r.get("status", ""),
        }
        for r in rows
    ]


def get_group_max_seq(group_id: str) -> int:
    """Return the highest documents.seq in the group (0 when the group is empty).

    Used to anchor the "5 most recent documents in the group" mention section at
    the group's latest document rather than at the workflow-owning parent (whose
    seq is the lowest in the group), so docs created after the parent (e.g. a memo)
    are not silently excluded from the recent-docs context.
    """
    if not group_id:
        return 0
    row = get_store()._fetch_one(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM documents WHERE group_id = ?",
        [group_id],
    )
    return row.get("max_seq", 0) if row else 0


# ─────────────────────────────────────────────────────────────────────────────
# Legacy compatibility API (migrated from store.py, phase 'documents').
# Ports the store.FlowGateStore document methods with identical SQL and return shapes.
# Uses the `documents` table. _normalize_document_row fills compatibility keys
# (type/project/owner and next_action/next) exactly as store.py did.
# ─────────────────────────────────────────────────────────────────────────────

_VALID_STATUS_TRANSITIONS: dict[str, set] = {
    "open": {"closed", "rejected"},
    "accepted": {"closed", "cancelled"},
}


def _normalize_document_row(row) -> Optional[dict]:
    if row is None:
        return None
    doc = dict(row)
    if "type" not in doc and "type_code" in doc:
        doc["type"] = doc["type_code"]
    if "project" not in doc and "project_id" in doc:
        doc["project"] = doc["project_id"]
    if "owner" not in doc and "owner_id" in doc:
        doc["owner"] = doc["owner_id"]
    if doc.get("next_action") is None and doc.get("meta"):
        try:
            meta = json.loads(doc["meta"])
        except Exception:
            meta = None
        if isinstance(meta, dict) and meta.get("next_action") is not None:
            doc["next_action"] = meta["next_action"]
    if doc.get("next") is None and doc.get("next_action") is not None:
        doc["next"] = doc["next_action"]
    return doc


def _read_target_id_from_memo_file(memo_file: str) -> Optional[str]:
    """Extract target_id from a processed memo file."""
    if not memo_file:
        return None
    from modules.flow_gate.storage.paths import get_storage_root
    storage_root = str(get_storage_root())
    memo_path = os.path.join(storage_root, "processed", memo_file)
    if not os.path.exists(memo_path):
        return None
    try:
        with open(memo_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.strip()
        if not content.startswith("---"):
            return None
        end_idx = content.find("---", 3)
        if end_idx == -1:
            return None
        yaml_block = content[3:end_idx].strip()
        for line in yaml_block.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^target_id\s*:\s*(.+)$", line)
            if match:
                return match.group(1).strip()
        return None
    except Exception:
        return None


def get_document_by_id(doc_id: str) -> Optional[dict]:
    return _normalize_document_row(
        get_store()._fetch_one("SELECT * FROM documents WHERE doc_id = ?", [doc_id])
    )


def get_document_by_pk(pk: int) -> Optional[dict]:
    return _normalize_document_row(
        get_store()._fetch_one("SELECT * FROM documents WHERE id = ?", [pk])
    )


def get_documents_by_group_id(
    group_id: str, exclude_types: tuple = None, exclude_statuses: tuple = None
) -> list[dict]:
    clauses = ["group_id = ?"]
    params: list = [group_id]
    if exclude_types:
        placeholders = ",".join(["?"] * len(exclude_types))
        clauses.append(f"type_code NOT IN ({placeholders})")
        params.extend(exclude_types)
    if exclude_statuses:
        placeholders = ",".join(["?"] * len(exclude_statuses))
        clauses.append(f"status NOT IN ({placeholders})")
        params.extend(exclude_statuses)
    rows = get_store()._fetch_all(
        f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY created_at ASC, id ASC",
        params,
    )
    return [_normalize_document_row(r) for r in rows]


def get_documents_by_target_id(
    target_id: str, types: tuple = None, statuses: tuple = None
) -> list[dict]:
    clauses = ["target_id = ?"]
    params: list = [target_id]
    if types:
        placeholders = ",".join(["?"] * len(types))
        clauses.append(f"type_code IN ({placeholders})")
        params.extend(types)
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    rows = get_store()._fetch_all(
        f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY created_at ASC, id ASC",
        params,
    )
    return [_normalize_document_row(r) for r in rows]


def get_all_documents() -> list[dict]:
    rows = get_store()._fetch_all("SELECT * FROM documents ORDER BY id DESC")
    return [_normalize_document_row(r) for r in rows]


def get_documents_filtered(
    project: str = None, doc_type: str = None, status: str = None,
    owner: str = None, priority: str = None, query: str = None,
) -> list[dict]:
    clauses: list = []
    params: list = []
    if project:
        clauses.append("project_id = ?")
        params.append(project)
    if doc_type:
        clauses.append("type_code = ?")
        params.append(doc_type)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if owner:
        clauses.append("owner = ?")
        params.append(owner)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if query:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(doc_id) LIKE ?)")
        q = f"%{query.lower()}%"
        params.extend([q, q])
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return get_store()._fetch_all(
        f"SELECT * FROM documents {where_sql} ORDER BY updated_at DESC, id DESC", params
    )


def search_documents(
    q: str,
    project: str = None,
    doc_type: str = None,
    status: str = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Title/doc_id text search with optional metadata facets (R0001 Phase 1).

    Multi-dialect safe: uses ``LOWER(col) LIKE ?`` which behaves identically on
    SQLite / MySQL / PostgreSQL (no FTS syntax → no dialect.py branching needed).
    Returns ``(rows, total_count)`` where total ignores limit/offset for paging.
    """
    clauses: list = []
    params: list = []
    if project:
        clauses.append("project_id = ?")
        params.append(project)
    if doc_type:
        clauses.append("type_code LIKE ?")
        params.append(f"{doc_type}%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if q:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(doc_id) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    store = get_store()
    total_row = store._fetch_one(
        f"SELECT COUNT(*) AS cnt FROM documents {where_sql}", params
    )
    total = total_row["cnt"] if total_row else 0
    rows = store._fetch_all(
        f"SELECT * FROM documents {where_sql}"
        f" ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return [_normalize_document_row(r) for r in rows], total


def list_documents_for_fulltext(
    project: str = None, doc_type: str = None, status: str = None,
) -> list[dict]:
    """Return facet-filtered documents (no text predicate) for body full-text search.

    R0001 Phase 2: the body markdown lives on the filesystem (no ``content`` column),
    so the content-search service reads each candidate file rather than matching in
    SQL. This helper applies only the cheap metadata facets (project/type/status, the
    same prefix-``type_code LIKE`` semantics as the Phase 1 endpoint) to narrow the
    candidate set the service must read, ordered ``updated_at DESC`` so the service can
    keep that ordering without re-sorting. Multi-dialect safe (plain equality/LIKE).
    """
    clauses: list = []
    params: list = []
    if project:
        clauses.append("project_id = ?")
        params.append(project)
    if doc_type:
        clauses.append("type_code LIKE ?")
        params.append(f"{doc_type}%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = get_store()._fetch_all(
        f"SELECT * FROM documents {where_sql} ORDER BY updated_at DESC, id DESC", params
    )
    return [_normalize_document_row(r) for r in rows]


def get_documents_by_status_and_types(status: str, types: tuple) -> list[dict]:
    if not types:
        return []
    placeholders = ",".join(["?"] * len(types))
    return get_store()._fetch_all(
        f"SELECT * FROM documents WHERE status = ? AND type_code IN ({placeholders})"
        f" ORDER BY updated_at ASC, id ASC",
        [status, *types],
    )


def get_documents_by_status(status: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM documents WHERE status = ? ORDER BY id DESC", [status]
    )


def get_documents_grouped_by_status() -> dict[str, list[dict]]:
    return {
        "open": get_documents_by_status("open"),
        "closed": get_documents_by_status("closed"),
        "rejected": get_documents_by_status("rejected"),
    }


def get_open_documents() -> list[dict]:
    return get_documents_by_status("open")


def get_pending_nr_tr_documents() -> list[dict]:
    open_docs = get_open_documents()
    pending = []
    for doc in open_docs:
        if doc.get("type") not in ("N", "T"):
            continue
        linked = get_documents_by_target_id(doc["doc_id"], types=("NR", "TR"))
        if not linked:
            pending.append(doc)
    return pending


def get_recently_closed_or_rejected_documents(limit: int = 10) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM documents WHERE status IN ('closed', 'rejected')"
        " ORDER BY updated_at DESC, id DESC LIMIT ?",
        [limit],
    )


def get_rejected_documents_with_reasons() -> list[dict]:
    store = get_store()
    docs = store._fetch_all(
        "SELECT * FROM documents WHERE status = 'rejected' ORDER BY updated_at DESC, id DESC"
    )
    result = []
    for doc in docs:
        events = store._fetch_all(
            "SELECT event_id, event_type, memo_file, reason, related_doc_id,"
            " related_target_id, note, created_at"
            " FROM events WHERE doc_id = ? AND (reason = 'rejected' OR (note IS NOT NULL"
            " AND (LOWER(note) LIKE '%rejected%' OR note LIKE '%rejected%')))"
            " ORDER BY event_id DESC",
            [doc["doc_id"]],
        )
        doc["reject_events"] = events
        result.append(doc)
    return result


def get_outbox_documents(group_id: str = None) -> list[dict]:
    OUTBOX_TYPES = ("R", "B", "AC", "RJ")  # group 0022 §7.3: A/V removed (doc types retired)
    placeholders = ",".join(["?"] * len(OUTBOX_TYPES))
    params = list(OUTBOX_TYPES)
    where_extra = ""
    if group_id:
        where_extra = " AND group_id = ?"
        params.append(group_id)
    return get_store()._fetch_all(
        f"SELECT * FROM documents WHERE type_code IN ({placeholders}){where_extra}"
        f" ORDER BY created_at DESC, id DESC",
        params,
    )


def get_inbox_process_documents(group_id: str = None, doc_type: str = None) -> list[dict]:
    INBOX_PROCESS_TYPES = ("AR", "DS", "D", "DB", "P", "L", "DC", "N", "NR",  # group 0022 §7.3: Q removed
                           "T", "TR", "TV", "TVR", "VR", "M", "CH")  # L0044.0008 §2: conversation
    placeholders = ",".join(["?"] * len(INBOX_PROCESS_TYPES))
    params = list(INBOX_PROCESS_TYPES)
    where_extra = ""
    if group_id:
        where_extra += " AND group_id = ?"
        params.append(group_id)
    if doc_type:
        where_extra += " AND type_code = ?"
        params.append(doc_type)
    return get_store()._fetch_all(
        f"SELECT * FROM documents WHERE type_code IN ({placeholders}){where_extra}"
        f" ORDER BY created_at DESC, id DESC",
        params,
    )


def get_next_outbox_seq(group_id: str, doc_type: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) as cnt FROM documents WHERE group_id = ? AND type_code = ?",
        [group_id, doc_type],
    )
    return (row["cnt"] if row else 0) + 1


def insert_document(
    doc_id: str, doc_type: str, project: str, module: str,
    title: str, target_id: str = None, group_id: str = None,
    owner: str = None, priority: str = None, due_date: str = None,
    next_action: str = None, direction: str = None, status: str = "open",
    file_path: str = None,
) -> int:
    """Create a document using D009, derive seq within group_id+type_code, and return lastrowid."""
    now = datetime.now().isoformat()
    meta = json.dumps({"next_action": next_action}) if next_action else None
    filename = os.path.basename(file_path) if file_path else None
    store = get_store()
    with store.transaction() as s:
        if group_id is not None:
            row = s._fetch_one(
                "SELECT COUNT(*) as cnt FROM documents WHERE group_id = ? AND type_code = ?",
                [group_id, doc_type],
            )
        else:
            row = s._fetch_one(
                "SELECT COUNT(*) as cnt FROM documents"
                " WHERE group_id IS NULL AND type_code = ? AND project_id = ?",
                [doc_type, project],
            )
        seq = (row["cnt"] if row else 0) + 1
        s._execute(
            "INSERT INTO documents"
            " (doc_id, type_code, project_id, module, target_id, group_id, owner_id,"
            "  priority, due_date, title, status, direction, meta, seq, file_path,"
            "  filename, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [doc_id, doc_type, project, module, target_id, group_id, owner, priority,
             due_date, title, status, direction, meta, seq, file_path, filename, now, now],
        )
        rid = s._fetch_one("SELECT last_insert_rowid() AS rid")
    return rid["rid"] if rid else None


def update_document_status(doc_id: str, new_status: str) -> bool:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET status = ?, updated_at = ? WHERE doc_id = ?",
        [new_status, now, doc_id],
    )
    return True


def update_document_status_validated(doc_id: str, new_status: str) -> tuple:
    if new_status not in ("closed", "rejected", "cancelled"):
        return False, f"Invalid status: {new_status}"
    doc = get_document_by_id(doc_id)
    if doc is None:
        return False, f"Document not found: {doc_id}"
    current = doc["status"]
    allowed = _VALID_STATUS_TRANSITIONS.get(current)
    if allowed is None or new_status not in allowed:
        return False, f"Cannot transition from '{current}' to '{new_status}'"
    update_document_status(doc_id, new_status)
    return True, f"{doc_id}: {current} → {new_status}"


def update_document_status_by_pk(pk: int, new_status: str) -> None:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET status = ?, updated_at = ? WHERE id = ?",
        [new_status, now, pk],
    )


def update_document_metadata(doc_id: str, owner: str = None, priority: str = None,
                             due_date: str = None) -> bool:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET owner = ?, priority = ?, due_date = ?, updated_at = ? WHERE doc_id = ?",
        [owner, priority, due_date, now, doc_id],
    )
    return True


def update_document_metadata_validated(doc_id: str, owner: str = None,
                                       priority: str = None, due_date: str = None) -> tuple:
    if get_document_by_id(doc_id) is None:
        return False, f"Document not found: {doc_id}"
    update_document_metadata(doc_id, owner, priority, due_date)
    return True, f"{doc_id}: metadata updated"


def update_document_fields(doc_id: str, **fields) -> None:
    allowed = {
        "title", "target_id", "group_id", "owner", "priority", "due_date",
        "next", "direction", "status",
        "tv_type", "pass_criteria", "worker_tier", "author_type", "requested_by",
        "review_required", "superseded_by", "previous_tv", "previous_t",
        "previous_ds", "triggered_by", "seq_num",
    }
    sets: list = []
    params: list = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    now = datetime.now().isoformat()
    sets.append("updated_at = ?")
    params.append(now)
    params.append(doc_id)
    get_store()._execute(
        f"UPDATE documents SET {', '.join(sets)} WHERE doc_id = ?", params
    )


def set_review_required(doc_id: str, flag: bool) -> None:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET review_required = ?, updated_at = ? WHERE doc_id = ?",
        [1 if flag else 0, now, doc_id],
    )


def set_superseded_by(doc_id: str, superseded_by: str) -> None:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET superseded_by = ?, updated_at = ? WHERE doc_id = ?",
        [superseded_by, now, doc_id],
    )


def set_triggered_by(doc_id: str, triggered_by: str) -> None:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE documents SET triggered_by = ?, updated_at = ? WHERE doc_id = ?",
        [triggered_by, now, doc_id],
    )


def get_latest_t_number_in_group(group_id: str) -> int:
    rows = get_store()._fetch_all(
        "SELECT doc_id FROM documents WHERE group_id = ? AND type_code = 'T'", [group_id]
    )
    max_n = 0
    for row in rows:
        m = re.match(r"^.+-T(\d+)$", row["doc_id"])
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                continue
    return max_n


def get_latest_ds_in_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM documents WHERE group_id = ? AND type_code = 'DS'"
        " ORDER BY id DESC LIMIT 1",
        [group_id],
    )


def get_latest_d_in_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM documents WHERE group_id = ? AND type_code = 'D'"
        " ORDER BY id DESC LIMIT 1",
        [group_id],
    )


def get_docs_for_tree_by_group(group_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM documents WHERE group_id = ? ORDER BY doc_id DESC", [group_id]
    )


def get_orphan_docs_for_tree(project_id: str, known_group_ids: list) -> list[dict]:
    store = get_store()
    if known_group_ids:
        placeholders = ",".join(["?"] * len(known_group_ids))
        return store._fetch_all(
            f"SELECT * FROM documents WHERE project_id = ?"
            f" AND (group_id IS NULL OR group_id NOT IN ({placeholders}))"
            f" ORDER BY module, doc_id DESC",
            [project_id, *known_group_ids],
        )
    return store._fetch_all(
        "SELECT * FROM documents WHERE project_id = ? ORDER BY module, doc_id DESC",
        [project_id],
    )


def get_linked_result_documents(target_id: str) -> list[dict]:
    """List NR/TR documents referencing target_id, preferring the DB value and falling back to file parsing."""
    rows = get_store()._fetch_all(
        "SELECT d.doc_id, d.type, d.status, d.title, d.created_at, d.target_id, e.memo_file"
        " FROM documents d"
        " LEFT JOIN events e ON e.doc_id = d.doc_id AND e.event_type = 'created'"
        " WHERE d.type IN ('NR', 'TR')"
        " ORDER BY d.id DESC"
    )
    linked: list[dict] = []
    for row_dict in rows:
        stored_target_id = (row_dict.get("target_id") or "").strip()
        if stored_target_id:
            if stored_target_id != target_id:
                continue
        else:
            memo_target_id = _read_target_id_from_memo_file(row_dict.get("memo_file") or "")
            if memo_target_id != target_id:
                continue
        linked.append({
            "doc_id": row_dict["doc_id"],
            "type": row_dict["type"],
            "status": row_dict["status"],
            "title": row_dict["title"],
            "created_at": row_dict["created_at"],
            "target_id": stored_target_id or target_id,
        })
    return linked


def has_open_result_for_target(target_id: str) -> tuple:
    linked = get_linked_result_documents(target_id)
    open_doc_ids = [d["doc_id"] for d in linked if d.get("status") == "open"]
    return len(open_doc_ids) > 0, open_doc_ids


# ── Numbering (DEPRECATED: non-atomic LIKE max+1; new code uses numbering_service) ────

def get_next_number(project: str, module: str, doc_type: str) -> int:
    prefix = f"{project}-{module}-{doc_type}"
    rows = get_store()._fetch_all(
        "SELECT doc_id FROM documents WHERE doc_id LIKE ?", [f"{prefix}%"]
    )
    if not rows:
        return 1
    max_num = 0
    for row in rows:
        suffix = row["doc_id"][len(prefix):]
        try:
            max_num = max(max_num, int(suffix))
        except ValueError:
            continue
    return max_num + 1


def get_next_doc_id(project: str, module: str, doc_type: str) -> str:
    num = get_next_number(project, module, doc_type)
    return f"{project}-{module}-{doc_type}{num:03d}"
