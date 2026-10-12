"""Reuse boundary of the card settings (flowgate.v02.0053 T#2, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never
changed: the Graph API envelope (user JWT, RBAC, error shape), the Flow load / disposal check and the
Flow guard, the active run's node states, the AI repeat count choices (the same set the AI invoke
dialogs and the engine use), the AI connection and AI Team rows and the project's registered document
types. The review stage (``review_stage``) also takes from here: the Flow's engine ownership, which
node an AI run or a result document belongs to, the project default reviewer, the v0.1 document review
loop (started as the AI invoke route starts it, its review hops told the card's review basis, and its
state) and the v0.1 approval as the unmanned chain makes it, followed by the Graph contact a person's
approval reaches, and whether the AI run of a node still holds the Flow's group AI lease (flowgate.v02.0056
T#1). The source tools' step type judgement takes the open run node a token was mapped to.
flowgate.v02.0056 T#3: an AI run's node result is the one registered with that run's own tokens
(``node_of_ai_run``), so the review stage never takes a result someone else registered as the run's.

flowgate.v02.0056 T#2 (the instruction stage of a card run): which open node in its instruction stage an AI
run worked for, the card instruction documents that node's tokens registered, and the Graph's own two steps
that follow the stage — the result stage's start (``node_dispatch.start_result_stage``) and the stage's
failure record (``node_dispatch.instruction_stage_failed``, the AI start failure path).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from modules.flow_gate.db import ai_groups as db_ai_groups
from modules.flow_gate.db import ai_invoke_document_review_loops as db_review_loops
from modules.flow_gate.db import ai_providers as db_ai_providers
from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.api import envelope, v01_contact
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.projection.run_documents import registered_documents
from modules.flow_gate.graph_workflow.services import canvas_service, graph_service, node_dispatch
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.settings import ai_execution_policy_service

__all__ = ["GraphApiError", "with_flow_guard", "registered_doc_types", "authenticate", "authorize", "invalid",
           "run", "load_flow", "require_writable", "repeat_choices", "ai_exists", "team_exists",
           "node_running", "graph_owned", "project_of", "node_of_ai_run", "node_of_result", "open_node_of_token",
           "node_ai_run_active", "instruction_node_of_ai_run", "registered_instructions", "start_result_stage",
           "instruction_stage_failed",
           "default_reviewer", "last_rejection", "start_review_loop", "review_loop_row", "review_loop_active",
           "approve"]

_log = logging.getLogger(__name__)

OPEN_NODE_STATUS = "open"
REVIEW_NODE_STATUS = "review"
STOPPED_LOOP_STAGE = "stopped"
API_BASE_URL = "/flowgate/api/v1"            # operator base, as the Graph node start uses
LOCALE = "ko"
APPROVE_PERMISSION = "document.approve"
# The TR2 approval attempt key of a card approval (the unmanned chain's own is "auto:").
TR2_REQUEST_KEY_PREFIX = "card:"
TR2_REQUEST_KEY_LEN = 48


def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict, required: str) -> None:
    envelope.authorize(user_id, group, required)


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def require_writable(flow_id: str) -> None:
    graph_service.require_writable(flow_id)


def repeat_choices() -> tuple[int, ...]:
    """-1 (until it passes / restarts until done), 0 (none), 1..the configured max."""
    return ai_execution_policy_service.repeat_count_choices(allow_zero=True)


def ai_exists(provider_id: str) -> bool:
    """An AI connection row with this id (ids are global, whoever owns the row)."""
    return provider_id in db_ai_providers.all_provider_ids()


def team_exists(ai_group_id: str) -> bool:
    return db_ai_groups.get(ai_group_id) is not None


def node_running(flow_id: str, node_key: str) -> bool:
    """The node is open in the Flow's active run (its start already took the settings), or in review
    while the card's AI review works on its result (that review follows them)."""
    run_row: Optional[dict] = run_store.active_run(flow_id)
    if run_row is None:
        return False
    for r in run_store.node_runs(run_row["run_id"]):
        if r["node_key"] != node_key:
            continue
        if r["status"] == OPEN_NODE_STATUS:
            return True
        if r["status"] == REVIEW_NODE_STATUS and r.get("result_doc_id"):
            return review_loop_active(r["result_doc_id"])
    return False


# ── the review stage ─────────────────────────────────────────────────────────

def graph_owned(flow_id: str) -> bool:
    return read_ownership(flow_id).state == "graph"


def project_of(flow_id: str) -> Optional[str]:
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


def _module_of(flow_id: str) -> Optional[str]:
    parts = flow_id.split(".")
    return parts[1] if len(parts) > 2 and parts[1] != "none" else None


