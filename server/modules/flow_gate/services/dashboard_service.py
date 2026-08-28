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

# Notification feed (🔔) shows a deliberately QUIETER subset than the dashboard recent-activity card.
# R0001 group 0118 ("the notification feature is useless"): a single document registration decomposes into multiple
# workflow_events — action_taken/doc_created + state_changed(review_submit) + state_changed(review_approve),
# plus the parent's child_created / workflow_decide state_changed transitions — and the feed projected
# EVERY one as its own notification ("3-5 per registration"). The user judged the feature useless because of
# this noise. Narrowing the feed to genuinely notable, ~one-per-document events collapses a registration
# to a single notification while leaving the dashboard card's full activity stream
# (_ACTIVITY_EVENT_TYPES, via list_recent_activities) completely untouched — the two surfaces are now
# decoupled.
#
# Both doc_created AND action_taken are kept: creation is recorded as exactly ONE of them depending on the
# path (inbox `new` → action_taken/doc_created; workflow-decision → doc_created; never both — verified
# against the live event log), so dropping either would zero out creation notifications for one path.
# The dropped noise is `state_changed` (the per-document review submit/approve micro-transitions and the
# parent cascade) and `doc_edited` (every self/rework edit). `qna_answered` and `group_approved` are kept
# as meaningful milestones.
#
# R0001 group 0135 / N0008: `continuous_work_ended` is now ALSO promoted — but ONLY this single terminal
# event. It fires exactly once, when an unmanned continuous (self-chaining) run reaches its target and
# stops (inbox_routes._continuation_self_chain), so it does NOT reintroduce the 0118 per-step noise: a
# 10-step chain still emits ~10 doc_created rows PLUS one terminal "continuous work finished" row that reads
# differently from the intermediate inflow. Single-mode work has no such event and is unaffected. The
# present-tense `work_started` and the raw `state_changed` micro-transitions stay OUT (0118 / group 0125
# NR0003 recommendation 4 invariant): promoting a per-transition state signal is what caused the notification flood.
_NOTIFICATION_EVENT_TYPES = (
    "doc_created",
    "action_taken",
    "qna_answered",
    "group_approved",
    "continuous_work_ended",
    "continuous_work_failed",
    # flowgate.default.0157: the test-run auto-recovery loop signals. `test_run_repair` fires at most
    # MAX_REPAIR_ATTEMPTS times per doc (an INFRA failure being re-fired); `test_run_repair_exhausted`
    # fires once at the cap — the single case the user must intervene. Both bounded → no 0118 flood.
    "test_run_repair",
    "test_run_repair_exhausted",
)


# Action-series code for a file-less group-discard record. Source of truth:
# process_service._GROUP_DISCARD_TYPE. Mirrored here as a literal so the dashboard
# query has no import coupling to process_service (heavy module / circular risk).
_GROUP_DISCARD_TYPE = "DC"
_WORKFLOW_ROOT_TYPES = ("R", "B")

# Newest-events window scanned by _event_rows (0279 P3-10).
#
# The query below had NO LIMIT: it returned every matching workflow_event for the
# project, twice LEFT JOINed against documents and users, and _normalized_activities
# then ran _metadata() JSON parsing plus _normalize_activity() over all of them and
# sorted the result in Python — only for _page() to slice off the first ~10 rows.
# Cost grew linearly and without bound as the event log accumulated, on a query that
# runs on every dashboard load and every 🔔 poll. This is the DB-side answer to
# R0001 ("is it the DB or the files?"): nothing here is slow at 500 events and all of it
# is slow at 500,000, which is why the stall appeared gradually rather than at once.
#
# The ORDER BY is already newest-first and both callers render newest-first from the
# head of the list, so taking the newest N is the window they actually consume. N is
# set far above any page size (callers pass limits in the tens) to leave room for the
# post-fetch filtering in _normalize_activity and _without_terminal_group_items.
#
# Accepted tradeoff, stated plainly: `total`, `has_more` and `unread_count` are now
# computed over this window, so they saturate here instead of counting the whole
# history — a bell badge that would have read 5,000 reads 2,000. The displayed items
# are unchanged. Making those counts exact without the full scan needs a COUNT(*)
# aggregate per surface, which is a separate change.
_EVENT_SCAN_LIMIT = 2000


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


