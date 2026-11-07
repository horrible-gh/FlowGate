"""0145 T#3: authority for AI Graph reads. No write/execution capability is inferred."""
from dataclasses import dataclass
import re

from fastapi import HTTPException
from flowgate.card_document_edit import chat_authz, chat_errors
from modules.flow_gate.db import documents, tokens
from modules.flow_gate.graph_workflow.services import canvas_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import token_service


@dataclass(frozen=True)
class Scope:
    flow_id: str
    project_id: str
    user_id: str
    token_id: str
    ai_run_id: str
    node_key: str | None = None
    attempt_id: str | None = None
    run_id: str | None = None


def refuse(code="token_binding_mismatch", status=403):
    raise GraphApiError(status, code, {}, message=code)


def verify(raw):
    if not raw:
        refuse("unauthorized", 401)
    try:
        token = token_service.verify(raw)
    except HTTPException:
        refuse("unauthorized", 401)
    if not isinstance(token, dict) or chat_authz.token_live(token) != chat_authz.LIVE:
        refuse("unauthorized", 401)
    return token


def _same(left, right):
    return bool(left) and str(left) == str(right or "")


def _live(run):
    if run is None:
        refuse("run_finished", 409)
    if run.get("status") in ("cancelled", "cancelling") or run.get("end_reason") == "cancelled":
        refuse("run_cancelled", 409)
    try:
        chat_authz._check_run_state(run)
    except GraphApiError as exc:
        if exc.code == chat_errors.RUN_CANCELLED:
            refuse("run_cancelled", 409)
        refuse("run_finished", 409)
    if run.get("status") != "running":
        refuse("run_finished", 409)


def _worker_binding(token, run):
    axes = ((token.get("ai_run_id"), run.get("run_id")),
            (token.get("token_id"), run.get("current_token_id") or run.get("token_id")),
            (token.get("issued_to"), run.get("issued_to")),
            (token.get("project") or token.get("project_id"), run.get("project_id")),
            (token.get("group_id"), run.get("group_id")),
            (token.get("doc_ref"), run.get("doc_ref")),
            (token.get("action_scope"), run.get("action_scope")))
    if not all(_same(a, b) for a, b in axes):
        refuse()
    mapped = run_store._one(
        "SELECT m.run_id, m.node_key, m.attempt_id, m.map_kind, r.group_id, r.status AS run_status, "
        "r.finished_at, a.status AS attempt_status, a.active_node_key, a.ended_at "
        "FROM graph_run_token_map m JOIN graph_runs r ON r.run_id = m.run_id "
        "JOIN graph_node_attempts a ON a.attempt_id = m.attempt_id AND a.run_id = m.run_id "
        "AND a.node_key = m.node_key JOIN graph_node_runs n ON n.run_id = m.run_id AND n.node_key = m.node_key "
        "WHERE m.token_id = ?", [token["token_id"]])
    if not mapped or mapped.get("map_kind") not in (run_store.MAP_AI_START, run_store.MAP_ATTEMPT):
        refuse()
    if not _same(mapped.get("group_id"), token.get("group_id")):
        refuse()
    if (mapped["run_status"] not in ("running", "paused", "idle") or mapped.get("finished_at")
            or mapped["attempt_status"] != "active" or mapped.get("ended_at")
            or not _same(mapped.get("active_node_key"), mapped["node_key"])):
        refuse("run_finished", 409)
    active = run_store.active_run(mapped["group_id"])
    if not active or active["run_id"] != mapped["run_id"]:
        refuse()
    # Read selected columns only: the ledger's lease/authentication secrets never enter the response.
    execution = run_store._one(
        "SELECT exec_id, ai_run_id, status, cancel_requested_at FROM graph_attempt_runs "
        "WHERE exec_id = ? AND attempt_id = ? AND run_id = ?",
        [run.get("graph_exec_id"), mapped["attempt_id"], mapped["run_id"]])
    if not execution or not _same(execution.get("ai_run_id"), run["run_id"]):
        refuse()
    if execution.get("cancel_requested_at"):
        refuse("run_cancelled", 409)
    if execution.get("status") != "running":
        refuse("run_finished", 409)
    return mapped


def authorize(token, flow_id, run_hint=None):
    if chat_authz.token_live(token) != chat_authz.LIVE:
        refuse("unauthorized", 401)
    kind = token.get("action_scope")
    if kind not in ("chat", "new", "edit", "review"):
        refuse("token_kind_not_allowed")
    run = run_hint if run_hint is not None else chat_authz._get_run_record(token.get("ai_run_id"))
    if kind == "chat":
        try:
            chat_authz.check_run_token_binding(run, token)
        except GraphApiError as exc:
            if exc.code == chat_errors.RUN_FINISHED:
                refuse("run_finished", 409)
            refuse()
    elif run is None:
        refuse("run_finished", 409)
    _live(run)
    if not isinstance(flow_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", flow_id):
        refuse("invalid_request", 422)
    # Before target lookup: a foreign/nonexistent foreign ID has the same refusal.
    if not _same(token.get("group_id"), flow_id):
        refuse("flow_access_denied")
    mapped = None if kind == "chat" else _worker_binding(token, run)
    group = canvas_service.load_flow(flow_id)
    project = token.get("project") or token.get("project_id")
    if not _same(project, group.get("project_id")):
        refuse("flow_access_denied")
    doc = documents.get_by_id(token.get("doc_ref"))
    if not doc or not _same(doc.get("group_id"), flow_id) or not _same(doc.get("project_id"), project):
        refuse()
    if not has_permission(token.get("issued_to"), project, "perm_document_read"):
        refuse("flow_access_denied")
    return Scope(flow_id, project, token["issued_to"], token["token_id"], run["run_id"],
                 mapped["node_key"] if mapped else None, mapped["attempt_id"] if mapped else None,
                 mapped["run_id"] if mapped else None), group


def recheck(scope, run_hint=None):
    fresh = tokens.get_by_id(scope.token_id)
    if not fresh or chat_authz.token_live(fresh) != chat_authz.LIVE:
        refuse("unauthorized", 401)
    current, _ = authorize(fresh, scope.flow_id, run_hint)
    if current != scope:
        refuse()