def _ai_node_in_review(run_row: dict, node_key: str) -> Optional[tuple[dict, str]]:
    """(the run revision's node row, its result) when the AI node is in review with a result."""
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == node_key), None)
    if row is None or row["status"] != REVIEW_NODE_STATUS or not row.get("result_doc_id"):
        return None
    record = definition_store.revision(run_row["group_id"], int(run_row["revision_no"])) or {}
    node = next((n for n in record.get("nodes") or [] if n["node_key"] == node_key), None)
    if node is None or node.get("assignee_kind") != "ai":
        return None
    return node, row["result_doc_id"]


def node_of_ai_run(flow_id: str, ai_run_id: str) -> Optional[tuple[dict, str]]:
    """The AI node of the Flow's active run that this AI run wrote the result of (its tokens carry the
    run id and were mapped to the node), when that node is in review: (node row, result doc id).

    flowgate.v02.0056 T#3: the result must be the run's own — registered with one of those tokens, as their
    ``token_consumed`` records show (the evidence the run reconciliation binds from). A result someone else
    registered while the run went on (a person's, with the node's dispatch token) is not this run's to review
    or approve: None, and it waits for the card approval."""
    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id:
        return None
    mapped = get_store()._fetch_all(
        "SELECT m.node_key, m.token_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND t.ai_run_id = ?", [run_row["run_id"], ai_run_id])
    tokens: dict[str, list[str]] = {}
    for r in mapped or []:
        tokens.setdefault(r["node_key"], []).append(r["token_id"])
    for key, token_ids in tokens.items():
        found = _ai_node_in_review(run_row, key)
        if found is not None and _registered_with(flow_id, found[1], token_ids):
            return found
    return None


def _registered_with(flow_id: str, doc_id: str, token_ids: list[str]) -> bool:
    """``doc_id`` was registered with one of ``token_ids`` (their ``token_consumed`` records)."""
    return any(d["doc_id"] == doc_id for d in registered_documents(flow_id, token_ids))


def _lease_held(lease: Optional[dict]) -> bool:
    """An unexpired lease row (read only: the run reads that ask may sit inside a Flow guard, so the
    expired row is left for the lease's own recovery instead of being reclaimed here)."""
    if not lease or not lease.get("run_id") or not lease.get("expires_at"):
        return False
    try:
        expires = datetime.fromisoformat(str(lease["expires_at"]))
    except ValueError:
        return False
    return (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc)


def node_ai_run_active(flow_id: str, node_key: str) -> bool:
    """The AI run that holds the Flow's group AI lease now is one of the node's (a token mapped to the
    node in the Flow's active run carries its run id): the node's AI has not ended yet."""
    lease = db_leases.get(flow_id)
    run_row = run_store.active_run(flow_id) if _lease_held(lease) else None
    if run_row is None:
        return False
    mapped = get_store()._fetch_one(
        "SELECT m.node_key FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND m.node_key = ? AND t.ai_run_id = ?",
        [run_row["run_id"], node_key, lease["run_id"]])
    return mapped is not None


def node_of_result(flow_id: str, doc_id: Optional[str]) -> Optional[dict]:
    """The AI node of the Flow's active run whose result in review is ``doc_id`` (its node row)."""
    run_row = run_store.active_run(flow_id)
    row = run_store.node_run_by_result(run_row["run_id"], doc_id) if run_row and doc_id else None
    found = _ai_node_in_review(run_row, row["node_key"]) if row else None
    return found[0] if found else None


def open_node_of_token(token_rec: dict) -> Optional[tuple[str, dict]]:
    """(Flow, the run revision's node row) of the node a token was mapped to (``graph_run_token_map``:
    the node's dispatch token, and every token its AI start or a retry of it minted), while that run is
    its own Flow's active run and the node is still open; else None. The node row carries the node run's
    card run stage as ``run_stage`` (flowgate.v02.0056 T#2)."""
    token_id, flow_id = token_rec.get("token_id"), token_rec.get("group_id")
    mapped = run_store.node_run_by_token(token_id) if token_id and flow_id else None
    if mapped is None:
        return None
    run_row = run_store.active_run(flow_id)
    if run_row is None or run_row["run_id"] != mapped["run_id"]:
        return None
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == mapped["node_key"]), None)
    if row is None or row["status"] != OPEN_NODE_STATUS:
        return None
    record = definition_store.revision(flow_id, int(run_row["revision_no"])) or {}
    node = next((n for n in record.get("nodes") or [] if n["node_key"] == mapped["node_key"]), None)
    return (flow_id, {**node, "run_stage": row.get("stage")}) if node is not None else None


# ── the instruction stage (flowgate.v02.0056 T#2) ────────────────────────────

