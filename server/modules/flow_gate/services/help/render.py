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
from .locale import (QUESTION_HELP_COPY, SUBMIT_SUMMARIES, SUBMIT_TITLES, SUMMARIES, TITLES, _AUTHORING_GUIDE_TITLES, _ITEM_NOTES, _copy)
from .tree import (BULK_ITEM_MAX, BULK_ITEM_MIN, CATALOG_ORDER, GUIDE_TYPES, HelpSupplierError, ITEM_FORM, _CATALOG)
from .visibility import (_is_design_type, decide_visibility)
from .content import (_CONTENT_SUPPLIERS, _WORK_PLAN_TEMPLATE_TITLE, _child_authoring_guide, _child_design_template, _child_source_tool)

def item_url(base_url: str, name: str, child: Optional[str] = None) -> str:
    if child is None:
        return f"{base_url}/help/items/{name}"
    return f"{base_url}/help/items/{name}/{child}"

def _design_type_rows(ctx: dict) -> list[dict]:
    rows = db_templates.list_document_types(project_id=None, series="design", locale=ctx["locale"])
    return [row for row in rows if row.get("is_active", 1)]

def enumerate_children(name: str, ctx: dict) -> list[dict]:
    """Child entries for a ``children`` item; empty list for a ``content`` item."""
    base = ctx["base_url"]
    if name == "source_tools":
        registry = ctx["registry"] or {"tools": []}
        return [
            {
                "name": tool["name"],
                "title": tool["name"],
                "summary": tool["summary"],
                "method": tool["method"],
                "path": tool["path"],
                "scope": tool["scope"],
                "url": item_url(base, "source_tools", tool["name"]),
            }
            for tool in registry["tools"]
        ]

    if name == "design_template":
        children = []
        for row in _design_type_rows(ctx):
            code = row["type_code"]
            children.append({
                "name": code,
                "title": row.get("type_name") or code,
                "summary": row.get("description") or "",
                "url": item_url(base, "design_template", code),
            })
        # The work plan is not in the design series, so it is listed only for the worker
        # who is actually writing one — a D author has no use for the WP body format.
        if str(ctx.get("doc_type") or "").upper() == WORK_PLAN_TYPE:
            children.append({
                "name": WORK_PLAN_TYPE,
                "title": _copy(_WORK_PLAN_TEMPLATE_TITLE, ctx["locale"], "title"),
                "summary": _copy(_WORK_PLAN_TEMPLATE_TITLE, ctx["locale"], "summary"),
                "url": item_url(base, "design_template", WORK_PLAN_TYPE),
            })
        return children

    if name == "authoring_guide":
        doc_type = ctx.get("doc_type")
        if doc_type not in GUIDE_TYPES:
            return []
        return [{
            "name": doc_type,
            "title": _copy(_AUTHORING_GUIDE_TITLES, ctx["locale"], doc_type),
            "summary": _copy(SUMMARIES, ctx["locale"], "authoring_guide"),
            "url": item_url(base, "authoring_guide", doc_type),
        }]

    return []

def _children_count(name: str, ctx: dict) -> Optional[int]:
    if ITEM_FORM[name] != "children":
        return None
    return len(enumerate_children(name, ctx))

def _title_for(name: str, ctx: dict) -> str:
    if name == "submit":
        scope_titles = SUBMIT_TITLES.get(ctx["locale"]) or {}
        override = scope_titles.get(ctx.get("action_scope") or "")
        if override:
            return override
    return _copy(TITLES, ctx["locale"], name)

def _summary_for(name: str, ctx: dict) -> str:
    if name == "submit":
        scope_summaries = SUBMIT_SUMMARIES.get(ctx["locale"]) or {}
        override = scope_summaries.get(ctx.get("action_scope") or "")
        if override:
            return override
    return _copy(SUMMARIES, ctx["locale"], name)

def build_index(ctx: dict) -> dict:
    """Names, one-line summaries and what is hidden — never any item body."""
    items: list[dict] = []
    hidden: list[dict] = []
    for name in LEGACY_ORDER:
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            hidden.append({"name": name, "reason": decision.reason})
            continue
        items.append({
            "name": name,
            "title": _title_for(name, ctx),
            "summary": _summary_for(name, ctx),
            "form": ITEM_FORM[name],
            "children_count": _children_count(name, ctx),
            "url": item_url(ctx["base_url"], name),
        })
    return {"items": items, "hidden": hidden}

def _item_notes(name: str, ctx: dict) -> list[str]:
    locale = ctx["locale"]
    base = ctx["base_url"]
    if name == "group_documents":
        return [_copy(_ITEM_NOTES, locale, "group_documents").format(base=base)]
    if name == "document_access":
        return [_copy(_ITEM_NOTES, locale, "document_access")]
    if name == "document_attachments":
        return [_copy(_ITEM_NOTES, locale, "document_attachments")]
    if name == "question":
        return [QUESTION_HELP_COPY[locale]["continue_note"]]
    if name == "design_template":
        return [_copy(_ITEM_NOTES, locale, "design_template_children")]
    if name == "changed_files_format":
        return [_copy(_ITEM_NOTES, locale, "changed_files")]
    if name == "source_tools":
        registry = ctx["registry"] or {"notes": []}
        return tool_registry.items_view_notes(registry, locale)
    return []

