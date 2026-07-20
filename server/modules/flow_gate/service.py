"""FlowGate MVP — Preview / Apply / Error business logic."""
from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any
from datetime import date, datetime

from . import db
from . import linter


STALE_OPEN_HOURS = 72
WORKFLOW_GAP_RULES = {
    "stale_open_days": 3,
    "overdue_grace_days": 0,
    "followup_grace_days_from_n": 2,
    "followup_grace_days_from_t": 2,
    "review_stuck_days": 2,
}
PRIORITY_ORDER = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
BUCKET_DIRS = {
    "inbox": db.INBOX_DIR,
    "processed": db.PROCESSED_DIR,
    "error": db.ERROR_DIR,
    "conflict": db.CONFLICT_DIR,
}

# ── Group Numbering/Inheritance Constants ────────────────────────────
_ROOT_TYPES: frozenset[str] = frozenset({"M", "N"})
_CHAIN_MAX_DEPTH: int = 20
_M_FOLLOWUP_TYPES: frozenset[str] = frozenset({"T", "D", "P", "L"})


# ── File I/O Helpers ─────────────────────────────────────────────────

def _safe_filename(filename: str) -> bool:
    """Block path traversal attacks."""
    return ".." not in filename and "/" not in filename and "\\" not in filename


def _move_file(filename: str, src_dir: str, dst_dir: str):
    src = os.path.join(src_dir, filename)
    dst = os.path.join(dst_dir, filename)
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.move(src, dst)


def _resolve_duplicate_filename(filename: str) -> str:
    """Append a suffix like _2, _3 if the same filename exists in processed/accept."""
    name_part, ext = os.path.splitext(filename)
    exists_in_processed = os.path.exists(os.path.join(db.PROCESSED_DIR, filename))
    exists_in_accept = _file_exists_in_accept(filename)
    if not exists_in_processed and not exists_in_accept:
        return filename
    for suffix in range(2, 100):
        candidate = f"{name_part}_{suffix}{ext}"
        if not os.path.exists(os.path.join(db.PROCESSED_DIR, candidate)) and \
           not _file_exists_in_accept(candidate):
            return candidate
    raise ValueError(f"Failed to resolve duplicate filename: {filename}")


def _file_exists_in_accept(filename: str) -> bool:
    """Check whether a filename exists in the accept directory (including subdirectories)."""
    for root, _, files in os.walk(db.ACCEPT_DIR):
        if filename in files:
            return True
    return False