def instruction_node_of_ai_run(flow_id: str, ai_run_id: str) -> Optional[tuple[dict, dict]]:
    """(the run revision's node row, its node run) of the AI node of the Flow's active run that this AI run
    worked for (its tokens carry the run id and were mapped to the node) while that node is open in its
    card's instruction stage; else None."""
    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id:
        return None
    mapped = get_store()._fetch_all(
        "SELECT m.node_key FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND t.ai_run_id = ?", [run_row["run_id"], ai_run_id])
    record = definition_store.revision(flow_id, int(run_row["revision_no"])) or {}
    for key in dict.fromkeys(r["node_key"] for r in mapped or []):
        row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == key), None)
        if row is None or row["status"] != OPEN_NODE_STATUS or row.get("stage") != node_dispatch.INSTRUCTION_STAGE:
            continue
        node = next((n for n in record.get("nodes") or [] if n["node_key"] == key), None)
        if node is not None and node.get("assignee_kind") == "ai":
            return node, row
    return None


def registered_instructions(flow_id: str, run_id: str, node_key: str, doc_type: str) -> list[dict]:
    """The live documents of ``doc_type`` registered with the node's tokens in the run, the latest first."""
    docs = registered_documents(flow_id, run_store.token_ids(run_id, node_key))   # in registration order
    wanted = str(doc_type or "").strip().upper()
    return [d for d in reversed(docs) if str(d.get("type_code") or "").strip().upper() == wanted
            and d.get("status") != "cancelled" and not d.get("superseded_by")]


def start_result_stage(flow_id: str, run_id: str, node_key: str, instruction_doc_id: Optional[str],
                       issued_to: str) -> None:
    node_dispatch.start_result_stage(flow_id, run_id, node_key, instruction_doc_id, issued_to, project_of(flow_id))


def instruction_stage_failed(flow_id: str, run_id: str, node_key: str) -> None:
    node_dispatch.instruction_stage_failed(flow_id, run_id, node_key, project_of(flow_id))


def default_reviewer(project_id: Optional[str]) -> Optional[str]:
    """The project default reviewer: the fallback of the v0.1 review gate (first enabled AI)."""
    from modules.flow_gate.services.ai_invoke import review as v01_review  # noqa: PLC0415 — AI stack on use

    return v01_review.resolve_reviewer(None, None, project_id)


def last_rejection(doc_id: str) -> Optional[str]:
    """The reason of the document's last rejection (its rejection history, else the latest reason
    column), or None when it was never rejected."""
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history  # noqa: PLC0415

    doc = db_documents.get_by_id(doc_id) or {}
    history = parse_rejection_history(doc.get("rejection_history"))
    reason = (history[-1].get("reason") if history else None) or doc.get("rejection_reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def start_review_loop(project_id: Optional[str], flow_id: str, doc_id: str, issued_to: str,
                      loop: dict, basis_note: Callable[[Optional[str]], str],
                      node_key: Optional[str] = None) -> Optional[dict]:
    """Start the v0.1 document review loop on ``doc_id`` as the AI invoke route starts it (single
    run, ``action_scope`` review, its stage-aware issuer: a review token, an edit token for a
    rework hop). A refusal is logged and the result left to a person (returns None).

    Every review hop's mention -- the first one, a retry's reissue and the review after a rework all
    come from this issuer -- opens with ``basis_note(the document's last rejection)``: the review
    basis the card chose, said with the last rejection as it is when the hop is issued. The engine
    adds its own one-line criteria name only when it switches stages, never to the first hop.

    flowgate.v02.0056 T#3: every hop, review and rework, also carries the memos of the card ``node_key`` turned
    on for AI reading (``card_memo_hooks``), read when the hop is issued."""
    from modules.flow_gate.services import ai_invoke_service, card_memo_hooks, invoke_mention_service  # noqa: PLC0415
    from modules.flow_gate.services import workflow_decision_service  # noqa: PLC0415

    def _issue(ai_run_id: Optional[str] = None) -> dict:
        if getattr(_issue, "loop_stage", None) == ai_invoke_service.REWORK_HOP_KIND:
            issued = invoke_mention_service.issue_rework_request(
                doc_id=doc_id, issued_to=issued_to, api_base_url=API_BASE_URL, locale=LOCALE, ai_run_id=ai_run_id)
            return {**issued, "mention": card_memo_hooks.prepend_memo_section(
                issued.get("mention") or "", flow_id, node_key=node_key)}
        issued = workflow_decision_service.request_review(
            doc_id=doc_id, issued_to=issued_to, api_base_url=API_BASE_URL, ref_doc_ids=None, locale=LOCALE,
            ai_run_id=ai_run_id)
        mention = issued.get("mention") or ""
        if mention:
            mention = card_memo_hooks.prepend_memo_section(mention, flow_id, node_key=node_key)
            mention = invoke_mention_service.prepend_messages_section(
                mention, [basis_note(last_rejection(doc_id))], LOCALE)
        return {"raw_token": issued["token"], "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"], "mention": mention}

    try:
        return ai_invoke_service.start_run(
            project_id=project_id or "", module=_module_of(flow_id), group_id=flow_id, doc_ref=doc_id,
            action_scope="review", mode="single", continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale=None, issued_to=issued_to,
            api_base_url=API_BASE_URL, mention_builder=lambda _raw, _scratch: None, issue_builder=_issue,
            document_review_loop=dict(loop),
        )
    except Exception:  # noqa: BLE001 — the result stays in review for a person
        _log.warning("card review loop start failed flow=%s doc=%s", flow_id, doc_id, exc_info=True)
        return None


