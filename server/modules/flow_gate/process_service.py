"""FlowGate — process flow service (OutBox/InBox/approval/rejection/group)."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from typing import Any

from . import db
from .db import groups as db_groups
from .db import projects as db_projects
from . import template_provision
from . import linter
from .numbering import numbering_service
from .storage import paths as storage_paths
from .db.document_type_labels import get_type_name, get_type_names_map


# ── Type → display-name mapping (locale-aware, DB-backed) ────────────────────
# Remove hardcoding: query the document_type_names table (T471)
# Use get_type_name(type_code, locale) or get_type_names_map(locale)

TYPE_ACTION_MESSAGES: dict[str, str] = {
    "R": "A requirements definition has been submitted.",
    "B": "A bug report has been submitted.",
    "Q": "A question has been submitted.",
    "A": "This is a response to the question.",
    "AR": "Approval request. Please review.",
    "DS": "The instruction document has been approved. Please begin work.",
    "D": "The design document has been submitted.",
    "DB": "The DB design document has been submitted.",
    "P": "The protocol document has been submitted.",
    "L": "The logic design document has been submitted.",
    "DC": "Design completion has been declared.",
    "N": "The instruction document has been approved. Please begin work.",
    "NR": "The investigation report has been submitted.",
    "T": "The instruction document has been approved. Please begin work.",
    "TR": "The task report has been submitted.",
    "V": "Review requested.",
    "VR": "The review report has been submitted.",
    "M": "The memo has been delivered.",
    "CH": "The conversation has been updated.",
    "AC": "Approved.",
    "RJ": "Rejected. Please check the reason.",
}

WORKFLOW_ROOT_TYPES = {"R", "B"}

# Locale-aware section headings for the default B (bug) template body.
# Previously these were hardcoded in Korean, so a B created under any locale
# always emitted Korean headings. Fallback order: requested locale → en → ko.
_BUG_TEMPLATE_SECTIONS: dict[str, list[str]] = {
    "ko": ["재현 절차", "기대 동작", "실제 동작", "환경 및 영향 범위"],
    "en": ["Steps to Reproduce", "Expected Behavior", "Actual Behavior", "Environment & Impact Scope"],
    "ja": ["再現手順", "期待する動作", "実際の動作", "環境および影響範囲"],
}


def _build_bug_template_body(locale: str = "en") -> str:
    """Build the default B template body with locale-aware section headings."""
    sections = (
        _BUG_TEMPLATE_SECTIONS.get(locale)
        or _BUG_TEMPLATE_SECTIONS.get((locale or "").split("-")[0])
        or _BUG_TEMPLATE_SECTIONS["en"]
    )
    lines: list[str] = []
    for idx, section in enumerate(sections):
        lines.append(f"## {section}")
        if idx < len(sections) - 1:
            lines.append("")
    return "\n".join(lines)

# InBox action mapping by type (P002 §8-7, plus revision-request support from D004 §3.1)
TYPE_ACTIONS: dict[str, list[str]] = {
    "Q": ["answer"],
    "AR": ["approve", "reject"],
    "DS": ["approve", "reject", "clipboard"],
    "N": ["approve", "reject", "clipboard"],
    "T": ["approve", "reject", "clipboard"],
    "D": ["review_request", "revision_request"],
    "P": ["review_request", "revision_request"],
    "L": ["review_request", "revision_request"],
    "DB": ["review_request", "revision_request"],
    "DC": ["approve", "reject", "review_request"],
    "NR": ["review_request", "revision_request", "approve", "reject"],
    "TR": ["review_request", "revision_request", "approve", "reject"],
    "VR": ["approve_batch", "reject_batch"],
    "M": [],
    "CH": [],  # L0044.0008 §3.5: conversation — no review actions (non-gate, like M)
}

# Types eligible for review requests
REVIEW_REQUEST_TYPES = {"D", "DB", "P", "L", "DC", "NR", "TR"}

# Types that can be approved/rejected
APPROVABLE_TYPES = {"AR", "DS", "N", "T", "DC", "VR", "TR", "NR"}

# Types eligible for revision requests (D004 §3.1)
REVISION_REQUEST_TYPES = {"D", "DB", "P", "L", "NR", "TR"}

# Type mapping for auto-generating result drafts in inbox after approval
AUTO_RESULT_DRAFT_TYPES = {"N": "NR", "T": "TR"}

# Targets collected for bulk VR transfer
VR_MIGRATION_TARGET_TYPES = {"D", "DB", "P", "L", "NR", "TR", "VR"}

PREFIX_SUFFIX_MAX = 99

LOCATION_LABELS: dict[str, str] = {
    "inbox": "InBox",
    "processed": "Processed",
    "outbox": "OutBox",
    "accept": "Accept",
    "reject": "Reject",
    "cancelled": "Cancelled",
    "missing": "File not found",
}

# Review scope — for parsing ## headings in NR/TR templates
_RULE_TEMPLATE_DIRS: list[str] = [
    os.path.join(db.STORAGE_DIR, "_rule", "templates"),
    os.path.normpath(os.path.join(db.STORAGE_DIR, "..", "_documents", "_rule", "templates")),
]

_REPORT_TEMPLATE_MAP: dict[str, str] = {
    "NR": "template_inv_report.md",
    "TR": "template_task_report.md",
}

# Next-step document type → template mapping (T014)
NEXT_TYPE_TEMPLATE_MAP: dict[str, str] = {
    "N": "template_investigation.md",
    "NR": "template_inv_report.md",
    "T": "template_mini_task.md",
    "TR": "template_task_report.md",
    "D": "template_design.md",
    "P": "template_protocol_design.md",
    "L": "template_logic.md",
}

# DC review preset
DC_REVIEW_PRESETS: list[dict[str, Any]] = [
    {"label": "Full review", "targets": ["D", "DB", "P", "L"]},
    {"label": "Exclude D", "targets": ["DB", "P", "L"]},
    {"label": "Focus on DB + L", "targets": ["DB", "L"]},
    {"label": "Quick review (D only)", "targets": ["D"]},
]

GROUP_ACTION_REASON_OPTIONS: list[dict[str, str]] = [
    {"value": "goal_completed", "label": "Goal achieved"},
    {"value": "scope_changed", "label": "Requirements/scope changed"},
    {"value": "duplicate_or_merged", "label": "Duplicate/merged"},
    {"value": "blocked_or_on_hold", "label": "On hold/blocked"},
    {"value": "other", "label": "Other"},
]
# AC (final approval) and DC (group discard) are file-less action records, not workflow
# step documents. Excluding them keeps current-stage/last-action computation honest
# (a discarded group must not show the dormant "Design Completed" DC stage label).
GROUP_HISTORY_EXCLUDED_TYPES: tuple[str, ...] = ("AC", "DC")
GROUP_HISTORY_EXCLUDED_STATUSES: tuple[str, ...] = ("cancelled",)
CANCEL_FOLLOWUP_ACTION_LABELS: dict[str, str] = {
    "resubmit": "Resubmit (re-register in InBox)",
    "discard": "Discard (move to cancelled)",
    "user_edit": "User edits directly (keep file)",
}

# Types for which cancellation is allowed in D005 (T035: cancellation only within D005 scope)
D005_CANCEL_TYPES: frozenset[str] = frozenset({
    "N", "T", "TR", "NR", "D", "DS", "R", "M", "P", "L",
})

# Next-step guidance label (T040) — dynamically generated from locale data (T471)
def _get_next_label(type_code: str, locale: str = "en") -> str:
    """Build the next-step guidance message with locale-aware type names."""
    type_name = get_type_names_map(locale).get(type_code, type_code)
    return f"{type_code} {type_name} document is required"

GROUP_NEXT_ACTION_FLOW: dict[str, tuple[str, ...]] = {
    "R": ("DS", "N", "T"),
    "DS": ("D", "P", "L", "DB", "DC"),
    "DC": ("N", "T"),
    "N": ("NR",),
    "T": ("TR",),
}

GROUP_NEXT_ACTION_STAGE_BY_TYPE: dict[str, str] = {
    "R": "R",
    "AR": "R",
    "DS": "DS",
    "D": "DS",
    "DB": "DS",
    "P": "DS",
    "L": "DS",
    "DC": "DC",
    "N": "N",
    "NR": "N",
    "T": "T",
    "TR": "T",
    "TV": "T",
    "TVR": "T",
}

GROUP_NEXT_ACTION_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "R": ("R", "AR"),
    "DS": ("DS", "D", "DB", "P", "L"),
    "DC": ("DC",),
    "N": ("N", "NR"),
    "T": ("T", "TR", "TV", "TVR"),
}

# Canonical prefixes allowed to be exposed through the next_action path (T051)
_ALLOWED_PATH_PREFIXES: frozenset[str] = frozenset({
    "_rule", "accept", "inbox", "outbox", "reject",
})


def _get_project_roots(project: str) -> tuple[str, str]:
    """Return docs_root and project_root for the project. Return empty strings when unset."""
    env_project_root = os.environ.get(f"FLOWGATE_PROJECT_ROOT_OVERRIDE_{project}", "")
    row = db.get_project_settings_by_project(project)
    if row:
        return str(row.get("docs_root") or ""), str(row.get("project_root") or env_project_root or "")
    for row in db.get_project_settings():
        if row.get("project") == project:
            return str(row.get("docs_root") or ""), str(row.get("project_root") or env_project_root or "")
    return "", str(env_project_root or "")


def _find_in_allowed_storage_buckets(basename: str) -> str:
    """Search allowed buckets under storage and return the absolute path (T051 fallback).

    Allowed buckets: inbox, accept, outbox, reject, _rule.
    Because transferred files gain a prefix in their name, check not only exact
    matches but also "_basename" suffix matches (the post-transfer filename pattern).
    Return an empty string when the file cannot be found.
    """
    if not basename:
        return ""
    allowed_dirs = [
        db.INBOX_DIR,
        db.ACCEPT_DIR,
        db.OUTBOX_DIR,
        db.REJECT_DIR,
        os.path.join(db.STORAGE_DIR, "_rule"),
    ]
    suffix = "_" + basename
    for bucket_dir in allowed_dirs:
        for dirpath, _dirs, filenames in os.walk(bucket_dir):
            for f in filenames:
                if f == basename or f.endswith(suffix):
                    return os.path.join(dirpath, f)
    return ""


def _to_docs_root_relative(filename: str, docs_root: str) -> str:
    """Convert a filename to a docs_root-relative path (T051/T052).

    1. When docs_root is set, search under docs_root and return a relative path
       that starts with an allowed prefix (_rule/accept/inbox/outbox/reject).
    2. If no location starting with an allowed prefix is found, search the
       allowed storage buckets instead (including suffix matching).
       - If the fallback absolute path is under docs_root and is an allowed
         prefix path, return it as a relative path (T052).
       - If it is not under docs_root, return the absolute path as-is.
    3. If it cannot be found anywhere, return an empty string. Never return a
       bare filename.
    """
    filename = (filename or "").strip()
    if not filename:
        return ""
    basename = os.path.basename(filename) or filename
    docs_root = (docs_root or "").strip()
    if docs_root:
        for dirpath, _dirs, filenames in os.walk(docs_root):
            if basename in filenames:
                found_abs = os.path.join(dirpath, basename)
                try:
                    rel = os.path.relpath(found_abs, docs_root)
                except ValueError:
                    continue
                first_component = rel.split(os.sep)[0]
                if first_component in _ALLOWED_PATH_PREFIXES:
                    return rel
                # Not an allowed prefix → keep searching
    # docs_root unset, or file not found in an allowed-prefix location
    # → fall back to allowed storage buckets
    fallback_abs = _find_in_allowed_storage_buckets(basename)
    # T052: if the fallback absolute path is under docs_root, validate the
    # allowed prefix and return a relative path
    if fallback_abs and docs_root:
        try:
            rel = os.path.relpath(fallback_abs, docs_root)
        except ValueError:
            return fallback_abs
        if not rel.startswith(".."):
            first_component = rel.split(os.sep)[0]
            if first_component in _ALLOWED_PATH_PREFIXES:
                return rel
    return fallback_abs


def _build_reference_qa_items(requirement_doc_id: str, docs_root: str = "") -> list[dict]:
    """Flatten the linked Q/A context based on the requirement document."""
    requirement_doc_id = (requirement_doc_id or "").strip()
    if not requirement_doc_id:
        return []

    reference_qa: list[dict] = []
    q_docs = db.get_documents_by_target_id(requirement_doc_id, types=("Q",))
    for q_doc in q_docs:
        q_doc_id = str(q_doc.get("doc_id") or "").strip()
        raw_q_file = db.get_created_memo_file(q_doc_id) or ""
        reference_qa.append({
            "type": "Q",
            "doc_id": q_doc_id,
            "title": str(q_doc.get("title") or ""),
            "file": _to_docs_root_relative(raw_q_file, docs_root),
        })
        if not q_doc_id:
            continue
        a_docs = db.get_documents_by_target_id(q_doc_id, types=("A",))
        for a_doc in a_docs:
            a_doc_id = str(a_doc.get("doc_id") or "").strip()
            raw_a_file = db.get_created_memo_file(a_doc_id) or ""
            reference_qa.append({
                "type": "A",
                "doc_id": a_doc_id,
                "title": str(a_doc.get("title") or ""),
                "file": _to_docs_root_relative(raw_a_file, docs_root),
            })

    return reference_qa


def _resolve_requirement_context(source_doc: dict[str, Any] | None, docs_root: str = "") -> dict[str, Any]:
    """Follow the source document's target chain to find the requirement context."""
    source_doc = source_doc or {}
    current_target_id = str(source_doc.get("target_id") or "").strip()
    if not current_target_id:
        return {
            "requirement_doc_id": "",
            "requirement_title": "",
            "requirement_file": "",
            "reference_qa": [],
        }

    visited_doc_ids: set[str] = set()
    closest_parent_doc: dict[str, Any] | None = None
    requirement_doc: dict[str, Any] | None = None

    while current_target_id:
        if current_target_id in visited_doc_ids:
            return {
                "requirement_doc_id": "",
                "requirement_title": "",
                "requirement_file": "",
                "reference_qa": [],
            }
        visited_doc_ids.add(current_target_id)

        parent_doc = db.get_document_by_id(current_target_id)
        if parent_doc is None:
            return {
                "requirement_doc_id": "",
                "requirement_title": "",
                "requirement_file": "",
                "reference_qa": [],
            }

        if closest_parent_doc is None:
            closest_parent_doc = parent_doc

        if str(parent_doc.get("type") or "").strip() == "R":
            requirement_doc = parent_doc
            break

        current_target_id = str(parent_doc.get("target_id") or "").strip()

    requirement_doc = requirement_doc or closest_parent_doc
    requirement_doc_id = str((requirement_doc or {}).get("doc_id") or "")
    requirement_title = str((requirement_doc or {}).get("title") or "")
    raw_req_file = db.get_created_memo_file(requirement_doc_id) or "" if requirement_doc_id else ""
    requirement_file = _to_docs_root_relative(raw_req_file, docs_root)

    return {
        "requirement_doc_id": requirement_doc_id,
        "requirement_title": requirement_title,
        "requirement_file": requirement_file,
        "reference_qa": _build_reference_qa_items(requirement_doc_id, docs_root),
    }


def _build_next_action_context_fields(source_doc: dict[str, Any] | None, docs_root: str = "") -> dict[str, Any]:
    """Build the source/requirement context shared by next-action candidates."""
    source_doc = source_doc or {}
    source_doc_id = str(source_doc.get("doc_id") or "")
    source_title = str(source_doc.get("title") or "")
    raw_source_file = db.get_created_memo_file(source_doc_id) or "" if source_doc_id else ""
    source_file = _to_docs_root_relative(raw_source_file, docs_root)

    context = {
        "source_doc_id": source_doc_id,
        "source_title": source_title,
        "source_file": source_file,
        "requirement_doc_id": "",
        "requirement_title": "",
        "requirement_file": "",
        "reference_qa": [],
    }
    if not source_doc_id:
        return context

    context.update(_resolve_requirement_context(source_doc, docs_root))
    return context


def _build_next_action_candidates(
    candidate_types: list[str] | tuple[str, ...] | set[str],
    source_doc: dict[str, Any] | None,
    group_id: str,
    project: str,
    module: str,
    docs_root: str = "",
    project_root: str = "",
    locale: str = "en",
) -> list[dict]:
    """Convert the next-action type list into a common payload shape."""
    ordered_types: list[str] = []
    seen_types: set[str] = set()
    for raw_type in candidate_types:
        next_type = str(raw_type or "").strip()
        if not next_type or next_type in seen_types:
            continue
        seen_types.add(next_type)
        ordered_types.append(next_type)

    context_fields = _build_next_action_context_fields(source_doc, docs_root)
    return [
        {
            "type": next_type,
            "label": _get_next_label(next_type, locale),
            "group_id": group_id,
            "project": project,
            "module": module,
            "docs_root": docs_root,
            "project_root": project_root,
            **context_fields,
        }
        for next_type in ordered_types
    ]


def _find_latest_source_doc_for_stage(
    docs: list[dict[str, Any]],
    current_action_type: str,
) -> dict[str, Any] | None:
    """Find the representative document to use as source context for the current action stage."""
    preferred_types = GROUP_NEXT_ACTION_SOURCE_TYPES.get(current_action_type, ())
    for preferred_type in preferred_types:
        for doc in reversed(docs):
            if str(doc.get("type") or "").strip() == preferred_type:
                return doc

    for doc in reversed(docs):
        normalized_type = GROUP_NEXT_ACTION_STAGE_BY_TYPE.get(str(doc.get("type") or "").strip())
        if normalized_type == current_action_type:
            return doc
    return None


def _resolve_group_next_action_context(
    docs: list[dict],
    group_id: str,
    project: str,
    module: str,
    locale: str = "en",
) -> dict[str, Any]:
    """Compute the current-action context and candidate list for the group detail panel."""
    current_action_type = ""
    for doc in reversed(docs):
        normalized_type = GROUP_NEXT_ACTION_STAGE_BY_TYPE.get(str(doc.get("type") or "").strip())
        if normalized_type:
            current_action_type = normalized_type
            break

    source_doc = _find_latest_source_doc_for_stage(docs, current_action_type)
    next_action_candidates: list[dict] = []
    if current_action_type == "DC":
        next_action_candidates = _collect_next_stage_candidates(source_doc, group_id, project, module, locale)
    elif current_action_type:
        existing_types = {
            str(doc.get("type") or "").strip()
            for doc in docs
            if str(doc.get("type") or "").strip()
        }
        candidate_types = [
            next_type
            for next_type in GROUP_NEXT_ACTION_FLOW.get(current_action_type, ())
            if next_type not in existing_types
        ]
        docs_root, project_root = _get_project_roots(project)
        next_action_candidates = _build_next_action_candidates(
            candidate_types,
            source_doc,
            group_id,
            project,
            module,
            docs_root=docs_root,
            project_root=project_root,
            locale=locale,
        )

    selected_next_action_type = ""
    preferred_type = str((source_doc or {}).get("next") or "").strip()
    candidate_type_set = {candidate["type"] for candidate in next_action_candidates}
    if preferred_type in candidate_type_set:
        selected_next_action_type = preferred_type

    if not selected_next_action_type and next_action_candidates:
        selected_next_action_type = str(next_action_candidates[0].get("type") or "")

    return {
        "current_action_type": current_action_type,
        "next_action_candidates": next_action_candidates,
        "selected_next_action_type": selected_next_action_type,
    }


def _collect_next_stage_candidates(
    source_doc: dict[str, Any] | None,
    group_id: str,
    project: str,
    module: str,
    locale: str = "en",
) -> list[dict]:
    """Collect the list of next-step candidates for the group.

    Returns:
        list of {
            "type": str,
            "label": str,
            "group_id": str,
            "project": str,
            "module": str,
            "source_doc_id": str,
            "source_title": str,
            "source_file": str,
            "requirement_doc_id": str,
            "requirement_title": str,
            "requirement_file": str,
            "reference_qa": list[dict],
        }
        Return [] when there are no candidates.
    """
    try:
        docs = db.get_documents_by_group_id(group_id)
        ds_docs = [
            d
            for d in docs
            if str(d.get("type") or "").strip() == "DS" and d.get("status") == "accepted"
        ]

        candidate_types: set[str] = set()
        if ds_docs:
            for ds_doc in ds_docs:
                next_val = str(ds_doc.get("next") or "").strip()
                if not next_val:
                    next_val = "N"
                candidate_types.add(next_val)
        else:
            candidate_types = {"N"}

        existing_types = {
            str(d.get("type") or "").strip()
            for d in docs
            if str(d.get("type") or "").strip()
        }
        candidate_types -= existing_types
        docs_root, project_root = _get_project_roots(project)
        return _build_next_action_candidates(
            sorted(candidate_types),
            source_doc,
            group_id,
            project,
            module,
            docs_root=docs_root,
            project_root=project_root,
            locale=locale,
        )
    except Exception:
        return []


def _parse_template_headings(doc_type: str) -> list[str]:
    """Extract the list of ## headings from an NR/TR template file."""
    template_name = _REPORT_TEMPLATE_MAP.get(doc_type)
    if not template_name:
        return []
    template_path = ""
    for d in _RULE_TEMPLATE_DIRS:
        candidate = os.path.join(d, template_name)
        if os.path.isfile(candidate):
            template_path = candidate
            break
    if not template_path:
        return []
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    headings: list[str] = []
    for line in content.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            headings.append(m.group(1).strip())
    return headings


# ── Filename safety ───────────────────────────────────────────────────────────

def _safe_filename(filename: str) -> bool:
    return ".." not in filename and "/" not in filename and "\\" not in filename


# ── OutBox service ────────────────────────────────────────────────────────────

