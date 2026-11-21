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

class HelpSupplierError(RuntimeError):
    """A supplier could not build its content (missing context, storage failure).

    Never downgraded into a per-item 403/404: a storage failure dressed up as a
    permission failure sends the worker looking for a permission it already has
    (L-0005 §2-7).
    """

VERSION = "v1"

BULK_ITEM_MIN = 1

BULK_ITEM_MAX = 10

GROUP_DOCUMENTS_LIMIT = 20

GROUP_DOCUMENTS_MORE_LIMIT = 5

CATALOG_ORDER: tuple[str, ...] = (
    "notices",
    "group_documents",
    "document_access",
    "document_attachments",
    "doc_type",
    "question",
    "submit",
    "source_tools",
    "design_template",
    "authoring_guide",
    "test_commands",
    "tr_self_check",
    "changed_files_format",
    *chat_help_content.ITEMS,
)

ITEM_FORM: dict[str, str] = {
    "notices": "content",
    "group_documents": "content",
    "document_access": "content",
    "document_attachments": "content",
    "doc_type": "content",
    "question": "content",
    "submit": "content",
    "source_tools": "children",
    "design_template": "children",
    "authoring_guide": "children",
    "test_commands": "content",
    "tr_self_check": "content",
    "changed_files_format": "content",
}

ITEM_FORM.update({name: "content" for name in chat_help_content.ITEMS})

_CATALOG = frozenset(CATALOG_ORDER)

ALWAYS_VISIBLE = frozenset({
    "notices", "group_documents", "document_access", "document_attachments", "doc_type", "question", "submit",
})

USER_SESSION_VISIBLE = frozenset({"document_access", "doc_type"})

AUTHORING_SCOPES = frozenset({"new", "edit"})

GUIDE_TYPES = frozenset({"N", "T", "T2", "TR", "TR2", "TS"})

INVESTIGATION_TYPES = frozenset({"N", "NR"})

MUTATING_TYPES = tool_registry.MUTATING_STEP_TYPES

AREA_LEAVES = {
    "conversation": {"turn": "chat_turn", "encoding": "chat_turn", "recovery": "chat_recovery"},
    "documents": {"access": "document_access", "partial": "document_access", "unreadable": "document_access", "doc_type": "doc_type", "group_list": "group_documents", "edit": "chat_documents"},
    "attachments": {"read": "document_attachments", "upload_request": "document_attachments"},
    "cards": {"state": "chat_control", "ai_options": "chat_control", "place": "chat_control", "topology": "chat_control", "planning": "chat_control", "run": "chat_control", "roles": "chat_control", "questions": "chat_questions"},
    "question": {"register": "question", "precheck": "question", "answers": "question", "policy": "question"},
    "submit": {"format": "submit", "changed_files": "changed_files_format", "test_commands": "test_commands", "self_check": "tr_self_check"},
}
AREA_ORDER = ("notices", "conversation", "documents", "attachments", "source_tools", "cards", "question", "submit", "design_template", "authoring_guide")
# 0061 card 6: the old flat item names (chat_control, chat_turn, document_access, document_attachments,
# ...) are no longer addressable for worker/chat tokens -- ``CATALOG_ORDER`` is the public area list and
# every other address is ``area/leaf``. ``LEGACY_ORDER`` survives only as (a) the console user-session
# index, whose response is a human-facing contract and stays as it was, and (b) the internal supplier
# keys the leaves are built from (the values of ``AREA_LEAVES``); no token can request them by name.
LEGACY_ORDER = CATALOG_ORDER
CATALOG_ORDER = AREA_ORDER
_CATALOG = frozenset(LEGACY_ORDER)

AREA_LEAVES["documents"]["edit_by_id"] = "chat_documents"
for leaf in ("self_check_http", "self_check_request", "self_check_lifecycle", "self_check_errors_request", "self_check_errors_execution", "self_check_errors_recovery", "self_check_baseline", "self_check_parallel", "self_check_attempt"):
    AREA_LEAVES["submit"][leaf] = "tr_self_check"

for leaf in ("self_check_group", "self_check_errors_busy", "self_check_errors_ownership", "self_check_baseline_conditions"):
    AREA_LEAVES["submit"][leaf] = "tr_self_check"
AREA_LEAVES["submit"] = {name.replace("self_check_", "check_"): source for name, source in AREA_LEAVES["submit"].items()}

# Chat-specific attachment scope and error rules remain independently readable.
AREA_LEAVES["attachments"]["request_rules"] = "chat_documents"
