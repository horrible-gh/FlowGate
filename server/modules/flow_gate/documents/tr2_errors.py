"""TR2 error catalog; retryability is defined here only."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Tr2ErrorSpec:
    retryable: bool
    http_status: int
    message: str


TR2_ERRORS: dict[str, Tr2ErrorSpec] = {
    code: Tr2ErrorSpec(retry, status, message)
    for code, retry, status, message in (
        ("tr2_source_locked", True, 409, "Source is locked"),
        ("tr2_worktree_dirty", True, 409, "Worktree is dirty"),
        ("tr2_git_unavailable", True, 503, "Git worktree is unavailable"),
        ("tr2_in_progress", True, 409, "Approval is in progress"),
        ("tr2_validation_failed", True, 422, "Validation failed"),
        ("tr2_commit_failed", True, 500, "Commit failed"),
        ("tr2_source_drift", False, 409, "Source changed since proposal"),
        ("tr2_spec_changed", False, 409, "Proposal changed; reload the latest revision"),
        ("tr2_spec_invalid", False, 422, "Invalid TR2 proposal"),
        ("tr2_spec_immutable", False, 409, "Proposal cannot be changed in its current state"),
        ("tr2_item_not_found", False, 404, "Edit-spec item not found"),
        ("tr2_edit_not_applicable", False, 422, "Edit is not applicable"),
        ("tr2_apply_failed", False, 500, "Apply failed"),
        ("tr2_path_unsafe", False, 422, "Unsafe source path"),
        ("tr2_workflow_conflict", False, 409, "T2/TR2 workflow pair mismatch"),
        ("tr2_validation_command_unapproved", False, 422, "Validation command is not approved"),
        ("tr2_validation_command_os_mismatch", False, 422, "Validation command OS mismatch"),
        ("tr2_validation_command_unverified", False, 422, "Validation command is unverified"),
        ("tr2_nested_transaction", False, 409, "Nested transaction is forbidden"),
        ("tr2_principal_required", False, 403, "Mutation principal required"),
        ("tr2_recovery_required", False, 409, "Recovery required"),
        ("tr2_history_revision_required", False, 409, "A new revision is required"),
        ("tr2_history_invariant_error", False, 500, "TR2 history invariant violated"),
        ("tr2_precheck_failed", False, 422, "Precheck failed"),
    )
}


def retryable(code: str) -> bool:
    return TR2_ERRORS[code].retryable


def error_payload(code: str, *, attempt_id=None, details=None) -> dict:
    spec = TR2_ERRORS[code]
    return {"code": code, "message": spec.message, "retryable": retryable(code),
            "attempt_id": attempt_id, "details": details or {}}