def create_workflow_root(
    project: str, module: str, title: str,
    slug: str, priority: str, body: str,
    owner: str = "admin",
    group_id: str = "",
    new_group_name: str = "",
    doc_type: str = "R",
    template: str = "default",
    locale: str = "en",
) -> dict:
    """Create an R/B workflow-root document and reserve its group/document ID."""
    # Validate input values
    errors: list[str] = []
    doc_type = (doc_type or "R").strip().upper()
    if doc_type not in WORKFLOW_ROOT_TYPES:
        errors.append(f"Invalid workflow root type: '{doc_type}'")

    title = (title or "").strip()
    if not title:
        errors.append("title must be between 1 and 100 characters.")
    elif len(title) > linter.TITLE_MAX_LEN:
        errors.append(f"title must be at most {linter.TITLE_MAX_LEN} characters.")

    slug_input = (slug or "").strip()
    slug_value = ""
    if slug_input:
        slug_value = _slugify(slug_input)
        if slug_value == "untitled":
            errors.append("Please use English letters and numbers for the slug.")

    priority = (priority or "medium").strip().lower()
    if priority not in linter.VALID_PRIORITIES:
        errors.append(f"Invalid priority: '{priority}'")

    # Validate project/module
    allowed = db.get_allowed_projects()
    project_names, project_modules = linter._normalize_allowed_projects(allowed)

    if project_names and project not in project_names:
        errors.append(f"Unregistered project/module combination: {project}/{module}")
    elif project_modules:
        if (project, "") not in project_modules and (project, module) not in project_modules:
            errors.append(f"Unregistered project/module combination: {project}/{module}")

    # Group-related validation
    group_id = (group_id or "").strip()
    new_group_name = (new_group_name or "").strip()

    if group_id and new_group_name:
        errors.append("Please specify either an existing group or a new group, not both.")

    if errors:
        return {"status": "error", "errors": errors}

    # Group handling logic
    final_group_id = None

    if new_group_name:
        # Create a new group
        try:
            group_code = numbering_service.reserve_group(project, module or "none")
            final_group_id = f"{project}.{module or 'none'}.{group_code}"
            db.insert_group(final_group_id, project, module or "none", new_group_name, priority)
        except Exception as e:
            return {"status": "error", "errors": [str(e)]}
    elif group_id:
        # Validate the existing group
        existing_group = db.get_group(group_id)
        if not existing_group:
            errors.append(f"Group does not exist: {group_id}")
        elif existing_group.get("project_id") != project:
            errors.append(f"The group does not belong to this project: {group_id}")
        else:
            final_group_id = group_id
            # Use the existing group's module as the source of truth.
            # It takes precedence over the client-provided module.
            group_module = (existing_group.get("module") or "").strip()
            if group_module:
                module = group_module

        if errors:
            return {"status": "error", "errors": errors}
    else:
        # Group unspecified: automatically create a new group
        try:
            group_code = numbering_service.reserve_group(project, module or "none")
            final_group_id = f"{project}.{module or 'none'}.{group_code}"
            db.insert_group(final_group_id, project, module or "none", title, priority)
        except Exception as e:
            return {"status": "error", "errors": [str(e)]}

    # A group owns exactly one workflow root, either R or B.
    existing_docs = db.get_documents_by_group_id(final_group_id)
    for _d in existing_docs:
        existing_type = str(_d.get("type_code") or _d.get("type") or "").strip().upper()
        if existing_type in WORKFLOW_ROOT_TYPES:
            code = (
                "group_r_already_exists"
                if doc_type == "R" and existing_type == "R"
                else "group_root_already_exists"
            )
            return {
                "status": "error",
                "errors": [{
                    "code": code,
                    "message": f"A workflow root ({existing_type}) already exists in this group.",
                }],
            }

    doc_code = numbering_service.reserve_document(
        final_group_id, doc_type, module=module or "none"
    )
    doc_number = doc_code
    auto_slug = _slugify(title)
    filename_slug = slug_value or auto_slug
    filename = f"{doc_code}_{filename_slug}.md"

    if doc_type == "B" and template != "none" and not (body or "").strip():
        body = _build_bug_template_body(locale)

    # Frontmatter is human-readable metadata, so use the short form.
    short_group_id = storage_paths.group_dir_name(final_group_id)
    project_label = storage_paths.project_dir_name(project)
    header_lines = [
        "---",
        f"group_id: {short_group_id}",
        f"type: {doc_type}",
        f"doc_number: {doc_number}",
        f"project: {project_label}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        "target_id:",
        "next:",
        "---",
        "",
        body or "",
    ]
    content = "\n".join(header_lines)

    # Write the file to OutBox (AI worker communication channel — planned to switch
    # to an API in M016)
    outbox = db.outbox_dir(project)
    os.makedirs(outbox, exist_ok=True)
    filepath = os.path.join(outbox, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # Write the body file into the storage documents tree
    # (the actual document base users see in Explorer)
    storage_filepath = storage_paths.document_path(
        project_id=project,
        group_code=final_group_id,
        doc_code=doc_number,
        filename=f"{filename_slug}.md",
        module=module or "none",
    )
    try:
        storage_filepath.parent.mkdir(parents=True, exist_ok=True)
        storage_filepath.write_text(content, encoding="utf-8")
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise

    # Register in the DB
    doc_id = f"{final_group_id}.{doc_code}"
    try:
        db.insert_document(
            doc_id=doc_id,
            doc_type=doc_type,
            project=project,
            module=module,
            title=title,
            group_id=final_group_id,
            priority=priority,
            status="draft",
            direction="outbox",
            file_path=str(storage_filepath),
        )
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        if storage_filepath.exists():
            storage_filepath.unlink()
        raise
    db.insert_event(doc_id, "created", memo_file=filename)

    # groups-table registration was already handled above (for new groups)

    return {
        "status": "success",
        "doc_id": doc_id,
        "doc_number": doc_number,
        "group_id": final_group_id,
        "created_file": os.path.abspath(filepath),
        "message": TYPE_ACTION_MESSAGES[doc_type],
    }


def create_requirement(
    project: str, module: str, title: str,
    slug: str, priority: str, body: str,
    owner: str = "admin",
    group_id: str = "",
    new_group_name: str = "",
    doc_type: str = "R",
    template: str = "default",
    locale: str = "en",
) -> dict:
    """Backward-compatible entry point for workflow-root creation."""
    return create_workflow_root(
        project=project,
        module=module,
        title=title,
        slug=slug,
        priority=priority,
        body=body,
        owner=owner,
        group_id=group_id,
        new_group_name=new_group_name,
        doc_type=doc_type,
        template=template,
        locale=locale,
    )


def create_answer(doc_pk: int, answers: list[dict]) -> dict:
    """Create the Q→A response form in OutBox."""
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "errors": ["Document not found"]}
    if doc["type"] != "Q":
        return {"status": "error", "errors": [f"Only Q-type documents can be answered (current: {doc['type']})"]}

    # Validate the response
    errors: list[str] = []
    for ans in answers:
        idx = ans.get("index", 0)
        text = (ans.get("text") or "").strip()
        if not text:
            errors.append(f"Response for question {idx} is empty.")
    if errors:
        return {"status": "error", "errors": errors}

    group_id = doc["group_id"]
    project = doc["project"]
    module = doc.get("module") or ""
    priority = doc.get("priority") or "medium"
    title = doc["title"] + " — Response"

    # Parse questions from the Q file body
    q_memo = db.get_created_memo_file(doc["doc_id"])
    q_content = _read_file_from_any_bucket(q_memo) if q_memo else None
    target_id_val = doc["doc_id"]

    # Build the A body
    body_lines: list[str] = []
    questions = _parse_questions(q_content) if q_content else []
    if questions:
        for i, q_text in enumerate(questions):
            ans_text = ""
            for ans in answers:
                if ans.get("index") == i + 1:
                    ans_text = ans.get("text", "")
                    break
            body_lines.append("### Q")
            body_lines.append(q_text)
            body_lines.append("")
            body_lines.append("### A")
            body_lines.append(ans_text)
            body_lines.append("")
    else:
        for ans in answers:
            body_lines.append("### Q")
            body_lines.append("(Question)")
            body_lines.append("")
            body_lines.append("### A")
            body_lines.append(ans.get("text", ""))
            body_lines.append("")

    body = "\n".join(body_lines)

    doc_code = numbering_service.reserve_document(
        group_id, "A", module=module or "none"
    )
    doc_number = doc_code
    filename = f"{doc_code}_{_slugify(title)}.md"

    header_lines = [
        "---",
        f"group_id: {group_id}",
        "type: A",
        f"doc_number: {doc_number}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        f"target_id: {target_id_val}",
        "next:",
        "---",
        "",
        body,
    ]
    content = "\n".join(header_lines)

    os.makedirs(db.OUTBOX_DIR, exist_ok=True)
    filepath = os.path.join(db.OUTBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    doc_id = f"{group_id}-{doc_code}"
    try:
        db.insert_document(
            doc_id=doc_id, doc_type="A", project=project, module=module,
            title=title, group_id=group_id, target_id=target_id_val,
            priority=priority, status="draft", direction="outbox",
        )
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise
    db.insert_event(doc_id, "answer_generated", memo_file=filename)
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "group_id": group_id,
        "created_file": os.path.abspath(filepath),
        "message": "Response has been created.",
    }


def approve_document(
    doc_pk: int,
    approved_types: list[str] | None = None,  # DEPRECATED: ignored; kept for flow_gate.py compatibility
    confirm_extra: bool = False,
) -> dict:
    """Approve an InBox document (create AC + transfer files).

    For DC type, approve it in bundle mode using approved_files from the DC
    header together with DS-based discovery.
    """
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": f"Document not found: doc_id={doc_pk}"}

    source_type = doc["type"]
    if source_type not in APPROVABLE_TYPES:
        return {"status": "error", "message": f"{source_type} is not eligible for approval"}

    group_id = doc["group_id"]
    project = doc["project"]
    module = doc.get("module") or ""
    priority = doc.get("priority") or "medium"
    source_memo = db.get_created_memo_file(doc["doc_id"])
    source_filename = source_memo or ""

    migration_results: list[dict] = []

    if source_type == "DC":
        # 1. Read approved_files from the DC header
        source_content = _read_dc_file_content(source_filename)
        if source_content is None:
            return {"status": "error", "message": "Unable to read the DC file"}
        header, parse_err = linter.parse_yaml_header(source_content)
        if parse_err or header is None:
            return {"status": "error", "message": f"Failed to parse the DC header: {parse_err}"}
        approved_files = _normalize_approved_files(header.get("approved_files") or [])
        target_id_dc = (header.get("target_id") or "").strip()

        # 2. Validate approved_files
        validation_issues = _collect_approved_file_validation_issues(
            approved_files, group_id
        )
        if validation_issues:
            return {
                "status": "error",
                "message": "; ".join(issue["message"] for issue in validation_issues),
                "error_file_items": _build_dc_error_file_items(
                    group_id,
                    issues=validation_issues,
                ),
            }

        # 3. Discover bundle targets
        mismatch_files = _collect_dc_bundle_mismatch_files(approved_files, target_id_dc)
        if mismatch_files:
            return {
                "status": "error",
                "message": "Unable to find bundle target files",
                "error_file_items": _build_dc_error_file_items(
                    group_id,
                    mismatch_files=mismatch_files,
                ),
            }
        bundle_files, extra_files = resolve_bundle_targets(
            approved_files, target_id_dc, group_id
        )
        if not bundle_files:
            return {
                "status": "error",
                "message": "Unable to find bundle target files",
                "error_file_items": _build_dc_error_file_items(
                    group_id,
                    mismatch_files=approved_files,
                ),
            }

        # 4. C3: extra discovered files — return extra_found when confirm is absent
        if extra_files and not confirm_extra:
            try:
                item_payload = _build_dc_item_payload(group_id, approved_files, extra_files)
            except ValueError as exc:
                return {"status": "error", "message": str(exc)}
            return {
                "status": "extra_found",
                "group_id": group_id,
                "extra_files": extra_files,
                "extra_file_items": item_payload["extra_file_items"],
                "bundle_files": bundle_files,
                "bundle_file_items": item_payload["bundle_file_items"],
                "approved_file_items": item_payload["approved_file_items"],
                "message": "Additional files were found during DS discovery. Please confirm whether to include them in the approval.",
            }

        # 5. Execute bundle approval
        dc_result = _dc_approve_artifacts(group_id, doc["doc_id"], bundle_files)
        if not dc_result.get("success", True):
            return {"status": "error", "message": dc_result.get("error", "Bundle approval failed")}
        migration_results = dc_result.get("migration", [])
    elif source_type == "VR":
        # Bulk transfer
        targets = _collect_migration_targets(group_id)
        batch_result = _batch_migrate(group_id, targets, db.ACCEPT_DIR, "accepted")
        if not batch_result["success"]:
            return {"status": "error", "message": batch_result.get("error", "Bulk transfer failed")}
        migration_results = batch_result["results"]
    else:
        # Single-file transfer: instructions (DS/N/T) move from inbox, others from processed
        if source_filename:
            source_dir = db.INBOX_DIR if source_type in ("DS", "N", "T") else db.PROCESSED_DIR
            mig = _migrate_single_file(
                group_id, source_filename, source_type, source_dir, db.ACCEPT_DIR,
            )
            if mig:
                migration_results.append(mig)

    # Update the source document status
    db.update_document_status_by_pk(doc_pk, "accepted")
    db.insert_event(doc["doc_id"], "accepted", memo_file=source_filename)

    # Create the AC file
    ac_result = _create_ac_file(
        group_id, project, module, priority,
        doc["doc_id"], source_filename, source_type,
    )

    # Clean up only the OutBox source files corresponding to the current
    # processed document and its direct target document.
    cleaned_outbox_files = _cleanup_related_outbox_files(doc, source_filename)

    auto_inbox_result: dict[str, str] = {}
    result_type = AUTO_RESULT_DRAFT_TYPES.get(source_type)
    if result_type:
        auto_inbox_result = _create_auto_result_inbox_draft(
            group_id=group_id,
            project=project,
            module=module,
            priority=priority,
            source_doc_id=doc["doc_id"],
            source_type=source_type,
            source_title=doc["title"],
            result_type=result_type,
        )

    if group_id:
        db.update_group_updated_at(group_id)

    # next_actions: next-step guidance on DS/DC approval (T040)
    next_actions: list[dict] = []
    if source_type == "DS":
        try:
            next_actions = _collect_next_stage_candidates(doc, group_id, project, module)
        except Exception:
            next_actions = []
    elif source_type == "DC":
        try:
            has_rejection = any(
                m.get("action") == "rejected" for m in migration_results
            )
            if has_rejection:
                next_actions = []
            else:
                next_actions = _collect_next_stage_candidates(doc, group_id, project, module)
        except Exception:
            next_actions = []

    return {
        "status": "success",
        "action": "approve",
        "group_id": group_id,
        "source_type": source_type,
        "source_file": source_filename,
        "created_file": ac_result.get("file_path", ""),
        "migration": migration_results,
        "cleaned_outbox_files": cleaned_outbox_files,
        "auto_inbox_result_file": auto_inbox_result.get("file_path", ""),
        "auto_inbox_result_filename": auto_inbox_result.get("filename", ""),
        "inbox_result_file": auto_inbox_result.get("filename", ""),
        "next_actions": next_actions,
        "message": "Approval notice has been created.",
    }


def reject_document(doc_pk: int, reject_reason: str) -> dict:
    """Reject an InBox document (create RJ + transfer files)."""
    reject_reason = (reject_reason or "").strip()
    if not reject_reason:
        return {"status": "error", "errors": ["A rejection reason is required."]}

    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": f"Document not found: doc_id={doc_pk}"}

    source_type = doc["type"]
    if source_type not in APPROVABLE_TYPES:
        return {"status": "error", "message": f"{source_type} is not eligible for rejection"}

    group_id = doc["group_id"]
    project = doc["project"]
    module = doc.get("module") or ""
    priority = doc.get("priority") or "medium"
    source_memo = db.get_created_memo_file(doc["doc_id"])
    source_filename = source_memo or ""

    migration_results: list[dict] = []

    if source_type == "DC":
        # DC rejection (full rejection): mark all D/DB/P/L documents in the group as rejected
        dc_result = _dc_reject_all_artifacts(group_id, reject_reason)
        migration_results = dc_result.get("migration", [])
    elif source_type == "VR":
        targets = _collect_migration_targets(group_id)
        batch_result = _batch_migrate(group_id, targets, db.REJECT_DIR, "rejected")
        if not batch_result["success"]:
            return {"status": "error", "message": batch_result.get("error", "Bulk transfer failed")}
        migration_results = batch_result["results"]
    else:
        if source_filename:
            source_dir = db.INBOX_DIR if source_type in ("DS", "N", "T") else db.PROCESSED_DIR
            mig = _migrate_single_file(
                group_id, source_filename, source_type, source_dir, db.REJECT_DIR,
            )
            if mig:
                migration_results.append(mig)

    db.update_document_status_by_pk(doc_pk, "rejected")
    db.insert_event(doc["doc_id"], "rejected", memo_file=source_filename, reason=reject_reason)

    source_next = doc.get("next") or ""
    rj_result = _create_rj_file(
        group_id, project, module, priority,
        doc["doc_id"], source_filename, source_type,
        reject_reason, migration_results,
        next_type=source_next,
    )

    if group_id:
        db.update_group_updated_at(group_id)

    # On V (review request) rejection, automatically create the RJ result file in inbox
    rj_inbox_file = ""
    if source_type == "V":
        rj_inbox_file = _create_inbox_result_file(
            group_id, project, module, priority,
            doc["doc_id"], doc["title"], "RJ", "Rejected",
            reject_reason=reject_reason,
        )

    return {
        "status": "success",
        "action": "reject",
        "group_id": group_id,
        "source_type": source_type,
        "source_file": source_filename,
        "created_file": rj_result.get("file_path", ""),
        "migration": migration_results,
        "inbox_result_file": rj_inbox_file,
        "message": "Rejection notice has been created.",
    }


def cancel_document(doc_pk: int, cancel_reason: str, followup_action: str) -> dict:
    """Cancel an approved document and clean up files according to the follow-up handling mode.

    D005 cancellation-allowed types: N / T / TR / NR / D / DS / R / M / P / L
    Behavior when canceling an N-type document:
    - Follow-up handling is fixed to InBox resubmission (resubmit).
    - Automatically cascade-cancel linked NR documents in accepted state (cascade_nr).
    Behavior when canceling a T-type document:
    - Three follow-up handling choices are available (resubmit / discard / user_edit).
    - Automatically cascade-cancel linked TR documents in accepted state
      (cascade_tr, fixed to discard).
    Cancels are allowed only for the PM (user). Automatic worker calls are prohibited.
    """
    cancel_reason = (cancel_reason or "").strip()
    if not cancel_reason:
        return {"status": "error", "errors": ["A cancellation reason is required."]}

    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": f"Document not found: doc_id={doc_pk}"}

    current_status = (doc.get("status") or "").strip().lower()
    if current_status != "accepted":
        return {
            "status": "error",
            "message": f"Only approved documents can be cancelled (current status: {doc.get('status')})",
        }

    doc_type = (doc.get("type") or "").strip().upper()
    group_id = (doc.get("group_id") or "").strip()

    # T035: only D005-allowed types can be canceled
    if doc_type not in D005_CANCEL_TYPES:
        return {
            "status": "error",
            "message": f"{doc_type} is not eligible for approval cancellation (D005 cancellable types: {', '.join(sorted(D005_CANCEL_TYPES))})",
        }

    # N type: follow-up handling is fixed to InBox resubmission
    if doc_type == "N":
        action = "resubmit"
    else:
        action = (followup_action or "").strip().lower()
        if action not in CANCEL_FOLLOWUP_ACTION_LABELS:
            return {"status": "error", "errors": ["Please select a valid follow-up action."]}

    source_memo = db.get_created_memo_file(doc["doc_id"]) or ""
    migration_results: list[dict] = []

    if action in {"resubmit", "discard"}:
        if not source_memo:
            return {"status": "error", "message": "Original file information could not be found"}

        accept_file_path = _find_file_in_accept(source_memo)
        if not accept_file_path:
            return {"status": "error", "message": "The original file could not be found in the accept repository"}

        if action == "resubmit":
            os.makedirs(db.INBOX_DIR, exist_ok=True)
            dest_path = os.path.join(db.INBOX_DIR, source_memo)
            if os.path.exists(dest_path):
                return {"status": "error", "message": f"An identical file already exists in InBox: {source_memo}"}
            _strip_status_header_inplace(accept_file_path)
            shutil.move(accept_file_path, dest_path)
            migration_results.append({
                "original": source_memo,
                "migrated_to": os.path.abspath(dest_path),
                "status": "cancelled",
                "followup_action": action,
            })
        else:
            os.makedirs(db.CANCELLED_DIR, exist_ok=True)
            cancelled_name = _resolve_unique_filename(
                db.CANCELLED_DIR,
                os.path.basename(accept_file_path),
            )
            dest_path = os.path.join(db.CANCELLED_DIR, cancelled_name)
            shutil.move(accept_file_path, dest_path)
            migration_results.append({
                "original": source_memo,
                "migrated_to": os.path.abspath(dest_path),
                "status": "cancelled",
                "followup_action": action,
            })

    db.update_document_status_by_pk(doc_pk, "cancelled")
    db.insert_event(
        doc["doc_id"],
        "cancelled",
        memo_file=source_memo or None,
        reason=cancel_reason,
        note=action,
    )

    # On N cancellation: cascade-cancel linked NR documents in accepted state
    # (cascade_nr, D002)
    # On T cancellation: cascade-cancel linked TR documents in accepted state
    # (cascade_tr, fixed to discard, D005)
    cascade_cancelled: list[dict] = []
    if doc_type == "N":
        nr_docs = db.get_documents_by_target_id(
            doc["doc_id"], types=("NR",), statuses=("accepted",)
        )
        for nr_doc in nr_docs:
            db.update_document_status_by_pk(nr_doc["id"], "cancelled")
            db.insert_event(
                nr_doc["doc_id"],
                "cancelled",
                reason=f"N cancellation cascade ({doc['doc_id']})",
                note="cascade_nr",
            )
            cascade_cancelled.append({"doc_id": nr_doc["doc_id"], "type": nr_doc["type"]})

    elif doc_type == "T":
        # T035: automatically cascade-cancel accepted TR on T cancellation
        # (fixed to discard)
        tr_docs = db.get_documents_by_target_id(
            doc["doc_id"], types=("TR",), statuses=("accepted",)
        )
        for tr_doc in tr_docs:
            # Move the TR file from accept/ → cancelled/ (fixed to discard)
            tr_memo = db.get_created_memo_file(tr_doc["doc_id"])
            if tr_memo:
                tr_accept_path = _find_file_in_accept(tr_memo)
                if tr_accept_path:
                    os.makedirs(db.CANCELLED_DIR, exist_ok=True)
                    cancelled_name = _resolve_unique_filename(
                        db.CANCELLED_DIR, os.path.basename(tr_accept_path)
                    )
                    shutil.move(tr_accept_path, os.path.join(db.CANCELLED_DIR, cancelled_name))
            db.update_document_status_by_pk(tr_doc["id"], "cancelled")
            db.insert_event(
                tr_doc["doc_id"],
                "cancelled",
                reason=f"T cancellation cascade ({doc['doc_id']})",
                note="cascade_tr",
            )
            cascade_cancelled.append({"doc_id": tr_doc["doc_id"], "type": "TR"})

    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "action": "cancel",
        "group_id": group_id,
        "doc_id": doc["doc_id"],
        "followup_action": action,
        "followup_label": CANCEL_FOLLOWUP_ACTION_LABELS[action],
        "migration": migration_results,
        "cascade_cancelled": cascade_cancelled,
        "message": "Approval cancellation completed.",
    }


