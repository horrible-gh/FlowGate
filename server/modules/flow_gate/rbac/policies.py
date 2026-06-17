"""RBAC policies — own/all branching and context-dependent permission evaluation.

Handle policies that cannot be decided by simple permission_id membership:
  - document.delete.own.draft : If perm_document_delete is not held,
    allow deletion only for documents owned by the user and in draft state (D011 r1 §3-2, PM decision No.4)
  - document.update : Allow updates until final approval, except terminal lifecycle states

Usage:
    from modules.flow_gate.rbac.policies import can_delete_document, PolicyResult
    result = can_delete_document(user_id, project_id, document_row)
    if not result.allowed:
        raise HTTPException(403, result.reason)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from modules.flow_gate.documents.document_service import is_document_editable
from modules.flow_gate.rbac.permission_service import get_user_permissions

# Draft status value (used for document delete own.draft condition)
_DRAFT_STATUS = "draft"


@dataclass
class PolicyResult:
    allowed: bool
    reason: str = field(default="")

    def __bool__(self) -> bool:
        return self.allowed


def can_delete_document(
    user_id: str,
    project_id: str,
    document: dict,
) -> PolicyResult:
    """Determine whether a document can be deleted (own/all branching).

    Priority:
      1. If perm_document_delete is held → allow deleting any document (all)
      2. If owner_id == user_id and status is draft → allow deletion (own.draft)
      3. Otherwise → deny

    The document dict must contain 'owner_id' and 'status' keys.
    """
    perms = get_user_permissions(user_id, project_id)

    if "perm_document_delete" in perms:
        return PolicyResult(allowed=True, reason="all")

    owner_id: Optional[str] = document.get("owner_id")
    status: Optional[str] = document.get("status")

    if owner_id == user_id and status == _DRAFT_STATUS:
        return PolicyResult(allowed=True, reason="own.draft")

    return PolicyResult(allowed=False, reason="Permission denied: document.delete")


def can_update_document(
    user_id: str,
    project_id: str,
    document: dict,
) -> PolicyResult:
    """Determine whether a document can be updated.

    Requires perm_document_update and an editable document.
    """
    perms = get_user_permissions(user_id, project_id)

    if "perm_document_update" not in perms:
        return PolicyResult(allowed=False, reason="Permission denied: perm_document_update")

    if not is_document_editable(document):
        return PolicyResult(allowed=False, reason="Document is not editable")

    return PolicyResult(allowed=True, reason="ok")


def evaluate(
    user_id: str,
    project_id: str,
    permission_id: str,
    context: Optional[dict] = None,
) -> PolicyResult:
    """Generic policy evaluation entry point.

    Context-dependent policies (document.delete, document.update) require a context dict.
    Other permission_id checks are determined by simple membership.
    """
    _CONTEXT_DEPENDENT = {
        "perm_document_delete": can_delete_document,
        "perm_document_update": can_update_document,
    }

    if permission_id in _CONTEXT_DEPENDENT:
        doc = context or {}
        return _CONTEXT_DEPENDENT[permission_id](user_id, project_id, doc)

    perms = get_user_permissions(user_id, project_id)
    allowed = permission_id in perms
    return PolicyResult(
        allowed=allowed,
        reason="ok" if allowed else f"Permission denied: {permission_id}",
    )