def _fetch_terminal_group_ids(project_id: str) -> set[str]:
    """Groups that have left active work: final-approved AC or discarded DC."""
    rows = get_store()._fetch_all(
        """
        SELECT DISTINCT group_id
        FROM documents
        WHERE project_id = ?
          AND group_id IS NOT NULL
          AND (
                (type_code IN (?, ?) AND doc_review_status = 'wf_done')
             OR type_code = ?
          )
        """,
        [project_id, *_WORKFLOW_ROOT_TYPES, _GROUP_DISCARD_TYPE],
    )
    return {row["group_id"] for row in rows if row.get("group_id")}


def _event_rows(
    project_id: str,
    event_types: tuple[str, ...] = _ACTIVITY_EVENT_TYPES,
    scan_limit: int = _EVENT_SCAN_LIMIT,
) -> list[dict]:
    """Newest ``scan_limit`` matching workflow events for a project, newest first.

    See ``_EVENT_SCAN_LIMIT`` for why the window exists and what it costs.
    """
    placeholders = ",".join("?" for _ in event_types)
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
        LIMIT ?
        """,
        [project_id, *event_types, scan_limit],
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
        # R0001 group 0135 / N0008 (mockup 3): populated by _attach_review_signals after the item list is
        # built. `verdict` is the latest AI review verdict (pass|issues|hold|null), `finding_count` the
        # number of AI findings, `status` the doc_review_status. The 🔔 feed uses these to paint the
        # trust colour (🟢🟡🔴) and AI badge on each row so "shown as done but needs checking" (AI issues)
        # is flagged in-list — the source is the same document_reviews the triage cockpit reads
        # (NR0009 §finding 5). Left as null when the document has no review yet.
        "review": None,
    }


def _finding_count(findings: Any) -> int:
    """Number of AI findings recorded on a review (findings is a JSON-array string)."""
    if not findings:
        return 0
    try:
        data = json.loads(findings) if isinstance(findings, str) else findings
    except (ValueError, TypeError):
        return 0
    return len(data) if isinstance(data, list) else 0


def _attach_review_signals(items: list[dict]) -> None:
    """Enrich each item's document DTO with its AI verdict + review status (mockup 3 trust colours).

    R0001 group 0135 / N0008: the live-feed mockup paints every completed row with a trust colour
    (🟢 pass / 🟡 hold / 🔴 issues) and an AI badge so the user can see "says it is done but needs checking" without
    opening the document. The signals already exist — latest `document_reviews.verdict` + `doc_review_status`
    (NR0009 §finding 5, same source as the triage cockpit) — so this is a read-only projection: no new column,
    no migration. Defensive by design: a minimal store without a document_reviews table (or a fresh install
    before the first review) simply yields null verdicts and the rows render neutral.
    """
    doc_ids = {
        item["document"]["doc_id"]
        for item in items
        if item.get("document")
    }
    if not doc_ids:
        return
    store = get_store()
    ordered = sorted(doc_ids)
    placeholders = ",".join("?" for _ in ordered)

    statuses: dict[str, Any] = {}
    try:
        for row in store._fetch_all(
            f"SELECT doc_id, doc_review_status FROM documents WHERE doc_id IN ({placeholders})",
            ordered,
        ):
            statuses[row["doc_id"]] = row.get("doc_review_status")
    except Exception:  # pragma: no cover - defensive against minimal/legacy stores
        _log.debug("feed review-status enrichment skipped", exc_info=True)

    verdicts: dict[str, dict] = {}
    try:
        for row in store._fetch_all(
            f"SELECT doc_id, verdict, findings FROM document_reviews "
            f"WHERE doc_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
            ordered,
        ):
            verdicts.setdefault(row["doc_id"], row)  # newest-first → first seen is latest
    except Exception:  # pragma: no cover - table absent in minimal test stores / fresh installs
        _log.debug("feed review-verdict enrichment skipped", exc_info=True)

    for item in items:
        doc = item.get("document")
        if not doc:
            continue
        doc_id = doc["doc_id"]
        review = verdicts.get(doc_id)
        doc["review"] = {
            "status": statuses.get(doc_id),
            "verdict": review.get("verdict") if review else None,
            "finding_count": _finding_count(review.get("findings")) if review else 0,
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
    elif event_type == "continuous_work_ended":
        # R0001 group 0135 / N0008: the ONE terminal signal of an unmanned continuous run,
        # surfaced as a distinct "continuous work finished" notification so the final completion reads
        # differently from the per-step doc_created inflow. `doc` is the joined terminal
        # document (event carries document_id → joined_doc); navigation points at it so the
        # user lands on the last document of the finished chain. Only this terminal event is
        # promoted — intermediate state_changed stays excluded (0118 noise invariant).
        activity_type = "continuous_work_completed"
        if not doc:
            metadata_doc_id = metadata.get("doc_id")
            if metadata_doc_id:
                doc = documents.get(str(metadata_doc_id))
    elif event_type == "continuous_work_failed":
        # R0001 group 0154 / NR0004 Gap A: failure-path counterpart of continuous_work_ended. An
        # unmanned chain whose server-side test_run went RED assembles no TSR and stops silently; this
        # is the ONE terminal signal of that stop, surfaced as a distinct "continuous work failed" notification
        # pointing at the TS document that failed so the user lands on it. Fires once per failed run —
        # same once-per-terminal-event discipline as the completion signal, no 0118 per-step noise.
        activity_type = "continuous_work_failed"
        if not doc:
            metadata_doc_id = metadata.get("doc_id")
            if metadata_doc_id:
                doc = documents.get(str(metadata_doc_id))
    elif event_type == "test_run_repair":
        # flowgate.default.0157: the auto-recovery loop re-fired an INFRA-failed test run. Surfaced so
        # the user can see the retry; points at the TS document under repair. Bounded per doc.
        activity_type = "test_run_repair"
        if not doc:
            metadata_doc_id = metadata.get("doc_id")
            if metadata_doc_id:
                doc = documents.get(str(metadata_doc_id))
    elif event_type == "test_run_repair_exhausted":
        # flowgate.default.0157: the loop hit the attempt cap and stopped — the one case the user must
        # intervene. Points at the TS document; the attempt history rides in the event metadata.
        activity_type = "test_run_repair_exhausted"
        if not doc:
            metadata_doc_id = metadata.get("doc_id")
            if metadata_doc_id:
                doc = documents.get(str(metadata_doc_id))
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


def _normalized_activities(
    project_id: str,
    event_types: tuple[str, ...] = _ACTIVITY_EVENT_TYPES,
) -> list[dict]:
    """Return normalized inflow activities for a project, newest first.

    Shared by the dashboard recent-activity card (list_recent_activities, full _ACTIVITY_EVENT_TYPES)
    and the 🔔 notification feed (get_notification_feed). Group 0045 originally made the feed identical
    to the dashboard ("the same data, made persistent and unread-aware" — NR0003 §2). Group 0118
    (R0001 "the notification feature is useless") deliberately decoupled them: the feed now passes the quieter
    _NOTIFICATION_EVENT_TYPES so a single registration is one notification instead of 3~5, while the
    dashboard card keeps the full stream. event_types selects which surface this call serves; the
    normalization itself is identical so the two never diverge in shape.
    """
    rows = _event_rows(project_id, event_types)
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
    _attach_review_signals(items)
    items.sort(key=lambda item: (_parse_time(item["occurred_at"]), item["event_id"]), reverse=True)
    return items


def _activity_group_id(item: dict) -> str | None:
    group = item.get("group")
    if group and group.get("group_id"):
        return str(group["group_id"])
    navigation = item.get("navigation") or {}
    if navigation.get("group_id"):
        return str(navigation["group_id"])
    return None


def _without_terminal_group_items(project_id: str, items: list[dict]) -> list[dict]:
    """Drop final-approved/discarded groups from the notification feed.

    The explorer and active-workflow card already treat R/B `wf_done` and DC groups as terminal.
    Notification rows need the same boundary so old inflow and stale AI `issues` verdicts do not keep
    completed groups in the unread/attention surfaces.
    """
    terminal_group_ids = _fetch_terminal_group_ids(project_id)
    if not terminal_group_ids:
        return items
    return [
        item
        for item in items
        if (group_id := _activity_group_id(item)) not in terminal_group_ids
    ]


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
    """Assemble the 🔔 notification center payload for a project (R0001 group 0045, NR0003 option A).

    Returns the persistent document-inflow feed (newest first) plus the unread count derived from
    last_seen_at, so the header bell can render an unread badge. The watermark itself is owned by the
    caller (notification_seen table) and passed in, keeping this service decoupled from user-state
    storage and testable against workflow_events alone.
    """
    with get_store().transaction():
        items = _normalized_activities(project_id, _NOTIFICATION_EVENT_TYPES)
        items = _without_terminal_group_items(project_id, items)
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
              -- showing in the dashboard's workflow-status list (R0079.0001). This mirrors
              -- the is_discarded derivation in process_service (group tree) and the
              -- FE "in progress" stat card, both of which already drop discarded groups.
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


def _build_active_workflow_item(row: dict, project_id: str) -> dict:
    """Normalize one active-workflow row into a dashboard item.

    Raises DashboardDataError when the row's source data is internally inconsistent
    (orphan/empty sequence, missing title, mismatched head document, bad timestamp).
    Callers decide whether an individual bad row is fatal; list_active_workflows skips
    it so a single malformed workflow can't take down the whole dashboard (B0001/0122).
    """
    requirement_id = row["requirement_doc_id"]
    group_id = row["group_id"]
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
        # R0001 group 0125 / NR0003 recommendation 2: the stage badge previously only ever showed
        # pending / in_progress, so a finished head step was indistinguishable from a running one
        # ("there is no 'done' state at all"). Surface a 'done' state once the head document is approved
        # (or itself wf_done) so the active-workflow card can be scanned at a glance.
        head_status = row.get("result_doc_review_status")
        head_state = "done" if head_status in ("approved", "wf_done") else "in_progress"
        stage = {
            "state": head_state,
            "type_code": row["head_type"],
            "head_doc_id": result_doc_id,
            "head_doc_title": row.get("head_doc_title"),
            "head_doc_review_status": head_status,
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

    return {
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
    }


def list_active_workflows(project_id: str, limit: int) -> dict:
    """Active-workflow card data, tolerant of individual malformed rows.

    B0001 (group 0122): the dashboard read path used to be all-or-nothing — a single
    workflow left in an abnormal state (empty/orphan/zombie sequence, e.g. after the
    user deletes every step) made the whole summary endpoint return HTTP 500, taking
    the unrelated "recent activity" card down with it. We now skip the malformed
    row(s), log them, and render the healthy workflows. `skipped` surfaces how many
    rows were dropped for observability.
    """
    rows = _active_workflow_rows(project_id)
    seen_groups: set[str] = set()
    items: list[dict] = []
    skipped = 0

    for row in rows:
        group_id = row["group_id"]
        if group_id in seen_groups:
            # Duplicate active requirement for a group is a data anomaly; skip the
            # extra row rather than 500-ing the entire dashboard.
            _log.warning(
                "dashboard: skipping duplicate active requirement group=%s requirement=%s",
                group_id,
                row.get("requirement_doc_id"),
            )
            skipped += 1
            continue
        seen_groups.add(group_id)
        try:
            items.append(_build_active_workflow_item(row, project_id))
        except DashboardDataError as exc:
            _log.warning(
                "dashboard: skipping malformed active workflow group=%s: %s",
                group_id,
                exc,
            )
            skipped += 1

    items.sort(key=lambda item: item["group_id"])
    items.sort(key=lambda item: _parse_time(item["updated_at"]), reverse=True)
    total = len(items)
    page = items[:limit]
    return {
        "limit": limit,
        "total": total,
        "has_more": total > len(page),
        "items": page,
        "skipped": skipped,
    }


def _scalar_count(sql: str, params: list) -> int:
    """Run a single COUNT(...) query, returning 0 if the (optional) source table is absent.

    The state board aggregates several independent sources (documents, workflow_events,
    document_mention_copies). Some are optional in lighter deployments/tests, so a missing table
    degrades that one metric to 0 instead of failing the whole dashboard (B0001/0122 ethos).
    """
    try:
        row = get_store()._fetch_one(sql, params)
    except Exception:  # noqa: BLE001 — optional source; degrade this metric only
        _log.warning("work-state count failed: %s", sql.split()[0:6], exc_info=True)
        return 0
    if not row:
        return 0
    value = next(iter(row.values()))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_work_state_summary(project_id: str) -> dict:
    """Present-tense document work-STATE counts for the dashboard state board.

    R0001 group 0125 / NR0003: the 🔔 notification feed is a past-tense event stream, but what
    R0001 actually wants is "what state is it in right now" — working, done, copied, chain ended (NR0003 §finding 4).
    This aggregation answers that as scannable counts and is exposed on the dashboard summary,
    NOT on the notification feed (NR0003 recommendation 4 — keep _NOTIFICATION_EVENT_TYPES untouched).

    "Copied" deliberately UNIFIES the two formerly-separate copy records (NR0003 recommendation 3): the
    per-user document_mention_copies state table AND the prompt_copied workflow event, deduped to
    a distinct-document count so "which documents had their mention copied" is finally scannable in one place.
    """
    in_progress = _scalar_count(
        "SELECT COUNT(*) FROM documents "
        "WHERE project_id = ? AND doc_review_status = 'wf_in_progress'",
        [project_id],
    )
    done = _scalar_count(
        "SELECT COUNT(*) FROM documents "
        "WHERE project_id = ? AND doc_review_status = 'wf_done'",
        [project_id],
    )
    copied = _scalar_count(
        "SELECT COUNT(*) AS c FROM ("
        "  SELECT d.doc_id AS doc_id"
        "    FROM document_mention_copies mc"
        "    JOIN documents d ON d.doc_id = mc.doc_id"
        "   WHERE d.project_id = ?"
        "  UNION"
        "  SELECT d.doc_id AS doc_id"
        "    FROM workflow_events we"
        "    JOIN documents d ON d.id = we.document_id"
        "   WHERE we.project_id = ? AND we.event_type = 'prompt_copied'"
        ")",
        [project_id, project_id],
    )
    continuous_ended = _scalar_count(
        "SELECT COUNT(DISTINCT document_id) FROM workflow_events "
        "WHERE project_id = ? AND event_type = 'continuous_work_ended'",
        [project_id],
    )
    return {
        "in_progress": in_progress,
        "done": done,
        "copied": copied,
        "continuous_ended": continuous_ended,
    }


def get_dashboard_summary(
    project_id: str,
    activity_limit: int,
    workflow_limit: int,
) -> dict:
    with get_store().transaction():
        recent_activities = list_recent_activities(project_id, activity_limit)
        try:
            active_workflows = list_active_workflows(project_id, workflow_limit)
        except DashboardDataError as exc:
            # Defense in depth (B0001/0122): list_active_workflows already skips
            # individual malformed rows, so reaching here means the aggregation as a
            # whole failed. Degrade just the workflow card instead of failing the
            # entire dashboard — the recent-activity card must stay alive.
            _log.error(
                "dashboard: active workflow aggregation failed project=%s: %s",
                project_id,
                exc,
            )
            active_workflows = {
                "limit": workflow_limit,
                "total": 0,
                "has_more": False,
                "items": [],
                "skipped": 0,
                "degraded": True,
            }
        try:
            work_states = get_work_state_summary(project_id)
        except Exception:  # noqa: BLE001 — state board is additive, never fatal
            _log.exception("dashboard: work-state summary failed project=%s", project_id)
            work_states = {
                "in_progress": 0,
                "done": 0,
                "copied": 0,
                "continuous_ended": 0,
                "degraded": True,
            }
    return {
        "ok": True,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "recent_activities": recent_activities,
        "active_workflows": active_workflows,
        "work_states": work_states,
    }
