"""TR2 error catalog; retryability is defined here only."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


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
        # 0660 T0004 §1: the two source-root failures a retry cannot fix. A git-off project
        # whose src/<project_name>/<branch> is absent needs an operator to place the source
        # (or enable git integration); a group worktree whose provisioning failed for any
        # reason other than lock contention needs the git state looked at.
        ("tr2_source_root_missing", False, 409, "Project source directory is missing"),
        ("tr2_worktree_provision_failed", False, 503, "Group worktree could not be provisioned"),
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
        ("tr_history_recovery_required", False, 409, "TR history recovery required"),
        ("tr2_history_revision_required", False, 409, "A new revision is required"),
        # 0660 T0004 §3 (RC3): a Time Machine reopen whose commit cancel was blocked leaves
        # the approval commit live. Until the cancel is retried successfully the proposal
        # must not be saved, restored or approved against that source.
        ("tr2_revert_pending", False, 409, "The approved commit is still live; retry the commit cancel first"),
        ("tr2_history_invariant_error", False, 500, "TR2 history invariant violated"),
        ("tr2_precheck_failed", False, 422, "Precheck failed"),
        # 0565 T0030 §5/§6: the stored proposal itself, kept apart from apply recovery.
        ("tr2_body_missing", False, 409, "The proposal file is missing"),
        ("tr2_body_corrupt", False, 409, "The proposal file is unreadable or damaged"),
        ("tr2_body_schema_invalid", False, 409, "The stored proposal does not match the TR2 schema"),
        ("tr2_storage_mismatch", False, 409, "The document is not stored as a canonical TR2 proposal"),
        ("tr2_revision_not_found", False, 404, "No saved proposal revision with that number"),
        ("tr2_revision_unusable", False, 409, "The saved proposal revision is not a valid proposal"),
        ("tr2_internal_error", False, 500, "Internal server error"),
    )
}


def retryable(code: str) -> bool:
    return TR2_ERRORS[code].retryable


# Reasons for these codes are written by the TR2 validators themselves (a location and
# a short rule), so a screen may show them. Every other code's reason can carry an
# exception text, Git stderr or a host path: operators read those in the server log.
_PUBLIC_REASON_CODES = frozenset({
    "tr2_spec_invalid", "tr2_path_unsafe", "tr2_body_missing", "tr2_body_corrupt",
    "tr2_body_schema_invalid", "tr2_storage_mismatch", "tr2_revision_unusable",
})
# 0660 T0004 §1.3: the source-root codes name WHY with a closed enum, so a screen can tell
# "wait and retry" from "place the source" without ever seeing a path or Git stderr. Only
# these values pass; anything else (an internal SRC_ROOT_* name, a provisioning error
# text) is withheld exactly like any other non-public reason.
_PUBLIC_REASON_VALUES: dict[str, frozenset[str]] = {
    "tr2_git_unavailable": frozenset({"git_busy", "worktree_provisioning", "branch_merge_active"}),
    "tr2_source_root_missing": frozenset({"project_source_missing", "project_name_missing"}),
    "tr2_worktree_provision_failed": frozenset({"worktree_provision_failed"}),
    "tr2_revert_pending": frozenset({"commit_cancel_blocked"}),
}
# The detail keys a client may see for those closed-enum codes: where (``loc``) and why
# (``reason``). Any other key a raiser attaches stays in the server log (0660 T0004 §1.3).
_PUBLIC_ENUM_DETAIL_KEYS = frozenset({"loc", "reason"})
# A drive path, a UNC path or an absolute POSIX path of two or more segments.
_HOST_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s'\"(=])/[^/\s]+/")


def _scrub(value):
    if isinstance(value, str):
        return "[hidden]" if _HOST_PATH.search(value) else value
    if isinstance(value, dict):
        return {key: _scrub(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(child) for child in value]
    return value


def public_details(code: str, details: dict | None) -> dict:
    """What an HTTP client may see of an error's details (T0030 §5)."""
    kept = dict(details or {})
    allowed = _PUBLIC_REASON_VALUES.get(code)
    if allowed is not None:
        kept = {key: value for key, value in kept.items() if key in _PUBLIC_ENUM_DETAIL_KEYS}
        if kept.get("reason") not in allowed:
            kept.pop("reason", None)
    elif code not in _PUBLIC_REASON_CODES:
        kept.pop("reason", None)
    return _scrub(kept)


def error_payload(code: str, *, attempt_id=None, details=None) -> dict:
    spec = TR2_ERRORS[code]
    if details and details != public_details(code, details):
        log.info("TR2 error %s details withheld from client: %r", code, details)
    return {"code": code, "message": spec.message, "retryable": retryable(code),
            "attempt_id": attempt_id, "details": public_details(code, details)}
