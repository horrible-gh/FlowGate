"""Service boundary for dynamic help data and compatibility suppliers."""
from importlib import import_module
from .legacy.compat.visibility import resolve_context, context_envelope, _effective_doc_type, _is_design_type
from .legacy.compat import content as compat

def supplier(owner):
    return import_module(f"{__package__}.legacy.{owner}.content")

def legacy_content(name, ctx):
    if name in ("documents/doc_type", "documents/group_list"):
        return compat._CONTENT_SUPPLIERS["doc_type" if name.endswith("doc_type") else "group_documents"](ctx)
    if "/" not in name:
        return supplier("cards_area")._CONTENT_SUPPLIERS[name](ctx)
    area, leaf = name.split("/", 1)
    owner = "docs_area" if area in ("conversation", "documents", "attachments") else "cards_area"
    if name == "cards/recovery":
        return supplier("cards_area")._CONTENT_SUPPLIERS["chat_recovery"](ctx)
    if name in ("submit/self_check", "submit/check_errors", "submit/check_baseline"):
        data = supplier("cards_area")._content_tr_self_check(ctx)
        if leaf == "check_errors":
            return {"errors": data["errors"]}
        if leaf == "check_baseline":
            return data["baseline"]
        keys = ("role", "verification", "stage", "no_fallback", "tools", "http", "request", "lifecycle")
        return {key: data[key] for key in keys if key in data}
    if name == "conversation/read":
        data = supplier("docs_area")._content_document_access(ctx)
        return {key: data[key] for key in ("conversation_history", "partial") if key in data}
    if name == "documents/list":
        data = supplier("docs_area")._content_document_access(ctx)
        return {key: data[key] for key in ("flows", "partial") if key in data}
    if name == "attachments/link":
        return {"list": {"method": "GET", "url": ctx["base_url"] + "/flow/{flow_id}/attachments"},
                "link": "[name](fg-attachment:key)"}
    return import_module(f"{__package__}.legacy.{owner}.leaves").leaf_content(area, leaf, ctx)

def dynamic_values(ctx, spec):
    neutral = {"doc_id", "group_id", "flow_id", "node_key", "project", "module", "base_url"}
    values = {key: ctx[key] for key in neutral if key in ctx and ctx[key] is not None}
    values["token"] = "<YOUR_TOKEN>"
    values.setdefault("flow_id", ctx.get("group_id") or "{flow_id}")
    if "question_policy" in spec.get("placeholders", {}):
        from modules.flow_gate.services import question_policy
        token = ctx.get("token_rec") or {}
        try:
            values["question_policy"] = question_policy.text(token.get("issued_to"), ctx.get("group_id"), card=ctx.get("action_scope") != "chat") or ""
        except Exception:
            values["question_policy"] = ""
    if "probe" in spec.get("placeholders", {}):
        from modules.flow_gate.services import encoding_probe
        token = ctx.get("token_rec") or {}
        if ctx.get("principal_kind") == "worker" and token.get("token_id"):
            values["probe"] = encoding_probe.probe_for(str(token["token_id"]))
    if "test_commands" in spec.get("placeholders", {}):
        from modules.flow_gate.services import test_command_service
        values["test_commands"] = test_command_service.build_verified_commands_block(ctx.get("project") or "")
    if "tool_permissions" in spec.get("placeholders", {}):
        values["tool_permissions"] = [{k:row[k] for k in ("name", "scope", "method", "path") if k in row}
                                     for row in tool_rows(ctx)]
    if {"timeout_seconds", "self_check"} & spec.get("placeholders", {}).keys():
        from modules.flow_gate.services import tr_self_check_quota
        limits = tr_self_check_quota.resolve(ctx.get("token_rec"))
        values["timeout_seconds"] = limits["run_timeout_sec"]
        values["self_check"] = limits
    return values

def design_rows(ctx):
    from modules.flow_gate.db import templates
    from .tree import HelpSupplierError
    try:
        return templates.list_document_types(project_id=None, series="design", locale=ctx["locale"], include_locales=True)
    except Exception as exc:
        raise HelpSupplierError("document type lookup failed") from exc

def external_child(area, child, ctx):
    module = compat if area == "design_template" else supplier("cards_area")
    return getattr(module, "_child_" + area)(child, ctx)

def tool_rows(ctx):
    return (ctx.get("registry") or {}).get("tools", [])