def build_item(name: str, ctx: dict) -> dict:
    """One item body, envelope excluded — the same shape a bulk entry carries."""
    form = ITEM_FORM[name]
    payload: dict = {
        "name": name,
        "title": _title_for(name, ctx),
        "form": form,
    }

    if name == "source_tools":
        registry = ctx["registry"] or {"kind": "none", "source_mode": None, "reason": None}
        payload["kind"] = registry["kind"]
        payload["source_mode"] = registry["source_mode"]
        payload["reason"] = registry["reason"]
        # The same second catalog /help/tools returns. The source-tool
        # item must not be the one surface left answering "source tree only".
        payload["document_attachments"] = tool_registry.attachment_view(
            registry["kind"], ctx["locale"], ctx["base_url"]
        )

    if form == "children":
        payload["children"] = enumerate_children(name, ctx)
        if name == "design_template":
            doc_type = ctx.get("doc_type")
            payload["default_child"] = doc_type if _is_design_type(doc_type) else None
    else:
        payload["content"] = _CONTENT_SUPPLIERS[name](ctx)

    notes = _item_notes(name, ctx)
    # An empty registry is a normal answer, not a failure — say so rather than
    # returning a silent empty list (L-0005 §5, "no test commands").
    if name == "test_commands" and not payload["content"]["has_commands"]:
        notes = notes + [_copy(_ITEM_NOTES, ctx["locale"], "test_commands_empty")]
    if notes:
        payload["notes"] = notes
    return payload

def build_child(name: str, child: str, ctx: dict) -> dict:
    if name == "source_tools":
        return _child_source_tool(child, ctx)
    if name == "design_template":
        return _child_design_template(child, ctx)
    if name == "authoring_guide":
        return _child_authoring_guide(child, ctx)
    raise HelpSupplierError(f"item '{name}' has no children")

class BulkRequestError(ValueError):
    """``items`` was empty or over the per-request cap — 422, never a 403."""

def parse_bulk_names(raw_items: str) -> list[str]:
    """Comma-separated names, trimmed, de-duplicated, order preserved."""
    names: list[str] = []
    for part in (raw_items or "").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if len(names) < BULK_ITEM_MIN:
        raise BulkRequestError("items must contain at least one help item name")
    if len(names) > BULK_ITEM_MAX:
        raise BulkRequestError(
            f"Too many help items requested: {len(names)} (max {BULK_ITEM_MAX}). "
            "Use detail=true to expand everything."
        )
    return names

def build_bulk(requested_names: list[str], ctx: dict) -> tuple[list[dict], list[dict]]:
    """Return ``(items, unavailable)``; the caller decides the HTTP status."""
    items: list[dict] = []
    unavailable: list[dict] = []
    for name in requested_names:
        if name not in _CATALOG:
            unavailable.append({"name": name, "http_status": 404, "reason": "unknown_item"})
            continue
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            unavailable.append({"name": name, "http_status": 403, "reason": decision.reason})
            continue
        items.append(build_item(name, ctx))
    return items, unavailable


_legacy_build_index = build_index
_legacy_build_item = build_item
_legacy_build_child = build_child
_legacy_enumerate_children = enumerate_children
from .tree import AREA_ORDER, AREA_LEAVES, LEGACY_ORDER
from .locale import AREA_SUMMARIES, leaf_summary
from .leaves import leaf_content


def enumerate_children(name, ctx):
    if name in AREA_LEAVES:
        return [{"name": leaf, "summary": leaf_summary(leaf, ctx["locale"])}
                for leaf in AREA_LEAVES[name]
                if decide_visibility(name + "/" + leaf, ctx).visible]
    rows = _legacy_enumerate_children(name, ctx)
    return [{"name": row["name"], "summary": row["summary"]} for row in rows]


def build_index(ctx):
    if ctx.get("principal_kind") == "user_session":
        return _legacy_build_index(ctx)
    items = []
    summaries = AREA_SUMMARIES[ctx["locale"]]
    for name, summary in zip(AREA_ORDER, summaries):
        if not decide_visibility(name, ctx).visible:
            continue
        children = enumerate_children(name, ctx)
        if name != "notices" and not children:
            continue
        items.append({"name": name, "summary": summary, "children_count": len(children)})
    return {"items": items}


def build_item(name, ctx):
    if "/" in name:
        return build_child(*name.split("/", 1), ctx)
    if name not in CATALOG_ORDER and ctx.get("principal_kind") != "user_session":
        # 0061 card 6: the old flat names (chat_control, document_access, ...) are not items any more.
        raise HelpSupplierError("Unknown help item: " + name)
    if name in AREA_LEAVES or name in ("source_tools", "design_template", "authoring_guide"):
        payload = {"name": name, "form": "children", "children": enumerate_children(name, ctx)}
        if name == "design_template":
            payload["default_child"] = ctx.get("doc_type") if _is_design_type(ctx.get("doc_type")) else None
        return payload
    return _legacy_build_item(name, ctx)


def build_child(name, child, ctx):
    if name in AREA_LEAVES:
        if not decide_visibility(name + "/" + child, ctx).visible:
            raise HelpSupplierError("Unavailable help leaf: " + name + "/" + child)
        payload = {"name": name, "child": child, "form": "content", "content": leaf_content(name, child, ctx)}
        if (name, child) == ("submit", "test_commands") and not payload["content"].get("has_commands"):
            # An empty registry is a normal answer, not a failure (L-0005 section 5); the flat item said so.
            payload["notes"] = [_copy(_ITEM_NOTES, ctx["locale"], "test_commands_empty")]
        return payload
    return _legacy_build_child(name, child, ctx)


def build_bulk(requested_names, ctx):
    items, unavailable = [], []
    for name in requested_names:
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            unavailable.append({"name": name, "http_status": 404 if decision.reason == "unknown_item" else 403, "reason": decision.reason})
        else:
            items.append(build_item(name, ctx))
    return items, unavailable
