"""0061 T#8: freeze and commit one attempt result; integration is T#9's caller.

The Stop gate covers Git and the following DB transaction, but no Flow guard or
DB transaction spans Git. A durable pending reservation closes the concurrent
execution-request window; W and commit trailers cover Git/restart idempotency.
"""
from __future__ import annotations

from modules.flow_gate.db import documents, tr_commit_ledger
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import tr_commit_service
from modules.flow_gate.services.git import commit as git_commit
from flowgate.graph_run import fencing, workspace


def _ready(attempt_id, doc_id):
    fencing.check(attempt_id, "attempt_result_commit", statuses=fencing.OPENING)
    row = workspace.attempt(attempt_id)
    if run_store._one("SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? "
                      "AND status IN ('requested', 'running')", [attempt_id]):
        raise fencing.refuse(attempt_id, "attempt_result_commit", "execution_active")
    doc = documents.get_by_id(doc_id)
    binding = run_store.registered_document_attempt(row["group_id"], doc_id)
    if not doc or doc.get("group_id") != row["group_id"] or not binding or binding["attempt_id"] != attempt_id:
        raise fencing.refuse(attempt_id, "attempt_result_commit", "document_attempt_mismatch")
    return row, doc


def _stored(row):
    entry = tr_commit_ledger.by_attempt(row["attempt_id"])
    return {"attempt_id": row["attempt_id"], "node_key": row["node_key"],
            "result_commit": row["result_commit"], "committed": bool(entry and entry.get("commit_sha")),
            "commit_sha": (entry or {}).get("commit_sha"),
            "skipped_reason": (entry or {}).get("skip_reason"), "reused": True}


def commit_result(attempt_id: str, doc_id: str) -> dict:
    """Called after executions ended, outside all transactions/Flow guards.

    idle is allowed for the engine processing a person's approval; stopping and
    invalidated attempts are refused with the existing durable fence event.
    Failures retain the reservation and dirty tree for this same call to retry.
    """
    initial = workspace.attempt(attempt_id)
    if not initial:
        raise fencing.refuse(attempt_id, "attempt_result_commit", "attempt_not_found")
    flow_id = initial["group_id"]
    # Also drains already-authorized token mutations. own_ok supports auto-approval's
    # node_hold; the guard is never held while waiting on this gate or on W.
    with fencing.exclusive(flow_id, own_ok=True):
        row, doc = fencing.guarded(flow_id, lambda: _ready(attempt_id, doc_id))
        if row.get("result_commit"):
            return _stored(row)
        root = workspace.source_root(attempt_id, document_ok=True)
        if root is None:
            def document_only():
                _ready(attempt_id, doc_id)
                run_store._affected("UPDATE graph_node_attempts SET input_source = 'document_only' "
                                    "WHERE attempt_id = ? AND status = 'active'", [attempt_id])
            fencing.guarded(flow_id, document_only)
            return {"attempt_id": attempt_id, "node_key": row["node_key"], "result_commit": None,
                    "committed": False, "skipped_reason": "document_only"}
        project_id = workspace._project(row)
        _directory, _root, branch = workspace.location(row, project_id)

        def reserve():
            current, _doc = _ready(attempt_id, doc_id)
            if current.get("integration_status") not in (None, "", "none", "pending"):
                raise fencing.refuse(attempt_id, "attempt_result_commit", "integration_in_progress")
            run_store._affected(
                "UPDATE graph_node_attempts SET integration_status = 'pending', "
                "integration_reason = 'result_commit' WHERE attempt_id = ? AND status = 'active'",
                [attempt_id])
        fencing.guarded(flow_id, reserve)
        # 0111 T#8: a ref-layout attempt commits on its detached tree and moves its dedicated ref.
        ref = workspace.attempt_head(row, project_id)
        outcome = git_commit.create_attempt_commit(
            project_id, root, branch, row["input_commit"], attempt_id, doc_id,
            tr_commit_service.commit_subject(doc), **({"ref": ref} if ref else {}))
        reason = outcome.get("skipped_reason")
        if not outcome.get("committed") and reason not in ("no_changes", "artifacts_only"):
            def failed():
                _ready(attempt_id, doc_id)
                run_store._affected("UPDATE graph_node_attempts SET integration_reason = ? WHERE attempt_id = ?",
                                    ["result_commit:" + str(reason or "commit_failed"), attempt_id])
            fencing.guarded(flow_id, failed)
            return {**outcome, "attempt_id": attempt_id, "node_key": row["node_key"], "result_commit": None}
        result = outcome.get("commit_sha") if outcome.get("committed") else row["input_commit"]
        if not result:
            raise RuntimeError("attempt commit returned no verified result")

        def record():
            current, _doc = _ready(attempt_id, doc_id)
            if current.get("result_commit"):
                return _stored(current)
            ownership = {"group_id": flow_id, "doc_id": doc_id,
                         "attempt_id": attempt_id, "node_key": row["node_key"]}
            if outcome.get("committed"):
                tr_commit_ledger.record_commit(
                    **ownership, commit_sha=result, commit_subject=outcome["subject"])
            else:
                tr_commit_ledger.record_no_commit(**ownership, skip_reason=reason)
            # Same transaction as the ledger: a rollback leaves neither half.
            changed = run_store._affected(
                "UPDATE graph_node_attempts SET result_commit = ?, integration_status = 'pending', "
                "integration_reason = NULL WHERE attempt_id = ? AND status = 'active' "
                "AND COALESCE(result_commit, '') = ''",
                [result, attempt_id])
            if changed != 1:
                raise RuntimeError("attempt result changed while recording")
            return {**outcome, "attempt_id": attempt_id, "node_key": row["node_key"], "result_commit": result}
        return fencing.guarded(flow_id, record)
