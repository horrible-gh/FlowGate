"""0145 T#2: provenance for an explicit review of a legacy result with a lost handoff.

Never reconstruct an ai_invoke_runs row or its outcome. The immutable attempt,
writer execution and consumed token prove attribution; a new real review supplies
the missing judgement. No read/reconcile path calls this module.
"""
from datetime import datetime

from fastapi import HTTPException

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import graph_execution_hooks
from flowgate.card_settings import legacy_adapter as legacy, stage_assignment as sa


def _refuse(reason):
    raise HTTPException(409, detail={"code": "result_recovery_refused", "reason": reason})


def _stamp(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("ambiguous timestamp")
    return parsed


def verify(flow_id, run, row, attempt, writer, doc, node):
    """Read only, inside the Flow guard. Only untouched, initially submitted legacy results.

    Current run-id lookup is deliberately absent: that row may now be another
    group's execution. Revision changes and existing reviews need their normal
    intervention path, rather than an inferred historical submission.
    """
    origin = doc.get("origin_ai_run_id")
    if (doc.get("group_id") != flow_id or doc.get("project_id") != legacy.project_of(flow_id)
            or doc.get("status") == "cancelled" or doc.get("superseded_by")
            or int(doc.get("revision_no") or 0) != 0
            or doc.get("doc_review_status") not in ("pending_review", "revised")
            or not origin or not origin.startswith("aiv_") or not sa.card_lines.has_body(doc)):
        _refuse("document_basis_unverified")
    if (run.get("active_group_id") != flow_id or attempt.get("status") != "active"
            or attempt.get("run_id") != run["run_id"] or attempt.get("node_key") != row["node_key"]
            or legacy.active_attempt_id(run["run_id"], row["node_key"]) != attempt["attempt_id"]
            or row.get("status") != "review" or row.get("result_doc_id") != doc["doc_id"]):
        _refuse("attempt_basis_unverified")
    if row["node_key"] in legacy.held_nodes(flow_id) or legacy.node_running(flow_id, row["node_key"]):
        _refuse("card_busy_or_held")
    line = sa.card_lines.line_document(flow_id, row["node_key"], "report", node.get("node_type"), node.get("doc_type"))
    if not line or line.get("doc_id") != doc["doc_id"]:
        _refuse("slot_basis_unverified")
    db = get_store()
    execution = db._fetch_one("SELECT * FROM graph_attempt_runs WHERE exec_id = ?", [writer.get("intent_exec_id")])
    if (not execution or execution.get("attempt_id") != attempt["attempt_id"]
            or execution.get("run_id") != run["run_id"] or execution.get("kind") != "write"
            or execution.get("status") != "ended" or execution.get("ai_run_id") != origin
            or execution.get("assignment_stage") != sa.RESULT or execution.get("assignment_seq") != 0
            or writer.get("actor") != sa.AI or writer.get("stage_state") not in (sa.ACTIVE, sa.DONE)
            or writer.get("run_id") != run["run_id"] or writer.get("node_key") != row["node_key"]):
        _refuse("writer_basis_unverified")
    if db._fetch_one("SELECT id FROM document_reviews WHERE doc_id = ? LIMIT 1", [doc["doc_id"]]):
        _refuse("review_history_present")
    if db._fetch_one("SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? "
                     "AND (kind <> 'write' OR status IN ('requested', 'running')) LIMIT 1", [attempt["attempt_id"]]):
        _refuse("execution_history_requires_intervention")
    tokens = db._fetch_all(
        "SELECT t.token_id, t.provider_id, t.created_at, t.consumed_at, t.revoked_at, t.project, t.group_id, "
        "t.action_scope, t.ai_run_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND m.node_key = ? AND m.attempt_id = ? AND m.stage = 'result'",
        [run["run_id"], row["node_key"], attempt["attempt_id"]]) or []
    proven = []
    for token in tokens:
        if (token.get("project") != doc["project_id"] or token.get("group_id") != flow_id
                or token.get("action_scope") != "new" or token.get("ai_run_id") != origin
                or not token.get("consumed_at") or token.get("revoked_at")
                or not token.get("provider_id") or token.get("provider_id") != writer.get("provider_id")):
            continue
        try:
            if not (_stamp(execution["created_at"]) <= _stamp(token["created_at"])
                    <= _stamp(doc["created_at"]) <= _stamp(token["consumed_at"]) <= _stamp(execution["ended_at"])):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if legacy._registered_with(flow_id, doc["doc_id"], [token["token_id"]]):
            proven.append(token)
    if len(proven) != 1 or legacy.written_by(doc["doc_id"]) != {"author": sa.AI, "provider_id": writer["provider_id"]}:
        _refuse("consumed_token_unverified")
    return {"writer_exec_id": execution["exec_id"], "writer_ai_run_id": origin,
            "token_id": proven[0]["token_id"], "doc_id": doc["doc_id"], "revision_no": 0}


def prepare_explicit_review(flow_id, attempt, doc_id, payload):
    """Attach the first missing result_review to the existing explicit loop request.

    Its normal fences/transaction own every mutation. A mismatch rolls everything
    back; the new execution/assignment later use the usual verdict/completion path.
    """
    legacy.require_guard(flow_id)
    aid = attempt["attempt_id"]
    if sa.is_legacy_attempt(aid) or sa.current_review_assignment(aid, sa.RESULT_REVIEW) is not None:
        return None
    row = legacy.node_run(attempt["run_id"], attempt["node_key"])
    writer = sa.store.read(aid, sa.RESULT, 0)
    if not row or row.get("result_doc_id") != doc_id or not writer or writer.get("actor") != sa.AI:
        return None
    if not graph_execution_hooks.explicit():
        _refuse("explicit_review_required")
    run, doc = legacy.run_by_id(attempt["run_id"]), legacy.document_row(doc_id)
    node = legacy.run_node_row(flow_id, run, attempt["node_key"]) if run else None
    if not run or not doc or not node or run.get("status") not in ("idle", "running"):
        _refuse("context_unverified")
    proof = verify(flow_id, run, row, attempt, writer, doc, node)
    a = sa.start_stage(flow_id, run, node, aid, sa.RESULT_REVIEW, 1, author=sa.AI,
                       author_provider_id=writer["provider_id"], basis=sa.mark(doc),
                       explicit_retry=True, rerequest_of_seq=0)
    if not isinstance(a, dict) or a.get("actor") != sa.AI or not a.get("provider_id"):
        _refuse("real_reviewer_required")
    from flowgate.card_settings.assigned_review import review_loop_request
    requested, resolved = payload.get("loop") or {}, review_loop_request(flow_id, a)
    if any(requested.get(k) != resolved.get(k) for k in ("reviewer_provider_id", "review_count", "rework_provider_id")):
        _refuse("review_settings_mismatch")
    sa.move(writer, sa.DONE)
    payload.update(assignment_ref=sa.ref_of(a), recovery_provenance=proof,
                   invocation_origin=sa.origin(a, sa.RESULT_REVIEW_ROUND, flow_id, doc=doc, round_no=1))
    return a