def resume_document(doc_pk: int) -> dict:
    """Resume a rejected document (restore status=open + transfer file reject/ → inbox/).

    Keep the existing RJ event to preserve history instead of deleting it.
    """
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": f"Document not found: doc_id={doc_pk}"}
    if doc.get("status") != "rejected":
        return {
            "status": "error",
            "message": f"Only rejected documents can be resumed (current status: {doc.get('status')})",
        }

    source_memo = db.get_created_memo_file(doc["doc_id"])
    if not source_memo:
        return {"status": "error", "message": "Original file information could not be found"}

    # Search for the original file (or transferred prefixed file) in the reject/ directory
    reject_file_path = _find_file_in_reject(source_memo)
    if reject_file_path:
        os.makedirs(db.INBOX_DIR, exist_ok=True)
        dest_path = os.path.join(db.INBOX_DIR, source_memo)
        shutil.move(reject_file_path, dest_path)

    # Restore the status to open
    db.update_document_status_by_pk(doc_pk, "open")
    # Record the resume event (keep the existing RJ event)
    db.insert_event(doc["doc_id"], "resumed", memo_file=source_memo, note="Resumed after rejection")

    group_id = doc.get("group_id") or ""
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "doc_id": doc["doc_id"],
        "group_id": group_id,
        "message": "The document has been resumed. Please rework it in InBox and request approval again.",
    }


def create_review_request(
    doc_pk: int,
    *,
    targets: list[str] | None = None,
    focus: str | None = None,
    skipped_sections: list[str] | None = None,
) -> dict:
    """Create a V (review request) file in OutBox for D/P/L/NR/TR."""
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": "Document not found"}

    source_type = doc["type"]
    if source_type not in REVIEW_REQUEST_TYPES:
        return {
            "status": "error",
            "message": f"{source_type} is not eligible for a review request. Eligible types: {', '.join(sorted(REVIEW_REQUEST_TYPES))}",
        }

    group_id = doc["group_id"]
    project = doc["project"]
    module = doc.get("module") or ""
    priority = doc.get("priority") or "medium"
    source_memo = db.get_created_memo_file(doc["doc_id"])
    source_filename = source_memo or ""
    source_file_path = _find_file_in_any_bucket(source_filename) or os.path.join(db.INBOX_DIR, source_filename)
    title = f"{doc['title']} Review Request"

    doc_code = numbering_service.reserve_document(
        group_id, "V", module=module or "none"
    )
    doc_number = doc_code
    filename = f"{doc_code}_{_slugify(title)}.md"

    target_id_val = doc["doc_id"]

    body_lines = [
        "## Review Target",
        "",
        f"- Document: {source_filename}",
        f"- Path: {os.path.abspath(source_file_path)}",
        "",
    ]

    # Review-scope section (optional)
    scope_parts: list[str] = []
    if source_type == "DC" and targets:
        scope_parts.append(f"Review targets: {', '.join(targets)}")
    if source_type in ("NR", "TR") and skipped_sections:
        scope_parts.append(f"Already checked (optional): {', '.join(skipped_sections)}")
    if focus:
        scope_parts.append(f"Focus areas: {focus}")
    if scope_parts:
        body_lines.append("## Review Scope")
        body_lines.append("")
        body_lines.extend(scope_parts)
        body_lines.append("")

    body_lines.extend([
        "## Notes",
        "",
        "Please deliver this review request to the reviewer.",
        "After the review is complete, please submit a VR (review report) to InBox.",
    ])

    header_lines = [
        "---",
        f"group_id: {group_id}",
        "type: V",
        f"doc_number: {doc_number}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        f"target_id: {target_id_val}",
        "next:",
        "---",
        "",
    ]
    content = "\n".join(header_lines) + "\n".join(body_lines) + "\n"

    os.makedirs(db.OUTBOX_DIR, exist_ok=True)
    filepath = os.path.join(db.OUTBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    doc_id = f"{group_id}-{doc_code}"
    try:
        db.insert_document(
            doc_id=doc_id, doc_type="V", project=project, module=module,
            title=title, group_id=group_id, target_id=target_id_val,
            priority=priority, status="draft", direction="outbox",
        )
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise
    db.insert_event(doc_id, "created", memo_file=filename)
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "group_id": group_id,
        "source_type": source_type,
        "source_file": source_filename,
        "created_file": os.path.abspath(filepath),
        "message": "Review request has been created.",
    }


def create_revision_request(
    doc_pk: int,
    *,
    reason: str,
) -> dict:
    """Issue a revision request for D/P/L/DB/NR/TR (D004 §3.1).

    Switch the target document to pending and record a revision_requested event.
    """
    reason = (reason or "").strip()
    if not reason:
        return {"status": "error", "errors": ["A revision reason is required."]}

    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return {"status": "error", "message": "Document not found"}

    source_type = doc["type"]
    if source_type not in REVISION_REQUEST_TYPES:
        return {
            "status": "error",
            "message": f"{source_type} is not eligible for a revision request. Eligible types: {', '.join(sorted(REVISION_REQUEST_TYPES))}",
        }

    group_id = doc["group_id"]
    source_memo = db.get_created_memo_file(doc["doc_id"])
    source_filename = source_memo or ""

    # Switch the status to pending
    db.update_document_status_by_pk(doc_pk, "pending")
    db.insert_event(
        doc["doc_id"],
        "revision_requested",
        memo_file=source_filename,
        reason=reason,
    )

    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "group_id": group_id,
        "source_type": source_type,
        "source_file": source_filename,
        "message": "A revision request has been issued. Please revise and resubmit.",
    }


# ── Path copy (generate clipboard text) ───────────────────────────────────────

def build_clipboard_text(file_path: str, locale: str = "en") -> dict:
    """Read the YAML header from a file path and generate a P-001 §3 handoff message."""
    # file_path may be an absolute path or /storage/... format — convert it to a real path
    real_path = _resolve_storage_path(file_path)
    if not real_path or not os.path.exists(real_path):
        return {"status": "error", "message": f"File not found: {file_path}"}

    # T028: normalize based on absolute paths
    # (prevent loss of backslashes or dots)
    real_path = os.path.abspath(real_path)

    with open(real_path, "r", encoding="utf-8") as f:
        content = f.read()

    header, _ = linter.parse_yaml_header(content)
    if not header:
        return {"status": "error", "message": "Failed to parse YAML header"}

    doc_type = header.get("type", "")
    group_id = header.get("group_id", "")
    title = header.get("title", "")
    type_label = get_type_name(doc_type, locale)
    action_msg = TYPE_ACTION_MESSAGES.get(doc_type, "The document has been delivered.")

    clipboard_text = (
        f"[FlowGate] {action_msg}\n"
        f"Group: {group_id}\n"
        f"Type: {doc_type} ({type_label})\n"
        f"Title: {title}\n"
        f"Path: {real_path}"
    )

    return {
        "status": "success",
        "clipboard_text": clipboard_text,
    }


# ── InBox query service ───────────────────────────────────────────────────────

def get_inbox_list(group_id: str | None = None, doc_type: str | None = None, locale: str = "en") -> dict:
    """Return the InBox document list grouped by group_id."""
    docs = db.get_inbox_process_documents(group_id=group_id, doc_type=doc_type)
    return _group_documents_with_actions(docs, is_inbox=True, locale=locale)


def get_outbox_list(group_id: str | None = None, group_status: str | None = None, locale: str = "en") -> dict:
    """Return the OutBox document list grouped by group_id."""
    docs = db.get_outbox_documents(group_id=group_id)
    return _group_documents_with_actions(docs, is_inbox=False, locale=locale)


def get_inbox_detail(doc_pk: int, locale: str = "en") -> dict | None:
    """InBox document details (YAML header + body + actions by type)."""
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return None

    memo_file = db.get_created_memo_file(doc["doc_id"])
    resolved_path = _find_file_in_any_bucket(memo_file) if memo_file else None
    content = _read_file_from_any_bucket(memo_file) if memo_file else None

    header = None
    body = ""
    if content:
        header, body = _split_header_and_body(content)

    doc_type = doc["type"]
    actions = TYPE_ACTIONS.get(doc_type, [])

    return {
        "doc": doc,
        "doc_id": doc["id"],
        "group_id": doc.get("group_id", ""),
        "type": doc_type,
        "type_label": get_type_name(doc_type, locale),
        "title": doc["title"],
        "priority": doc.get("priority", ""),
        "status": doc.get("status", ""),
        "target_id": doc.get("target_id", ""),
        "next": doc.get("next", ""),
        "header": header,
        "body": body,
        "body_html": body,  # Markdown rendered by the template
        "actions": actions,
        "file_path": (
            os.path.abspath(resolved_path)
            if resolved_path else ""
        ),
        "template_sections": _parse_template_headings(doc_type) if doc_type in ("NR", "TR") else [],
        "dc_review_presets": DC_REVIEW_PRESETS if doc_type == "DC" else [],
        "dc_approval": _build_dc_approval_preview(doc) if doc_type == "DC" else {},
        "reject_reason": _get_reject_reason(doc["doc_id"]) if doc.get("status") == "rejected" else "",
    }


def get_answer_form_data(doc_pk: int) -> dict | None:
    """Return the response-form data for a Q document."""
    doc = db.get_document_by_pk(doc_pk)
    if doc is None:
        return None
    if doc["type"] != "Q":
        return None

    memo_file = db.get_created_memo_file(doc["doc_id"])
    content = _read_file_from_any_bucket(memo_file) if memo_file else None

    questions: list[dict] = []
    if content:
        q_list = _parse_questions(content)
        for i, q_text in enumerate(q_list, 1):
            questions.append({"index": i, "text": q_text})

    if not questions and content:
        _, body = _split_header_and_body(content)
        questions.append({"index": 1, "text": body.strip()})

    return {
        "doc_id": doc["id"],
        "group_id": doc.get("group_id", ""),
        "filename": memo_file or "",
        "type": "Q",
        "type_label": "Question",
        "title": doc["title"],
        "questions": questions,
    }


# ── Group service ─────────────────────────────────────────────────────────────

def _compute_current_stage(doc_type: str, doc_status: str, locale: str = "en") -> str:
    """Compute the current processing stage or next expected action from the type + status combination."""
    if doc_type == "AC":
        return "Approved"
    if doc_type == "RJ":
        return "Rejected"

    if doc_status == "cancelled":
        return "Approval Cancelled"

    if doc_status in ("accepted", "closed"):
        if doc_type == "TR":
            return "Ready to Close Group"
        if doc_type == "NR":
            return "Investigation Report Completed"
        if doc_type == "N":
            return "Investigation in Progress"
        if doc_type == "T":
            return "Task in Progress"
        if doc_type == "DS":
            return "Instruction in Progress"
        if doc_type == "DC":
            return "Design Completed"
        if doc_type in ("D", "DB", "P", "L"):
            return "Review in Progress"
        if doc_type == "VR":
            return "Review Completed"
        return f"{get_type_name(doc_type, locale)} completed"

    if doc_status == "rejected":
        return f"{get_type_name(doc_type, locale)} Rejected"

    if doc_type == "Q":
        return "Awaiting Response"
    if doc_type == "N":
        return "Awaiting Investigation Approval"
    if doc_type == "T":
        return "Awaiting Task Approval"
    if doc_type == "DS":
        return "Awaiting Instruction Approval"
    if doc_type == "AR":
        return "Awaiting Approval Request"
    if doc_type == "DC":
        return "Awaiting Design-Completion Review"
    if doc_type in ("D", "DB", "P", "L"):
        return "Awaiting Review Request"
    if doc_type in ("NR", "TR"):
        return "Awaiting Review Request"
    if doc_type == "VR":
        return "Awaiting Review Approval"
    if doc_type == "M":
        return "Check Memo"
    if doc_type == "CH":
        return "Conversation"
    if doc_type == "R":
        return "Awaiting Drafting"

    actions = TYPE_ACTIONS.get(doc_type, [])
    if "answer" in actions:
        return "Awaiting Response"
    if "review_request" in actions:
        return "Awaiting Review Request"
    if "approve" in actions or "approve_batch" in actions:
        return "Awaiting Approval"

    return get_type_name(doc_type, locale)


# ── Settings view ─────────────────────────────────────────────────────────────

def get_settings_view() -> dict:
    """Return the integrated data used by the Settings screen.

    Merge allowed_projects (project/module) with
    project_settings (docs_root/project_root) by project.
    """
    allowed_rows = db.get_allowed_projects()
    ps_rows = db.get_project_settings()

    allowed_project_names = {row["project"] for row in allowed_rows}
    ps_map: dict[str, dict] = {r["project"]: r for r in ps_rows}

    all_projects = sorted(allowed_project_names | set(ps_map.keys()))

    project_settings_view: list[dict] = []
    for p in all_projects:
        ps = ps_map.get(p, {})
        project_settings_view.append({
            "project": p,
            "docs_root": ps.get("docs_root", ""),
            "project_root": ps.get("project_root", ""),
            "updated_at": ps.get("updated_at", ""),
        })

    return {
        "project_settings_view": project_settings_view,
        "allowed_projects": allowed_rows,
    }


def get_group_list(status: str | None = None, locale: str = "en") -> dict:
    """Fetch the group list."""
    groups = db.get_all_groups(status=status)
    items: list[dict] = []
    for g in groups:
        gid = g["group_id"]
        docs = db.get_documents_by_group_id(
            gid,
            exclude_types=GROUP_HISTORY_EXCLUDED_TYPES,
            exclude_statuses=GROUP_HISTORY_EXCLUDED_STATUSES,
        )
        last_doc = docs[-1] if docs else None
        group_status = (g.get("status") or "").upper()
        current_stage = (
            _compute_current_stage(last_doc["type"], last_doc.get("status", ""), locale)
            if last_doc else "-"
        )
        if group_status == "CLOSED":
            current_stage = "Group Closed"
        elif group_status == "DISCARDED":
            current_stage = "Group Disposed"
        items.append({
            **g,
            "group_status": group_status,
            "last_action": {
                "type": last_doc["type"] if last_doc else "",
                "type_label": get_type_name(last_doc["type"], locale) if last_doc else "",
                "created_at": last_doc.get("created_at", "") if last_doc else "",
            },
            "current_stage": current_stage,
            "document_count": len(docs),
        })
    return {"groups": items}


def get_group_detail(group_id: str, locale: str = "en") -> dict | None:
    """Group details (document history + whether it can be closed)."""
    group = db.get_group(group_id)
    if group is None:
        return None

    group_status = (group.get("status") or "").upper()
    can_document_actions = group_status == "OPEN"
    docs = db.get_documents_by_group_id(
        group_id,
        exclude_types=GROUP_HISTORY_EXCLUDED_TYPES,
        exclude_statuses=GROUP_HISTORY_EXCLUDED_STATUSES,
    )
    can_close, close_reason = _can_close_group(group_id, docs)
    # B0001 fix: group-level events now live in the dedicated group_events table
    # (FK → groups) instead of the document-scoped events table. See migration 048.
    group_events = [
        {
            **event,
            "event_label": _build_group_event_label(event.get("event_type", "")),
            "reason_label": _get_group_action_reason_label(event.get("reason", "")),
        }
        for event in db.get_group_events(group_id)
        if event.get("event_type") in {"group_closed", "group_disposed"}
    ]

    doc_items: list[dict] = []
    for i, d in enumerate(docs, 1):
        # T032: for A type, include answer_generated when looking up memo_file
        memo_file = _get_doc_memo_file(d)
        resolved_path = None
        location_key = "missing"
        if memo_file:
            resolved_path, location_key = _find_file_location_in_any_bucket(memo_file)
        file_path = ""
        if resolved_path:
            try:
                file_path = os.path.abspath(resolved_path)
            except ValueError:
                file_path = ""

        doc_type = d["type"]
        doc_status = d.get("status", "")
        is_actionable = can_document_actions and doc_status == "open"
        status_label = _build_status_label(doc_status, location_key)
        next_action = _build_next_action_label(doc_type, doc_status, locale)
        # T034: flag indicating that D type follows the DC approval path
        is_d_apply_excluded = doc_type in ("D", "DB", "P", "L")
        doc_items.append({
            "seq": i,
            "id": d["id"],
            "doc_id": d["doc_id"],
            "type": doc_type,
            "type_label": get_type_name(doc_type, locale),
            "title": d["title"],
            "status": doc_status,
            "status_label": status_label,
            "created_at": d.get("created_at", ""),
            "file_path": file_path,
            "location": location_key,
            "location_label": LOCATION_LABELS.get(location_key, location_key),
            "next_action": next_action,
            "is_approvable": is_actionable and doc_type in APPROVABLE_TYPES,
            "is_rejectable": is_actionable and doc_type in APPROVABLE_TYPES,
            "is_review_requestable": is_actionable and doc_type in REVIEW_REQUEST_TYPES,
            "is_revision_requestable": is_actionable and doc_type in REVISION_REQUEST_TYPES,
            "is_resumable": can_document_actions and doc_status == "rejected" and doc_type in APPROVABLE_TYPES,
            "is_dc": doc_type == "DC",
            "is_d_apply_excluded": is_d_apply_excluded,  # T034
            "template_sections": _parse_template_headings(doc_type) if doc_type in ("NR", "TR") else [],
            "dc_review_presets": DC_REVIEW_PRESETS if doc_type == "DC" else [],
            "dc_approval": _build_dc_approval_preview(d) if doc_type == "DC" else {},
            "reject_reason": _get_reject_reason(d["doc_id"]) if doc_status == "rejected" else "",
        })

    next_action_context = _resolve_group_next_action_context(
        docs,
        group_id,
        str(group.get("project_id") or ""),
        str(group.get("module") or ""),
        locale,
    )
    next_action_flow_rows = [
        {
            "current": current_type,
            "next": list(next_types),
            "is_active": current_type == next_action_context["current_action_type"],
        }
        for current_type, next_types in GROUP_NEXT_ACTION_FLOW.items()
    ]

    return {
        **group,
        "documents": doc_items,
        "group_events": group_events,
        "can_close": can_close,
        "close_reason": close_reason,
        "group_action_reason_options": GROUP_ACTION_REASON_OPTIONS,
        "can_terminal_action": group_status == "OPEN",
        "can_document_actions": can_document_actions,
        "tv_tvr_sets": _build_tv_tvr_sets(docs),
        "next_action_candidates": next_action_context["next_action_candidates"],
        "next_action_candidate_types": [
            item["type"] for item in next_action_context["next_action_candidates"]
        ],
        "selected_next_action_type": next_action_context["selected_next_action_type"],
        "current_action_type": next_action_context["current_action_type"],
        "next_action_flow_rows": next_action_flow_rows,
    }


def _build_tv_tvr_sets(docs: list[dict]) -> list[dict]:
    """Build the list of TV/TVR sets keyed by T from the group document list (D008 §Task 2).

    Returns: [
        {
            "t_doc_id": str,
            "t_title": str,
            "t_status": str,
            "tv": [<TV doc + tv_status field>],
            "tvr": [<TVR doc>],
        }, ...
    ]
    Ordered by latest T first (id DESC).
    """
    t_docs = [d for d in docs if d.get("type") == "T"]
    t_docs_sorted = sorted(t_docs, key=lambda d: d.get("id") or 0, reverse=True)
    sets: list[dict] = []
    for t in t_docs_sorted:
        chain = db.get_tv_tvr_chain(t["doc_id"])
        sets.append({
            "t_doc_id": t["doc_id"],
            "t_title": t.get("title") or "",
            "t_status": t.get("status") or "",
            "tv": chain.get("tv", []),
            "tvr": chain.get("tvr", []),
        })
    return sets


def close_group(group_id: str, reason_option: str = "", reason_detail: str = "") -> dict:
    """Close the group."""
    group = db.get_group(group_id)
    if group is None:
        return {"status": "error", "message": f"Group not found: {group_id}"}
    current_status = (group.get("status") or "").upper()
    if current_status == "CLOSED":
        return {"status": "error", "message": f"Group is already closed: {group_id}"}
    if current_status == "DISCARDED":
        return {"status": "error", "message": f"Disposed groups cannot be closed: {group_id}"}

    return _apply_group_terminal_action(
        group_id=group_id,
        target_status="CLOSED",
        event_type="group_closed",
        action_label="Group Closed",
        success_message="The group has been closed.",
        reason_option=reason_option,
        reason_detail=reason_detail,
    )


# Group discard is modelled exactly like the final-approval AC document (TR0029.0008
# review #3): a file-less "virtual" documents row records the action. Critically, the
# group's status column is NOT flipped — the previous code wrote "DISCARDED", which is
# not in the groups.status CHECK constraint (OPEN/CLOSED/CANCELLED) and raised
# sqlite3.IntegrityError on first real use (review #1). AC works the same way: it never
# writes a bespoke group status, it creates a file-less doc and derives state from it.
#
# Type code is "DC" (=Discard), per TR0029.0008 review r2 #1: the reviewer directed
# that discard use the code "DC" rather than the invented "GD". This is safe in the
# live registry — "DC" is NOT a seeded/registered document type there (GET
# /help/doc_type lists no DC), so the action-series Discard label below is the only DC
# the live system knows. The dormant design-completion ("Design Completed") DC paths in
# process_service fire only for last-doc/stage computation; the discard DC doc is added
# to GROUP_HISTORY_EXCLUDED_TYPES (like AC) so it is never the last doc and those paths
# never mislabel a discarded group.
_GROUP_DISCARD_TYPE = "DC"  # action-series code for Discard (review r2 #1)


