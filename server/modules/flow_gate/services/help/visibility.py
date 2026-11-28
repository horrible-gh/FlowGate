"""Manifest conditions reuse established authorization judgments."""
from .context import resolve_context, context_envelope, _effective_doc_type, _is_design_type
from .legacy.compat.visibility import Decision, VISIBLE, _legacy_visibility
from .loader import canonical, definition, manifest
from .tree import CATALOG_ORDER, LEGACY_ORDER

def decide_visibility(name, ctx):
    if ctx.get("principal_kind") == "user_session":
        if name == "documents/doc_type":
            return _legacy_visibility("doc_type", ctx)
        if name == "documents":
            return VISIBLE
        if name in LEGACY_ORDER:
            return _legacy_visibility(name, ctx)
        return Decision(False, "unknown_item")
    name = canonical(name)
    spec = definition(name)
    if spec is None or name.startswith("mention/"):
        return Decision(False, "unknown_item")
    if spec["kind"] == "area" and name in ("conversation", "documents", "attachments", "cards", "question", "submit"):
        if any(decide_visibility(n, ctx).visible for n in manifest()["items"] if n.startswith(name + "/")):
            return VISIBLE
        return Decision(False, "no_visible_children")
    decision = _legacy_visibility(spec["visibility"], ctx)
    if decision.visible and name.startswith("source_tools/"):
        names = {tool["name"] for tool in (ctx.get("registry") or {}).get("tools", [])}
        if name.split("/", 1)[1] not in names:
            return Decision(False, "unknown_item")
    if decision.visible and name.startswith("authoring_guide/"):
        if name.split("/", 1)[1] != ctx.get("doc_type"):
            return Decision(False, "unknown_item")
    if decision.visible and name.startswith("design_template/"):
        child = name.split("/", 1)[1]
        if not (child == "WP" and ctx.get("doc_type") == "WP") and not _is_design_type(child):
            return Decision(False, "unknown_item")
    return decision

def visible_names(ctx):
    order = LEGACY_ORDER if ctx.get("principal_kind") == "user_session" else CATALOG_ORDER
    return [name for name in order if decide_visibility(name, ctx).visible]
