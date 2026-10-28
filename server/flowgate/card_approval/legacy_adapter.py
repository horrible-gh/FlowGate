"""Reuse boundary of the card approval (flowgate.v02.0056 T#1, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed:
the Graph API envelope (user JWT, RBAC, error shape, locale), the Flow load / disposal check, the run and
its node runs, the run revision's nodes, the Flow's engine ownership (refused as the run's stop / resume /
gate decision refuse it), the node approval state the run responses show (``node_approval``), the run read,
the Flow's group AI lease, the approver's permissions as the approve button resolves them, and the v0.1
document approval as the ``review_transitions`` route makes it: the review transition, the TR approval hook
every approval entry point calls and the Graph contact a person's approval reaches. Whether a line document
is written is the card documents' own judgement (``card_documents.lines.has_body``, flowgate.v02.0056 T#2).

flowgate.v02.0056 T#2: the card run's automatic approval of its instruction (``card_settings``) approves one
document through the same ``approve_document``, under the run issuer's authority as the Flow's server-owned
completion context (``system=True``: the principal the unmanned approval passes) instead of a person's.

A card document the v0.1 review rules cannot approve as it is — a result whose card review ended rejected
(``rejected``), or a line document a person wrote in the editor and never submitted (no review status) — is
first put back in review by the card approval itself (``_ready``): the v0.1 rule names the next state
(``mark_revised`` / ``submit`` -> ``pending_review``, a draft's document state ``submit`` -> ``open``) and
the state change is logged as v0.1 logs it, but the authority is the card approval's (``document.approve``,
flowgate.v02.0056 T#1): the approver is not asked for the author's edit permission (``document.update`` /
``own.draft``) the v0.1 ``mark_revised`` / ``submit`` transitions require, and nothing is added to the
permissions the v0.1 approval checks.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.api import envelope, v01_contact
from modules.flow_gate.graph_workflow.projection.run_documents import registered_documents
from modules.flow_gate.graph_workflow.services import canvas_service, graph_service, node_approval, run_service
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import definition_store, run_store

__all__ = ["GraphApiError", "authenticate", "authorize", "invalid", "run", "client_id_of", "locale_of", "load_flow",
           "run_flow_id", "require_writable", "require_graph_owned", "run_by_id", "node_run", "revision_node",
           "refusal", "run_payload", "group_ai_run", "permissions_of", "document", "approve_document",
           "after_approved", "stale_documents", "APPROVE_PERMISSION"]

APPROVE_PERMISSION = "document.approve"
# The TR2 approval attempt key of a person's card approval of a revision no attempt failed on (the card
# review's own is "card:", the unmanned chain's "auto:"); at most 64 characters. After a retryable failure of
# the same revision the key is the TR2 service's own retry key (``retry_state``: "retry:<attempt_id>"), the
# one the v0.1 approve button sends, so a card approval after the cause is fixed starts one new attempt —
# a repeated request replays it — instead of replaying the failure.
TR2_REQUEST_KEY_PREFIX = "card-approval:"
TR2_REQUEST_KEY_LEN = 48


def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict, required: str) -> None:
    envelope.authorize(user_id, group, required)


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def client_id_of(request) -> Optional[str]:
    return envelope.client_id_of(request)


def locale_of(request) -> str:
    return envelope.locale_of(request)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def run_flow_id(run_id: str) -> str:
    return run_service.run_flow_id(run_id)


def require_writable(flow_id: str) -> None:
    graph_service.require_writable(flow_id)


def require_graph_owned(flow_id: str) -> None:
    """The run's stop / resume / gate decision refusal of a Flow the Graph no longer owns."""
    own = read_ownership(flow_id)
    if own.state != "graph":
        raise run_service._ownership_error(flow_id, own)


def run_by_id(run_id: str) -> Optional[dict]:
    return run_store.run_by_id(run_id)


def node_run(run_id: str, node_key: str) -> Optional[dict]:
    return next((r for r in run_store.node_runs(run_id) if r["node_key"] == node_key), None)


def revision_node(run: dict, node_key: str) -> dict:
    """The node row of the revision the run pinned (node type, document type, label)."""
    record = definition_store.revision(run["group_id"], int(run["revision_no"])) or {}
    return next((n for n in record.get("nodes") or [] if n["node_key"] == node_key), {"node_key": node_key})


def refusal(flow_id: str, row: dict, node_type: Optional[str]) -> Optional[str]:
    """Why the node's card is not awaiting approval, or None — the same judgement the run responses show."""
    return node_approval.refusal(flow_id, row, node_type)


def run_payload(run_id: str) -> Optional[dict]:
    return run_service.run_payload(run_id, with_nodes=True)