def _move_to_error(filename: str, errors: list[str]):
    """Move a file to error/ and create a failure reason memo."""
    _move_file(filename, db.INBOX_DIR, db.ERROR_DIR)
    error_memo = os.path.join(db.ERROR_DIR, f"_error_{filename}")
    with open(error_memo, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write("reason: lint_failed\n")
        f.write(f"source_file: {filename}\n")
        f.write(f"created_at: {datetime.now().isoformat()}\n")
        f.write("---\n\n")
        f.write(f"# Lint Failed - {filename}\n\n")
        for e in errors:
            f.write(f"- {e}\n")


def _move_to_conflict(filename: str, reason: str, related_target_id: str | None = None):
    """Move a file to conflict/ and create a conflict reason memo."""
    _move_file(filename, db.INBOX_DIR, db.CONFLICT_DIR)
    conflict_memo = os.path.join(db.CONFLICT_DIR, f"_conflict_{filename}")
    with open(conflict_memo, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write("reason: conflict_detected\n")
        f.write(f"source_file: {filename}\n")
        if related_target_id:
            f.write(f"related_target_id: {related_target_id}\n")
        f.write(f"created_at: {datetime.now().isoformat()}\n")
        f.write("---\n\n")
        f.write(f"# Conflict Detected - {filename}\n\n")
        f.write(f"- {reason}\n")


def _bucket_dir(bucket: str) -> str | None:
    return BUCKET_DIRS.get(bucket)


def _safe_doc_id(doc_id: str) -> bool:
    return bool(doc_id) and ".." not in doc_id and "\\" not in doc_id


def _list_bucket_files(bucket: str, include_sidecar: bool = False) -> list[str]:
    folder = _bucket_dir(bucket)
    if not folder or not os.path.exists(folder):
        return []

    files: list[str] = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if not name.endswith(".md"):
            continue
        if not include_sidecar and name.startswith("_"):
            continue
        files.append(name)
    return files


def _read_bucket_file(bucket: str, filename: str) -> str | None:
    folder = _bucket_dir(bucket)
    if not folder or not _safe_filename(filename):
        return None
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_reason_from_sidecar(bucket: str, filename: str) -> dict[str, Any]:
    prefix = "_error_" if bucket == "error" else "_conflict_"
    sidecar = _read_bucket_file(bucket, f"{prefix}{filename}")
    if sidecar is None:
        return {"reason": "", "note": "", "related_target_id": ""}

    header, _ = linter.parse_yaml_header(sidecar)
    note_lines = [
        line.strip()[2:].strip()
        for line in sidecar.splitlines()
        if line.strip().startswith("- ")
    ]
    return {
        "reason": (header or {}).get("reason", ""),
        "note": "\n".join(note_lines),
        "related_target_id": (header or {}).get("related_target_id", ""),
    }


def _split_header_and_body(content: str) -> tuple[dict | None, str]:
    header, _ = linter.parse_yaml_header(content)
    stripped = content.strip()
    if not stripped.startswith("---"):
        return header, stripped

    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        return header, stripped

    body = stripped[end_idx + 3 :].strip()
    return header, body


# ── Inbox Query ──────────────────────────────────────────────────────

def list_inbox_files() -> list[str]:
    """Return the list of unprocessed inbox files (excluding files already applied)."""
    if not os.path.exists(db.INBOX_DIR):
        return []
    return sorted(
        f for f in os.listdir(db.INBOX_DIR)
        if os.path.isfile(os.path.join(db.INBOX_DIR, f))
        and f.endswith(".md")
        and not db.is_file_processed(f)
    )


def read_inbox_file(filename: str) -> str | None:
    if not _safe_filename(filename):
        return None
    path = os.path.join(db.INBOX_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Lint ──────────────────────────────────────────────────────────

def lint_inbox_file(filename: str) -> dict:
    content = read_inbox_file(filename)
    if content is None:
        return {"filename": filename, "valid": False,
                "errors": ["File not found"], "header": None, "content": ""}

    allowed = db.get_allowed_projects()
    header, errors = linter.lint_file_content(content, allowed)
    return {
        "filename": filename,
        "valid": len(errors) == 0,
        "errors": errors,
        "header": header,
        "content": content,
    }


def scan_and_lint_all() -> list[dict]:
    return [lint_inbox_file(f) for f in list_inbox_files()]


def get_reprocess_bucket_view(bucket: str) -> list[dict]:
    """Query reprocess candidates in the error/conflict bucket."""
    if bucket not in ("error", "conflict"):
        return []

    items: list[dict] = []
    for filename in _list_bucket_files(bucket):
        path = os.path.join(_bucket_dir(bucket) or "", filename)
        reason = _extract_reason_from_sidecar(bucket, filename)
        items.append({
            "filename": filename,
            "bucket": bucket,
            "reason": reason.get("reason") or "",
            "note": reason.get("note") or "",
            "related_target_id": reason.get("related_target_id") or "",
            "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat() if os.path.exists(path) else "",
        })
    return items


def requeue_file(bucket: str, filename: str) -> dict:
    """Return an error/conflict file back to inbox."""
    if bucket not in ("error", "conflict"):
        return {"success": False, "message": f"Unsupported bucket: {bucket}"}
    if not _safe_filename(filename):
        return {"success": False, "message": "Invalid filename"}

    src_dir = _bucket_dir(bucket) or ""
    src_path = os.path.join(src_dir, filename)
    if not os.path.exists(src_path):
        return {"success": False, "message": f"File not found: {filename}"}

    _move_file(filename, src_dir, db.INBOX_DIR)
    reason = _extract_reason_from_sidecar(bucket, filename)
    db.insert_event(
        f"memo:{filename}",
        "requeued",
        memo_file=filename,
        reason=reason.get("reason") or None,
        related_target_id=reason.get("related_target_id") or None,
        note=f"{bucket} -> inbox",
    )
    return {"success": True, "bucket": bucket, "filename": filename, "moved_to": "inbox"}


def reprocess_file(bucket: str, filename: str) -> dict:
    """Restore an error/conflict file to inbox and immediately apply it."""
    requeue = requeue_file(bucket, filename)
    if not requeue.get("success"):
        return {"success": False, "step": "requeue", "error": requeue.get("message", "requeue failed")}

    result = apply_file(filename)
    return {
        "success": bool(result.get("success")),
        "step": "apply",
        "bucket": bucket,
        "filename": filename,
        "apply_result": result,
    }


def apply_selected_inbox(filenames: list[str]) -> list[dict]:
    """Sequentially apply only the selected inbox files."""
    results: list[dict] = []
    seen: set[str] = set()
    for filename in filenames:
        clean = (filename or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        results.append(apply_file(clean))
    return results


def get_document_detail(doc_id: str) -> dict | None:
    """Return document details (document/events/links/source)."""
    if not _safe_doc_id(doc_id):
        return None

    doc = db.get_document_by_id(doc_id)
    if doc is None:
        return None

    events = db.get_events_by_doc_id(doc_id)
    linked_docs = db.get_linked_result_documents(doc_id)
    target_id = (doc.get("target_id") or "").strip()
    target_doc = db.get_document_by_id(target_id) if target_id else None

    source_memo = db.get_created_memo_file(doc_id)
    source_content: str | None = None
    source_bucket = ""
    if source_memo:
        doc_type = (doc.get("type") or "").strip()
        if doc_type in ("TV", "TVR"):
            # TV/TVR source files may be in 90_test_reports or the archive.
            for base, label in (
                (db.TEST_REPORTS_DIR, "test_reports"),
                (db.TEST_REPORTS_ARCHIVE_DIR, "test_reports_archive"),
            ):
                candidate = os.path.join(base, source_memo)
                if os.path.exists(candidate):
                    try:
                        with open(candidate, "r", encoding="utf-8") as f:
                            source_content = f.read()
                        source_bucket = label
                        break
                    except OSError:
                        source_content = None
        else:
            source_content = _read_bucket_file("processed", source_memo)
            if source_content is not None:
                source_bucket = "processed"
    source_header, source_body = _split_header_and_body(source_content or "") if source_content else (None, "")

    # ── Group Chain (L001 §4) ─────────────────────────────────────
    group_id = (doc.get("group_id") or "").strip() or None
    chain: dict | None = None
    chain_unavailable_reason: str | None = None
    if group_id:
        chain_docs = db.get_documents_by_group_id(group_id)
        chain_nodes, chain_tree_warnings = build_chain_tree(chain_docs, doc_id)
        gap_warnings = detect_gaps(group_id)
        chain = {
            "group_id": group_id,
            "nodes": chain_nodes,
            "gap_warnings": gap_warnings,
            "chain_warnings": chain_tree_warnings,
        }
    else:
        chain_unavailable_reason = "no group_id"

    # ── TV/TVR-specific Data ──────────────────────────────────────
    tv_extra: dict = {}
    doc_type = (doc.get("type") or "").strip()
    if doc_type == "TV":
        tv_extra["tv_status_row"] = db.get_tv_status(doc_id)
        tv_extra["tv_scenarios"] = db.get_tv_scenarios(doc_id)
        tv_extra["tv_clear_scope"] = db.get_tv_clear_scope(doc_id)
        # TVR link list
        tvr_docs = db.get_documents_by_target_id(doc_id, types=("TVR",))
        tv_extra["tvr_docs"] = list(tvr_docs)
    elif doc_type == "TVR":
        target_tv_id = (doc.get("target_id") or "").strip()
        if target_tv_id:
            tv_extra["target_tv_doc"] = db.get_document_by_id(target_tv_id)
            tv_extra["target_tv_status_row"] = db.get_tv_status(target_tv_id)
            tv_extra["target_tv_scenarios"] = db.get_tv_scenarios(target_tv_id)
        else:
            tv_extra["target_tv_doc"] = None
            tv_extra["target_tv_status_row"] = None
            tv_extra["target_tv_scenarios"] = []

    return {
        "doc": doc,
        "events": events,
        "linked_documents": linked_docs,
        "target_document": target_doc,
        "chain": chain,
        "chain_unavailable_reason": chain_unavailable_reason,
        "source": {
            "bucket": source_bucket,
            "filename": source_memo or "",
            "header": source_header,
            "body": source_body,
            "raw": source_content or "",
        },
        **tv_extra,
    }


def get_memo_detail(bucket: str, filename: str) -> dict | None:
    """Return the original memo content (header/body/related info) in a bucket."""
    if bucket not in BUCKET_DIRS:
        return None
    if not _safe_filename(filename):
        return None

    content = _read_bucket_file(bucket, filename)
    if content is None:
        return None

    header, body = _split_header_and_body(content)
    doc_id = None
    related_events: list[dict] = []
    target_id = (header or {}).get("target_id") if header else None

    if bucket == "processed":
        docs = db.get_all_documents()
        for d in docs:
            if db.get_created_memo_file(d["doc_id"]) == filename:
                doc_id = d["doc_id"]
                related_events = db.get_events_by_doc_id(doc_id)
                break

    reason = _extract_reason_from_sidecar(bucket, filename) if bucket in ("error", "conflict") else {
        "reason": "",
        "note": "",
        "related_target_id": "",
    }

    return {
        "bucket": bucket,
        "filename": filename,
        "header": header,
        "body": body,
        "raw": content,
        "doc_id": doc_id,
        "target_id": target_id,
        "related_events": related_events,
        "reason": reason,
    }


def get_filtered_documents(filters: dict[str, str]) -> dict:
    """Build document filter/search results and option candidates."""
    project = (filters.get("project") or "").strip()
    doc_type = (filters.get("type") or "").strip()
    status = (filters.get("status") or "").strip()
    owner = (filters.get("owner") or "").strip()
    priority = (filters.get("priority") or "").strip().lower()
    query = (filters.get("q") or "").strip()
    view = (filters.get("view") or "").strip()

    owner_missing = owner == "__missing__"
    if owner_missing:
        owner = ""

    docs = db.get_documents_filtered(
        project=project or None,
        doc_type=doc_type or None,
        status=status or None,
        owner=owner or None,
        priority=priority or None,
        query=query or None,
    )

    if owner_missing:
        docs = [d for d in docs if not (d.get("owner") or "").strip()]

    if view == "n_without_t":
        filtered_docs: list[dict] = []
        for d in docs:
            if d.get("type") != "N":
                continue
            followers = db.get_documents_by_target_id(d.get("doc_id", ""), types=("T",))
            if not followers:
                filtered_docs.append(d)
        docs = filtered_docs
    elif view == "high_priority":
        docs = [d for d in docs if (d.get("priority") or "").lower() in ("high", "urgent")]

    all_docs = db.get_all_documents()
    options = {
        "project": sorted({(d.get("project") or "") for d in all_docs if d.get("project")}),
        "type": sorted({(d.get("type") or "") for d in all_docs if d.get("type")}),
        "status": sorted({(d.get("status") or "") for d in all_docs if d.get("status")}),
        "owner": sorted({(d.get("owner") or "") for d in all_docs if d.get("owner")}),
        "priority": sorted({(d.get("priority") or "") for d in all_docs if d.get("priority")}),
    }

    return {
        "filters": {
            "project": project,
            "type": doc_type,
            "status": status,
            "owner": owner,
            "priority": priority,
            "q": query,
            "view": view,
        },
        "documents": docs,
        "count": len(docs),
        "options": options,
    }


def get_quick_filter_views() -> list[dict]:
    """Return frequently-used filter links and their counts."""
    all_docs = db.get_all_documents()
    open_docs = [d for d in all_docs if d.get("status") == "open"]
    high_priority = [d for d in all_docs if (d.get("priority") or "").lower() in ("high", "urgent")]
    unassigned_open = [d for d in open_docs if not (d.get("owner") or "").strip()]

    open_n = [d for d in open_docs if d.get("type") == "N"]
    n_without_t = []
    for doc in open_n:
        linked = db.get_documents_by_target_id(doc.get("doc_id", ""), types=("T",))
        if not linked:
            n_without_t.append(doc)

    return [
        {
            "name": "open",
            "label": "Open Documents",
            "query": "status=open",
            "count": len(open_docs),
        },
        {
            "name": "high_priority",
            "label": "High/Urgent",
            "query": "view=high_priority",
            "count": len(high_priority),
        },
        {
            "name": "owner_missing",
            "label": "No Owner (open)",
            "query": "status=open&owner=__missing__",
            "count": len(unassigned_open),
        },
        {
            "name": "n_without_t",
            "label": "N without follow-up T",
            "query": "status=open&type=N&view=n_without_t",
            "count": len(n_without_t),
        },
    ]


def get_file_browser_view(bucket: str) -> dict:
    """Return a file browser view for processed/error/conflict."""
    if bucket not in ("processed", "error", "conflict"):
        return {"bucket": bucket, "files": [], "total": 0}

    files: list[dict] = []
    for filename in _list_bucket_files(bucket):
        path = os.path.join(_bucket_dir(bucket) or "", filename)
        info = {
            "filename": filename,
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
            "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat() if os.path.exists(path) else "",
        }
        if bucket in ("error", "conflict"):
            info.update(_extract_reason_from_sidecar(bucket, filename))
        files.append(info)

    return {
        "bucket": bucket,
        "files": files,
        "total": len(files),
    }


def _parse_iso_datetime_safe(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def get_operational_stats() -> dict:
    """Return operational summary statistics (open/closed/rejected/conflict/recent/project)."""
    docs = db.get_all_documents()
    now = datetime.now()

    status_counts = {
        "open": sum(1 for d in docs if d.get("status") == "open"),
        "closed": sum(1 for d in docs if d.get("status") == "closed"),
        "rejected": sum(1 for d in docs if d.get("status") == "rejected"),
    }
    status_counts["conflict"] = len(_list_bucket_files("conflict"))

    recent_24h = 0
    for ev in db.get_recent_events(500):
        if ev.get("event_type") != "created":
            continue
        created_at = _parse_iso_datetime_safe(ev.get("created_at"))
        if created_at is None:
            continue
        if (now - created_at).total_seconds() <= 86400:
            recent_24h += 1

    by_project: dict[str, dict] = {}
    for d in docs:
        project = d.get("project") or "(unknown)"
        bucket = by_project.setdefault(
            project,
            {"total": 0, "open": 0, "closed": 0, "rejected": 0},
        )
        bucket["total"] += 1
        status = d.get("status")
        if status in ("open", "closed", "rejected"):
            bucket[status] += 1

    return {
        "status_counts": status_counts,
        "recent_created_24h": recent_24h,
        "projects": by_project,
        "updated_at": now.isoformat(),
    }


def get_conflict_comparison(filename: str) -> dict:
    """Return the comparison basis (target/related document differences) for a conflict file."""
    content = _read_bucket_file("conflict", filename)
    if content is None:
        return {
            "filename": filename,
            "exists": False,
            "reason": {},
            "incoming": None,
            "target_document": None,
            "open_result_documents": [],
            "comparison": {},
        }

    header, body = _split_header_and_body(content)
    reason = _extract_reason_from_sidecar("conflict", filename)
    target_id = (header or {}).get("target_id") or reason.get("related_target_id") or ""
    target_doc = db.get_document_by_id(target_id) if target_id else None
    open_results = db.get_documents_by_target_id(target_id, types=("NR", "TR"), statuses=("open",)) if target_id else []

    comparison = {
        "incoming_type": (header or {}).get("type", ""),
        "incoming_project": (header or {}).get("project", ""),
        "incoming_title": (header or {}).get("title", ""),
        "target_status": (target_doc or {}).get("status", ""),
        "target_title": (target_doc or {}).get("title", ""),
        "open_result_doc_ids": [d.get("doc_id") for d in open_results],
    }

    return {
        "filename": filename,
        "exists": True,
        "reason": reason,
        "incoming": {
            "header": header,
            "body": body,
            "raw": content,
        },
        "target_document": target_doc,
        "open_result_documents": open_results,
        "comparison": comparison,
    }


def build_memo_template(
    doc_type: str,
    project: str,
    title: str,
    module: str = "",
    target_id: str = "",
    owner: str = "",
    priority: str = "",
    due_date: str = "",
    body: str = "",
) -> dict:
    """Build a YAML header draft text for the web compose helper."""
    lines = ["---", f"type: {doc_type}", f"project: {project}"]
    if module.strip():
        lines.append(f"module: {module.strip()}")
    if target_id.strip():
        lines.append(f"target_id: {target_id.strip()}")
    if owner.strip():
        lines.append(f"owner: {owner.strip()}")
    if priority.strip():
        lines.append(f"priority: {priority.strip().lower()}")
    if due_date.strip():
        lines.append(f"due_date: {due_date.strip()}")
    lines.append(f"title: {title}")
    lines.append("---")
    lines.append("")
    lines.append(body or "(Write body here)")

    draft = "\n".join(lines).strip() + "\n"
    header, errors = linter.lint_file_content(draft, db.get_allowed_projects())
    return {
        "draft": draft,
        "header": header,
        "valid": len(errors) == 0,
        "errors": errors,
    }


def save_memo_template_to_inbox(filename: str, content: str) -> dict:
    """Save the compose helper result to inbox."""
    if not _safe_filename(filename):
        return {"success": False, "message": "Invalid filename"}
    if not filename.endswith(".md"):
        return {"success": False, "message": "Only .md files can be saved"}

    os.makedirs(db.INBOX_DIR, exist_ok=True)
    path = os.path.join(db.INBOX_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    lint_result = lint_inbox_file(filename)
    return {
        "success": True,
        "filename": filename,
        "valid": lint_result["valid"],
        "errors": lint_result["errors"],
    }


def envelope(kind: str, data: Any) -> dict:
    """Consistent JSON response wrapper."""
    return {
        "ok": True,
        "kind": kind,
        "generated_at": datetime.now().isoformat(),
        "data": data,
    }


# ── Group Numbering/Inheritance/Detection Logic (L001) ───────────────

def _assign_group_id(
    doc_type: str,
    target_id: str | None,
    project: str,
    module: str | None,
) -> tuple[str | None, list[str]]:
    """Issue or inherit a group_id based on L001 §2-1.

    Returns:
        (group_id, gap_warnings) — group_id may be None
    Raises:
        ValueError: Numbering sequence upper limit exceeded (L001 §5)
    """
    warnings: list[str] = []

    if not target_id:
        if doc_type in _ROOT_TYPES:
            group_id = db.issue_group_id(project, module or "")  # propagate ValueError
            return group_id, warnings
        else:
            warnings.append(f"Not a root type + no target_id — group not assigned ({doc_type})")
            return None, warnings

    parent = db.get_document_by_id(target_id)
    if parent is None:
        warnings.append(f"Parent document not found — {target_id}")
        return None, warnings

    parent_group_id = (parent.get("group_id") or "").strip() or None
    if not parent_group_id:
        warnings.append(f"Parent group_id missing — {target_id}")
        return None, warnings

    return parent_group_id, warnings


def detect_gaps(group_id: str) -> list[str]:
    """Return the list of workflow gap warnings within the group based on L001 §4-2."""
    docs = db.get_documents_by_group_id(group_id)
    warnings: list[str] = []

    # G-01: No TR for T
    for doc in docs:
        if doc.get("type") == "T":
            tr_exists = any(
                d.get("type") == "TR" and d.get("target_id") == doc["doc_id"]
                for d in docs
            )
            if not tr_exists:
                warnings.append(f"TR not registered — no TR for {doc['doc_id']}")

    # G-02: No NR for N
    for doc in docs:
        if doc.get("type") == "N":
            nr_exists = any(
                d.get("type") == "NR" and d.get("target_id") == doc["doc_id"]
                for d in docs
            )
            if not nr_exists:
                warnings.append(f"NR not registered — no NR for {doc['doc_id']}")

    # G-03: No follow-up document for M
    for doc in docs:
        if doc.get("type") == "M":
            followup_exists = any(
                d.get("target_id") == doc["doc_id"] and d.get("type") in _M_FOLLOWUP_TYPES
                for d in docs
            )
            if not followup_exists:
                warnings.append(f"No follow-up document — no follow-up for {doc['doc_id']}")

    # G-04: Only root exists
    if len(docs) == 1:
        warnings.append("No follow-up document — only root document in group")

    return warnings


def build_chain_tree(
    docs: list[dict],
    current_doc_id: str,
) -> tuple[list[dict], list[str]]:
    """Return BFS-ordered chain node list based on L001 §4-3."""
    warnings: list[str] = []
    if not docs:
        return [], warnings

    doc_ids = {d["doc_id"] for d in docs}

    # 1. Find root
    root_candidates = [d for d in docs if not (d.get("target_id") or "").strip()]
    if not root_candidates:
        root_candidates = [
            d for d in docs
            if (d.get("target_id") or "").strip() not in doc_ids
        ]
    if not root_candidates:
        root_candidates = sorted(docs, key=lambda d: d.get("created_at") or "")

    if len(root_candidates) > 1:
        warnings.append("Multiple roots detected")
        root_candidates = sorted(root_candidates, key=lambda d: d.get("created_at") or "")
    root = root_candidates[0]

    # 2. Build children map (created_at ASC)
    children_map: dict[str, list[dict]] = {}
    for doc in docs:
        pid = (doc.get("target_id") or "").strip()
        children_map.setdefault(pid, []).append(doc)
    for children in children_map.values():
        children.sort(key=lambda d: d.get("created_at") or "")

    # 3. BFS traversal
    result: list[dict] = []
    queue = [root]
    depth = 0
    visited: set[str] = set()

    while queue:
        depth += 1
        if depth > _CHAIN_MAX_DEPTH:
            warnings.append("Chain view depth limit exceeded — some nodes omitted")
            break
        next_queue: list[dict] = []
        for node in queue:
            nid = node["doc_id"]
            if nid in visited:
                continue
            visited.add(nid)
            result.append({
                "doc_id": nid,
                "type": node.get("type", ""),
                "parent_id": (node.get("target_id") or "").strip() or None,
                "title": node.get("title", ""),
                "status": node.get("status", ""),
                "is_current": nid == current_doc_id,
            })
            next_queue.extend(children_map.get(nid, []))
        queue = next_queue

    return result, warnings


# ── Preview ───────────────────────────────────────────────────────

def preview_file(filename: str) -> dict:
    result = lint_inbox_file(filename)
    if not result["valid"]:
        return {**result, "preview": None}

    header = result["header"]
    doc_type = header["type"]

    if doc_type == "CONFIRM":
        target = (header.get("target") or "").strip()
        target_exists = bool(target) and os.path.exists(os.path.join(db.INBOX_DIR, target))
        target_preview = lint_inbox_file(target) if target_exists else None
        failure_reasons: list[str] = []
        if not target:
            failure_reasons.append("target field is empty")
        elif not target_exists:
            failure_reasons.append(f"target file is not in inbox: {target}")
        elif target_preview and not target_preview.get("valid"):
            failure_reasons.append("target file failed lint")

        return {
            **result,
            "preview": {
                "action": "CONFIRM",
                "target": target,
                "target_exists": target_exists,
                "target_valid": bool(target_preview and target_preview.get("valid")),
                "target_errors": (target_preview or {}).get("errors", []),
                "target_header": (target_preview or {}).get("header", {}),
                "failure_reasons": failure_reasons,
                "note": "On CONFIRM Apply, the target memo is applied first, then the CONFIRM memo is moved to processed.",
            },
        }

    project = header["project"]
    module = (header.get("module") or "").strip()
    next_id = db.get_next_doc_id(project, module, doc_type)
    preview: dict = {
        "doc_id": next_id,
        "type": doc_type,
        "project": project,
        "module": header.get("module"),
        "group_id": (header.get("group_id") or "").strip() or None,
        "owner": (header.get("owner") or "").strip() or None,
        "priority": (header.get("priority") or "").strip().lower() or None,
        "due_date": (header.get("due_date") or "").strip() or None,
        "title": header["title"],
        "status": "open",
    }

    # If target_id is present, show link info regardless of type
    target_id = (header.get("target_id") or "").strip()
    if target_id:
        target_doc = db.get_document_by_id(target_id) if target_id else None
        preview["target_id"] = target_id
        preview["target_transition"] = (
            f"{target_id}: {target_doc['status']} → closed"
            if target_doc else
            f"{target_id}: document not found (will error on Apply)"
        )

    return {**result, "preview": preview}


# ── Apply ─────────────────────────────────────────────────────────

def apply_file(filename: str, _depth: int = 0) -> dict:
    """Numbering → DB save → inbox → processed move."""
    if _depth > 1:
        return {"success": False, "error": "CONFIRM chain depth exceeded", "filename": filename}

    if not _safe_filename(filename):
        return {"success": False, "error": "Invalid filename", "filename": filename}

    if db.is_file_processed(filename):
        return {"success": False, "error": "File already processed", "filename": filename}

    result = lint_inbox_file(filename)
    if not result["valid"]:
        _move_to_error(filename, result["errors"])
        db.insert_event(
            f"memo:{filename}",
            "lint_failed",
            memo_file=filename,
            reason="lint_failed",
            note="; ".join(result["errors"]),
        )
        return {"success": False, "error": "Lint validation failed",
                "errors": result["errors"], "filename": filename}

    file_hash = hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
    if db.is_hash_processed(file_hash):
        return {
            "success": False,
            "error": "File already processed",
            "filename": filename,
        }

    header = result["header"]
    doc_type = header["type"]

    # ── D/DB/P/L single-item Apply blocked: must be processed via DC ──
    if doc_type in ("D", "DB", "P", "L"):
        return {
            "success": False,
            "error": f"{doc_type} type cannot be applied individually. Submit via DC (design complete).",
            "filename": filename,
        }

    # ── CONFIRM Processing ──
    if doc_type == "CONFIRM":
        target = header.get("target", "")
        if not target:
            _move_to_error(filename, ["CONFIRM memo has no target"])
            db.insert_event(
                f"memo:{filename}",
                "confirm_failed",
                memo_file=filename,
                reason="confirm_target_missing",
                note="CONFIRM memo has no target",
            )
            return {"success": False, "error": "CONFIRM target missing", "filename": filename}

        target_path = os.path.join(db.INBOX_DIR, target)
        if not os.path.exists(target_path):
            _move_to_error(filename, [f"CONFIRM target '{target}' is not in inbox"])
            db.insert_event(
                f"memo:{filename}",
                "confirm_failed",
                memo_file=filename,
                reason="confirm_target_not_found",
                related_doc_id=target,
                note=f"CONFIRM target '{target}' is not in inbox",
            )
            return {"success": False, "error": f"target not found: {target}", "filename": filename}

        target_result = apply_file(target, _depth=_depth + 1)
        _move_file(filename, db.INBOX_DIR, db.PROCESSED_DIR)
        db.insert_event(
            f"memo:{filename}",
            "confirm_processed",
            memo_file=filename,
            reason="confirm_applied",
            related_doc_id=target,
            note=f"target={target}, success={target_result.get('success')}",
        )
        return {"success": target_result["success"],
                "confirm_target": target, "target_result": target_result,
                "filename": filename}

    # ── General Document Processing ──
    project = header["project"]
    module = (header.get("module") or "").strip()
    doc_id = db.get_next_doc_id(project, module, doc_type)
    title = header["title"]
    target_id = (header.get("target_id") or "").strip() or None
    owner = (header.get("owner") or "").strip() or None
    priority = (header.get("priority") or "").strip().lower() or None
    due_date = (header.get("due_date") or "").strip() or None

    # ── group_id: used directly from header ──
    group_id = (header.get("group_id") or "").strip() or None
    assign_warnings: list[str] = []

    # ── NR/TR: transition target document status to closed ──
    if doc_type in linter.NR_TR_TYPES:
        if not target_id:
            _move_to_error(filename, [f"{doc_type} type requires target_id"])
            db.insert_event(
                f"memo:{filename}",
                "apply_failed",
                memo_file=filename,
                reason="target_missing",
                note=f"{doc_type} type requires target_id",
            )
            return {"success": False, "error": "target_id missing", "filename": filename}

        target_doc = db.get_document_by_id(target_id)
        if target_doc is None:
            _move_to_error(filename, [f"target_id '{target_id}' document not found"])
            db.insert_event(
                f"memo:{filename}",
                "apply_failed",
                memo_file=filename,
                reason="target_not_found",
                related_target_id=target_id,
                note=f"target_id '{target_id}' document not found",
            )
            return {"success": False, "error": f"target not found: {target_id}", "filename": filename}

        has_conflict, open_result_doc_ids = db.has_open_result_for_target(target_id)
        if has_conflict:
            reason = (
                f"there are already open result documents for target '{target_id}': "
                f"{', '.join(open_result_doc_ids)}"
            )
            _move_to_conflict(filename, reason, related_target_id=target_id)
            db.insert_event(
                target_id,
                "conflict_detected",
                memo_file=filename,
                file_hash=file_hash,
                reason="conflict_open_result_exists",
                related_target_id=target_id,
                related_doc_id=",".join(open_result_doc_ids),
                note=reason,
            )
            return {"success": False, "error": reason, "filename": filename}

        ok, msg = db.update_document_status(target_id, "closed")
        if not ok:
            _move_to_error(filename, [f"target '{target_id}' status transition failed: {msg}"])
            db.insert_event(
                f"memo:{filename}",
                "apply_failed",
                memo_file=filename,
                reason="target_transition_failed",
                related_target_id=target_id,
                note=msg,
            )
            return {"success": False, "error": msg, "filename": filename}

        db.insert_event(target_id, "status_changed",
                        memo_file=filename, note=f"{msg} (by {doc_type})")

    db.insert_document(
        doc_id,
        doc_type,
        project,
        module,
        title,
        target_id=target_id,
        group_id=group_id,
        owner=owner,
        priority=priority,
        due_date=due_date,
    )
    db.insert_event(doc_id, "created", memo_file=filename, file_hash=file_hash, note=f"hash:{file_hash}")

    # Directive types (DS/N/T) remain in inbox — moved to accept/reject on approval/rejection
    # Q type moves directly to accept/ after apply (T030)
    if doc_type in ("DS", "N", "T"):
        actual_filename = filename
        moved_to = "inbox"
    elif doc_type == "Q":
        # Q moves inbox→accept/ directly (T030: process/ passthrough not needed)
        actual_filename = _resolve_duplicate_filename(filename)
        if actual_filename != filename:
            src = os.path.join(db.INBOX_DIR, filename)
            tmp = os.path.join(db.INBOX_DIR, actual_filename)
            if os.path.exists(src):
                os.rename(src, tmp)
        _move_file(actual_filename, db.INBOX_DIR, db.ACCEPT_DIR)
        moved_to = "accept"
    else:
        # Duplicate filename handling: append suffix if same filename exists in processed/accept
        actual_filename = _resolve_duplicate_filename(filename)
        if actual_filename != filename:
            src = os.path.join(db.INBOX_DIR, filename)
            tmp = os.path.join(db.INBOX_DIR, actual_filename)
            if os.path.exists(src):
                os.rename(src, tmp)
        _move_file(actual_filename, db.INBOX_DIR, db.PROCESSED_DIR)
        moved_to = "processed"

    gap_warnings = detect_gaps(group_id) if group_id else []
    all_warnings = assign_warnings + gap_warnings

    return {
        "success": True,
        "doc_id": doc_id,
        "group_id": group_id,
        "gap_warnings": all_warnings,
        "filename": actual_filename,
        "moved_to": moved_to,
    }


def apply_all_inbox() -> list[dict]:
    """Sequentially apply all .md files in inbox."""
    results: list[dict] = []
    for filename in list_inbox_files():
        results.append(apply_file(filename))
    return results


# ── CONFIRM Scan ──────────────────────────────────────────────────────

def process_confirm_memos() -> list[dict]:
    results = []
    for filename in list_inbox_files():
        content = read_inbox_file(filename)
        if content is None:
            continue
        header, _ = linter.parse_yaml_header(content)
        if header and header.get("type") == "CONFIRM":
            results.append(apply_file(filename))
    return results


# ── Status Update ────────────────────────────────────────────────────

def change_status(
    doc_id: str,
    new_status: str,
    reason_note: str | None = None,
    related_target_id: str | None = None,
) -> dict:
    ok, msg = db.update_document_status(doc_id, new_status)
    if ok:
        reason = "rejected" if new_status == "rejected" else None
        note = msg
        if reason_note:
            note = f"{msg} | reason: {reason_note}"
        db.insert_event(
            doc_id,
            "status_changed",
            reason=reason,
            related_target_id=related_target_id,
            note=note,
        )
    return {"success": ok, "message": msg}


def update_metadata(doc_id: str, owner: str | None, priority: str | None, due_date: str | None) -> dict:
    """Manually update document operational metadata."""
    owner_norm = (owner or "").strip() or None
    priority_norm = (priority or "").strip().lower() or None
    due_date_norm = (due_date or "").strip() or None

    errors = linter.validate_metadata_values(owner_norm, priority_norm, due_date_norm)
    if errors:
        return {"success": False, "message": "; ".join(errors)}

    ok, msg = db.update_document_metadata(doc_id, owner_norm, priority_norm, due_date_norm)
    if ok:
        db.insert_event(
            doc_id,
            "metadata_updated",
            note=f"owner={owner_norm or '-'}, priority={priority_norm or '-'}, due_date={due_date_norm or '-'}",
        )
    return {"success": ok, "message": msg}


# ── Briefing ─────────────────────────────────────────────────────────

class BriefContext:
    """Per-request prefetch shared by the brief / gaps / queue / handover path.

    0276 NR0003 발견 3: these four entry points each re-queried the same data.
    A single /brief request ran get_open_documents() up to four times, executed
    detect_workflow_gaps() twice in full, and issued five separate N+1 cascades
    (one query per open document, per queue item, per conflict event) — 100+
    queries for 30 open documents, growing linearly with the document count.

    Design decision (CH0004): the shared state is an *explicit object* threaded
    as an optional argument, not a module-level request-scoped cache. The server
    has no request-scope plumbing to hang a cache on, and a module-level one
    would leak a stale snapshot across requests and threads. Passing it also
    keeps the data flow visible at each call site.

    Every lookup is lazy and memoised, so each underlying query runs at most once
    per context and only if something actually asks for it — a standalone
    detect_workflow_gaps() does not pay for the linked-document scan it never
    reads, while a /brief request shares all of them.

    Callers that pass no context keep the previous behaviour: each public
    function builds a private one, so signatures stay backward compatible.
    """

    def __init__(self):
        self._open_docs: list[dict] | None = None
        self._linked_by_target: dict[str, list[dict]] | None = None
        self._followups_by_target: dict[str, set] | None = None
        self._latest_events: dict[str, dict | None] = {}
        self._workflow_gaps: dict | None = None

    @property
    def open_docs(self) -> list[dict]:
        """The open-document snapshot every consumer shares.

        Sharing it also makes one response internally consistent; the four
        separate queries could each observe a different commit.
        """
        if self._open_docs is None:
            self._open_docs = db.get_open_documents()
        return self._open_docs

    @property
    def linked_by_target(self) -> dict[str, list[dict]]:
        """target_id -> NR/TR documents. One scan instead of N+1 ①②③."""
        if self._linked_by_target is None:
            self._linked_by_target = db.get_linked_result_documents_map()
        return self._linked_by_target

    @property
    def followups_by_target(self) -> dict[str, set]:
        """target_id -> follow-up type codes. One query instead of N+1 ④.

        NR is included so get_handover()'s pending list resolves from it too.
        """
        if self._followups_by_target is None:
            self._followups_by_target = db.get_followup_type_map(("T", "TR", "NR"))
        return self._followups_by_target

    def linked(self, doc_id: str) -> list[dict]:
        """NR/TR documents referencing doc_id (same shape as db.get_linked_result_documents)."""
        return self.linked_by_target.get(doc_id, [])

    def has_followup(self, doc_id: str, types: tuple[str, ...]) -> bool:
        """Whether a follow-up document of any of `types` targets doc_id."""
        existing = self.followups_by_target.get(doc_id)
        return bool(existing and existing.intersection(types))

    def latest_events(self, doc_ids: list[str]) -> dict[str, dict | None]:
        """Memoised latest-event map; queries only ids not seen yet."""
        missing = [d for d in doc_ids if d and d not in self._latest_events]
        if missing:
            # Record every requested id first so documents without any event are
            # remembered as "looked up, none found" and never re-queried.
            for doc_id in missing:
                self._latest_events[doc_id] = None
            self._latest_events.update(db.get_latest_events_map(missing))
        return self._latest_events

    def workflow_gaps(self) -> dict:
        """detect_workflow_gaps() computed at most once per request."""
        if self._workflow_gaps is None:
            self._workflow_gaps = detect_workflow_gaps(ctx=self)
        return self._workflow_gaps


def get_brief(ctx: BriefContext | None = None) -> dict:
    ctx = ctx or BriefContext()
    open_docs = ctx.open_docs
    missing_nr_tr: list[dict] = []

    for doc in open_docs:
        if doc.get("type") not in ("N", "T"):
            continue
        if not ctx.linked(doc["doc_id"]):
            missing_nr_tr.append({
                "doc_id": doc["doc_id"],
                "type": doc["type"],
                "project": doc["project"],
                "module": doc.get("module"),
                "title": doc["title"],
                "status": doc["status"],
            })

    # Both of these used to recompute the gaps independently (the queue summary
    # via build_action_queue); the context computes them once.
    workflow_gaps = ctx.workflow_gaps()
    queue_summary = get_action_queue_summary(ctx=ctx)

    return {
        "open_documents": open_docs,
        "recent_events": db.get_recent_events(5),
        "missing_nr_tr_documents": missing_nr_tr,
        "queue_summary": queue_summary,
        "workflow_gaps": workflow_gaps,
    }


# ── INDEX View ───────────────────────────────────────────────────────

def build_index_view() -> dict:
    """Build INDEX view data based on DB state."""
    grouped = db.get_documents_grouped_by_status()
    order = ["open", "closed", "rejected"]
    status_groups = []
    total = 0

    for status in order:
        docs = grouped.get(status, [])
        status_groups.append({
            "status": status,
            "count": len(docs),
            "documents": docs,
        })
        total += len(docs)

    return {
        "status_groups": status_groups,
        "total_count": total,
        "generated_at": datetime.now().isoformat(),
    }


def rebuild_index_view() -> dict:
    """Rebuild INDEX view based on DB state in case of discrepancies."""
    # INDEX is based on DB queries, so rebuild is defined as regenerating the latest DB snapshot.
    view = build_index_view()
    view["rebuilt"] = True
    return view


# ── TV Dashboard Summary ─────────────────────────────────────────────

def get_tv_dashboard_summary() -> dict:
    """Return TV status summary for the dashboard (D008 §3 V-06).

    Return structure:
        {
            "counts": {"Open": int, "Running": int, "Fail": int, "Reject": int},
            "by_status": {"Open": [doc...], ...},
        }
    """
    target_statuses = ("Open", "Running", "Fail", "Reject")
    by_status: dict[str, list[dict]] = {s: [] for s in target_statuses}
    rows = db.get_active_tvs_by_statuses(list(target_statuses))
    for row in rows:
        tv_st = row.get("tv_status") or ""
        if tv_st in by_status:
            by_status[tv_st].append(row)
    counts = {s: len(items) for s, items in by_status.items()}
    return {
        "counts": counts,
        "by_status": by_status,
        "total": sum(counts.values()),
    }


# ── Handover ─────────────────────────────────────────────────────────

def get_handover(ctx: BriefContext | None = None) -> dict:
    """Return the handover draft structure for the next session."""
    ctx = ctx or BriefContext()
    # Same rule as db.get_pending_nr_tr_documents(): open N/T documents with no
    # NR/TR follow-up. Resolved from the prefetched map instead of one query per
    # open document (0276 NR0003 발견 3).
    pending = [
        doc for doc in ctx.open_docs
        if doc.get("type") in ("N", "T") and not ctx.has_followup(doc["doc_id"], ("NR", "TR"))
    ]
    return {
        "pending": pending,
        "open": ctx.open_docs,
        "recently_closed": db.get_recently_closed_or_rejected_documents(10),
        "queue_summary": get_action_queue_summary(ctx=ctx),
        "workflow_gaps": ctx.workflow_gaps(),
    }


def get_rejected_history() -> dict:
    """Summarize and return rejected documents and their rejection reason events."""
    items = db.get_rejected_documents_with_reasons()
    for item in items:
        reasons = item.get("reject_events") or []
        latest_reason = reasons[0] if reasons else None
        item["latest_reason"] = latest_reason
        item["next_action"] = (
            "Upload a new memo to inbox after addressing the rejection reason"
            if latest_reason else
            "Please record the rejection reason event first"
        )
    return {
        "total": len(items),
        "items": items,
    }


def get_conflict_history() -> dict:
    """Return the combined conflict bucket and conflict event history."""
    files = get_reprocess_bucket_view("conflict")
    conflict_events = db.get_conflict_events(200)
    return {
        "total_files": len(files),
        "files": files,
        "events": conflict_events,
    }


def _with_latest_event(docs: list[dict], ctx: "BriefContext | None" = None) -> list[dict]:
    """Inject the latest event summary into the document list."""
    doc_ids = [d["doc_id"] for d in docs]
    # This runs once per gap category and once per queue category (12 times per
    # /brief), over heavily overlapping document sets. The context memoises the
    # union so the repeats cost nothing (0276 NR0003 발견 3).
    latest_map = ctx.latest_events(doc_ids) if ctx else db.get_latest_events_map(doc_ids)

    enriched: list[dict] = []
    for doc in docs:
        event = latest_map.get(doc["doc_id"])
        item = dict(doc)
        item["latest_event"] = event
        item["latest_event_summary"] = (
            f"{event.get('event_type')} | {event.get('note') or ''}".strip()
            if event else ""
        )
        enriched.append(item)
    return enriched


def _find_target_docs_with_no_followup(
    open_docs: list[dict],
    followup_types: tuple[str, ...],
    ctx: "BriefContext | None" = None,
) -> list[dict]:
    """Find open documents that have no follow-up document with the given target_id."""
    result: list[dict] = []
    for doc in open_docs:
        followers = ctx.linked(doc["doc_id"]) if ctx else db.get_linked_result_documents(doc["doc_id"])
        if not any(f.get("type") in followup_types for f in followers):
            result.append(doc)
    return result


def _stale_open_documents(open_docs: list[dict], stale_hours: int) -> list[dict]:
    """Return open documents that have not been updated for at least the specified number of hours."""
    now = datetime.now()
    stale: list[dict] = []
    for doc in open_docs:
        updated_raw = doc.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated_at = datetime.fromisoformat(updated_raw)
        except ValueError:
            continue
        hours = (now - updated_at).total_seconds() / 3600.0
        if hours >= stale_hours:
            stale.append(doc)
    return stale


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _days_since(raw_datetime: str | None, now: datetime) -> float | None:
    dt = _parse_iso_datetime(raw_datetime)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 86400.0


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _append_gap_item(result: list[dict], doc: dict, reason: str):
    item = dict(doc)
    item["gap_reason"] = reason
    result.append(item)


def _build_gap_category(docs: list[dict], ctx: "BriefContext | None" = None) -> dict:
    enriched = _with_latest_event(docs, ctx)
    sorted_items = _sort_queue_items(enriched)
    return {
        "count": len(sorted_items),
        "items": sorted_items,
        "sample": sorted_items[:3],
    }


def detect_workflow_gaps(now: datetime | None = None, ctx: "BriefContext | None" = None) -> dict:
    """Detect operational gaps (stale/overdue/follow-up/review bottlenecks)."""
    now_dt = now or datetime.now()
    today = now_dt.date()
    ctx = ctx or BriefContext()
    open_docs = ctx.open_docs

    stale_open: list[dict] = []
    overdue: list[dict] = []
    unassigned_important: list[dict] = []
    missing_followup_from_n: list[dict] = []
    missing_followup_from_t: list[dict] = []
    review_stuck: list[dict] = []

    for doc in open_docs:
        doc_id = doc.get("doc_id") or ""
        doc_type = doc.get("type")
        priority = (doc.get("priority") or "").strip().lower()
        owner = (doc.get("owner") or "").strip()

        updated_days = _days_since(doc.get("updated_at"), now_dt)
        created_days = _days_since(doc.get("created_at"), now_dt)

        if updated_days is not None and updated_days >= WORKFLOW_GAP_RULES["stale_open_days"]:
            _append_gap_item(
                stale_open,
                doc,
                f"open for {int(updated_days)}+ days since last updated_at",
            )

        due = _parse_iso_date(doc.get("due_date"))
        if due is not None:
            overdue_days = (today - due).days
            if overdue_days > WORKFLOW_GAP_RULES["overdue_grace_days"]:
                _append_gap_item(
                    overdue,
                    doc,
                    f"due date {due.isoformat()} passed (+{overdue_days} days)",
                )

        if priority in ("high", "urgent") and not owner:
            _append_gap_item(unassigned_important, doc, f"{priority} priority document has no owner")

        if doc_type == "N" and created_days is not None and created_days >= WORKFLOW_GAP_RULES["followup_grace_days_from_n"]:
            if not ctx.has_followup(doc_id, ("T",)):
                _append_gap_item(
                    missing_followup_from_n,
                    doc,
                    f"no follow-up T within {int(created_days)} days of creation",
                )

        if doc_type == "T" and created_days is not None and created_days >= WORKFLOW_GAP_RULES["followup_grace_days_from_t"]:
            if not ctx.has_followup(doc_id, ("TR",)):
                _append_gap_item(
                    missing_followup_from_t,
                    doc,
                    f"no follow-up TR within {int(created_days)} days of creation",
                )

        if doc_type in ("NR", "TR") and updated_days is not None and updated_days >= WORKFLOW_GAP_RULES["review_stuck_days"]:
            _append_gap_item(
                review_stuck,
                doc,
                f"review document has been open for {int(updated_days)}+ days",
            )

    categories = {
        "stale_open": _build_gap_category(stale_open, ctx),
        "overdue": _build_gap_category(overdue, ctx),
        "unassigned_important": _build_gap_category(unassigned_important, ctx),
        "missing_followup_from_n": _build_gap_category(missing_followup_from_n, ctx),
        "missing_followup_from_t": _build_gap_category(missing_followup_from_t, ctx),
        "review_stuck": _build_gap_category(review_stuck, ctx),
    }

    counts = {name: cat["count"] for name, cat in categories.items()}
    return {
        "rules": dict(WORKFLOW_GAP_RULES),
        "counts": counts,
        "total": sum(counts.values()),
        "categories": categories,
    }


def _parse_due_date(due_date: str | None) -> tuple[int, str]:
    if not due_date:
        return (1, "9999-12-31")
    return (0, due_date)


def _queue_sort_key(doc: dict) -> tuple[int, tuple[int, str], str]:
    priority = (doc.get("priority") or "").strip().lower()
    priority_rank = PRIORITY_ORDER.get(priority, len(PRIORITY_ORDER))
    due_key = _parse_due_date(doc.get("due_date"))
    updated = doc.get("updated_at") or "9999-12-31T23:59:59"
    return (priority_rank, due_key, updated)


def _sort_queue_items(docs: list[dict]) -> list[dict]:
    return sorted(docs, key=_queue_sort_key)


def build_action_queue(ctx: BriefContext | None = None) -> dict:
    """Build the operational priority queue by category."""
    ctx = ctx or BriefContext()
    open_docs = ctx.open_docs
    workflow_gaps = ctx.workflow_gaps()
    open_n = [d for d in open_docs if d.get("type") == "N"]
    open_t = [d for d in open_docs if d.get("type") == "T"]
    open_review = db.get_documents_by_status_and_types("open", ("NR", "TR"))
    rejected = db.get_documents_by_status("rejected")
    stale_open = workflow_gaps["categories"]["stale_open"]["items"]

    needs_dispatch = _find_target_docs_with_no_followup(open_n, ("T",), ctx)
    needs_result = _find_target_docs_with_no_followup(open_t, ("NR", "TR"), ctx)

    # One query for every conflict event's document instead of one per event
    # (0276 NR0003 발견 3, N+1 ⑤). Insertion order still follows the event order.
    conflict_docs_map: dict[str, dict] = {}
    conflict_events = db.get_conflict_events(50)
    conflict_doc_map = db.get_documents_by_ids(
        [e.get("doc_id") for e in conflict_events if e.get("doc_id")]
    )
    for e in conflict_events:
        doc_id = e.get("doc_id")
        if not doc_id or doc_id in conflict_docs_map:
            continue
        doc = conflict_doc_map.get(doc_id)
        if doc:
            conflict_docs_map[doc_id] = doc

    if os.path.exists(db.CONFLICT_DIR):
        for fname in sorted(os.listdir(db.CONFLICT_DIR)):
            if fname.startswith("_"):
                continue
            base = {
                "doc_id": f"conflict-file:{fname}",
                "type": "-",
                "project": "-",
                "module": "",
                "title": fname,
                "status": "conflict",
                "updated_at": "",
                "target_id": "",
            }
            conflict_docs_map[base["doc_id"]] = base

    categories_raw = {
        "needs_dispatch": needs_dispatch,
        "needs_result": needs_result,
        "needs_review": open_review,
        "rejected_followup": rejected,
        "conflict": list(conflict_docs_map.values()),
        "stale_open": stale_open,
    }

    # Prime the latest-event cache for every category in one query, so the
    # per-category _with_latest_event() calls below are all cache hits.
    ctx.latest_events([
        d.get("doc_id") for docs in categories_raw.values() for d in docs if d.get("doc_id")
    ])

    categories: dict[str, dict] = {}
    for name, docs in categories_raw.items():
        enriched = _with_latest_event(docs, ctx)
        with_actions: list[dict] = []
        for item in enriched:
            doc_id = item.get("doc_id") or ""
            if doc_id.startswith("conflict-file:"):
                item["next_action"] = "Check conflict reason, then decide to reprocess or discard"
            else:
                linked_docs = ctx.linked(doc_id) if doc_id else []
                _, desc = _suggest_next_action(item, linked_docs)
                item["next_action"] = desc
            with_actions.append(item)
        sorted_items = _sort_queue_items(with_actions)
        categories[name] = {
            "count": len(sorted_items),
            "items": sorted_items,
            "sample": sorted_items[:3],
        }

    return {
        "stale_open_hours": STALE_OPEN_HOURS,
        "workflow_gaps": workflow_gaps,
        "categories": categories,
    }


def get_action_queue_summary(ctx: BriefContext | None = None) -> dict:
    queue = build_action_queue(ctx=ctx or BriefContext())
    categories = queue["categories"]
    return {
        "stale_open_hours": queue["stale_open_hours"],
        "counts": {k: v["count"] for k, v in categories.items()},
        "samples": {k: v["sample"] for k, v in categories.items()},
        "workflow_gaps": {
            "rules": queue["workflow_gaps"]["rules"],
            "counts": queue["workflow_gaps"]["counts"],
            "total": queue["workflow_gaps"]["total"],
            "samples": {
                k: v["sample"]
                for k, v in queue["workflow_gaps"]["categories"].items()
            },
        },
    }


def _suggest_next_action(doc: dict, linked_docs: list[dict]) -> tuple[str, str]:
    """Suggest the next action based on document status/links."""
    doc_type = doc.get("type")
    status = doc.get("status")

    if status == "rejected":
        return "followup_rework", "Check rejection reason and request rework"

    if status == "open" and doc_type == "N":
        has_t = any(d.get("type") == "T" for d in linked_docs)
        if not has_t:
            return "dispatch", "Draft follow-up T work order"

    if status == "open" and doc_type == "T":
        has_tr = any(d.get("type") == "TR" for d in linked_docs)
        if not has_tr:
            return "result_request", "Draft follow-up TR result request"

    if status == "open" and doc_type in ("NR", "TR"):
        return "review_request", "Draft review request"

    return "monitor", "Monitor status and provide further instructions"


def build_worker_draft(doc_id: str) -> dict | None:
    """Build a dispatch draft for the operations leader to send to a worker."""
    doc = db.get_document_by_id(doc_id)
    if not doc:
        return None

    linked_docs = db.get_linked_result_documents(doc_id)
    target_id = (doc.get("target_id") or "").strip()
    target_doc = db.get_document_by_id(target_id) if target_id else None
    recent_events = db.get_recent_events_by_doc_id(doc_id, 5)
    latest_event = recent_events[0] if recent_events else None

    action_type, action_description = _suggest_next_action(doc, linked_docs)

    project = doc.get("project", "")
    module = doc.get("module") or ""
    module_text = module if module else "(all)"
    title = doc.get("title", "")
    status = doc.get("status", "")

    worker_message = (
        "[FlowGate Work Order Draft]\n"
        f"- Target Document: {doc_id} ({doc.get('type', '')})\n"
        f"- Project/Module: {project}/{module_text}\n"
        f"- Title: {title}\n"
        f"- Current Status: {status}\n"
        f"- Suggested Action: {action_type} ({action_description})\n"
        f"- target_id: {target_id or '-'}\n"
        "- Linked Documents: "
        + (
            ", ".join(f"{d.get('doc_id')}[{d.get('type')}/{d.get('status')}]" for d in linked_docs)
            if linked_docs else
            "none"
        )
        + "\n"
        + "- Latest Event: "
        + (
            f"{latest_event.get('event_type')} / {latest_event.get('note') or ''}" if latest_event else "none"
        )
        + "\n"
        + "- Allowed Files/Restricted Scope: [operator to confirm]\n"
        + "- Request: Please review the draft based on the above information and reply with your work plan."
    )

    return {
        "doc": {
            "doc_id": doc_id,
            "type": doc.get("type"),
            "title": title,
            "project": project,
            "module": module,
            "status": status,
            "updated_at": doc.get("updated_at"),
            "target_id": target_id,
        },
        "target_document": target_doc,
        "linked_documents": linked_docs,
        "recent_events": recent_events,
        "latest_event": latest_event,
        "suggested_action": {
            "type": action_type,
            "description": action_description,
        },
        "worker_message_template": worker_message,
    }
