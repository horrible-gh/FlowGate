"""Main dashboard summary data assembly."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from modules.flow_gate.db.connection import get_store

_log = logging.getLogger(__name__)

_ACTIVITY_EVENT_TYPES = (
    "doc_created",
    "action_taken",
    "doc_edited",
    "state_changed",
    "qna_answered",
    "group_approved",
)


# Action-series code for a file-less group-discard record. Source of truth:
# process_service._GROUP_DISCARD_TYPE. Mirrored here as a literal so the dashboard
# query has no import coupling to process_service (heavy module / circular risk).
_GROUP_DISCARD_TYPE = "DC"


class DashboardDataError(RuntimeError):
    """Dashboard source data is internally inconsistent."""


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: Any) -> str:
    return _parse_time(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _metadata(raw: Any) -> tuple[dict[str, Any], str]:
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}, text
    return (value if isinstance(value, dict) else {}), text


def _fetch_documents(doc_ids: set[str]) -> dict[str, dict]:
    if not doc_ids:
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    rows = get_store()._fetch_all(
        f"SELECT * FROM documents WHERE doc_id IN ({placeholders})",
        sorted(doc_ids),
    )
    return {row["doc_id"]: row for row in rows}


def _fetch_project_groups(project_id: str) -> dict[str, dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM groups WHERE project_id = ? AND deleted_at IS NULL",
        [project_id],
    )
    return {row["group_id"]: row for row in rows}


def _event_rows(project_id: str) -> list[dict]:
    placeholders = ",".join("?" for _ in _ACTIVITY_EVENT_TYPES)
    return get_store()._fetch_all(
        f"""
        SELECT
            we.*,
            d.doc_id AS joined_doc_id,
            d.project_id AS joined_doc_project_id,
            d.group_id AS joined_doc_group_id,
            d.type_code AS joined_doc_type_code,
            d.title AS joined_doc_title,
            u.username AS actor_username
        FROM workflow_events we
        LEFT JOIN documents d ON d.id = we.document_id
        LEFT JOIN users u ON u.user_id = we.actor_user_id
        WHERE we.project_id = ?
          AND we.event_type IN ({placeholders})
        ORDER BY we.created_at DESC, we.id DESC
        """,
        [project_id, *_ACTIVITY_EVENT_TYPES],
    )


def _joined_document(row: dict, project_id: str) -> dict | None:
    if row.get("joined_doc_id") and row.get("joined_doc_project_id") == project_id:
        return {
            "doc_id": row["joined_doc_id"],
            "project_id": row["joined_doc_project_id"],
            "group_id": row.get("joined_doc_group_id"),
            "type_code": row.get("joined_doc_type_code"),
            "title": row.get("joined_doc_title"),
        }
    return None


def _document_dto(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "doc_id": doc["doc_id"],
        "type_code": doc.get("type_code"),
        "title": doc.get("title"),
    }


def _transition(row: dict, *, strip_review_prefix: bool = False) -> dict:
    def clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        if strip_review_prefix and text.startswith("review:"):
            return text.removeprefix("review:")
        return text

    return {
        "from_state": clean(row.get("from_state")),
        "to_state": clean(row.get("to_state")),
    }


def _normalize_activity(
    row: dict,
    project_id: str,
    documents: dict[str, dict],
    groups: dict[str, dict],
) -> dict | None:
    metadata, metadata_text = _metadata(row.get("metadata"))
    event_type = row.get("event_type")
    joined_doc = _joined_document(row, project_id)
    doc = joined_doc
    transition = None

    if event_type == "doc_created":
        activity_type = "document_created"
    elif event_type == "action_taken":
        if metadata.get("action_code") != "doc_created" or not metadata.get("doc_id"):
            return None
        activity_type = "document_created"
        doc = documents.get(str(metadata["doc_id"]))
    elif event_type == "doc_edited":
        activity_type = "document_edited"
        metadata_doc_id = metadata.get("doc_id")
        if metadata_doc_id:
            doc = documents.get(str(metadata_doc_id))
    elif event_type == "qna_answered":
        activity_type = "question_answered"
        doc = (
            documents.get(str(metadata.get("a_doc_id")))
            or documents.get(str(metadata.get("q_doc_id")))
        )
        transition = _transition(row)
    elif event_type == "group_approved":
        activity_type = "group_approved"
        doc = None
        if row.get("from_state") is not None or row.get("to_state") is not None:
            transition = _transition(row)
    elif event_type == "state_changed":
        action = str(metadata.get("action") or "")
        is_question_answer = (
            (joined_doc and joined_doc.get("type_code") == "Q"
             and row.get("to_state") in {"answered", "closed"})
            or action == "auto_answered"
            or metadata_text == "q_answered"
        )
        is_review = (
            str(row.get("from_state") or "").startswith("review:")
            or str(row.get("to_state") or "").startswith("review:")
            or action.startswith("review_")
        )
        if is_question_answer:
            activity_type = "question_answered"
        elif is_review:
            activity_type = "workflow_state_changed"
        elif joined_doc:
            activity_type = "document_state_changed"
        elif row.get("group_id"):
            activity_type = "workflow_state_changed"
        else:
            return None
        transition = _transition(row, strip_review_prefix=is_review)
    else:
        return None

    if doc and doc.get("project_id") != project_id:
        doc = None

    group_id = doc.get("group_id") if doc else row.get("group_id")
    group = groups.get(group_id) if group_id else None
    if doc and row.get("group_id") and doc.get("group_id") != row.get("group_id"):
        _log.warning(
            "Dashboard activity group mismatch event_id=%s", row.get("id")
        )

    try:
        occurred_at = _utc_iso(row.get("created_at"))
    except (TypeError, ValueError):
        _log.warning(
            "Dashboard activity excluded due to invalid time event_id=%s", row.get("id")
        )
        return None

    navigation: dict[str, Any]
    if doc:
        navigation = {
            "kind": "document",
            "doc_id": doc["doc_id"],
            "group_id": doc.get("group_id"),
        }
    elif group:
        navigation = {"kind": "group", "group_id": group["group_id"]}
    else:
        navigation = {"kind": "none"}

    actor = None
    if row.get("actor_user_id") and row.get("actor_username"):
        actor = {
            "user_id": row["actor_user_id"],
            "username": row["actor_username"],
        }

    return {
        "event_id": row["id"],
        "activity_type": activity_type,
        "occurred_at": occurred_at,
        "actor": actor,
        "group": (
            {"group_id": group["group_id"], "title": group["title"]}
            if group else None
        ),
        "document": _document_dto(doc),
        "transition": transition,
        "navigation": navigation,
    }


def _normalized_activities(project_id: str) -> list[dict]:
    """Return ALL normalized inflow activities for a project, newest first.

    Shared by the dashboard recent-activity card (list_recent_activities) and the 🔔 notification
    feed (get_notification_feed, group 0045). Both read the same workflow_events source — the
    notification center is "the same data, made persistent and unread-aware" (NR0003 §2), so it must
    not diverge from the dashboard's normalization.
    """
    rows = _event_rows(project_id)
    metadata_doc_ids: set[str] = set()
    for row in rows:
        metadata, _ = _metadata(row.get("metadata"))
        for key in ("doc_id", "q_doc_id", "a_doc_id"):
            if metadata.get(key):
                metadata_doc_ids.add(str(metadata[key]))

    documents = _fetch_documents(metadata_doc_ids)
    groups = _fetch_project_groups(project_id)
    items = [
        item
        for row in rows
        if (item := _normalize_activity(row, project_id, documents, groups)) is not None
    ]
    items.sort(key=lambda item: (_parse_time(item["occurred_at"]), item["event_id"]), reverse=True)
    return items


def _page(items: list[dict], limit: int) -> dict:
    page = items[:limit]
    return {
        "limit": limit,
        "total": len(items),
        "has_more": len(items) > len(page),
        "items": page,
    }


def list_recent_activities(project_id: str, limit: int) -> dict:
    return _page(_normalized_activities(project_id), limit)


def _count_unread(items: list[dict], last_seen_at: Any) -> int:
    """Count feed items strictly newer than the user's last-seen watermark.

    A null/empty watermark (never marked read) means every item is unread. An unparseable watermark
    is treated the same way rather than silently hiding inflow.
    """
    if not last_seen_at:
        return len(items)
    try:
        cutoff = _parse_time(last_seen_at)
    except (TypeError, ValueError):
        return len(items)
    unread = 0
    for item in items:
        try:
            if _parse_time(item["occurred_at"]) > cutoff:
                unread += 1
        except (TypeError, ValueError):
            continue
    return unread


def get_notification_feed(project_id: str, last_seen_at: Any, limit: int) -> dict:
    """Assemble the 🔔 notification center payload for a project (R0001 group 0045, NR0003 A안).

    Returns the persistent document-inflow feed (newest first) plus the unread count derived from
    last_seen_at, so the header bell can render an unread badge. The watermark itself is owned by the
    caller (notification_seen table) and passed in, keeping this service decoupled from user-state
    storage and testable against workflow_events alone.
    """
    with get_store().transaction():
        items = _normalized_activities(project_id)
    feed = _page(items, limit)
    return {
        "ok": True,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "last_seen_at": _utc_iso(last_seen_at) if last_seen_at else None,
        "unread_count": _count_unread(items, last_seen_at),
        "recent_activities": feed,
    }


def _active_workflow_rows(project_id: str) -> list[dict]:
    return get_store()._fetch_all(
        """
        WITH active AS (
            SELECT
                r.doc_id AS requirement_doc_id,
                r.title AS requirement_title,
                r.group_id,
                r.updated_at AS requirement_updated_at,
                g.title AS group_title,
                ws.id AS sequence_id,
                ws.updated_at AS sequence_updated_at
            FROM documents r
            JOIN groups g
              ON g.group_id = r.group_id
             AND g.project_id = r.project_id
             AND g.deleted_at IS NULL
            LEFT JOIN workflow_sequences ws ON ws.doc_id = r.doc_id
            WHERE r.project_id = ?
              AND r.type_code IN ('R', 'B')
              AND r.doc_review_status = 'wf_in_progress'
              AND r.group_id IS NOT NULL
              -- Exclude discarded groups: a group carrying a file-less DC (discard)
              -- record is terminated, but the discard never flips the requirement out
              -- of wf_in_progress, so without this guard the discarded group keeps
              -- showing in the dashboard "워크플로 현황" list (R0079.0001). This mirrors
              -- the is_discarded derivation in process_service (group tree) and the
              -- FE "작업 중" stat card, both of which already drop discarded groups.
              AND NOT EXISTS (
                  SELECT 1 FROM documents dc
                  WHERE dc.group_id = r.group_id
                    AND dc.project_id = r.project_id
                    AND dc.type_code = ?
              )
        ),
        eligible_heads AS (
            SELECT
                wsi.*,
                d.doc_review_status AS result_doc_review_status,
                ROW_NUMBER() OVER (
                    PARTITION BY wsi.sequence_id
                    ORDER BY
                        CASE WHEN wsi.result_doc_id IS NOT NULL THEN 0 ELSE 1 END,
                        wsi.sort_order
                ) AS head_rank
            FROM workflow_sequence_items wsi
            LEFT JOIN documents d ON d.doc_id = wsi.result_doc_id
            WHERE wsi.result_doc_id IS NULL
               OR (
                    wsi.type NOT IN ('M')
                    AND COALESCE(d.doc_review_status, '') NOT IN ('approved')
               )
        ),
        sequence_stats AS (
            SELECT
                wsi.sequence_id,
                COUNT(*) AS item_count,
                SUM(
                    CASE
                        WHEN wsi.result_doc_id IS NOT NULL
                         AND (
                              wsi.type = 'M'
                              OR COALESCE(d.doc_review_status, '') = 'approved'
                         )
                        THEN 1
                        ELSE 0
                    END
                ) AS completed_item_count
            FROM workflow_sequence_items wsi
            LEFT JOIN documents d ON d.doc_id = wsi.result_doc_id
            GROUP BY wsi.sequence_id
        )
        SELECT
            active.*,
            COALESCE(stats.item_count, 0) AS sequence_item_count,
            COALESCE(stats.completed_item_count, 0) AS completed_item_count,
            head.id AS head_item_id,
            head.type AS head_type,
            head.result_doc_id,
            head.result_doc_review_status,
            head.updated_at AS head_updated_at,
            result_doc.project_id AS head_doc_project_id,
            result_doc.group_id AS head_doc_group_id,
            result_doc.type_code AS head_doc_type_code,
            result_doc.title AS head_doc_title,
            result_doc.updated_at AS head_doc_updated_at
        FROM active
        LEFT JOIN sequence_stats stats
          ON stats.sequence_id = active.sequence_id
        LEFT JOIN eligible_heads head
          ON head.sequence_id = active.sequence_id
         AND head.head_rank = 1
        LEFT JOIN documents result_doc ON result_doc.doc_id = head.result_doc_id
        """,
        [project_id, _GROUP_DISCARD_TYPE],
    )


def list_active_workflows(project_id: str, limit: int) -> dict:
    rows = _active_workflow_rows(project_id)
    seen_groups: set[str] = set()
    items: list[dict] = []

    for row in rows:
        requirement_id = row["requirement_doc_id"]
        group_id = row["group_id"]
        if group_id in seen_groups:
            raise DashboardDataError(f"multiple active requirements for group: {group_id}")
        seen_groups.add(group_id)
        if row.get("sequence_id") is None:
            raise DashboardDataError(f"workflow sequence missing: {requirement_id}")
        if int(row.get("sequence_item_count") or 0) <= 0:
            raise DashboardDataError(f"workflow sequence empty: {requirement_id}")
        if not row.get("requirement_title") or not row.get("group_title"):
            raise DashboardDataError(f"workflow title missing: {requirement_id}")

        total_steps = int(row["sequence_item_count"])
        completed_steps = min(int(row.get("completed_item_count") or 0), total_steps)
        requirement_navigation = {
            "kind": "document",
            "doc_id": requirement_id,
            "group_id": group_id,
        }
        result_doc_id = row.get("result_doc_id")
        if row.get("head_item_id") is None:
            stage = {
                "state": "pending",
                "type_code": "AC",
                "head_doc_id": None,
                "head_doc_title": None,
                "head_doc_review_status": None,
            }
            navigation = requirement_navigation
        elif result_doc_id:
            if (
                row.get("head_doc_project_id") != project_id
                or row.get("head_doc_group_id") != group_id
                or row.get("head_doc_type_code") != row.get("head_type")
                or not row.get("head_doc_title")
            ):
                raise DashboardDataError(f"invalid workflow head document: {result_doc_id}")
            stage = {
                "state": "in_progress",
                "type_code": row["head_type"],
                "head_doc_id": result_doc_id,
                "head_doc_title": row.get("head_doc_title"),
                "head_doc_review_status": row.get("result_doc_review_status"),
            }
            navigation = {
                "kind": "document",
                "doc_id": result_doc_id,
                "group_id": group_id,
            }
        else:
            stage = {
                "state": "pending",
                "type_code": row["head_type"],
                "head_doc_id": None,
                "head_doc_title": None,
                "head_doc_review_status": None,
            }
            navigation = requirement_navigation

        time_values = [
            row.get("requirement_updated_at"),
            row.get("sequence_updated_at"),
            row.get("head_updated_at"),
        ]
        if result_doc_id:
            time_values.append(row.get("head_doc_updated_at"))
        try:
            updated_at_dt = max(_parse_time(value) for value in time_values if value)
        except (TypeError, ValueError) as exc:
            raise DashboardDataError(
                f"invalid workflow timestamp: {requirement_id}"
            ) from exc

        items.append({
            "group_id": group_id,
            "group_title": row["group_title"],
            "requirement": {
                "doc_id": requirement_id,
                "title": row["requirement_title"],
            },
            "stage": stage,
            "progress": {
                "completed_steps": completed_steps,
                "total_steps": total_steps,
                "percent": round((completed_steps / total_steps) * 100),
            },
            "updated_at": _utc_iso(updated_at_dt),
            "navigation": navigation,
        })

    items.sort(key=lambda item: item["group_id"])
    items.sort(key=lambda item: _parse_time(item["updated_at"]), reverse=True)
    total = len(items)
    page = items[:limit]
    return {
        "limit": limit,
        "total": total,
        "has_more": total > len(page),
        "items": page,
    }


def get_dashboard_summary(
    project_id: str,
    activity_limit: int,
    workflow_limit: int,
) -> dict:
    with get_store().transaction():
        recent_activities = list_recent_activities(project_id, activity_limit)
        active_workflows = list_active_workflows(project_id, workflow_limit)
    return {
        "ok": True,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "recent_activities": recent_activities,
        "active_workflows": active_workflows,
    }
