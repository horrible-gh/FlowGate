"""Review exhaustion policy and durable approval evidence (v02gui.0029).

Keep the loop's non-pass intact. Use the existing automatic approval path, pin its
revision across retries, and record the exception separately from a review pass.
"""
from __future__ import annotations

import json

from flowgate.card_settings import fields, repository
from flowgate.card_settings import legacy_adapter as legacy

EXHAUSTED = "review_count_exhausted"
DECIDED = "review_exhaustion_approval_requested"
APPROVED = "approved_after_review_exhausted"


def eligible(flow_id, node, loop, *, author="ai"):
    if author != "ai" or loop.get("stop_reason") != EXHAUSTED:
        return False
    if loop.get("stop_detail") == "awaiting_author_revision":
        return False
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    return kind in (fields.RUN, fields.PLAN) and repository.values(
        flow_id, node["node_key"]).get("after_review_exhausted", "continue") == "continue"


def records(run_id, node_key, attempt_id, cause):
    from modules.flow_gate.db.connection import get_store
    rows = get_store()._fetch_all(
        "SELECT payload_json FROM graph_run_events WHERE run_id = ? AND cause = ? ORDER BY event_seq",
        [run_id, cause]) or []
    found = []
    for row in rows:
        data = json.loads(row["payload_json"])
        if data.get("node_key") == node_key and data.get("attempt_id") == attempt_id:
            found.append(data)
    return found


def decision(run_id, key, attempt_id, stage, seq=None, loop_id=None):
    rows = records(run_id, key, attempt_id, DECIDED)
    return next((r for r in reversed(rows) if r.get("stage") == stage
                 and (seq is None or r.get("seq") == seq)
                 and (loop_id is None or r.get("loop_id") == loop_id)), None)


def remember(run_id, key, attempt_id, stage, loop, doc, seq=None):
    """Caller holds the Flow guard. Never retarget a recorded decision to a newer revision."""
    old = decision(run_id, key, attempt_id, stage, seq, loop.get("run_id"))
    if old:
        return old
    data = {"node_key": key, "attempt_id": attempt_id, "stage": stage, "seq": seq,
            "loop_id": loop.get("run_id"), "doc_id": doc["doc_id"],
            "revision_no": int(doc.get("revision_no") or 0),
            "review_count": loop.get("review_count"), "stop_reason": EXHAUSTED}
    legacy.append_run_event(run_id, DECIDED, EXHAUSTED, data)
    return data


def approved(flow_id, run_id, data):
    """Idempotent evidence, written only after the normal approval succeeded."""
    def write():
        rows = records(run_id, data["node_key"], data.get("attempt_id"), APPROVED)
        if any((r.get("stage"), r.get("seq"), r.get("loop_id")) ==
               (data.get("stage"), data.get("seq"), data.get("loop_id")) for r in rows):
            return
        legacy.append_run_event(run_id, APPROVED, EXHAUSTED,
                                {k: data.get(k) for k in ("node_key", "attempt_id", "stage", "seq", "loop_id",
                                                         "doc_id", "revision_no", "review_count", "stop_reason")})
    legacy.with_flow_guard(flow_id, write)


def wire(row, attempt_id):
    rows = records(row["run_id"], row["node_key"], attempt_id, APPROVED)
    return {"review_exhaustion_approval": ({k: rows[-1].get(k) for k in
            ("stage", "doc_id", "revision_no", "review_count", "stop_reason")} if rows else None)}