def with_viewer(answer: dict, group: dict, user_id: str) -> dict:
    """flowgate.v02.0105 T#2 B-6: the answered run tells whether the user may approve its cards (``viewer.can_approve``)."""
    return run_service.with_viewer(answer, group, user_id)


def group_ai_run(flow_id: str) -> Optional[str]:
    """The AI run that holds the Flow's group AI lease (the v0.1 approve route refuses then), or None."""
    lease = db_leases.get_active(flow_id)
    return str(lease.get("run_id")) if lease and lease.get("run_id") else None


def permissions_of(user_id: str) -> set[str]:
    """The user's permissions, resolved as the approve button resolves them."""
    from modules.flow_gate.db import users as db_users  # noqa: PLC0415 — v0.1 stack on use
    from modules.flow_gate.workflow.routers.workflow import _get_user_permissions  # noqa: PLC0415

    return set(_get_user_permissions(db_users.get_by_id(user_id) or {"user_id": user_id, "is_admin": 0}))


def document(doc_id: str) -> Optional[dict]:
    return db_documents.get_by_id(doc_id)


def _refused(doc_id: str, reason: str, detail: Optional[str] = None) -> GraphApiError:
    params = {"doc_id": doc_id, "reason": reason}
    if detail:
        params["detail"] = detail
    return GraphApiError(409, "document_approval_refused", params)


def _retryable_tr2(exc: Exception) -> bool:
    """A TR2 refusal the unmanned chain retries once with a new attempt key (``settle_completed_step``)."""
    from modules.flow_gate.documents import tr2_service  # noqa: PLC0415
    from modules.flow_gate.documents.tr2_errors import TR2_ERRORS  # noqa: PLC0415

    return (isinstance(exc, tr2_service.Tr2ValidationError) and exc.code != "tr2_in_progress"
            and exc.code in TR2_ERRORS and bool(TR2_ERRORS[exc.code].retryable))


# The card approval's own step before the v0.1 approval: review status -> the v0.1 review action naming
# the state the approval can start from.
_READY_ACTIONS = {"rejected": "mark_revised", "": "submit"}


def _ready(doc: dict, user_id: str, permissions: set[str]) -> dict:
    """Put a rejected result / a written, never submitted line document back in review (pending_review; a
    draft also opens), as the v0.1 rules name the states, under the card approval's authority
    (``document.approve``). Logged as the v0.1 transition logs it."""
    from modules.flow_gate.db.connection import now_iso  # noqa: PLC0415
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415
    from modules.flow_gate.workflow.event_logger import log_state_changed  # noqa: PLC0415
    from modules.flow_gate.workflow.transition_rules import get_doc_review_rule, get_doc_rule  # noqa: PLC0415

    if APPROVE_PERMISSION not in permissions:
        raise pipeline_service.PermissionError(f"Insufficient permissions: {APPROVE_PERMISSION} is required")
    doc_id = doc["doc_id"]
    current = doc.get("doc_review_status") or ""
    action = _READY_ACTIONS[current]
    next_review = get_doc_review_rule(current, action)
    if next_review is None:
        raise pipeline_service.TransitionError(
            f"Invalid review transition: doc_review_status={current!r} + action={action!r}")
    fields = {"doc_review_status": next_review, "updated_at": now_iso()}
    rule = get_doc_rule(str(doc.get("status") or ""), action) if action == "submit" else None
    if rule is not None:
        fields["status"] = rule.next_state                     # draft -> open, as an inbox submission
    updated = db_documents.update(doc_id, fields)
    if not updated:
        raise pipeline_service.TransitionError("Review status transition failed")
    log_state_changed(project_id=doc.get("project_id") or "", actor_user_id=user_id,
                      from_state=f"review:{current}", to_state=f"review:{next_review}",
                      group_id=doc.get("group_id"), document_id=doc.get("id"), action_code=f"review_{action}")
    return updated


def needs_ready(doc: dict) -> bool:
    return (doc.get("doc_review_status") or "") in _READY_ACTIONS


def tr2_request_key(doc: dict) -> str:
    """The attempt key of a card approval of the TR2 ``doc`` as it is now: the server's retry key when its
    latest attempt failed retryably on this revision, else the card approval's key of the revision."""
    from modules.flow_gate.documents import tr2_approval_service  # noqa: PLC0415

    retry = tr2_approval_service.retry_state(doc)
    if retry.get("allowed") and retry.get("request_key"):
        return str(retry["request_key"])
    digest = hashlib.sha256(f"{doc['doc_id']}:{int(doc.get('revision_no') or 0)}".encode("utf-8")).hexdigest()
    return TR2_REQUEST_KEY_PREFIX + digest[:TR2_REQUEST_KEY_LEN]


