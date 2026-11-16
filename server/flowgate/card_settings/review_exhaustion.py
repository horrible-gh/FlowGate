"""Review exhaustion policy and durable approval evidence (v02gui.0029).

Keep the loop's non-pass intact. Use the existing automatic approval path, pin its
revision across retries, and record the exception separately from a review pass.
"""
from __future__ import annotations

import json
import logging

from flowgate.card_settings import fields, repository
from flowgate.card_settings import legacy_adapter as legacy

_log = logging.getLogger(__name__)

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


# v02gui.0029 (0002-CH turn 35-36): the next card's AI is told which earlier cards came through with their review count
# spent (approved without passing a review). Its own section of the prompt only: no document is changed, no state added.
_NOTICE = {
    "ko": ("앞 카드 검수 알림",
           "앞 카드 가운데 검수를 통과하지 못하고 넘어온 카드가 있습니다. 검수 횟수를 모두 써서 마지막 수정본이 "
           "검수 없이 승인된 것입니다. 그 결과물을 그대로 믿지 말고 직접 확인한 뒤 작업하십시오.",
           "- {label}: 문서 {doc_id} {revision}판, 검수 {count}회 모두 미통과"),
    "en": ("Earlier card review notice",
           "Some earlier cards came through without passing their review: the review count ran out and the last "
           "revision was approved without a review. Do not take their results as verified; check them yourself first.",
           "- {label}: document {doc_id} revision {revision}, not passed in {count} review(s)"),
    "ja": ("前のカードの検収通知",
           "前のカードの中に検収を通過せずに進んだカードがあります。検収回数を使い切り、最後の修正版が検収なしで"
           "承認されました。その成果物をそのまま信じず、自分で確認してから作業してください。",
           "- {label}: 文書 {doc_id} 第{revision}版、検収{count}回すべて不通過"),
}


def upstream(run_id, node_key):
    """The cards before ``node_key`` (every ancestor along the live edges of the run's revision, in stored order) whose
    current attempt was approved after its review count ran out: ``[{node_key, label, doc_id, revision_no,
    review_count}]``. The evidence is the run read's own (``wire``: the node's current attempt)."""
    from modules.flow_gate.graph_workflow.store import run_store  # noqa: PLC0415

    run = legacy.run_by_id(run_id)
    if run is None:
        return []
    graph = legacy.load_context(run).revision
    parents: dict = {}
    for edge in graph.live_edges():
        parents.setdefault(edge.target, []).append(edge.source)
    seen, stack = set(), list(parents.get(node_key, []))
    while stack:
        key = stack.pop()
        if key in seen or key == node_key:
            continue
        seen.add(key)
        stack.extend(parents.get(key, []))
    done = {r["node_key"] for r in run_store.node_runs(run_id) if r["status"] == "done"}
    attempts = run_store.current_attempts(run_id)
    found = []
    for node in graph.nodes:
        if node.key not in seen or node.key not in done:
            continue
        rows = records(run_id, node.key, attempts.get(node.key), APPROVED)
        if rows:
            found.append({"node_key": node.key, "label": node.label or node.key,
                          **{k: rows[-1].get(k) for k in ("doc_id", "revision_no", "review_count")}})
    return found


def upstream_notice(job):
    """``card_settings_ports.upstream_notice``: the prompt section naming the earlier cards approved after their review
    count ran out, or None (none, or the evidence could not be read: the AI starts as before)."""
    try:
        found = upstream(job["run_id"], job["node_key"])
    except Exception:  # noqa: BLE001 - a notice never blocks the card's AI start
        _log.warning("upstream exhaustion notice unreadable run=%s node=%s", job.get("run_id"), job.get("node_key"),
                     exc_info=True)
        return None
    if not found:
        return None
    locale = job.get("locale") if job.get("locale") in _NOTICE else "ko"
    header, lead, line = _NOTICE[locale]
    items = [line.format(label=f["label"], doc_id=f.get("doc_id") or "-", revision=f.get("revision_no") or "-",
                         count=f.get("review_count") or "-") for f in found]
    return f"## {header}\n---\n{lead}\n" + "\n".join(items)
