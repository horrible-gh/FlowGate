from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from modules.flow_gate import template_provision
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.documents.constants import WORK_PLAN_TYPE
from modules.flow_gate.services import (
    engine_recipe_service,
    remote_tool_service,
    test_command_service,
    tool_registry,
    tr_scope_service,
    work_plan_service,
)

from modules.flow_gate.services import chat_help_content

_logger = logging.getLogger(__name__)
from .locale import (normalize_locale)
from .tree import (ALWAYS_VISIBLE, AUTHORING_SCOPES, CATALOG_ORDER, GUIDE_TYPES, MUTATING_TYPES, USER_SESSION_VISIBLE, _CATALOG)

class Decision(NamedTuple):
    visible: bool
    reason: Optional[str]

VISIBLE = Decision(True, None)

def _effective_doc_type(token_rec: dict) -> tuple[Optional[str], bool]:
    """The type this token is working on (L-0005 §2-2).

    Authoring scopes resolve through the same workflow-head lookup ``tool_registry``
    uses for the tool judgment, so the type-conditional items and the source tools
    can never be decided from two different types.
    """
    action_scope = token_rec.get("action_scope")
    if action_scope in AUTHORING_SCOPES:
        return remote_tool_service._worker_token_step_type_result(token_rec)
    doc_ref = token_rec.get("doc_ref")
    if not doc_ref:
        return None, False
    try:
        doc = db_documents.get_by_id(doc_ref)
    except Exception:
        _logger.warning("help context: document lookup failed for %s", doc_ref, exc_info=True)
        return None, True
    if not doc:
        return None, False
    return doc.get("type_code"), False

def resolve_context(token_rec: dict, locale: str, base_url: str) -> dict:
    """Everything the catalog needs about this caller, resolved once per request."""
    locale = normalize_locale(locale)
    if token_rec.get("_is_user_jwt"):
        return {
            "principal_kind": "user_session",
            "project": None,
            "group_id": None,
            "doc_id": None,
            "doc_type": None,
            "action_scope": None,
            "tool_kind": "none",
            "source_mode": None,
            "reason": "user_session",
            "locale": locale,
            "base_url": base_url,
            "registry": None,
            "scratch_dir": None,
            "continuous": False,
            "token_rec": token_rec,
        }

    project = token_rec.get("project")
    doc_type, _lookup_failed = _effective_doc_type(token_rec)
    registry = tool_registry.resolve_registry(token_rec, project, locale)
    return {
        "principal_kind": "worker",
        "project": project,
        "group_id": token_rec.get("group_id"),
        "doc_id": token_rec.get("doc_ref"),
        "doc_type": doc_type,
        "action_scope": token_rec.get("action_scope"),
        "tool_kind": registry["kind"],
        "source_mode": registry["source_mode"],
        # resolve_registry already applies the §4 precedence
        # (source_mode_local → token_scope_none → step_lookup_failed → null).
        "reason": registry["reason"],
        "locale": locale,
        "base_url": base_url,
        "registry": registry,
        "scratch_dir": token_rec.get("scratch_dir"),
        # An unmanned chain token carries the target sequence it is walking toward.
        "continuous": bool(token_rec.get("continuation_target_seq")),
        "token_rec": token_rec,
    }

def context_envelope(ctx: dict) -> dict:
    return {
        "doc_id": ctx["doc_id"],
        "doc_type": ctx["doc_type"],
        "action_scope": ctx["action_scope"],
        "tool_kind": ctx["tool_kind"],
        "source_mode": ctx["source_mode"],
        "reason": ctx["reason"],
    }

def _is_design_type(type_code: Optional[str]) -> bool:
    if not type_code:
        return False
    try:
        return bool(template_provision.is_design_type(type_code))
    except Exception:
        # Narrow side on doubt: an unshown item costs one unused help call, a shown
        # one that 403s costs the worker a debugging detour (D-0003 §3-4).
        _logger.warning("help visibility: design-type lookup failed for %s", type_code, exc_info=True)
        return False

def _legacy_visibility(name: str, ctx: dict) -> Decision:
    """Visible for this caller? The index and every direct call share this answer."""
    if name not in _CATALOG:
        return Decision(False, "unknown_item")

    if ctx["principal_kind"] == "user_session":
        if name in USER_SESSION_VISIBLE:
            return VISIBLE
        return Decision(False, "user_session")

    if name in chat_help_content.ITEMS:
        if ctx.get("action_scope") == "chat":
            return VISIBLE
        return Decision(False, "not_chat")

    if name in ALWAYS_VISIBLE:
        return VISIBLE

    if name == "source_tools":
        if ctx.get("source_mode") != "remote":
            return Decision(False, "source_mode_local")
        if ctx.get("tool_kind") == "none":
            return Decision(False, "token_scope_none")
        return VISIBLE

    authoring = ctx.get("action_scope") in AUTHORING_SCOPES
    doc_type = ctx.get("doc_type")

    if name == "design_template":
        if authoring and template_provision.has_body_template(doc_type):
            return VISIBLE
        return Decision(False, "not_design_type")

    if name == "authoring_guide":
        if authoring and doc_type in GUIDE_TYPES:
            return VISIBLE
        return Decision(False, "no_guide_for_type")

    if name == "test_commands":
        if authoring and doc_type == "TS":
            return VISIBLE
        return Decision(False, "not_ts_type")

    if name == "tr_self_check":
        # Same judgment as api_server_tools.definitions_for_run(): a worker token on a TR edit
        # step, or (0638 T#1) on a TR(new) step -- doc_type is then the workflow head's type.
        # 0110 T#3: a Graph attempt token sees it too -- its run executes in its attempt tree
        # (content "graph_attempt"); before that it was always refused with 409.
        if ctx.get("principal_kind") == "worker" and ctx.get("action_scope") in AUTHORING_SCOPES and doc_type == "TR":
            return VISIBLE
        return Decision(False, "not_tr_edit")

    if name == "changed_files_format":
        if authoring and doc_type in MUTATING_TYPES:
            return VISIBLE
        return Decision(False, "not_mutating_type")

    return Decision(False, "unknown_item")

def visible_names(ctx: dict) -> list[str]:
    """Catalog order, filtered — the source of the ``detail=true`` request list."""
    return [name for name in CATALOG_ORDER if decide_visibility(name, ctx).visible]


def decide_visibility(name: str, ctx: dict) -> Decision:
    from .tree import AREA_LEAVES, LEGACY_ORDER
    if ctx.get("principal_kind") == "user_session" and name in LEGACY_ORDER:
        return _legacy_visibility(name, ctx)
    area, separator, leaf = name.partition("/")
    if separator:
        source = AREA_LEAVES.get(area, {}).get(leaf)
        if source is None:
            return Decision(False, "unknown_item")
        return _legacy_visibility(source, ctx)
    if name in AREA_LEAVES:
        if any(decide_visibility(name + "/" + leaf, ctx).visible for leaf in AREA_LEAVES[name]):
            return VISIBLE
        return Decision(False, "no_visible_children")
    return _legacy_visibility(name, ctx)