def _principal(user_id: str, flow_id: str, system: bool):
    """A person's approval (the card approval), or the Flow's server-owned completion context (an automatic
    approval, as the unmanned approval and the card review's approval pass it)."""
    from modules.flow_gate.services.mutation_policy import human_principal, system_principal  # noqa: PLC0415

    return system_principal(user_id=user_id, group_id=flow_id) if system else human_principal({"user_id": user_id})


def _transition(doc: dict, user_id: str, permissions: set[str], locale: str, principal) -> dict:
    """The v0.1 review transition to approved. A document the v0.1 rules cannot approve as it is (a
    rejected result, a written line document never submitted) is put back in review first (``_ready``) —
    in the same transaction, except for TR2, whose approval must open its own (``tr2_nested_transaction``):
    a refused TR2 then stays in review (pending_review), awaiting the next card approval."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review  # noqa: PLC0415

    doc_id = doc["doc_id"]
    tr2 = str(doc.get("type_code") or "").upper() == "TR2"

    def approve(current: dict) -> dict:
        revision = int(current.get("revision_no") or 0)
        for attempt in range(2 if tr2 else 1):
            request_key = tr2_request_key(current) if tr2 else None
            try:
                return transition_document_review(
                    doc_id=doc_id, action="approve", actor_user_id=user_id, user_permissions=permissions,
                    locale=locale, mutation_principal=principal, expected_revision=revision,
                    request_key=request_key)
            except Exception as exc:  # noqa: BLE001 — the caller maps it
                if not (tr2 and attempt == 0 and _retryable_tr2(exc)):
                    raise
                current = db_documents.get_by_id(doc_id) or current   # retried once: the server's retry key
        raise AssertionError("unreachable")

    if not needs_ready(doc):
        return approve(doc)
    if tr2:
        return approve(_ready(doc, user_id, permissions))
    with get_store().transaction():
        return approve(_ready(doc, user_id, permissions))


def approve_document(flow_id: str, doc: dict, user_id: str, permissions: set[str], locale: str, *,
                     system: bool = False) -> None:
    """Approve one document of the card as a person's approval does (``system``: as an automatic approval
    under the Flow's completion context); a refusal raises a GraphApiError (403 ``approval_forbidden``,
    409 ``group_ai_running`` / ``document_approval_refused``)."""
    from modules.flow_gate.documents import tr2_service  # noqa: PLC0415
    from modules.flow_gate.services import tr_commit_service  # noqa: PLC0415
    from modules.flow_gate.services.mutation_policy import MutationPolicyError  # noqa: PLC0415
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415

    doc_id = doc["doc_id"]
    try:
        result = _transition(doc, user_id, permissions, locale, _principal(user_id, flow_id, system))
    except pipeline_service.PermissionError as exc:
        raise GraphApiError(403, "approval_forbidden", {"doc_id": doc_id, "detail": str(exc)}) from exc
    except tr2_service.Tr2ValidationError as exc:
        raise _refused(doc_id, exc.code) from exc
    except MutationPolicyError as exc:
        raise GraphApiError(409, "group_ai_running", {"flow_id": flow_id, "run_id": exc.error.get("run_id")}) from exc
    except (pipeline_service.TransitionError, ValueError) as exc:
        raise _refused(doc_id, "transition_refused", str(exc)) from exc
    tr_commit_service.on_document_approved(doc_id, result)
    after_approved(flow_id, doc_id)


def stale_documents(flow_id: str, node_key: Optional[str]) -> set[str]:
    """flowgate.v02.0061 T#7 (0017-TR question 440): the card's documents an ended attempt of the node left behind —
    registered (new or edit) with the tokens of the node's invalidated attempts in the Flow's active run (a Stop, a
    lost lease, a failed worktree) and with none of its live tokens. They stay as the card's history but are no
    document of the attempt the node is worked on by now: the card approval neither approves nor waits for them."""
    if not node_key or not get_store().table_exists("graph_node_attempts"):
        return set()
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return set()
    old_tokens = run_store.invalidated_token_ids(run_row["run_id"], node_key)
    if not old_tokens:
        return set()
    old = {d["doc_id"] for d in registered_documents(flow_id, old_tokens)}
    live_tokens = run_store.token_ids(run_row["run_id"], node_key)
    live = {d["doc_id"] for d in registered_documents(flow_id, live_tokens)} if live_tokens and old else set()
    return old - live


def after_approved(flow_id: str, doc_id: str) -> None:
    """The Graph contact of a person's approval: the node whose result it is moves on (or, while the run
    is stopped, is done and its propagation waits); any other document binds nothing."""
    v01_contact.after_review_decided(flow_id, doc_id, "approve")