def _ensure_group_discard_type() -> None:
    """Idempotently register the file-less group-discard action doc type (DC=Discard).

    Symmetric to the seeded AC (Approve)/RJ (Reject) action types. Registered lazily so DBs
    that predate this feature get the label without depending on a migration run.
    Any failure is non-fatal — get_type_name falls back to the bare code, so the
    discard itself never breaks because of a label.
    """
    try:
        from .db import templates as _tpl
        if _tpl.get_document_type_by_code(_GROUP_DISCARD_TYPE, "action", None):
            return
        row = _tpl.create_document_type({
            "type_code": _GROUP_DISCARD_TYPE,
            "series": "action",
            "color": "#dc2626",
            "is_system": 1,
            "is_active": 1,
            "sort_order": 30,
            "type_name": "폐기",
            "locale": "ko",
        })
        if row:
            from .db.connection import get_store as _get_store
            store = _get_store()
            for loc, name in (("en", "Discard"), ("ja", "廃棄")):
                try:
                    store._execute(
                        "INSERT INTO document_type_names"
                        " (document_type_id, locale, type_name) VALUES (?, ?, ?)"
                        " ON CONFLICT(document_type_id, locale) DO UPDATE SET"
                        " type_name = excluded.type_name",
                        [row["id"], loc, name],
                    )
                except Exception:
                    pass
        from .db.document_type_labels import invalidate_type_label_cache
        invalidate_type_label_cache()
    except Exception:
        pass


def is_group_disposed(group_id: str | None) -> bool:
    """True when the group has been disposed (a terminal DC discard document exists).

    Single source of truth for the "disposed group" signal — the same file-less DC
    document that the dashboard exclusion (dashboard_service) and the document-detail
    ``group_disposed`` flag (documents.py) key off. Server-side action endpoints call
    this to REJECT any forward workflow action on a document whose group was discarded
    (TR0079.0003 rework): hiding the action bar on the client is UX only and depends on
    SSE arriving, so a stale or un-refreshed client could still fire approve/reject/
    advance — this guard makes a disposed group's documents inert at the source.
    """
    if not group_id:
        return False
    try:
        return any(
            d.get("type_code") == _GROUP_DISCARD_TYPE
            for d in db.get_documents_by_group_id(group_id)
        )
    except Exception:
        # Fail open: a lookup failure must not block legitimate work on live groups.
        return False


def dispose_group(group_id: str, reason_option: str = "", reason_detail: str = "") -> dict:
    """Discard a group by creating a file-less discard document (AC-style).

    Mirrors the final-approval AC flow: a documents row with file_path=None records the
    discard as a first-class workflow artifact (header / tab / history), and NO
    out-of-constraint group status is written. The reason is required by the UI and is
    stored on the group_disposed event. Idempotent: one un-reverted discard per group.
    """
    group = db.get_group(group_id)
    if group is None:
        return {"status": "error", "message": f"Group not found: {group_id}"}

    project = group.get("project_id") or ""
    module = group.get("module") or "none"

    group_docs = db.get_documents_by_group_id(group_id)
    if any(d.get("type_code") == _GROUP_DISCARD_TYPE for d in group_docs):
        return {"status": "error", "message": f"Group is already disposed: {group_id}"}

    _ensure_group_discard_type()

    # Anchor the discard to the group's workflow root (like AC's target_id), when present.
    root_doc = next(
        (d for d in group_docs if d.get("type_code") in WORKFLOW_ROOT_TYPES),
        None,
    )

    doc_code = numbering_service.reserve_document(group_id, _GROUP_DISCARD_TYPE, module=module)
    doc_id = f"{group_id}.{doc_code}"

    reason_value, reason_label = _normalize_group_reason_option(reason_option)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = _build_group_action_note(
        action_label="Group Disposed",
        at=now,
        reason_value=reason_value,
        reason_label=reason_label,
        reason_detail=(reason_detail or "").strip(),
        warning="",
    )

    # NR0003 §6 (atomicity): previously insert_document committed on its own and the
    # group_disposed event was written afterward. When the event failed (the FK bug
    # below) the DC placeholder document was already committed, and the §2080 idempotency guard then
    # refused to retry — leaving a half-disposed "zombie" group. Wrap the document
    # creation, review-status reset, and the event in ONE transaction (re-entrant: the
    # inner insert_document/insert_group_event reuse this txn) so a failure rolls back
    # the DC document and the discard can be retried cleanly.
    from .db.connection import get_store as _get_store
    with _get_store().transaction() as _s:
        db.insert_document(
            doc_id=doc_id,
            doc_type=_GROUP_DISCARD_TYPE,
            project=project,
            module=module,
            title="Group Discard",
            group_id=group_id,
            target_id=(root_doc or {}).get("doc_id"),
            status="closed",      # terminal: the discard action is complete (no review step)
            direction="outbox",
            file_path=None,       # file-less by design (placeholder document)
        )
        # The discard record is a terminal action, never a review target: clear any
        # review-status default so the header/info panel cannot render "awaiting review"
        # (review r2 #3) and the review action bar stays empty (review r2 #4).
        try:
            _s._execute(
                "UPDATE documents SET doc_review_status=NULL, review_required=0 WHERE doc_id=?",
                [doc_id],
            )
        except Exception:
            pass
        # B0001 fix: the group-level event belongs in group_events (FK → groups), NOT the
        # document-scoped events table (events.doc_id FK → documents). Writing it with
        # doc_id=group_id raised FOREIGN KEY constraint failed → 500. See migration 048.
        db.insert_group_event(group_id, "group_disposed", reason=reason_value, note=note)

    return {
        "status": "success",
        "group_id": group_id,
        # project is returned so the dispose route can scope its SSE refresh broadcast
        # to the disposed group's project (TR0079.0003 — SSE-driven action-bar refresh).
        "project": project,
        "doc_id": doc_id,
        "discarded_at": now,
        "message": "The group has been disposed.",
    }


def create_group(
    project_id: str,
    title: str,
    module: str = "none",
    parent_id: str | None = None,
    priority: str | None = None,
) -> dict:
    """Create a group and return {group_id, created_at}."""
    project_id = (project_id or "").strip()
    title = (title or "").strip()
    module = (module or "none").strip() or "none"

    if not project_id:
        return {"status": "error", "message": "project_id is required."}
    if not title:
        return {"status": "error", "message": "title is required."}

    if parent_id:
        parent = db.get_group(parent_id)
        if parent is None:
            return {"status": "error", "message": f"Parent group not found: {parent_id}"}

    group_code = numbering_service.reserve_group(project_id, module)
    group_id = f"{project_id}.{module}.{group_code}"
    now = db.now_iso()
    group = db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": module,
        "parent_id": parent_id,
        "title": title,
        "priority": priority,
        "status": "draft",
        "created_at": now,
        "updated_at": now,
    })
    return {
        "status": "success",
        "group_id": group["group_id"],
        "created_at": group["created_at"],
    }


def update_group(
    group_id: str,
    title: str | None = None,
    priority: str | None = None,
    module: str | None = None,
) -> dict:
    """Update group information (title/priority/module) and return {group_id, updated_at}."""
    group = db.get_group(group_id)
    if group is None:
        return {"status": "error", "message": f"Group not found: {group_id}"}

    updates: dict = {}
    if title is not None:
        title = title.strip()
        if not title:
            return {"status": "error", "message": "title cannot be blank."}
        updates["title"] = title
    if priority is not None:
        updates["priority"] = priority
    if module is not None:
        updates["module"] = module

    if not updates:
        return {
            "status": "success",
            "group_id": group_id,
            "updated_at": group.get("updated_at", ""),
        }

    updated = db_groups.update(group_id, updates)
    return {
        "status": "success",
        "group_id": group_id,
        "updated_at": updated["updated_at"],
    }


