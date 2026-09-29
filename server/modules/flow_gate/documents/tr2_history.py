"""TR2 source-history alignment is derived from Git ledger, not apply backups."""
from __future__ import annotations

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import tr_commit_ledger as db_ledger
from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.services import tr_commit_service


def history_root_row(row_id: int) -> dict | None:
    visited: set[int] = set()
    current = row_id
    while current and current not in visited:
        visited.add(current)
        row = db_ledger.get_by_id(current)
        if row is None:
            return None
        parent = row.get("restored_from_id")
        if parent is None:
            return row
        current = int(parent)
    return None


def source_history_state(doc_id: str) -> str:
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != "TR2":
        return "invariant_error"
    session = tr_commit_service.conflict_session(doc["group_id"])
    if session and session.get("doc_id") == doc_id:
        return "conflict"
    rows = [row for row in db_ledger.list_by_group(doc["group_id"], limit=1000)
            if row.get("doc_id") == doc_id]
    if any(row.get("state") == "no_commit" for row in rows):
        return "invariant_error"
    for row in rows:
        root = history_root_row(int(row["id"]))
        if root is None or root.get("doc_id") != doc_id:
            return "invariant_error"
        attempt = db_attempts.successful_root(int(root["id"]))
        if attempt is None or attempt.get("commit_sha") != root.get("commit_sha"):
            return "invariant_error"
        if not attempt.get("spec_fingerprint") or not attempt.get("baseline_fingerprint"):
            return "invariant_error"
    if rows:
        # Ledger identities are insufficient when Git history has been rewritten.
        # Check reachability from the server-owned worktree, never an edit-spec or
        # short-lived apply backup. Published terminal rows are explained by base
        # history and may no longer be present in the group worktree.
        from modules.flow_gate.documents import tr2_service as tr2
        from modules.flow_gate.services import git_service
        try:
            root = tr2.resolve_source_root(doc["project_id"], doc["group_id"])
            for row in rows:
                if row.get("reopened_terminal_at"):
                    continue
                for sha in (row.get("commit_sha"), row.get("cancel_commit")):
                    if sha and git_service._run_git(
                        ["merge-base", "--is-ancestor", sha, "HEAD"], cwd=root
                    ).returncode != 0:
                        return "invariant_error"
        except Exception:
            return "invariant_error"
    if doc.get("doc_review_status") != "approved":
        return "aligned"
    if not rows:
        return "invariant_error"
    if any(row.get("state") == "live" and not row.get("reopened_terminal_at")
           for row in rows):
        return "aligned"
    if any(row.get("state") == "canceled" and row.get("cancel_commit")
           for row in rows):
        return "restore_pending"
    if any(row.get("state") == "live" and row.get("reopened_terminal_at")
           for row in rows):
        return "aligned"
    return "invariant_error"