def review_loop_row(ai_run_id: str) -> Optional[dict]:
    row = db_review_loops.get(ai_run_id)
    return dict(row) if row else None


def review_loop_active(doc_id: str) -> bool:
    """A document review loop is still working on ``doc_id`` (not stopped)."""
    if not get_store().table_exists("ai_invoke_document_review_loops"):
        return False
    return any(r.get("current_stage") != STOPPED_LOOP_STAGE for r in db_review_loops.list_by_document(doc_id) or [])


def _retryable_tr2(exc: Exception) -> bool:
    """A TR2 refusal the unmanned chain retries once with a new attempt key (``settle_completed_step``)."""
    from modules.flow_gate.documents import tr2_service  # noqa: PLC0415
    from modules.flow_gate.documents.tr2_errors import TR2_ERRORS  # noqa: PLC0415

    return (isinstance(exc, tr2_service.Tr2ValidationError) and exc.code != "tr2_in_progress"
            and exc.code in TR2_ERRORS and bool(TR2_ERRORS[exc.code].retryable))


def approve(flow_id: str, doc_id: str, actor_user_id: str) -> bool:
    """The v0.1 approval of ``doc_id`` as the unmanned chain makes it (``inbox_routes.settle_completed_step``),
    then the TR approval hook every approval entry point calls and the Graph contact a person's approval
    reaches. True when approved; any refusal is logged and the result left to a person (False).

    - Authority: the run issuer's own permissions, resolved as the approve button resolves them; without
      ``document.approve`` nothing is attempted.
    - Principal: the issuer as the Flow's server-owned completion context (``system_principal``, the one
      the unmanned approval passes), so the group mutation guard admits it as it admits that approval.
    - Revision: the document's revision as read here (``expected_revision``).
    - TR2: its own approval (precheck, source lock, apply, validation, commit) under an attempt key of
      the document and revision, retried once with a new key on a retryable TR2 refusal.
    """
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.db import users as db_users  # noqa: PLC0415
    from modules.flow_gate.services import tr_commit_service  # noqa: PLC0415
    from modules.flow_gate.services.mutation_policy import system_principal  # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import transition_document_review  # noqa: PLC0415
    from modules.flow_gate.workflow.routers.workflow import _get_user_permissions  # noqa: PLC0415

    actor = db_users.get_by_id(actor_user_id) or {"user_id": actor_user_id, "is_admin": 0}
    permissions = _get_user_permissions(actor)
    if APPROVE_PERMISSION not in permissions:
        _log.info("card approval left to a person flow=%s doc=%s: the issuer lacks %s",
                  flow_id, doc_id, APPROVE_PERMISSION)
        return False
    doc = db_documents.get_by_id(doc_id) or {}
    revision = int(doc.get("revision_no") or 0)
    tr2 = str(doc.get("type_code") or "").upper() == "TR2"
    result = None
    for attempt in range(2 if tr2 else 1):
        request_key = None
        if tr2:
            digest = hashlib.sha256(f"{doc_id}:{revision}:{attempt}".encode("utf-8")).hexdigest()
            request_key = TR2_REQUEST_KEY_PREFIX + digest[:TR2_REQUEST_KEY_LEN]
        try:
            result = transition_document_review(
                doc_id=doc_id, action="approve", actor_user_id=actor_user_id, user_permissions=permissions,
                locale=LOCALE, mutation_principal=system_principal(user_id=actor_user_id, group_id=flow_id),
                expected_revision=revision, request_key=request_key)
            break
        except Exception as exc:  # noqa: BLE001 — the result stays in review for a person
            if tr2 and attempt == 0 and _retryable_tr2(exc):
                continue
            _log.warning("card approval refused flow=%s doc=%s", flow_id, doc_id, exc_info=True)
            return False
    tr_commit_service.on_document_approved(doc_id, result)
    v01_contact.after_review_decided(flow_id, doc_id, "approve")
    return True