def _apply_group_terminal_action(
    *,
    group_id: str,
    target_status: str,
    event_type: str,
    action_label: str,
    success_message: str,
    reason_option: str,
    reason_detail: str,
) -> dict:
    """Handle group close/discard actions in a shared path."""
    docs = db.get_documents_by_group_id(group_id)
    can_close, close_reason = _can_close_group(group_id, docs)
    warning = ""
    if not can_close and close_reason:
        warning = f"Pending items note: {close_reason}"

    ok, msg = db.update_group_status(group_id, target_status)
    if not ok:
        return {"status": "error", "message": msg}

    reason_value, reason_label = _normalize_group_reason_option(reason_option)
    detail_text = (reason_detail or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = _build_group_action_note(
        action_label=action_label,
        at=now,
        reason_value=reason_value,
        reason_label=reason_label,
        reason_detail=detail_text,
        warning=warning,
    )
    # B0001 fix: group_closed is a group-level event → group_events (FK → groups). The
    # close path has no carrier document to anchor to, which is exactly why anchoring
    # group events to documents was never viable; the dedicated channel resolves close
    # and dispose consistently. See NR0003 / migration 048.
    db.insert_group_event(
        group_id,
        event_type,
        reason=reason_value,
        note=note,
    )

    return {
        "status": "success",
        "group_id": group_id,
        "group_status": target_status,
        "closed_at": now,
        "message": success_message,
        "warning": warning,
    }


# ── Module list queries ───────────────────────────────────────────────────────

def get_modules_for_project(project: str) -> list[str]:
    """Return the list of modules belonging to the project."""
    allowed = db.get_allowed_projects()
    modules: list[str] = []
    for row in allowed:
        if row.get("project") == project:
            m = (row.get("module") or "").strip()
            if m:
                modules.append(m)
    return sorted(set(modules))


def get_projects_with_modules() -> list[dict]:
    """Build the module list by project."""
    allowed = db.get_allowed_projects()
    project_map: dict[str, list[str]] = {}
    for row in allowed:
        p = row.get("project", "")
        m = (row.get("module") or "").strip()
        if p:
            project_map.setdefault(p, [])
            if m:
                project_map[p].append(m)
    return [
        {"project": p, "modules": sorted(set(ms))}
        for p, ms in sorted(project_map.items())
    ]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert a title into a filename-safe form."""
    text = text.strip().lower()
    text = re.sub(r"[\s-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:50] or "untitled"


def _resolve_storage_path(file_path: str) -> str | None:
    """Resolve a stored path (relative / '/storage/...' / legacy absolute) to a real OS path.

    Delegates to the unified storage.paths.resolve_storage_path() (L0054.0002 §4),
    which returns only existing files inside the storage jail. The sole caller
    (build_clipboard_text) re-checks existence, so a None for a missing file is
    behaviour-equivalent to the previous best-effort normpath.
    """
    if not file_path:
        return None
    resolved = storage_paths.resolve_storage_path(file_path)
    return str(resolved) if resolved is not None else None


def _find_file_in_reject(filename: str) -> str | None:
    """Search the reject/ directory for the original filename (or transferred prefixed file)."""
    return _find_file_in_bucket_tree(db.REJECT_DIR, filename)


def _find_file_in_accept(filename: str) -> str | None:
    """Search the accept/ directory for the original filename (or transferred prefixed file)."""
    return _find_file_in_bucket_tree(db.ACCEPT_DIR, filename)


def _find_file_in_bucket_tree(base_dir: str, filename: str) -> str | None:
    """Search the transfer-bucket tree for the original filename (or prefixed file)."""
    if not filename:
        return None
    suffix = f"_{filename}"
    for root, _, files in os.walk(base_dir):
        if filename in files:
            return os.path.join(root, filename)
        for name in files:
            if name.endswith(suffix):
                return os.path.join(root, name)
    return None


def _get_reject_reason(doc_id: str) -> str:
    """Extract the rejection reason from a rejection event."""
    events = db.get_events_by_doc_id(doc_id)
    for event in events:
        if event.get("event_type") == "rejected" and event.get("reason"):
            return event["reason"]
    return ""


def _get_doc_memo_file(doc: dict) -> str | None:
    """Look up the memo filename for the document.

    For A type, also check the 'answer_generated' event (T032).
    """
    memo = db.get_created_memo_file(doc["doc_id"])
    if memo:
        return memo
    if doc.get("type") == "A":
        for ev in db.get_events_by_doc_id(doc["doc_id"]):
            if ev.get("event_type") == "answer_generated" and ev.get("memo_file"):
                return ev["memo_file"]
    return None


def _strip_status_header_inplace(file_path: str) -> None:
    """Remove the status line from the YAML header before re-inserting."""
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines or lines[0].strip() != "---":
        return

    closing_index = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_index = idx
            break
    if closing_index == -1:
        return

    header_lines = lines[1:closing_index]
    filtered_lines = [
        line for line in header_lines
        if not re.match(r"^\s*status\s*:", line)
    ]
    if len(filtered_lines) == len(header_lines):
        return

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines([lines[0], *filtered_lines, *lines[closing_index:]])


def _build_status_label(doc_status: str, location_key: str) -> str:
    """Create the status label displayed in the group detail UI."""
    status = (doc_status or "").strip().lower()
    if status == "accepted":
        return "Accepted"
    if status == "rejected":
        return "Rejected"
    if status == "cancelled":
        return "Cancelled"
    if status == "closed":
        return "Closed"
    if status in ("open", "active"):
        return "Open" if location_key == "inbox" else "Open (In Progress)"
    if not status:
        return "Unknown"
    return status


def _build_next_action_label(doc_type: str, doc_status: str, locale: str = "en") -> str:
    """Create the next-action label displayed in the group detail UI."""
    status = (doc_status or "").strip().lower()
    if status == "rejected":
        return "Review the rejection reason, then resume or rework"
    if status == "cancelled":
        return "Resubmit/discard/direct edit, then continue follow-up work"
    if status in ("accepted", "closed"):
        return "Proceed to the next step or confirm closure"
    if status not in ("open", "active"):
        return "Status check required"
    if doc_type == "Q":
        return "Draft Response"
    if doc_type in REVIEW_REQUEST_TYPES:
        return "Create a review request or approve/reject"
    if doc_type in APPROVABLE_TYPES:
        return "Process approval/rejection"
    return "Awaiting Next Action"


def _find_file_location_in_any_bucket(filename: str) -> tuple[str | None, str]:
    """Find the file and its location in inbox/processed/outbox/accept/reject/cancelled."""
    if not filename:
        return None, "missing"

    direct_buckets = [
        ("inbox", db.INBOX_DIR),
        ("processed", db.PROCESSED_DIR),
        ("outbox", db.OUTBOX_DIR),
        ("cancelled", db.CANCELLED_DIR),
        ("accept", db.ACCEPT_DIR),
        ("reject", db.REJECT_DIR),
    ]
    for bucket, folder in direct_buckets:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path, bucket

    suffix = f"_{filename}"
    for root, _, files in os.walk(db.ACCEPT_DIR):
        for name in files:
            if name == filename or name.endswith(suffix):
                return os.path.join(root, name), "accept"
    for root, _, files in os.walk(db.REJECT_DIR):
        for name in files:
            if name == filename or name.endswith(suffix):
                return os.path.join(root, name), "reject"
    for root, _, files in os.walk(db.CANCELLED_DIR):
        for name in files:
            if name == filename or name.endswith(suffix):
                return os.path.join(root, name), "cancelled"

    return None, "missing"


def _find_file_in_any_bucket(filename: str) -> str | None:
    """Find the file in inbox/processed/outbox/accept/reject and return the real path."""
    path, _ = _find_file_location_in_any_bucket(filename)
    return path


def _read_file_from_any_bucket(filename: str) -> str | None:
    """Find and read the file in inbox/processed/outbox/accept."""
    path = _find_file_in_any_bucket(filename)
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def _split_header_and_body(content: str) -> tuple[dict | None, str]:
    header, _ = linter.parse_yaml_header(content)
    stripped = content.strip()
    if not stripped.startswith("---"):
        return header, stripped
    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        return header, stripped
    body = stripped[end_idx + 3:].strip()
    return header, body


def _parse_questions(content: str) -> list[str]:
    """Extract questions from '### Q' sections in a Q document body."""
    _, body = _split_header_and_body(content)
    questions: list[str] = []
    current_lines: list[str] = []
    in_q_section = False
    for line in body.split("\n"):
        if line.strip() == "### Q":
            if in_q_section:
                q_text = "\n".join(current_lines).strip()
                if q_text:
                    questions.append(q_text)
            current_lines = []
            in_q_section = True
        elif line.strip() == "### A" and in_q_section:
            q_text = "\n".join(current_lines).strip()
            if q_text:
                questions.append(q_text)
            current_lines = []
            in_q_section = False
        elif in_q_section:
            current_lines.append(line)
    if in_q_section:
        q_text = "\n".join(current_lines).strip()
        if q_text:
            questions.append(q_text)
    return questions


def _group_documents_with_actions(docs: list[dict], is_inbox: bool, locale: str = "en") -> dict:
    """Group the document list by group_id and assign actions by type."""
    group_map: dict[str, dict] = {}
    for d in docs:
        gid = d.get("group_id") or "(ungrouped)"
        if gid not in group_map:
            group_obj = db.get_group(gid) if gid != "(ungrouped)" else None
            group_map[gid] = {
                "group_id": gid,
                "group_status": (group_obj or {}).get("status", ""),
                "documents": [],
            }

        doc_type = d.get("type", "")
        memo_file = db.get_created_memo_file(d["doc_id"])
        resolved_file_path = _find_file_in_any_bucket(memo_file) if memo_file else None

        item = {
            "doc_id": d["id"],
            "doc_id_str": d["doc_id"],
            "type": doc_type,
            "type_label": get_type_name(doc_type, locale),
            "title": d.get("title", ""),
            "priority": d.get("priority", ""),
            "status": d.get("status", ""),
            "next": d.get("next", ""),
            "created_at": d.get("created_at", ""),
            "file_path": (
                os.path.abspath(resolved_file_path)
                if resolved_file_path else ""
            ),
        }

        if is_inbox:
            item["actions"] = TYPE_ACTIONS.get(doc_type, [])

        group_map[gid]["documents"].append(item)

    return {"groups": list(group_map.values())}


def _normalize_group_reason_option(reason_option: str) -> tuple[str, str]:
    """Normalize the option value for group close/discard reasons."""
    option_map = {
        item["value"]: item["label"]
        for item in GROUP_ACTION_REASON_OPTIONS
    }
    key = (reason_option or "").strip()
    if key in option_map:
        return key, option_map[key]
    return "other", option_map["other"]


def _get_group_action_reason_label(reason_value: str) -> str:
    """Convert the group close/discard reason value into a display label."""
    return _normalize_group_reason_option(reason_value)[1] if reason_value else ""


def _build_group_event_label(event_type: str) -> str:
    """Return the display label for a group event type."""
    return {
        "group_closed": "Group Closed",
        "group_disposed": "Group Disposed",
    }.get(event_type, event_type)


def _build_group_action_note(
    *,
    action_label: str,
    at: str,
    reason_value: str,
    reason_label: str,
    reason_detail: str,
    warning: str,
) -> str:
    """Build the note string for a group close/discard event."""
    lines = [
        f"{action_label}: {at}",
        f"Reason option: {reason_label} ({reason_value})",
    ]
    if reason_detail:
        lines.append(f"Detailed reason: {reason_detail}")
    if warning:
        lines.append(f"Note: {warning}")
    return "\n".join(lines)


def _can_close_group(group_id: str, docs: list[dict]) -> tuple[bool, str]:
    """Determine whether the group can be closed (L-002 §3)."""
    tr_docs = [d for d in docs if d.get("type") == "TR"]
    if not tr_docs:
        return False, "There are no TR documents in the group"

    for tr in tr_docs:
        status = tr.get("status", "")
        if status not in ("accepted", "closed", "rejected"):
            return False, f"There is an incomplete TR: {tr.get('doc_id', '')}"

    return True, ""


def _read_memo_header(path: str) -> dict[str, Any] | None:
    """Read the YAML header of a memo file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    header, _ = linter.parse_yaml_header(content)
    return header


def _get_next_ac_sequence(project: str, module: str) -> int:
    """Calculate the next AC sequence number, including AC items not yet recorded in the DB."""
    max_seq = db.get_next_number(project, module, "AC") - 1
    filename_pattern = re.compile(r"^AC\d+_.*\.md$", re.IGNORECASE)

    for base_dir in (db.OUTBOX_DIR, db.ACCEPT_DIR):
        if not os.path.exists(base_dir):
            continue
        for root, _, files in os.walk(base_dir):
            for name in files:
                if not filename_pattern.match(name):
                    continue

                header = _read_memo_header(os.path.join(root, name))
                if not header or str(header.get("type") or "").strip() != "AC":
                    continue
                if str(header.get("project") or "").strip() != project:
                    continue
                if str(header.get("module") or "").strip() != module:
                    continue

                match = re.fullmatch(r"AC(\d+)", str(header.get("doc_number") or "").strip())
                if match:
                    max_seq = max(max_seq, int(match.group(1)))

    return max_seq + 1


def _render_design_template_section(
    next_type: str, req_locale: str, r: dict[str, Any]
) -> list[str]:
    """Render the design-template body section (group 0024 / L0013 §2-4).

    Embeds the resolved body — NEVER a file path (AC-1). Badges (global source /
    ko fallback) accumulate independently (L0013 §2-4 review #3). Delegates to
    template_provision.render_provision_block so the worker mention and this
    self-contained path share one renderer (single source of truth).
    """
    return ["", template_provision.render_provision_block(next_type, req_locale, r)]


def _build_self_contained_sections(
    group_id: str, project: str, module: str, priority: str,
    source_doc_id: str, next_type: str | None, locale: str = "ko",
) -> list[str]:
    """Create the self-contained sections to append to the bottom of AC/RJ documents (T014)."""
    lines: list[str] = []

    # 1. Group reference document list
    lines.extend([
        "",
        f"## Reference Documents (Group: {group_id})",
        "",
        "> Paths may change when files are moved.",
        "",
        "| doc_id | type | title | status | path |",
        "|--------|------|-------|--------|------|",
    ])
    docs = db.get_documents_by_group_id(group_id)
    for d in docs:
        doc_doc_id = d["doc_id"]
        doc_type = d["type"]
        doc_title = d.get("title", "")
        doc_status = d.get("status", "")
        memo_file = db.get_created_memo_file(doc_doc_id)
        file_path = ""
        if memo_file:
            resolved = _find_file_in_any_bucket(memo_file)
            if resolved:
                file_path = os.path.abspath(resolved)
        lines.append(f"| {doc_doc_id} | {doc_type} | {doc_title} | {doc_status} | {file_path} |")

    # 2. Next-step writing guide
    #    Design types (D/P/L/DB) embed the DB-held template BODY in the writer's
    #    locale (group 0024 — replaces the As-Is `Template: <abs path>` line, AC-1).
    #    Non-design mapped types (N/NR/T/TR) keep the legacy file-path pointer
    #    (out of this feature's scope — D0010 §3-1 / L0013 DEFERRED).
    is_design_next = bool(next_type) and template_provision.is_design_type(next_type)
    is_legacy_mapped = bool(next_type) and next_type in NEXT_TYPE_TEMPLATE_MAP
    if is_design_next or is_legacy_mapped:
        seq_placeholder = "{seq}"
        lines.extend(["", "## Next Step", "", f"type: {next_type}"])

        if is_design_next:
            req_locale = template_provision.normalize_locale(locale)
            r = template_provision.resolve_active_template(project, next_type, req_locale)  # type: ignore[arg-type]
            lines.extend(_render_design_template_section(next_type, req_locale, r))  # type: ignore[arg-type]
        else:
            template_name = NEXT_TYPE_TEMPLATE_MAP[next_type]  # type: ignore[index]
            template_path = ""
            for d in _RULE_TEMPLATE_DIRS:
                candidate = os.path.join(d, template_name)
                if os.path.isfile(candidate):
                    template_path = os.path.abspath(candidate)
                    break
            if not template_path:
                template_path = os.path.join(_RULE_TEMPLATE_DIRS[0], template_name)
            lines.append(f"Template: {template_path}")

        lines.extend([
            "",
            "```yaml",
            "---",
            f"type: {next_type}",
            f"doc_id: {project}-{module}-{next_type}{seq_placeholder}",
            f"project: {project}",
            f"module: {module}",
            f"group_id: {group_id}",
            f"ref: {source_doc_id}",
            "title:",
            f"priority: {priority}",
            "---",
            "```",
        ])

    # 3. Q writing guidance
    lines.extend([
        "",
        "## If you're unsure, write a Q",
        "",
        "If the next direction is unclear, do not guess—write a Q.",
        "",
        "```yaml",
        "---",
        "type: Q",
        f"doc_id: {project}-{module}-Q{{seq}}",
        f"project: {project}",
        f"module: {module}",
        f"group_id: {group_id}",
        f"ref: {source_doc_id}",
        "title:",
        "---",
        "```",
    ])

    return lines


def _create_ac_file(
    group_id: str, project: str, module: str, priority: str,
    source_doc_id: str,
    source_filename: str, source_type: str,
    locale: str = "en",
) -> dict:
    """Create an AC (approval notice) file in OutBox."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    target_id_val = source_doc_id
    title = f"{get_type_name(source_type, locale)} Approval"
    source_slug = _slugify(os.path.splitext(source_filename)[0]) if source_filename else _slugify(title)

    seq = _get_next_ac_sequence(project, module)
    doc_number = f"AC{seq:03d}"
    filename = f"AC{seq:03d}_{source_slug}.md"

    body_lines = [
        "# Approval Document",
        "",
        "## Approved Document Information",
        "",
        f"- Document ID: {source_doc_id}",
        f"- Document Type: {get_type_name(source_type, locale)} ({source_type})",
        f"- Original File: {source_filename or '-'}",
        "",
        "## Approver Information",
        "",
        "- Approver: FlowGate system",
        "- Approval Result: Approved",
        "",
        "## Timestamp",
        "",
        f"- Approved At: {now_str}",
    ]

    header_lines = [
        "---",
        f"group_id: {group_id}",
        "type: AC",
        f"doc_number: {doc_number}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        f"target_id: {target_id_val}",
        "next:",
        "---",
        "",
    ]
    content = "\n".join(header_lines) + "\n".join(body_lines) + "\n"

    os.makedirs(db.OUTBOX_DIR, exist_ok=True)
    filepath = os.path.join(db.OUTBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return {"file_path": os.path.abspath(filepath), "filename": filename}


def _create_rj_file(
    group_id: str, project: str, module: str, priority: str,
    source_doc_id: str,
    source_filename: str, source_type: str,
    reject_reason: str, migration_results: list[dict],
    next_type: str | None = None,
    locale: str = "en",
) -> dict:
    """Create an RJ (rejection notice) file in OutBox."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    target_id_val = source_doc_id
    title = f"{get_type_name(source_type, locale)} Rejection"
    source_slug = _slugify(os.path.splitext(source_filename)[0]) if source_filename else _slugify(title)

    doc_code = numbering_service.reserve_document(
        group_id, "RJ", module=module or "none"
    )
    doc_number = doc_code
    filename = f"{doc_code}_{source_slug}.md"

    body_lines = [
        "## Rejection Details",
        "",
        f"- Rejected Target: {source_filename}",
        f"- Rejected At: {now_str}",
        "- Decision: Rejected",
        "",
        "## Reason for Rejection",
        "",
        reject_reason,
    ]

    if migration_results:
        body_lines.extend(["", "## Transfer Results", "", "| Original File | Transferred To |", "|---|---|"])
        for m in migration_results:
            body_lines.append(f"| {m.get('original', '')} | {m.get('migrated_to', '')} |")

    body_lines.extend([
        "",
        "## Notes",
        "",
        "Please deliver this rejection notice to the PL/sub-leader.",
        "Put a new proposal in InBox, or write a Q if anything is unclear.",
    ])

    # Add self-contained sections (T014)
    body_lines.extend(_build_self_contained_sections(
        group_id, project, module, priority, source_doc_id, next_type, locale,
    ))

    header_lines = [
        "---",
        f"group_id: {group_id}",
        "type: RJ",
        f"doc_number: {doc_number}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        f"target_id: {target_id_val}",
        "next:",
        "---",
        "",
    ]
    content = "\n".join(header_lines) + "\n".join(body_lines) + "\n"

    os.makedirs(db.OUTBOX_DIR, exist_ok=True)
    filepath = os.path.join(db.OUTBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    doc_id = f"{group_id}-{doc_code}"
    try:
        db.insert_document(
            doc_id=doc_id, doc_type="RJ", project=project, module=module,
            title=title, group_id=group_id, target_id=target_id_val,
            priority=priority, status="draft", direction="outbox",
        )
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise
    db.insert_event(doc_id, "created", memo_file=filename, reason=reject_reason)

    return {"file_path": os.path.abspath(filepath), "filename": filename}


# ── File transfer ─────────────────────────────────────────────────────────────

def _migrate_single_file(
    group_id: str, filename: str, doc_type: str,
    source_dir: str, dest_base: str,
) -> dict | None:
    """Transfer a single file to the accept/reject directory."""
    parsed = linter.parse_group_id(group_id) if group_id else None
    if not parsed:
        return None

    project = parsed["project"]
    module = parsed["module"]
    dest_dir = os.path.join(dest_base, project, module, doc_type)
    os.makedirs(dest_dir, exist_ok=True)

    seq = _get_migration_type_seq(group_id, doc_type, dest_dir)
    prefixed = _build_migration_filename(group_id, module, doc_type, seq, filename)
    final_name = _resolve_prefix_filename(dest_dir, prefixed)

    src_path = os.path.join(source_dir, filename)
    if os.path.exists(src_path):
        shutil.move(src_path, os.path.join(dest_dir, final_name))

    return {
        "original": filename,
        "migrated_to": os.path.abspath(os.path.join(dest_dir, final_name)),
    }


def _batch_migrate(
    group_id: str, target_files: list[dict],
    dest_base: str, action: str,
) -> dict:
    """Bulk transfer (L-002 §2-5 transaction)."""
    results: list[dict] = []
    rollback_stack: list[dict] = []

    try:
        for file_info in target_files:
            filename = file_info["filename"]
            doc_type = file_info["doc_type"]
            source_dir = file_info["source_dir"]

            parsed = linter.parse_group_id(group_id)
            if not parsed:
                continue

            project = parsed["project"]
            module = parsed["module"]
            dest_dir = os.path.join(dest_base, project, module, doc_type)
            os.makedirs(dest_dir, exist_ok=True)

            seq = _get_migration_type_seq(group_id, doc_type, dest_dir)
            prefixed = _build_migration_filename(group_id, module, doc_type, seq, filename)
            final_name = _resolve_prefix_filename(dest_dir, prefixed)

            src_path = os.path.join(source_dir, filename)
            dst_path = os.path.join(dest_dir, final_name)
            if os.path.exists(src_path):
                shutil.move(src_path, dst_path)
                rollback_stack.append({"src": dst_path, "dst": src_path})

            results.append({
                "original": filename,
                "migrated_to": os.path.abspath(dst_path),
                "status": action,
            })

        return {"success": True, "results": results}

    except Exception as e:
        # Roll back
        for rb in reversed(rollback_stack):
            try:
                if os.path.exists(rb["src"]):
                    shutil.move(rb["src"], rb["dst"])
            except Exception:
                pass
        return {"success": False, "error": str(e), "results": results}


# ── DC bundle approval helpers ────────────────────────────────────────────────

def _read_dc_file_content(filename: str) -> str | None:
    """Read DC file contents from inbox or processed."""
    for directory in [db.INBOX_DIR, db.PROCESSED_DIR]:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return None


def _normalize_approved_files(raw_files: Any) -> list[str]:
    """Normalize the approved_files header value into a filename list for display/approval."""
    if not isinstance(raw_files, list):
        return []

    normalized: list[str] = []
    for item in raw_files:
        filename = str(item).strip()
        if filename:
            normalized.append(filename)
    return normalized


def _collect_dc_artifact_memo_file_candidates(group_id: str) -> dict[str, list[str]]:
    """Collect candidate memo_file -> doc_id mappings for design artifacts within group_id."""
    memo_file_to_doc_ids: dict[str, list[str]] = {}
    for group_doc in db.get_documents_by_group_id(group_id):
        if group_doc.get("type") not in _DC_ARTIFACT_TYPES:
            continue
        memo_file = _get_doc_memo_file(group_doc)
        doc_id = (group_doc.get("doc_id") or "").strip()
        if not memo_file or not doc_id:
            continue
        doc_ids = memo_file_to_doc_ids.setdefault(memo_file, [])
        if doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return memo_file_to_doc_ids


def _build_dc_artifact_memo_file_map(group_id: str) -> dict[str, str]:
    """Build a single memo_file -> doc_id mapping for design artifacts within group_id."""
    memo_file_to_doc_ids = _collect_dc_artifact_memo_file_candidates(group_id)

    ambiguous_items = {
        memo_file: doc_ids
        for memo_file, doc_ids in memo_file_to_doc_ids.items()
        if len(doc_ids) > 1
    }
    if ambiguous_items:
        details = ", ".join(
            f"{memo_file} -> {', '.join(doc_ids)}"
            for memo_file, doc_ids in sorted(ambiguous_items.items())
        )
        raise ValueError(f"doc_id mapping is ambiguous: {details}")

    return {
        memo_file: doc_ids[0]
        for memo_file, doc_ids in memo_file_to_doc_ids.items()
        if doc_ids
    }


def _make_dc_file_item(
    *,
    doc_id: str,
    file_name: str,
    reason: str = "",
    source: str,
) -> dict[str, str]:
    """Build the common item schema used to display DC approval targets."""
    return {
        "doc_id": doc_id,
        "file_name": file_name,
        "reason": reason,
        "source": source,
    }


def _build_dc_file_items(
    file_names: list[str],
    memo_file_map: dict[str, str],
    *,
    source: str,
    reason: str = "",
) -> list[dict[str, str]]:
    """Convert a filename list into a structure for displaying doc_id + file_name."""
    items: list[dict[str, str]] = []
    missing_files: list[str] = []
    for file_name in file_names:
        doc_id = memo_file_map.get(file_name)
        if not doc_id:
            missing_files.append(file_name)
            continue
        items.append(_make_dc_file_item(
            doc_id=doc_id,
            file_name=file_name,
            reason=reason,
            source=source,
        ))

    if missing_files:
        missing_text = ", ".join(missing_files)
        raise ValueError(f"Could not find a doc_id mapping: {missing_text}")
    return items


def _collect_approved_file_validation_issues(
    approved_files: list[str],
    group_id: str,
) -> list[dict[str, str]]:
    """Collect approved_files validation results into the error-item structure."""
    if not approved_files:
        return [{
            "file_name": "",
            "reason": "approved_files must specify at least one file",
            "message": "approved_files must specify at least one file",
        }]

    issues: list[dict[str, str]] = []
    group_docs = db.get_documents_by_group_id(group_id)
    group_memo_files = {
        db.get_created_memo_file(d["doc_id"])
        for d in group_docs
        if db.get_created_memo_file(d["doc_id"])
    }

    for filename in approved_files:
        if filename not in group_memo_files:
            issues.append({
                "file_name": filename,
                "reason": "No document exists in the group",
                "message": f"A file listed in approved_files does not exist: {filename}",
            })
            continue
        if _locate_file(filename) is None:
            issues.append({
                "file_name": filename,
                "reason": "File not found",
                "message": f"Could not find a file listed in approved_files: {filename}",
            })

    return issues


def _resolve_dc_error_doc_id(
    memo_file_candidates: dict[str, list[str]],
    file_name: str,
) -> tuple[str, str]:
    """Safely interpret doc_id for error display."""
    doc_ids = memo_file_candidates.get(file_name) or []
    if len(doc_ids) == 1:
        return doc_ids[0], ""
    if len(doc_ids) > 1:
        return "", "Ambiguous doc_id mapping"
    return "", ""


def _build_dc_error_file_items(
    group_id: str,
    *,
    issues: list[dict[str, str]] | None = None,
    mismatch_files: list[str] | None = None,
) -> list[dict[str, str]]:
    """Convert C4/C5 errors into the common item schema."""
    memo_file_candidates = _collect_dc_artifact_memo_file_candidates(group_id)
    items: list[dict[str, str]] = []

    for issue in issues or []:
        file_name = (issue.get("file_name") or "").strip()
        if not file_name:
            continue
        doc_id, doc_id_reason = _resolve_dc_error_doc_id(memo_file_candidates, file_name)
        reason = (issue.get("reason") or "").strip()
        if doc_id_reason:
            reason = f"{reason} / {doc_id_reason}" if reason else doc_id_reason
        items.append(_make_dc_file_item(
            doc_id=doc_id,
            file_name=file_name,
            reason=reason,
            source="error",
        ))

    for file_name in mismatch_files or []:
        normalized_name = str(file_name).strip()
        if not normalized_name:
            continue
        doc_id, doc_id_reason = _resolve_dc_error_doc_id(
            memo_file_candidates,
            normalized_name,
        )
        reason = "Not eligible for DS linkage"
        if doc_id_reason:
            reason = f"{reason} / {doc_id_reason}"
        items.append(_make_dc_file_item(
            doc_id=doc_id,
            file_name=normalized_name,
            reason=reason,
            source="error",
        ))

    return items


def _build_dc_item_payload(
    group_id: str,
    approved_files: list[str],
    extra_files: list[str] | None = None,
) -> dict[str, Any]:
    """Build the common item payload for DC approval preview/extra_found."""
    normalized_extra_files = list(extra_files or [])
    memo_file_map = _build_dc_artifact_memo_file_map(group_id)
    approved_file_items = _build_dc_file_items(
        approved_files,
        memo_file_map,
        source="yaml",
    )
    extra_file_items = _build_dc_file_items(
        normalized_extra_files,
        memo_file_map,
        source="ds_scan",
    )
    return {
        "approved_files": list(approved_files),
        "approved_file_items": approved_file_items,
        "extra_files": normalized_extra_files,
        "extra_file_items": extra_file_items,
        "bundle_files": list(approved_files) + normalized_extra_files,
        "bundle_file_items": list(approved_file_items) + list(extra_file_items),
    }


def _build_dc_approval_preview(doc: dict) -> dict[str, Any]:
    """Build the approved_files preview data used by the DC approval panel."""
    preview: dict[str, Any] = {
        "approved_files": [],
        "approved_file_items": [],
        "bundle_files": [],
        "bundle_file_items": [],
        "extra_files": [],
        "extra_file_items": [],
        "preview_error": "",
    }
    if (doc.get("type") or "") != "DC":
        return preview

    source_filename = db.get_created_memo_file(doc["doc_id"]) or ""
    if not source_filename:
        preview["preview_error"] = "DC file not found."
        return preview

    source_content = _read_dc_file_content(source_filename)
    if source_content is None:
        preview["preview_error"] = "Unable to read the DC file."
        return preview

    header, parse_err = linter.parse_yaml_header(source_content)
    if parse_err or header is None:
        preview["preview_error"] = f"Failed to parse the DC header: {parse_err}"
        return preview

    group_id = (doc.get("group_id") or "").strip()
    target_id_dc = (header.get("target_id") or "").strip()
    approved_files = _normalize_approved_files(header.get("approved_files") or [])
    try:
        preview.update(_build_dc_item_payload(group_id, approved_files))
    except ValueError as exc:
        preview["preview_error"] = str(exc)
        return preview

    val_errors = validate_approved_files(approved_files, group_id, target_id_dc)
    if val_errors:
        preview["preview_error"] = "; ".join(val_errors)
        return preview

    bundle_files, extra_files = resolve_bundle_targets(
        approved_files, target_id_dc, group_id
    )
    if not bundle_files:
        preview["preview_error"] = "Unable to find bundle target files"
        return preview

    try:
        preview.update(_build_dc_item_payload(group_id, approved_files, extra_files))
    except ValueError as exc:
        preview["preview_error"] = str(exc)
        return preview
    return preview


def validate_approved_files(
    approved_files: list[str],
    group_id: str,
    target_id: str,
) -> list[str]:
    """Validate approved_files. Return the list of error messages."""
    _ = target_id
    issues = _collect_approved_file_validation_issues(approved_files, group_id)
    return [issue["message"] for issue in issues]


def _collect_dc_ds_linked_files(target_id: str) -> list[str]:
    """Return the memo_file list of accepted design artifacts linked to the DS target."""
    if not target_id:
        return []

    ds_linked_docs = db.get_documents_by_target_id(
        target_id, types=("D", "P", "L", "DB"), statuses=("accepted",)
    )
    ds_linked_files = [
        db.get_created_memo_file(d["doc_id"])
        for d in ds_linked_docs
    ]
    return [f for f in ds_linked_files if f]


def _collect_dc_bundle_mismatch_files(
    approved_files: list[str],
    target_id: str,
) -> list[str]:
    """Return the list of files in approved_files that are not linked DS targets."""
    ds_linked_set = set(_collect_dc_ds_linked_files(target_id))
    return [filename for filename in approved_files if filename not in ds_linked_set]


def resolve_bundle_targets(
    approved_files: list[str],
    target_id: str,
    group_id: str,
) -> tuple[list[str], list[str]]:
    """Return the list of files targeted for bundle approval and the list of additionally discovered files.

    Returns:
        (bundle_files, extra_files)
        bundle_files: combined list of approved_files + extra_files
        extra_files: files additionally discovered through DS search (C3 case)
        If bundle_files is empty, it is a C5 error case (handled by the caller)
    """
    _ = group_id
    ds_linked_files = _collect_dc_ds_linked_files(target_id)

    ds_linked_set = set(ds_linked_files)

    # If an explicitly listed file is not found in the DS search result, it is a C5 case
    for filename in approved_files:
        if filename not in ds_linked_set:
            return [], []

    extra_files = [f for f in ds_linked_files if f not in set(approved_files)]
    bundle_files = list(approved_files) + extra_files
    return bundle_files, extra_files


# ── DC artifact bundle approval/rejection ─────────────────────────────────────

_DC_ARTIFACT_TYPES = {"D", "DB", "P", "L"}


def _dc_approve_artifacts(
    group_id: str,
    dc_doc_id: str,
    bundle_files: list[str],
) -> dict:
    """Bulk-approve the artifacts included in bundle_files during DC approval.

    Roll back everything if transfer fails.
    """
    bundle_set = set(bundle_files)
    docs = db.get_documents_by_group_id(group_id)
    migration_results: list[dict] = []
    rollback_stack: list[dict] = []  # (doc_pk, prev_status, src_path, dst_path)

    try:
        for d in docs:
            if d["type"] not in _DC_ARTIFACT_TYPES:
                continue

            memo_file = db.get_created_memo_file(d["doc_id"])
            if not memo_file:
                continue
            if memo_file not in bundle_set:
                continue

            source_dir = _locate_file(memo_file)
            if not source_dir:
                continue

            prev_status = d.get("status", "")
            parsed = linter.parse_group_id(group_id)
            if not parsed:
                continue

            proj = parsed["project"]
            mod = parsed["module"]
            dest_dir = os.path.join(db.ACCEPT_DIR, proj, mod, d["type"])
            os.makedirs(dest_dir, exist_ok=True)

            seq = _get_migration_type_seq(group_id, d["type"], dest_dir)
            prefixed = _build_migration_filename(group_id, mod, d["type"], seq, memo_file)
            final_name = _resolve_prefix_filename(dest_dir, prefixed)

            src_path = os.path.join(source_dir, memo_file)
            dst_path = os.path.join(dest_dir, final_name)
            if os.path.exists(src_path):
                shutil.move(src_path, dst_path)
                rollback_stack.append({
                    "doc_pk": d["id"],
                    "prev_status": prev_status,
                    "src": dst_path,
                    "dst": src_path,
                })

            db.update_document_status_by_pk(d["id"], "accepted")
            db.insert_event(
                d["doc_id"], "accepted", memo_file=memo_file,
                note=f"DC bundle approval (DC: {dc_doc_id})",
            )

            migration_results.append({
                "original": memo_file,
                "migrated_to": os.path.abspath(dst_path),
                "action": "accepted",
            })

        return {"success": True, "migration": migration_results}

    except Exception as e:
        for rb in reversed(rollback_stack):
            try:
                if os.path.exists(rb["src"]):
                    shutil.move(rb["src"], rb["dst"])
                db.update_document_status_by_pk(rb["doc_pk"], rb["prev_status"])
            except Exception:
                pass
        return {"success": False, "error": str(e), "migration": migration_results}


def _dc_reject_all_artifacts(group_id: str, reject_reason: str) -> dict:
    """When fully rejecting a DC, mark all D/DB/P/L in the group as rejected."""
    docs = db.get_documents_by_group_id(group_id)
    migration_results: list[dict] = []

    for d in docs:
        if d["type"] not in _DC_ARTIFACT_TYPES:
            continue
        status = d.get("status", "")
        if status not in ("open", "active"):
            continue

        memo_file = db.get_created_memo_file(d["doc_id"])
        if not memo_file:
            continue
        source_dir = _locate_file(memo_file)
        if not source_dir:
            continue

        mig = _migrate_single_file(group_id, memo_file, d["type"], source_dir, db.REJECT_DIR)
        db.update_document_status_by_pk(d["id"], "rejected")
        db.insert_event(d["doc_id"], "rejected", memo_file=memo_file,
                        reason=reject_reason, note="Rejected due to full DC rejection")
        if mig:
            mig["action"] = "rejected"
            migration_results.append(mig)

    return {"migration": migration_results}


def _collect_migration_targets(group_id: str) -> list[dict]:
    """Collect files targeted for bulk transfer on VR approval/rejection."""
    docs = db.get_documents_by_group_id(group_id)
    targets: list[dict] = []

    for d in docs:
        if d["type"] in VR_MIGRATION_TARGET_TYPES:
            status = d.get("status", "")
            if status in ("active", "open"):
                memo_file = db.get_created_memo_file(d["doc_id"])
                if memo_file:
                    source_dir = _locate_file(memo_file)
                    if source_dir:
                        targets.append({
                            "filename": memo_file,
                            "doc_type": d["type"],
                            "source_dir": source_dir,
                        })

    return targets


def _locate_file(filename: str) -> str | None:
    """Find the directory where the file exists."""
    for d in [db.INBOX_DIR, db.PROCESSED_DIR, db.OUTBOX_DIR]:
        if os.path.exists(os.path.join(d, filename)):
            return d
    return None


def _build_migration_filename(
    group_id: str, module: str, doc_type: str, seq: int, original_filename: str,
) -> str:
    """Build the transferred filename (L-002 §2-4)."""
    type_seq = f"{doc_type}{seq:03d}"
    return f"{group_id}_{module}_{type_seq}_{original_filename}"


def _get_migration_type_seq(group_id: str, doc_type: str, dest_dir: str) -> int:
    """Count existing files with the same group_id+doc_type in the transfer directory and return the next seq."""
    if not os.path.exists(dest_dir):
        return 1
    prefix = f"{group_id}_"
    type_marker = f"_{doc_type}"
    count = 0
    for name in os.listdir(dest_dir):
        if name.startswith(prefix) and type_marker in name:
            count += 1
    return count + 1


def _resolve_prefix_filename(dest_dir: str, prefix_filename: str) -> str:
    """Add a suffix when a filename conflict occurs (L-002 §2-4)."""
    if not os.path.exists(os.path.join(dest_dir, prefix_filename)):
        return prefix_filename

    name_part, ext = os.path.splitext(prefix_filename)
    for suffix in range(1, PREFIX_SUFFIX_MAX + 1):
        candidate = f"{name_part}_{suffix}{ext}"
        if not os.path.exists(os.path.join(dest_dir, candidate)):
            return candidate

    raise ValueError(f"Failed to resolve filename collision: {prefix_filename} (suffix limit {PREFIX_SUFFIX_MAX} exceeded)")


def _resolve_unique_filename(dest_dir: str, filename: str) -> str:
    """Create a unique filename by adding a suffix on directory-level filename conflicts."""
    if not os.path.exists(os.path.join(dest_dir, filename)):
        return filename
    stem, ext = os.path.splitext(filename)
    for suffix in range(2, PREFIX_SUFFIX_MAX + 2):
        candidate = f"{stem}_{suffix}{ext}"
        if not os.path.exists(os.path.join(dest_dir, candidate)):
            return candidate
    raise ValueError(f"Failed to resolve filename collision: {filename} (suffix limit {PREFIX_SUFFIX_MAX + 1} exceeded)")


def _create_inbox_result_file(
    group_id: str, project: str, module: str, priority: str,
    source_doc_id: str, source_title: str,
    result_type: str, result_label: str,
    reject_reason: str = "",
    locale: str = "en",
) -> str:
    """Automatically create the VR or RJ result file in inbox after V (review request) approval/rejection."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    type_label = get_type_name(result_type, locale)
    title = f"{source_title} — {type_label}"

    filename = f"{result_type}_{_slugify(title)}.md"

    body_lines = [
        f"## {type_label}",
        "",
        f"- Target Document: {source_doc_id}",
        f"- Title: {source_title}",
        f"- Result: {result_label}",
        f"- Processed At: {now_str}",
    ]
    if reject_reason:
        body_lines.extend(["", "## Reason for Rejection", "", reject_reason])

    header_lines = [
        "---",
        f"group_id: {group_id}",
        f"type: {result_type}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {priority}",
        f"target_id: {source_doc_id}",
        "next:",
        "---",
        "",
    ]
    content = "\n".join(header_lines) + "\n".join(body_lines) + "\n"

    os.makedirs(db.INBOX_DIR, exist_ok=True)
    filepath = os.path.join(db.INBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filename


def _create_auto_result_inbox_draft(
    *,
    group_id: str,
    project: str,
    module: str,
    priority: str,
    source_doc_id: str,
    source_type: str,
    source_title: str,
    result_type: str,
    locale: str = "en",
) -> dict[str, str]:
    """Automatically create the NR/TR result draft in inbox after N/T approval."""
    os.makedirs(db.INBOX_DIR, exist_ok=True)

    result_label = get_type_name(result_type, locale)
    safe_priority = priority if priority in linter.VALID_PRIORITIES else "medium"
    title = f"{source_title} — {result_label} Draft"
    if len(title) > linter.TITLE_MAX_LEN:
        title = title[:linter.TITLE_MAX_LEN]

    base_filename = f"{result_type}_{_slugify(source_doc_id + '_' + source_title)}.md"
    filename = _resolve_unique_filename(db.INBOX_DIR, base_filename)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    source_type_label = get_type_name(source_type, locale)
    body_lines = [
        f"## {result_label} Draft Instructions",
        "",
        f"- Target Instruction Document: {source_doc_id} ({source_type_label})",
        f"- Target Title: {source_title}",
        f"- Approved At: {now_str}",
        "",
        "## Result Summary",
        "",
        "- Summarize the work/investigation performed.",
        "- State the rationale for decisions and any remaining risks.",
        "",
        "## Pre-submission Checklist",
        "",
        "- Confirm that all required template fields are filled out.",
        "- Attach related deliverables/links in the body if needed.",
        "",
        "> After writing, you can Apply once lint passes.",
    ]

    header_lines = [
        "---",
        f"group_id: {group_id}",
        f"type: {result_type}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {safe_priority}",
        f"target_id: {source_doc_id}",
        "next: V",
        "---",
        "",
    ]
    content = "\n".join(header_lines) + "\n".join(body_lines) + "\n"

    filepath = os.path.join(db.INBOX_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return {
        "filename": filename,
        "file_path": os.path.abspath(filepath),
    }


def _cleanup_related_outbox_files(source_doc: dict[str, Any], source_filename: str) -> list[str]:
    """Clean up only the OutBox source files corresponding to the currently processed document and its direct target document."""
    candidates: list[tuple[str, str, str]] = []
    source_group_id = (source_doc.get("group_id") or "").strip()
    source_type = (source_doc.get("type") or "").strip()
    source_doc_id = (source_doc.get("doc_id") or "").strip()
    target_doc_id = (source_doc.get("target_id") or "").strip()

    if source_filename:
        candidates.append((source_filename, source_group_id, source_type))

    if source_doc_id:
        source_created_file = db.get_created_memo_file(source_doc_id)
        if source_created_file:
            candidates.append((source_created_file, source_group_id, source_type))

    if target_doc_id:
        target_doc = db.get_document_by_id(target_doc_id)
        target_created_file = db.get_created_memo_file(target_doc_id)
        if target_created_file:
            candidates.append((
                target_created_file,
                (target_doc or {}).get("group_id", ""),
                (target_doc or {}).get("type", ""),
            ))

    cleaned_files: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for filename, expected_group_id, expected_type in candidates:
        key = (filename, expected_group_id, expected_type)
        if key in seen:
            continue
        seen.add(key)
        removed = _cleanup_outbox_file(
            filename=filename,
            expected_group_id=expected_group_id,
            expected_type=expected_type,
        )
        if removed:
            cleaned_files.append(removed)

    return cleaned_files


def _cleanup_outbox_file(filename: str, expected_group_id: str = "", expected_type: str = "") -> str:
    """Clean up one matching OutBox file and return the removed absolute path."""
    if not filename or not _safe_filename(filename):
        return ""

    path = os.path.join(db.OUTBOX_DIR, filename)
    if not os.path.isfile(path):
        return ""

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        header, _ = linter.parse_yaml_header(content)
    except Exception:
        return ""

    if not header:
        return ""
    if expected_group_id and (header.get("group_id") or "") != expected_group_id:
        return ""
    if expected_type and (header.get("type") or "") != expected_type:
        return ""

    os.remove(path)
    return os.path.abspath(path)


# ══════════════════════════════════════════════════════════════════
# TV / TVR service (T044) — implements D008, P001, L001, DB001
# ══════════════════════════════════════════════════════════════════

# L001 §2-1 TV status set
TV_STATUS_OPEN = "Open"
TV_STATUS_RUNNING = "Running"
TV_STATUS_PASS = "Pass"
TV_STATUS_FAIL = "Fail"
TV_STATUS_CLOSED = "Closed"
TV_STATUS_REJECT = "Reject"
TV_STATUS_DISCARDED = "discarded"

_TV_T_ALLOWED_STATUS = {"accepted", "monitoring", "done", "closed"}
_TV_PERMIT_SCENARIO_APPEND = {TV_STATUS_OPEN, TV_STATUS_RUNNING}
_TV_TVR_ELIGIBLE = {TV_STATUS_PASS, TV_STATUS_FAIL, TV_STATUS_CLOSED}
_TVR_APPROVABLE_STATUSES = {"draft", "open"}
_FOLLOWUP_VALUES = {"rerun_t", "tv_fix", "user_edit", "design_reopen"}

_TV_STATUS_TO_DOCUMENTS_STATUS = {
    TV_STATUS_OPEN: "open",
    TV_STATUS_RUNNING: "open",
    TV_STATUS_PASS: "open",
    TV_STATUS_FAIL: "open",
    TV_STATUS_CLOSED: "accepted",
    TV_STATUS_REJECT: "rejected",
    TV_STATUS_DISCARDED: "rejected",
}


def _tv_error(message: str, code: str = "validation", **extra) -> dict:
    payload = {"status": "error", "error": code, "message": message}
    payload.update(extra)
    return payload


def _parse_scenarios_json(raw: str | None) -> tuple[list[str], str | None]:
    """Convert scenarios_json input into a list of scenario titles."""
    raw = (raw or "").strip()
    if not raw:
        return [], None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], f"Failed to parse scenarios_json: {e}"
    if not isinstance(parsed, list):
        return [], "scenarios_json must be a list"
    titles: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            title = item.strip()
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
        else:
            return [], "Each scenarios_json item must be a string or a {title:...} object"
        if not title:
            return [], "A scenarios_json item has an empty title"
        titles.append(title)
    return titles, None


def _parse_refs_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _resolve_tv_clear_impl():
    """Return the clear-execution implementation.

    MVP: because actual test-environment control is manually separated outside
    this layer, the default implementation marks checked items as 'ok'. In test
    environments, you can inject a failure path by setting
    `process_service._clear_impl_override`.
    """
    return _clear_impl_override


# Test hook: default is None (returns success). When set, use dict[str, str]
# or a callable.
# MEMO_023 Q-10: the server service layer owns clear execution.
_clear_impl_override: Any = None


def _run_clear(scope: dict) -> dict[str, str]:
    """Run clear according to the clear_scope flags and return the per-item results."""
    override = _resolve_tv_clear_impl()
    scopes: list[str] = []
    if scope.get("clear_db"):
        scopes.append("db")
    if scope.get("clear_fs"):
        scopes.append("fs")
    if scope.get("clear_cache"):
        scopes.append("cache")
    if scope.get("clear_logs"):
        scopes.append("logs")

    result: dict[str, str] = {}
    if callable(override):
        return dict(override(scopes))
    if isinstance(override, dict):
        for key in scopes:
            result[key] = str(override.get(key, "ok"))
        return result
    for key in scopes:
        result[key] = "ok"
    return result


def _tv_doc_to_number(tv_doc_id: str) -> int | None:
    return db.get_doc_seq_num(tv_doc_id, "TV")


def _tvr_doc_to_number(tvr_doc_id: str) -> int | None:
    return db.get_doc_seq_num(tvr_doc_id, "TVR")


def _t_doc_to_number(t_doc_id: str) -> int | None:
    return db.get_doc_seq_num(t_doc_id, "T")


def _sanitize_title(title: str) -> str:
    return (title or "").strip()[:linter.TITLE_MAX_LEN]


def _write_test_report_file(doc_id: str, content: str) -> str:
    """Write the TV/TVR document file under 90_test_reports/ and return the filename."""
    os.makedirs(db.TEST_REPORTS_DIR, exist_ok=True)
    slug = _slugify(doc_id)
    filename = f"{doc_id}_{slug}.md" if slug != _slugify("") else f"{doc_id}.md"
    path = os.path.join(db.TEST_REPORTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def _read_test_report_file(filename: str) -> str | None:
    if not filename or not _safe_filename(filename):
        return None
    # Normal location
    primary = os.path.join(db.TEST_REPORTS_DIR, filename)
    if os.path.exists(primary):
        with open(primary, "r", encoding="utf-8") as f:
            return f.read()
    archived = os.path.join(db.TEST_REPORTS_ARCHIVE_DIR, filename)
    if os.path.exists(archived):
        with open(archived, "r", encoding="utf-8") as f:
            return f.read()
    return None


def _find_test_report_path(filename: str) -> str | None:
    if not filename or not _safe_filename(filename):
        return None
    for base in (db.TEST_REPORTS_DIR, db.TEST_REPORTS_ARCHIVE_DIR):
        path = os.path.join(base, filename)
        if os.path.exists(path):
            return path
    return None


def _move_test_report_to_archive(filename: str) -> str | None:
    """Move TV/TVR files from 90_test_reports → 98_archive/90_test_reports."""
    if not filename:
        return None
    src = os.path.join(db.TEST_REPORTS_DIR, filename)
    if not os.path.exists(src):
        return None
    os.makedirs(db.TEST_REPORTS_ARCHIVE_DIR, exist_ok=True)
    dst = os.path.join(db.TEST_REPORTS_ARCHIVE_DIR, filename)
    if os.path.exists(dst):
        stem, ext = os.path.splitext(filename)
        for suffix in range(2, PREFIX_SUFFIX_MAX + 2):
            candidate = f"{stem}_{suffix}{ext}"
            cand_path = os.path.join(db.TEST_REPORTS_ARCHIVE_DIR, candidate)
            if not os.path.exists(cand_path):
                dst = cand_path
                break
    shutil.move(src, dst)
    return os.path.abspath(dst)


def _build_tv_file_content(
    *, doc_id: str, group_id: str, project: str, module: str,
    target_id: str, title: str, tv_type: str, pass_criteria: str,
    worker_tier: str | None, previous_tv: str | None, refs: list[str],
    clear_db: bool, clear_fs: bool, clear_cache: bool, clear_logs: bool,
    scenarios: list[str],
) -> str:
    lines = [
        "---",
        f"group_id: {group_id}",
        "type: TV",
        f"doc_id: {doc_id}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"target_id: {target_id}",
        f"tv_type: {tv_type}",
        f"pass_criteria: {pass_criteria}",
        f"reviewer: PM",
    ]
    if worker_tier:
        lines.append(f"worker_tier: {worker_tier}")
    if previous_tv:
        lines.append(f"previous_tv: {previous_tv}")
    if refs:
        lines.append("refs:")
        for ref in refs:
            lines.append(f"  - {ref}")
    lines.append("clear_scope:")
    lines.append(f"  db: {'true' if clear_db else 'false'}")
    lines.append(f"  filesystem: {'true' if clear_fs else 'false'}")
    lines.append(f"  cache: {'true' if clear_cache else 'false'}")
    lines.append(f"  logs: {'true' if clear_logs else 'false'}")
    lines.append("---")
    lines.append("")
    lines.append("## Scenarios")
    lines.append("")
    if scenarios:
        for i, title in enumerate(scenarios, 1):
            lines.append(f"- [ ] S{i}. {title}")
            lines.append("  - Preconditions:")
            lines.append("  - Input:")
            lines.append("  - Expected Result:")
            lines.append("  - Expected Result (Error Cases):")
    else:
        lines.append("- [ ] (No scenarios provided)")
    lines.append("")
    return "\n".join(lines)


def _build_tvr_file_content(
    *, doc_id: str, group_id: str, project: str, module: str,
    target_id: str, title: str, summary_status: str,
    scenarios: list[dict],
    rejection_reason: str | None = None,
    rerun_t: str | None = None, previous_tvr: str | None = None,
    design_reopen_ds: str | None = None,
) -> str:
    lines = [
        "---",
        f"group_id: {group_id}",
        "type: TVR",
        f"doc_id: {doc_id}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"target_id: {target_id}",
        f"summary_status: {summary_status}",
        f"approver: PM",
    ]
    if rejection_reason:
        lines.append(f"rejection_reason: {rejection_reason}")
    if rerun_t:
        lines.append(f"rerun_t: {rerun_t}")
    if previous_tvr:
        lines.append(f"previous_tvr: {previous_tvr}")
    if design_reopen_ds:
        lines.append(f"design_reopen_ds: {design_reopen_ds}")
    lines.append("---")
    lines.append("")
    lines.append("## Scenario Results")
    lines.append("")
    lines.append("| # | Scenario | Result | Notes |")
    lines.append("|---|---|---|---|")
    for s in scenarios:
        idx = s.get("scenario_idx")
        title = s.get("title", "")
        result = (s.get("result") or "-") or "-"
        note = (s.get("note") or "").replace("|", "\\|")
        if s.get("disabled"):
            result = "disabled"
            note = s.get("disabled_reason") or note
        lines.append(f"| S{idx} | {title} | {result} | {note} |")
    lines.append("")
    lines.append("## Overall Verdict")
    lines.append("")
    lines.append(f"- Status: {summary_status}")
    if rejection_reason:
        lines.append(f"- Reason for Rejection: {rejection_reason}")
    lines.append("")
    return "\n".join(lines)


def _compute_tv_aggregate(tv_doc_id: str) -> tuple[str, int, int]:
    """Aggregate scenarios → (tv_status, progress_done, progress_total).

    Apply the L001 §3 / §4-1 / MEMO_023 Q-08 rules together.
    """
    scenarios = [s for s in db.get_tv_scenarios(tv_doc_id) if not s.get("disabled")]
    total = len(scenarios)
    counts = {"pass": 0, "fail": 0, "skip": 0, "hold": 0, "none": 0}
    for s in scenarios:
        r = (s.get("result") or "").strip().lower()
        if r in counts:
            counts[r] += 1
        else:
            counts["none"] += 1
    done = counts["pass"] + counts["fail"] + counts["skip"] + counts["hold"]

    if counts["fail"] >= 1:
        return TV_STATUS_FAIL, done, total
    if counts["hold"] >= 1:
        return TV_STATUS_RUNNING, done, total
    if counts["none"] >= 1:
        return TV_STATUS_RUNNING, done, total
    if total >= 1 and counts["pass"] >= 1 and counts["fail"] == 0 and counts["hold"] == 0 and counts["none"] == 0:
        return TV_STATUS_PASS, done, total
    return TV_STATUS_RUNNING, done, total


def _sync_documents_status_from_tv(tv_doc_id: str, tv_status: str) -> None:
    """Map documents.status according to the tv_status value (DB001 §2-5)."""
    mapped = _TV_STATUS_TO_DOCUMENTS_STATUS.get(tv_status, "open")
    db.update_document_fields(tv_doc_id, status=mapped)


def _transition_tv_status(tv_doc_id: str, new_status: str,
                          done: int | None = None, total: int | None = None) -> None:
    db.update_tv_status(tv_doc_id, new_status, progress_done=done, progress_total=total)
    _sync_documents_status_from_tv(tv_doc_id, new_status)


def _apply_aggregate(tv_doc_id: str) -> tuple[str, int, int]:
    """After aggregation, apply state transitions only within the Running/Pass/Fail range."""
    tv_status_row = db.get_tv_status(tv_doc_id)
    current = (tv_status_row or {}).get("tv_status") or TV_STATUS_RUNNING
    new_status, done, total = _compute_tv_aggregate(tv_doc_id)

    # Allow automatic aggregation only in the Running stage.
    # Moves among Running/Pass/Fail are allowed.
    if current in {TV_STATUS_RUNNING, TV_STATUS_PASS, TV_STATUS_FAIL}:
        if current != new_status or (tv_status_row or {}).get("progress_done") != done \
           or (tv_status_row or {}).get("progress_total") != total:
            _transition_tv_status(tv_doc_id, new_status, done, total)
            if new_status in {TV_STATUS_PASS, TV_STATUS_FAIL}:
                db.insert_event(
                    tv_doc_id, "tv_aggregated",
                    note=f"done={done}/total={total} status={new_status}",
                )
    return new_status, done, total


# ── TV creation ───────────────────────────────────────────────────────────────

def create_tv(
    *,
    target_id: str,
    tv_type: str,
    title: str,
    project: str,
    module: str | None,
    refs: list[str] | None = None,
    clear_db: bool = True,
    clear_fs: bool = True,
    clear_cache: bool = False,
    clear_logs: bool = False,
    pass_criteria: str = "all",
    scenarios_json: str | None = None,
    previous_tv: str | None = None,
    worker_tier: str | None = None,
    author_type: str = "worker",
    requested_by: str | None = None,
) -> dict:
    """Create a new TV document (P001 §2-1)."""
    errors: list[str] = []
    target_id = (target_id or "").strip()
    tv_type = (tv_type or "").strip().lower()
    title = _sanitize_title(title)
    project = (project or "").strip()
    module = (module or "").strip()
    pass_criteria = (pass_criteria or "all").strip().lower()

    if not target_id:
        errors.append("target_id is required")
    if tv_type not in linter.VALID_TV_TYPES:
        errors.append(f"Invalid tv_type: {tv_type}")
    if not title:
        errors.append("title is required")
    if not project:
        errors.append("project is required")
    if pass_criteria not in linter.VALID_PASS_CRITERIA:
        errors.append(f"Invalid pass_criteria: {pass_criteria}")
    if errors:
        return _tv_error("; ".join(errors))

    # Verify the corresponding T (L001 §1, §3)
    t_doc = db.get_document_by_id(target_id)
    if t_doc is None or t_doc.get("type") != "T":
        return _tv_error(f"Corresponding T document not found: {target_id}",
                        code="target_not_found")
    t_status = (t_doc.get("status") or "").strip().lower()
    if t_status not in _TV_T_ALLOWED_STATUS:
        return _tv_error(
            f"The corresponding T is not in accepted/monitoring/done/closed status: {t_status}",
            code="target_state",
        )

    # Prevent duplicate active TVs (L001 §3)
    existing_active = db.get_active_tv_for_t(target_id)
    if existing_active:
        return _tv_error(
            f"This T already has an active TV: {existing_active.get('doc_id')}",
            code="invalid_transition",
        )

    # Parse scenarios
    titles, parse_err = _parse_scenarios_json(scenarios_json)
    if parse_err:
        return _tv_error(parse_err)

    # Reserve the TV doc_id (same number as T)
    t_num = _t_doc_to_number(target_id)
    if t_num is None:
        return _tv_error(f"Failed to parse T number: {target_id}")
    tv_doc_id = db.build_tv_doc_id(project, module, t_num)

    # Block if the same doc_id already exists (L001 §5-4: reusing the same number is forbidden)
    if db.get_document_by_id(tv_doc_id):
        return _tv_error(
            f"TV document ID is already in use: {tv_doc_id}. Proceed via the rerun-T path.",
            code="invalid_transition",
        )

    group_id = (t_doc.get("group_id") or "").strip()
    refs = refs or []

    content = _build_tv_file_content(
        doc_id=tv_doc_id, group_id=group_id, project=project,
        module=module or "", target_id=target_id, title=title,
        tv_type=tv_type, pass_criteria=pass_criteria,
        worker_tier=worker_tier, previous_tv=previous_tv, refs=refs,
        clear_db=clear_db, clear_fs=clear_fs,
        clear_cache=clear_cache, clear_logs=clear_logs,
        scenarios=titles,
    )
    filename = _write_test_report_file(tv_doc_id, content)

    # DB: document + tv_status + tv_scenarios + tv_clear_scope
    try:
        db.insert_document(
            doc_id=tv_doc_id, doc_type="TV",
            project=project, module=module or None, title=title,
            target_id=target_id, group_id=group_id or None,
            priority=(t_doc.get("priority") or "medium"),
            status="open",
        )
    except Exception:
        archive_path = os.path.join(db.TEST_REPORTS_DIR, filename)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        raise
    db.update_document_fields(
        tv_doc_id,
        tv_type=tv_type,
        pass_criteria=pass_criteria,
        worker_tier=worker_tier,
        author_type=author_type,
        requested_by=requested_by,
        previous_tv=previous_tv,
        seq_num=t_num,
    )
    db.insert_tv_status(tv_doc_id, tv_status=TV_STATUS_OPEN,
                        progress_done=0, progress_total=len(titles))
    db.insert_tv_clear_scope(
        tv_doc_id,
        clear_db=1 if clear_db else 0,
        clear_fs=1 if clear_fs else 0,
        clear_cache=1 if clear_cache else 0,
        clear_logs=1 if clear_logs else 0,
    )
    if titles:
        db.insert_tv_scenarios_bulk(tv_doc_id, titles, source="worker")
    db.insert_event(tv_doc_id, "created", memo_file=filename,
                    related_target_id=target_id)
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "tv_doc_id": tv_doc_id,
        "filename": filename,
        "group_id": group_id,
        "progress_total": len(titles),
    }


# ── TV approval (Open → Running) ──────────────────────────────────────────────

def approve_tv(tv_doc_id: str) -> dict:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current != TV_STATUS_OPEN:
        return _tv_error(
            f"Only TVs in Open status can be approved (current: {current})",
            code="invalid_transition", **{"from": current, "to": TV_STATUS_RUNNING},
        )

    # Environment lock (L001 §3, MEMO_023 Q-09)
    busy = db.get_running_tv_in_env(
        doc.get("project") or "", doc.get("module"),
        exclude_tv_doc_id=tv_doc_id,
    )
    if busy:
        db.insert_event(tv_doc_id, "tv_env_busy",
                        note=f"active_tv={busy.get('doc_id')}")
        return {
            "status": "error",
            "error": "env_busy",
            "active_tv": busy.get("doc_id"),
            "message": "Another TV is currently running",
            "http_status": 423,
        }

    _transition_tv_status(tv_doc_id, TV_STATUS_RUNNING,
                          done=(ts or {}).get("progress_done", 0),
                          total=(ts or {}).get("progress_total", 0))
    db.insert_event(tv_doc_id, "tv_approved")
    group_id = (doc.get("group_id") or "").strip()
    if group_id:
        db.update_group_updated_at(group_id)

    return {"status": "success", "tv_doc_id": tv_doc_id, "tv_status": TV_STATUS_RUNNING}


# ── Scenario result input ─────────────────────────────────────────────────────

def input_scenario_result(tv_doc_id: str, scenario_idx: int,
                          result: str, note: str | None = None) -> dict:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    result = (result or "").strip().lower()
    if result not in ("pass", "fail", "skip", "hold"):
        return _tv_error(f"Invalid result: {result}")

    if result in ("fail", "skip", "hold") and not (note or "").strip():
        return _tv_error(f"{result} result requires note (reason).")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current != TV_STATUS_RUNNING:
        return _tv_error(
            f"Results can only be entered in Running status (current: {current})",
            code="invalid_transition",
        )

    scenarios = db.get_tv_scenarios(tv_doc_id)
    target = next((s for s in scenarios if s.get("scenario_idx") == scenario_idx), None)
    if target is None:
        return _tv_error(f"Scenario not found: idx={scenario_idx}", code="not_found")
    if target.get("disabled"):
        return _tv_error(f"Scenario is disabled: idx={scenario_idx}")

    db.update_tv_scenario_result(tv_doc_id, scenario_idx, result, note=note or None)
    db.insert_event(tv_doc_id, "tv_scenario_result",
                    note=f"S{scenario_idx} {result}: {(note or '').strip()[:80]}")

    new_status, done, total = _apply_aggregate(tv_doc_id)
    return {
        "status": "success",
        "tv_status": new_status,
        "progress": f"{done}/{total}",
        "scenario": {"id": scenario_idx, "result": result},
    }


# ── hold → skip transition ────────────────────────────────────────────────────

def hold_to_skip(tv_doc_id: str, scenario_idx: int, reason: str) -> dict:
    reason = (reason or "").strip()
    if not reason:
        return _tv_error("reason is required")
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current != TV_STATUS_RUNNING:
        return _tv_error(
            f"hold→skip transitions are only allowed in Running status (current: {current})",
            code="invalid_transition",
        )

    scenarios = db.get_tv_scenarios(tv_doc_id)
    target = next((s for s in scenarios if s.get("scenario_idx") == scenario_idx), None)
    if target is None:
        return _tv_error(f"Scenario not found: idx={scenario_idx}",
                        code="not_found")
    if (target.get("result") or "").lower() != "hold":
        return _tv_error(f"Not in hold status: idx={scenario_idx}",
                        code="invalid_transition")

    db.update_tv_scenario_hold_to_skip(tv_doc_id, scenario_idx, reason)
    db.insert_event(tv_doc_id, "tv_scenario_hold_to_skip",
                    note=f"idx={scenario_idx} reason={reason[:80]}")
    new_status, done, total = _apply_aggregate(tv_doc_id)
    return {
        "status": "success",
        "tv_status": new_status,
        "progress": f"{done}/{total}",
    }


# ── Add scenario ──────────────────────────────────────────────────────────────

def append_scenario(tv_doc_id: str, title: str,
                    precondition: str = "", input_text: str = "",
                    expected: str = "") -> dict:
    title = (title or "").strip()
    if not title:
        return _tv_error("title is required")
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current not in _TV_PERMIT_SCENARIO_APPEND:
        return _tv_error(
            f"Scenarios can only be added in Open/Running status (current: {current})",
            code="invalid_transition",
        )

    new_idx = db.append_tv_scenario(tv_doc_id, title, source="user")
    db.insert_event(tv_doc_id, "tv_scenario_appended",
                    note=f"idx={new_idx} source=user")

    # Update progress_total and aggregate
    new_status, done, total = _apply_aggregate(tv_doc_id)
    # Open state must not become Running due to aggregation —
    # _apply_aggregate handles only Running.
    if current == TV_STATUS_OPEN:
        total = len([s for s in db.get_tv_scenarios(tv_doc_id) if not s.get("disabled")])
        db.update_tv_status(tv_doc_id, TV_STATUS_OPEN,
                            progress_done=0, progress_total=total)

    return {
        "status": "success",
        "scenario_idx": new_idx,
        "tv_status": (db.get_tv_status(tv_doc_id) or {}).get("tv_status"),
        "progress_total": total,
    }


# ── TV execution (run / clear) ────────────────────────────────────────────────

def run_tv(tv_doc_id: str, mode: str = "full",
           scenario_ids: list[int] | None = None,
           clear_before_run: bool = True) -> dict:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current != TV_STATUS_RUNNING:
        return _tv_error(
            f"Execution is only allowed in Running status (current: {current})",
            code="invalid_transition",
        )

    mode = (mode or "full").strip().lower()
    if mode not in ("full", "partial"):
        return _tv_error(f"Invalid mode: {mode}")
    if mode == "partial" and not scenario_ids:
        return _tv_error("scenario_ids are required in partial mode")

    # Re-check the environment lock
    busy = db.get_running_tv_in_env(
        doc.get("project") or "", doc.get("module"),
        exclude_tv_doc_id=tv_doc_id,
    )
    if busy:
        db.insert_event(tv_doc_id, "tv_env_busy",
                        note=f"active_tv={busy.get('doc_id')}")
        return {
            "status": "error",
            "error": "env_busy",
            "active_tv": busy.get("doc_id"),
            "message": "Another TV is currently running",
            "http_status": 423,
        }

    clear_result: dict[str, str] = {}
    scenarios_started = True
    if clear_before_run:
        scope = db.get_tv_clear_scope(tv_doc_id) or {}
        db.insert_event(tv_doc_id, "tv_clear_started",
                        note=f"scope={','.join(k for k in ('clear_db','clear_fs','clear_cache','clear_logs') if scope.get(k))}")
        clear_result = _run_clear(scope)
        if any(v != "ok" for v in clear_result.values()):
            # Clear failed — no automatic rollback; keep Running
            db.insert_event(
                tv_doc_id, "tv_clear_failed",
                note=",".join(f"{k}={v}" for k, v in clear_result.items()),
            )
            return {
                "status": "success",  # Request accepted; expose failure via result flags
                "tv_status": current,
                "clear_result": clear_result,
                "scenarios_started": False,
                "retry_endpoint": f"/flow_gate/tv/{tv_doc_id}/clear/retry",
            }

    # Actual scenario execution is manual and external. The server only keeps the state as Running.
    return {
        "status": "success",
        "tv_status": current,
        "mode": mode,
        "scenario_ids": list(scenario_ids or []),
        "clear_result": clear_result,
        "scenarios_started": scenarios_started,
    }


def retry_clear(tv_doc_id: str) -> dict:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current != TV_STATUS_RUNNING:
        return _tv_error(
            f"Clear retry is only allowed in Running status (current: {current})",
            code="invalid_transition",
        )

    scope = db.get_tv_clear_scope(tv_doc_id) or {}
    db.insert_event(tv_doc_id, "tv_clear_retried",
                    note=f"scope={','.join(k for k in ('clear_db','clear_fs','clear_cache','clear_logs') if scope.get(k))}")
    clear_result = _run_clear(scope)
    if any(v != "ok" for v in clear_result.values()):
        db.insert_event(
            tv_doc_id, "tv_clear_failed",
            note=",".join(f"{k}={v}" for k, v in clear_result.items()),
        )
    return {
        "status": "success",
        "tv_status": current,
        "clear_result": clear_result,
    }


# ── Force-stop TV ─────────────────────────────────────────────────────────────

def close_tv_force(tv_doc_id: str, reason: str, create_tvr: bool = False) -> dict:
    reason = (reason or "").strip()
    if not reason:
        return _tv_error("reason is required")
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    # L001 §4-1: allow transitions such as Open→discarded, Running→Closed, etc.
    if current == TV_STATUS_OPEN:
        # Not-yet-executed state → mark as discarded
        _transition_tv_status(tv_doc_id, TV_STATUS_DISCARDED,
                              done=(ts or {}).get("progress_done", 0),
                              total=(ts or {}).get("progress_total", 0))
        db.insert_event(tv_doc_id, "tv_discarded", reason=reason)
        return {"status": "success", "tv_status": TV_STATUS_DISCARDED}

    if current not in {TV_STATUS_RUNNING, TV_STATUS_PASS, TV_STATUS_FAIL}:
        return _tv_error(
            f"Force close is only allowed in Running/Pass/Fail status (current: {current})",
            code="invalid_transition",
        )

    _transition_tv_status(tv_doc_id, TV_STATUS_CLOSED,
                          done=(ts or {}).get("progress_done", 0),
                          total=(ts or {}).get("progress_total", 0))
    db.insert_event(tv_doc_id, "tv_closed", reason=reason, note="forced")

    tvr_doc_id = None
    if create_tvr:
        tvr_res = create_tvr(tv_doc_id, summary_status_override=TV_STATUS_CLOSED)
        if tvr_res.get("status") == "success":
            tvr_doc_id = tvr_res.get("tvr_doc_id")

    # Archive: move the previous set if one exists
    _maybe_archive_previous_set(tv_doc_id)

    group_id = (doc.get("group_id") or "").strip()
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "tv_status": TV_STATUS_CLOSED,
        "tvr_doc_id": tvr_doc_id,
    }


# ── TVR creation ──────────────────────────────────────────────────────────────

def create_tvr(tv_doc_id: str, summary_status_override: str | None = None) -> dict:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return _tv_error("TV document not found", code="not_found")

    ts = db.get_tv_status(tv_doc_id)
    current = (ts or {}).get("tv_status") or TV_STATUS_OPEN
    if current not in _TV_TVR_ELIGIBLE and summary_status_override != TV_STATUS_CLOSED:
        return _tv_error(
            f"TVR can only be created in Pass/Fail/Closed status (current: {current})",
            code="invalid_transition",
        )

    # Check existing TVR (L001 §3: do not create a new one unless accepted; use the existing draft)
    existing = db.get_documents_by_target_id(tv_doc_id, types=("TVR",))
    for ex in existing:
        if ex.get("status") != "accepted":
            return {
                "status": "success",
                "tvr_doc_id": ex["doc_id"],
                "existing": True,
            }

    project = doc.get("project") or ""
    module = doc.get("module") or ""
    tv_num = _tv_doc_to_number(tv_doc_id)
    if tv_num is None:
        return _tv_error(f"Failed to parse TV number: {tv_doc_id}")
    tvr_doc_id = db.build_tvr_doc_id(project, module, tv_num)

    if db.get_document_by_id(tvr_doc_id):
        # A TVR with the same number already exists — defensive branch
        return _tv_error(
            f"TVR document ID is already in use: {tvr_doc_id}",
            code="invalid_transition",
        )

    summary_status = summary_status_override or current
    scenarios = db.get_tv_scenarios(tv_doc_id)
    title = f"{doc.get('title') or tv_doc_id} — TVR"
    title = _sanitize_title(title)

    content = _build_tvr_file_content(
        doc_id=tvr_doc_id,
        group_id=doc.get("group_id") or "",
        project=project, module=module,
        target_id=tv_doc_id, title=title,
        summary_status=summary_status,
        scenarios=scenarios,
    )
    filename = _write_test_report_file(tvr_doc_id, content)

    try:
        db.insert_document(
            doc_id=tvr_doc_id, doc_type="TVR",
            project=project, module=module or None, title=title,
            target_id=tv_doc_id, group_id=doc.get("group_id"),
            priority=(doc.get("priority") or "medium"),
            status="draft",
        )
    except Exception:
        p = os.path.join(db.TEST_REPORTS_DIR, filename)
        if os.path.exists(p):
            os.remove(p)
        raise
    db.update_document_fields(tvr_doc_id, seq_num=tv_num)
    db.insert_event(tvr_doc_id, "created", memo_file=filename,
                    related_target_id=tv_doc_id)

    group_id = (doc.get("group_id") or "").strip()
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "tvr_doc_id": tvr_doc_id,
        "filename": filename,
        "summary_status": summary_status,
    }


# ── TVR approval ──────────────────────────────────────────────────────────────

def approve_tvr(tvr_doc_id: str) -> dict:
    tvr_doc = db.get_document_by_id(tvr_doc_id)
    if tvr_doc is None or tvr_doc.get("type") != "TVR":
        return _tv_error("TVR document not found", code="not_found")
    current_status = (tvr_doc.get("status") or "").strip().lower()
    if current_status not in _TVR_APPROVABLE_STATUSES:
        return _tv_error(
            f"Only TVRs in draft/open status can be approved (current: {current_status})",
            code="invalid_transition",
        )

    tv_doc_id = (tvr_doc.get("target_id") or "").strip()
    # Update TVR status
    db.update_document_fields(tvr_doc_id, status="accepted")

    db.insert_event(tvr_doc_id, "tvr_accepted",
                    related_doc_id=tvr_doc.get("doc_id"))

    # All linked TVRs are accepted → TV Closed
    tv_closed = False
    if tv_doc_id:
        siblings_refresh = db.get_documents_by_target_id(tv_doc_id, types=("TVR",))
        if siblings_refresh and all(
            (s.get("status") or "") == "accepted" for s in siblings_refresh
        ):
            ts = db.get_tv_status(tv_doc_id) or {}
            current_tv_status = ts.get("tv_status") or TV_STATUS_OPEN
            if current_tv_status in {TV_STATUS_PASS, TV_STATUS_FAIL, TV_STATUS_RUNNING}:
                _transition_tv_status(
                    tv_doc_id, TV_STATUS_CLOSED,
                    done=ts.get("progress_done", 0),
                    total=ts.get("progress_total", 0),
                )
                db.insert_event(tv_doc_id, "tv_closed",
                                note="tvr_accepted",
                                related_doc_id=tvr_doc.get("doc_id"))
                tv_closed = True

    # Archive
    if tv_closed and tv_doc_id:
        _maybe_archive_previous_set(tv_doc_id)

    group_id = (tvr_doc.get("group_id") or "").strip()
    if group_id:
        db.update_group_updated_at(group_id)

    return {
        "status": "success",
        "tvr_doc_id": tvr_doc.get("doc_id"),
        "tv_closed": tv_closed,
    }


def _resolve_tvr_id_for_update(tvr_doc: dict) -> str:
    return tvr_doc.get("doc_id") or ""


# ── TVR rejection and follow-up branching ─────────────────────────────────────

def reject_tvr(tvr_doc_id: str, rejection_reason: str, followup: str) -> dict:
    rejection_reason = (rejection_reason or "").strip()
    followup = (followup or "").strip().lower()
    if not rejection_reason:
        return _tv_error("rejection_reason is required")
    if followup not in _FOLLOWUP_VALUES:
        return _tv_error(
            f"Invalid followup: {followup} (allowed: {', '.join(sorted(_FOLLOWUP_VALUES))})"
        )

    tvr_doc = db.get_document_by_id(tvr_doc_id)
    if tvr_doc is None or tvr_doc.get("type") != "TVR":
        return _tv_error("TVR document not found", code="not_found")
    current_status = (tvr_doc.get("status") or "").strip().lower()
    if current_status not in _TVR_APPROVABLE_STATUSES:
        return _tv_error(
            f"Only TVRs in draft/open status can be rejected (current: {current_status})",
            code="invalid_transition",
        )

    tv_doc_id = (tvr_doc.get("target_id") or "").strip()
    tv_doc = db.get_document_by_id(tv_doc_id) if tv_doc_id else None

    # Update TVR status
    db.update_document_fields(tvr_doc_id, status="rejected")
    db.insert_event(
        tvr_doc_id, "tvr_rejected",
        reason=rejection_reason, note=f"followup={followup}",
    )

    result: dict[str, Any] = {
        "status": "success",
        "tvr_doc_id": tvr_doc_id,
        "followup": followup,
    }

    # Common path: transition TV → Reject
    # (Reject remains the default even outside the user_edit path)
    tv_status_before = None
    if tv_doc:
        ts = db.get_tv_status(tv_doc_id) or {}
        tv_status_before = ts.get("tv_status")
        _transition_tv_status(
            tv_doc_id, TV_STATUS_REJECT,
            done=ts.get("progress_done", 0),
            total=ts.get("progress_total", 0),
        )
        db.insert_event(tv_doc_id, "tv_reject",
                        related_doc_id=tvr_doc_id,
                        note=f"followup={followup}")

    if followup == "rerun_t" and tv_doc:
        rerun = _followup_rerun_t(tv_doc, tvr_doc)
        result.update(rerun)
    elif followup == "tv_fix" and tv_doc:
        # Keep the existing TV as Reject. Leave new TV numbering as a manual trigger.
        # Set the review_required flag
        db.set_review_required(tv_doc_id, True)
        db.insert_event(tv_doc_id, "tv_review_flagged",
                        note="followup=tv_fix")
        result["tv_fix"] = {"tv_doc_id": tv_doc_id, "note": "tv_fix path — new TV numbering is manual"}
    elif followup == "user_edit":
        # No file move and no new numbering. TV status was already changed to Reject above.
        result["user_edit"] = {"tv_doc_id": tv_doc_id, "note": "user_edit path — keep file"}
    elif followup == "design_reopen" and tv_doc:
        reopen = _followup_design_reopen(tv_doc, tvr_doc)
        result.update(reopen)

    group_id = (tvr_doc.get("group_id") or "").strip()
    if group_id:
        db.update_group_updated_at(group_id)

    if tv_status_before is not None:
        result["tv_status_before"] = tv_status_before
        result["tv_status"] = TV_STATUS_REJECT
    return result


def _followup_rerun_t(tv_doc: dict, tvr_doc: dict) -> dict:
    """rerun_t: create a new T draft + mark the original TV review_required + set state to Reject."""
    project = tv_doc.get("project") or ""
    module = tv_doc.get("module") or ""
    group_id = tv_doc.get("group_id") or ""
    tv_doc_id = tv_doc.get("doc_id") or ""
    tvr_doc_id = tvr_doc.get("doc_id") or ""
    previous_t = (tv_doc.get("target_id") or "").strip()

    # Reserve the new T number (reusing the existing numbering rules)
    seq = db.get_next_number(project, module, "T")
    new_t_num = seq
    new_t_doc_id = db.build_t_doc_id(project, module, new_t_num)
    new_title = f"Rerun T — {tv_doc.get('title') or tv_doc_id} (from {tvr_doc_id})"

    # Create the draft file in inbox/
    filename = _create_rerun_t_inbox_draft(
        new_t_doc_id=new_t_doc_id, project=project, module=module,
        group_id=group_id, title=new_title,
        previous_t=previous_t, triggered_by=tvr_doc_id,
        priority=(tv_doc.get("priority") or "medium"),
    )

    # DB registration: status=draft, direction marked
    db.insert_document(
        doc_id=new_t_doc_id, doc_type="T", project=project,
        module=module or None, title=new_title,
        target_id=previous_t or None,
        group_id=group_id or None,
        priority=(tv_doc.get("priority") or "medium"),
        status="draft", direction="inbox",
    )
    db.update_document_fields(
        new_t_doc_id,
        previous_t=previous_t or None,
        triggered_by=tvr_doc_id,
    )
    db.insert_event(new_t_doc_id, "t_created_rerun",
                    memo_file=filename,
                    related_doc_id=tvr_doc_id,
                    related_target_id=previous_t or None,
                    note=f"previous_t={previous_t} status=draft")

    # Original TV: review_required=true
    db.set_review_required(tv_doc_id, True)
    db.insert_event(tv_doc_id, "tv_review_flagged",
                    note="followup=rerun_t",
                    related_doc_id=new_t_doc_id)

    return {
        "rerun_t": {
            "new_t_doc_id": new_t_doc_id,
            "filename": filename,
            "previous_t": previous_t,
            "triggered_by": tvr_doc_id,
        }
    }


def _create_rerun_t_inbox_draft(
    *, new_t_doc_id: str, project: str, module: str,
    group_id: str, title: str,
    previous_t: str, triggered_by: str, priority: str,
) -> str:
    os.makedirs(db.INBOX_DIR, exist_ok=True)
    filename = f"T_{_slugify(new_t_doc_id + '_' + title)}.md"
    filename = _resolve_unique_filename(db.INBOX_DIR, filename)
    safe_priority = priority if priority in linter.VALID_PRIORITIES else "medium"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "---",
        f"group_id: {group_id}",
        "type: T",
        f"doc_id: {new_t_doc_id}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {safe_priority}",
        f"target_id: {previous_t}",
        f"previous_t: {previous_t}",
        f"triggered_by: {triggered_by}",
        "status: draft",
        "next: TR",
        "---",
        "",
        "## Background",
        "",
        f"- Previous T: {previous_t}",
        f"- Trigger: {triggered_by} (TVR rejected)",
        f"- Created At: {now}",
        "",
        "## Rework Scope",
        "",
        "- Review the rejection reason and specify the scope of the required changes.",
        "- If needed, also list items for strengthening the TV scenarios.",
        "",
    ]
    path = os.path.join(db.INBOX_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return filename


def _followup_design_reopen(tv_doc: dict, tvr_doc: dict) -> dict:
    """design_reopen: create one DS draft + mark TV review_required + set state to Reject."""
    project = tv_doc.get("project") or ""
    module = tv_doc.get("module") or ""
    group_id = tv_doc.get("group_id") or ""
    tv_doc_id = tv_doc.get("doc_id") or ""
    tvr_doc_id = tvr_doc.get("doc_id") or ""

    # Look up existing DS / D
    prev_ds = db.get_latest_ds_in_group(group_id) if group_id else None
    prev_d = db.get_latest_d_in_group(group_id) if group_id else None
    previous_ds = (prev_ds or {}).get("doc_id") or ""
    target_d = (prev_d or {}).get("doc_id") or previous_ds

    # Reserve the DS number
    if not group_id:
        raise ValueError(f"DS numbering failed: group_id is missing (tv_doc={tv_doc_id})")
    ds_code = numbering_service.reserve_document(
        group_id, "DS", module=module or "none"
    )
    new_ds_doc_id = f"{group_id}.{ds_code}"
    new_title = f"Design Rework Request — {tv_doc.get('title') or tv_doc_id} (from {tvr_doc_id})"

    filename = _create_design_reopen_ds_draft(
        new_ds_doc_id=new_ds_doc_id, project=project, module=module,
        group_id=group_id, title=new_title,
        target_d=target_d, previous_ds=previous_ds, triggered_by=tvr_doc_id,
        priority=(tv_doc.get("priority") or "medium"),
    )

    db.insert_document(
        doc_id=new_ds_doc_id, doc_type="DS", project=project,
        module=module or None, title=new_title,
        target_id=target_d or None,
        group_id=group_id or None,
        priority=(tv_doc.get("priority") or "medium"),
        status="draft", direction="inbox",
    )
    db.update_document_fields(
        new_ds_doc_id,
        previous_ds=previous_ds or None,
        triggered_by=tvr_doc_id,
    )
    db.insert_event(new_ds_doc_id, "ds_created_reopen",
                    memo_file=filename,
                    related_doc_id=tvr_doc_id,
                    related_target_id=target_d or None,
                    note=f"previous_ds={previous_ds}")

    db.set_review_required(tv_doc_id, True)
    db.insert_event(tv_doc_id, "tv_review_flagged",
                    note="followup=design_reopen",
                    related_doc_id=new_ds_doc_id)

    return {
        "design_reopen": {
            "new_ds_doc_id": new_ds_doc_id,
            "filename": filename,
            "previous_ds": previous_ds,
            "target_d": target_d,
            "triggered_by": tvr_doc_id,
        }
    }


def _create_design_reopen_ds_draft(
    *, new_ds_doc_id: str, project: str, module: str,
    group_id: str, title: str,
    target_d: str, previous_ds: str, triggered_by: str, priority: str,
) -> str:
    """Save the DS draft created by design_reopen.

    The default save location is `_documents/FlowGate/10_requirements/`.
    At the same time, because DS is not connected to the existing intake
    pipeline, it leaves no copy in `inbox/` and exists only as a memo for
    design re-review. (Automatic D/P/L/DB bundle creation is out of scope.)
    """
    os.makedirs(db.DESIGN_REOPEN_DIR, exist_ok=True)
    filename = f"{new_ds_doc_id}_{_slugify(title)}.md"
    safe_priority = priority if priority in linter.VALID_PRIORITIES else "medium"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "---",
        f"group_id: {group_id}",
        "type: DS",
        f"doc_id: {new_ds_doc_id}",
        f"project: {project}",
        f"module: {module}",
        f"title: {title}",
        f"priority: {safe_priority}",
        f"target_id: {target_d}",
        f"previous_ds: {previous_ds}",
        f"triggered_by: {triggered_by}",
        "status: draft",
        "next: D",
        "---",
        "",
        "## Background",
        "",
        f"- Cause: TVR rejection (design_reopen) — {triggered_by}",
        f"- Previous DS: {previous_ds}",
        f"- Target D: {target_d}",
        f"- Created At: {now}",
        "",
        "## Redesign Scope",
        "",
        "- Identify which areas among D / P / L / DB require changes.",
        "- Automatic numbering covers one DS only; D/P/L/DB proceed separately.",
        "",
    ]
    path = os.path.join(db.DESIGN_REOPEN_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return filename


# ── Archive (DB001 §6) ────────────────────────────────────────────────────────

def _maybe_archive_previous_set(current_tv_doc_id: str) -> None:
    """When the current TV reaches Closed, move the previous set to the archive if one exists."""
    current = db.get_document_by_id(current_tv_doc_id)
    if current is None:
        return
    target_id = (current.get("target_id") or "").strip()
    if not target_id:
        return
    previous = db.get_previous_active_tv(target_id, current_tv_doc_id)
    if previous is None:
        return

    prev_tv_id = previous.get("doc_id") or ""
    prev_memo = db.get_created_memo_file(prev_tv_id)
    if prev_memo:
        _move_test_report_to_archive(prev_memo)
    # Move linked TVRs together as well
    prev_tvrs = db.get_documents_by_target_id(prev_tv_id, types=("TVR",))
    for tvr in prev_tvrs:
        tvr_memo = db.get_created_memo_file(tvr.get("doc_id") or "")
        if tvr_memo:
            _move_test_report_to_archive(tvr_memo)

    db.set_superseded_by(prev_tv_id, current_tv_doc_id)
    db.insert_event(prev_tv_id, "tv_superseded",
                    note=f"by={current_tv_doc_id}")


# ── Detail lookup (including TV/TVR-specific file paths) ─────────────────────

def get_tv_detail(tv_doc_id: str) -> dict | None:
    doc = db.get_document_by_id(tv_doc_id)
    if doc is None or doc.get("type") != "TV":
        return None
    ts = db.get_tv_status(tv_doc_id) or {}
    scenarios = db.get_tv_scenarios(tv_doc_id)
    clear_scope = db.get_tv_clear_scope(tv_doc_id) or {}
    tvrs = db.get_documents_by_target_id(tv_doc_id, types=("TVR",))
    events = db.get_events_by_doc_id(tv_doc_id)
    memo_file = db.get_created_memo_file(tv_doc_id)
    file_path = _find_test_report_path(memo_file) if memo_file else None
    return {
        "doc": doc,
        "tv_status": ts,
        "scenarios": scenarios,
        "clear_scope": clear_scope,
        "tvrs": tvrs,
        "events": events,
        "filename": memo_file,
        "file_path": os.path.abspath(file_path) if file_path else "",
    }


def get_tvr_detail(tvr_doc_id: str) -> dict | None:
    doc = db.get_document_by_id(tvr_doc_id)
    if doc is None or doc.get("type") != "TVR":
        return None
    tv_doc_id = (doc.get("target_id") or "").strip()
    tv_doc = db.get_document_by_id(tv_doc_id) if tv_doc_id else None
    events = db.get_events_by_doc_id(tvr_doc_id)
    memo_file = db.get_created_memo_file(tvr_doc_id)
    file_path = _find_test_report_path(memo_file) if memo_file else None
    return {
        "doc": doc,
        "tv_doc": tv_doc,
        "events": events,
        "filename": memo_file,
        "file_path": os.path.abspath(file_path) if file_path else "",
    }


# ── Tree API queries ──────────────────────────────────────────────────────────

_FILE_TREE_NATURAL_RE = re.compile(r"(\d+)")


def _file_tree_sort_key(name: str, is_dir: bool) -> tuple:
    """Sort key for file-tree entries (R0003 fix).

    Folders are grouped before files (folders-first convention), and within
    each group entries are ordered case-insensitively with numeric-aware
    ("natural") comparison so that, e.g., ``file2`` precedes ``file10`` and
    ``Zebra`` is not forced ahead of ``apple``. This replaces the previous bare
    ``sorted(os.listdir(...))`` which interleaved folders and files in raw
    Unicode codepoint order — causing numeric/uppercase-named files to float
    above lowercase-named folders.
    """
    parts = _FILE_TREE_NATURAL_RE.split(name.lower())
    # re.split with a capturing group yields text at even indices and digit
    # runs at odd indices; cast digit runs to int for natural ordering.
    natural = [int(part) if i % 2 else part for i, part in enumerate(parts)]
    return (0 if is_dir else 1, natural)


def get_file_tree(project_id: str) -> dict:
    """Return the project's file tree.

    Recursively scan the src/{project_name}/{branch} path.
    Return an empty node list when the directory does not exist.
    Entries are ordered folders-first, then by natural case-insensitive name
    (see :func:`_file_tree_sort_key`).
    """
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"

    if not project_name:
        return {"nodes": []}

    docs_root = str(src_root(project_name, branch))
    if not os.path.isdir(docs_root):
        return {"nodes": []}

    nodes: list[dict] = []
    node_id = 0

    def walk_directory(path: str, parent_id: str | None = None) -> None:
        nonlocal node_id
        try:
            raw_entries = os.listdir(path)
        except (OSError, PermissionError):
            return

        visible: list[tuple[str, str, bool]] = []
        for entry in raw_entries:
            # Do not expose DB files or hidden items in the tree
            if entry.startswith(".") or entry.lower().endswith(".db"):
                continue
            full_path = os.path.join(path, entry)
            visible.append((entry, full_path, os.path.isdir(full_path)))

        # Folders-first + natural, case-insensitive ordering (R0003).
        visible.sort(key=lambda item: _file_tree_sort_key(item[0], item[2]))

        for entry, full_path, is_dir in visible:
            node_id += 1
            current_id = str(node_id)

            if is_dir:
                node: dict[str, Any] = {
                    "id": current_id,
                    "parent_id": parent_id,
                    "type": "folder",
                    "name": entry,
                    "label": entry,
                    "path": os.path.relpath(full_path, docs_root),
                    "permissions": ["read"],
                    "children": [],
                }
                nodes.append(node)
                walk_directory(full_path, current_id)
            else:
                nodes.append({
                    "id": current_id,
                    "parent_id": parent_id,
                    "type": "file",
                    "name": entry,
                    "label": entry,
                    "path": os.path.relpath(full_path, docs_root),
                    "permissions": ["read", "download"],
                })

    walk_directory(docs_root)
    return {"nodes": nodes}


def create_storage_folder(project_id: str, parent_path: str, name: str) -> dict:
    """Create an empty folder inside the src tree."""
    import re
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj

    name = name.strip()
    if not name:
        return {"status": "error", "message": "Enter a name."}
    if re.search(r'[/\\:*?"<>|]', name):
        return {"status": "error", "message": "The name contains invalid characters."}

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    if not project_name:
        return {"status": "error", "message": "Project not found."}

    docs_root = src_root(project_name, branch)
    if parent_path:
        target = docs_root / parent_path / name
    else:
        target = docs_root / name

    if target.exists():
        return {"status": "error", "message": "An item with the same name already exists."}

    try:
        target.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "success"}


def create_storage_file(project_id: str, parent_path: str, name: str) -> dict:
    """Create an empty file inside the src tree."""
    import re
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj

    name = name.strip()
    if not name:
        return {"status": "error", "message": "Enter a name."}
    if re.search(r'[/\\:*?"<>|]', name):
        return {"status": "error", "message": "The name contains invalid characters."}

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    if not project_name:
        return {"status": "error", "message": "Project not found."}

    docs_root = src_root(project_name, branch)
    if parent_path:
        target = docs_root / parent_path / name
    else:
        target = docs_root / name

    if target.exists():
        return {"status": "error", "message": "An item with the same name already exists."}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "success"}


def get_group_tree(project_id: str) -> dict:
    """Return the project's document tree.

    Following the prototype document-explorer structure, return the
    project -> module -> group -> document hierarchy as a flat node list.
    """
    # Query the group list (based on the main DB)
    groups = db.get_groups_by_projects([project_id])

    # 0275 NR0003 원인 3: this tree used to issue one documents query per group
    # plus one 'created'-event memo_file query per document (≈ 2 + G + D queries
    # per load — thousands on a real project, ruinous against a remote DB).
    # Batch all three lookups upfront so a tree load stays at a handful of
    # queries regardless of tree size.
    #
    # 0276 NR0003 발견 1: batching alone traded query count for bind-parameter
    # count — the three batched lookups passed the project's whole group list and
    # whole doc_id list as IN(...) parameters (thousands of %s per tree load, and
    # still growing linearly with the project). Every one of those lists WAS the
    # project, so `project_id = ?` expresses the same filter with one parameter,
    # and the grouped/orphan split moves to Python below.
    group_ids = [dict(g)["group_id"] for g in groups]
    known_group_ids = set(group_ids)
    all_tree_docs = [dict(d) for d in db.get_docs_for_tree_by_project(project_id)]
    try:
        memo_files = db.get_created_memo_files_map_by_project(project_id)
    except Exception:
        memo_files = {}

    def _by_doc_id_desc(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda d: d.get("doc_id") or "", reverse=True)

    # Reproduces the ordering the replaced queries applied:
    #   grouped -> ORDER BY group_id, doc_id DESC (per-group list: doc_id DESC)
    #   orphans -> ORDER BY module, doc_id DESC   (NULL module first, as in SQL)
    docs_by_group: dict[str, list[dict]] = {gid: [] for gid in group_ids}
    orphan_docs: list[dict] = []
    for doc in all_tree_docs:
        doc_group_id = doc.get("group_id")
        if doc_group_id in known_group_ids:
            docs_by_group[doc_group_id].append(doc)
        else:
            orphan_docs.append(doc)
    for _gid, _docs in docs_by_group.items():
        docs_by_group[_gid] = _by_doc_id_desc(_docs)
    orphan_docs = sorted(
        _by_doc_id_desc(orphan_docs),
        key=lambda d: (d.get("module") is not None, d.get("module") or ""),
    )

    # Fixed column names from the main DB schema
    doc_type_col = "type_code"
    doc_file_col = "file_path"

    def _lookup_project(project_key: str) -> dict | None:
        try:
            row = db_projects.get_by_id(project_key)
            if row:
                return row
        except Exception:
            pass

        server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        candidate_paths = [
            os.path.join(server_dir, "flowgate.db"),
            os.path.abspath("flowgate.db"),
        ]
        seen_paths: set[str] = set()
        for db_path in candidate_paths:
            db_path = os.path.normpath(db_path)
            if db_path in seen_paths or not os.path.exists(db_path):
                continue
            seen_paths.add(db_path)
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as project_conn:
                    project_conn.row_factory = sqlite3.Row
                    has_projects = project_conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'"
                    ).fetchone()
                    if not has_projects:
                        continue
                    row = project_conn.execute(
                        "SELECT * FROM projects WHERE project_id = ?",
                        (project_key,),
                    ).fetchone()
                    if row:
                        return dict(row)
            except sqlite3.Error:
                continue
        return None

    project_label = project_id
    project_row = _lookup_project(project_id)
    if project_row:
        project_name = (project_row.get("project_name") or "").strip()
        if project_name:
            project_label = project_name

    nodes: list[dict] = []
    project_node_id = f"project:{project_id}"
    module_nodes: dict[str, dict[str, Any]] = {}

    _dir_map = {
        "outbox": db.OUTBOX_DIR,
        "inbox": db.INBOX_DIR,
        "processed": db.PROCESSED_DIR,
        "accept": db.ACCEPT_DIR,
        "reject": db.REJECT_DIR,
        "cancelled": db.CANCELLED_DIR,
    }

    def _module_label(module: str | None) -> str:
        return (module or "").strip() or "none"

    def _module_node(module: str | None, title: str | None = None) -> dict[str, Any]:
        label = _module_label(module)
        node_id = f"module:{project_id}:{label}"
        if node_id not in module_nodes:
            # For consistency with TR556, the "none" module uses the existing all-label title
            if label == "none":
                resolved_title = "All"
            else:
                resolved_title = (title or "").strip() or label
            module_nodes[node_id] = {
                "id": node_id,
                "parent_id": project_node_id,
                "node_type": "module",
                "type_code": None,
                "number": None,
                "filename": None,
                "label": label,
                "title": resolved_title,
                "has_md": False,
                "md_path": None,
                "children": [],
            }
            nodes.append(module_nodes[node_id])
        return module_nodes[node_id]

    def _group_number(group_id: str) -> str | None:
        tail = group_id.rsplit(".", 1)[-1]
        return tail if tail and tail.isdigit() else None

    def _build_doc_node(doc_row, parent_id: str) -> dict[str, Any]:
        doc = dict(doc_row)
        doc_id = doc["doc_id"]
        doc_type = doc.get(doc_type_col, "")
        title = doc.get("title", "")
        doc_type_label = get_type_name(doc_type)
        memo_file = memo_files.get(doc_id) or doc.get(doc_file_col)
        direction = doc.get("direction", "")
        _base_dir = _dir_map.get(direction) if direction else None
        _md_path = os.path.join(_base_dir, memo_file) if (_base_dir and memo_file) else memo_file
        return {
            "id": doc_id,
            "parent_id": parent_id,
            "node_type": "document",
            "type_code": doc_type,
            "number": doc_id.rsplit(".", 1)[-1],
            "filename": memo_file,
            "label": f"[{doc_type_label}]: {title}",
            "has_md": bool(memo_file),
            "md_path": _md_path,
        }

    nodes.append({
        "id": project_node_id,
        "parent_id": None,
        "node_type": "project",
        "type_code": None,
        "number": None,
        "filename": None,
        "label": project_label,
        "has_md": False,
        "md_path": None,
        "children": [],
    })

    # Also include modules registered in the project_modules table in the tree
    # (display even when there is no group)
    try:
        pm_rows = db.get_project_modules(project_id)
        for pm_row in pm_rows:
            _module_node(pm_row.get("name"), title=pm_row.get("title"))
    except Exception:
        pass  # Defensive handling for missing tables, etc.

    for group_row in groups:
        group = dict(group_row)
        group_id = group["group_id"]
        module_node = _module_node(group.get("module"))
        group_node: dict[str, Any] = {
            "id": group_id,
            "parent_id": module_node["id"],
            "node_type": "group",
            "type_code": None,
            "number": _group_number(group_id),
            "filename": None,
            "label": group.get("title", group_id),
            "has_md": False,
            "md_path": None,
            "is_final_approved": False,
            "is_discarded": False,
            "children": []
        }
        module_node["children"].append(group_node)
        nodes.append(group_node)

        docs = docs_by_group.get(group_id, [])

        # A group is final-approved when the R/B document that owns its workflow has
        # reached wf_done. This reuses the docs already fetched above so no extra
        # query is issued. Reuses the single source of truth: the final-approval AC
        # flipping the R doc to wf_done (see D0002 §2).
        #
        # A group is discarded when it carries a file-less DC (discard) record —
        # the AC-symmetric derivation (TR0029.0008 review r2 #5). The explorer hides
        # discarded groups by default, mirroring the final-approved hide toggle.
        for doc_row in docs:
            doc = dict(doc_row)
            if doc.get("type_code") in WORKFLOW_ROOT_TYPES and doc.get("doc_review_status") == "wf_done":
                group_node["is_final_approved"] = True
            if doc.get("type_code") == _GROUP_DISCARD_TYPE:
                group_node["is_discarded"] = True

        for doc_row in docs:
            doc_node = _build_doc_node(doc_row, group_id)
            group_node["children"].append(doc_node)
            nodes.append(doc_node)

    # Handle orphan documents: include documents whose group_id is missing from
    # the groups table under the uncategorized node (fetched upfront above,
    # together with the memo_file batch)
    orphan_nodes: dict[str, dict[str, Any]] = {}
    for doc_row in orphan_docs:
        doc = dict(doc_row)
        module_node = _module_node(doc.get("module"))
        group_key = doc.get("group_id") or "__ungrouped__"
        orphan_id = f"orphan:{module_node['id']}:{group_key}"
        if orphan_id not in orphan_nodes:
            label = f"Uncategorized: {group_key}" if group_key != "__ungrouped__" else "Uncategorized"
            orphan_nodes[orphan_id] = {
                "id": orphan_id,
                "parent_id": module_node["id"],
                "node_type": "orphan",
                "type_code": None,
                "number": _group_number(group_key),
                "filename": None,
                "label": label,
                "has_md": False,
                "md_path": None,
                "children": [],
            }
            module_node["children"].append(orphan_nodes[orphan_id])
            nodes.append(orphan_nodes[orphan_id])
        doc_node = _build_doc_node(doc, orphan_id)
        orphan_nodes[orphan_id]["children"].append(doc_node)
        nodes.append(doc_node)

    return {"nodes": nodes}
