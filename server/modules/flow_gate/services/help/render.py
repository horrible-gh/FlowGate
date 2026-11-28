"""File-first response assembly with independent compatibility fallbacks."""
from . import context, loader
from .fill import fill
from .language import metadata
from .visibility import decide_visibility
from .tree import HelpSupplierError, CATALOG_ORDER, GUIDE_TYPES
from .legacy.compat.render import (BulkRequestError, parse_bulk_names, item_url,
    _children_count, _design_type_rows, _item_notes, _summary_for, _title_for)
from .legacy.compat import render as compat

def _load(name, locale):
    try:
        return loader.load_item(name, locale)
    except loader.HelpContentError as exc:
        raise HelpSupplierError(str(exc)) from exc

def has_file(name, locale):
    return _load(name, locale)[0] is not None

def enumerate_children(name, ctx):
    if ctx.get("principal_kind") == "user_session" and name == "documents":
        return [{"name":"doc_type", "summary":"doc_type"}]
    if name == "source_tools":
        children = [{"name": row["name"], "summary": row.get("summary", row["name"])} for row in context.tool_rows(ctx)]
    elif name == "design_template":
        children = []
        for row in context.design_rows(ctx):
            if not row.get("is_active", 1):
                continue
            description = row.get("description") or ""
            applied = row.get("description_locale")
            fragment = "design_template/" + row["type_code"] + ":description"
            if description and applied != ctx["locale"]:
                ctx.setdefault("supplier_fallbacks", []).append(fragment)
                if ctx["locale"] == "ko" or applied is None:
                    description = ""
                    ctx.setdefault("supplier_omissions", []).append(fragment)
            children.append({"name":row["type_code"], "summary":description})
        if ctx.get("doc_type") == "WP":
            children.append({"name": "WP", "summary": "WP"})
    elif name == "authoring_guide":
        children = [{"name": ctx["doc_type"], "summary": ctx["doc_type"]}] if ctx.get("doc_type") in GUIDE_TYPES else []
    else:
        children = [{"name": n.split("/", 1)[1], "summary": n.split("/", 1)[1]}
                    for n, spec in loader.manifest()["items"].items()
                    if n.startswith(name + "/") and not spec.get("dynamic") and not spec.get("retained")
                    and decide_visibility(n, ctx).visible]
    for row in children:
        item, resolved, reasons = _load(name + "/" + row["name"], ctx["locale"])
        if item:
            row["summary"] = item["summary"]
    return children

def _build(name, ctx):
    canonical = loader.canonical(name)
    spec = loader.definition(canonical)
    if not spec:
        raise HelpSupplierError("unknown_item:" + name)
    if spec["kind"] == "area":
        children = enumerate_children(canonical, ctx)
        why = []
        for row in children:
            _, _, missing = _load(canonical + "/" + row["name"], ctx["locale"])
            why.extend(missing)
        payload = {"name": canonical, "form": "children", "children": children}
        if canonical == "design_template":
            payload["default_child"] = ctx.get("doc_type")
        return payload, why + ctx.get("supplier_fallbacks", [])
    item, resolved, reasons = _load(canonical, ctx["locale"])
    area, _, child = canonical.partition("/")
    payload = {"name": area, "form": "content"}
    if child:
        payload["child"] = child
    elif canonical == "notices":
        from .legacy.compat.locale import TITLES
        payload["title"] = TITLES[ctx["locale"]][canonical]
    if item:
        used = {name for value in loader.strings(item) for route, name in loader.TOKEN.findall(value) if not route}
        active_spec = dict(spec, placeholders={k:v for k,v in spec.get("placeholders", {}).items() if k in used})
        values = context.dynamic_values(ctx, active_spec)
        values.setdefault("flow_id", ctx.get("group_id") or "{flow_id}")
        content, original = fill({k:v for k,v in item.items() if k not in ("locale", "status")}, spec, values,
                                 loader.read_json("routes"), ctx["base_url"])
        payload.update(title=content["title"], content=content, original_fields=original)
    elif area in ("source_tools", "design_template", "authoring_guide"):
        payload = context.external_child(area, child, ctx)
        resolved = payload.get("content", {}).get("resolved_locale") or ctx["locale"]
        if resolved != ctx["locale"]:
            reasons.append(canonical + ":supplier_locale")
            if ctx["locale"] == "ko":
                for field in ("heading", "provenance", "body", "rendered"):
                    if payload.get("content", {}).pop(field, None):
                        ctx.setdefault("supplier_omissions", []).append(canonical + ":" + field)
        payload["content_source"] = "legacy"
    else:
        payload["content"] = context.legacy_content(canonical, ctx)
        payload["content_source"] = "legacy"
    if canonical != name:
        payload["name_info"] = {"requested": name, "canonical": canonical}
    if spec.get("retained"):
        payload["name_info"] = {"requested": name, "canonical": canonical, "replacement": "documents/list"}
        payload["replacement_note"] = {"ko":"목록 사용법은 documents/list를 참조하십시오.", "en":"See documents/list for the list APIs.", "ja":"一覧APIの使い方はdocuments/listを参照してください。"}[ctx["locale"]]
    return payload, reasons

def build_bulk(requested_names, ctx):
    if ctx.get("principal_kind") == "user_session":
        return compat.build_bulk(requested_names, ctx)
    requested = ctx["locale"]
    items, unavailable, reasons = [], [], []
    for name in requested_names:
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            unavailable.append({"name":name, "http_status":404 if decision.reason == "unknown_item" else 403, "reason":decision.reason})
        else:
            item, why = _build(name, ctx)
            items.append((name, item))
            reasons.extend(why)
    if reasons and requested != "ko":
        ctx["locale"] = "ko"
        items = [(name, _build(name, ctx)[0]) for name, _ in items]
    info = metadata(requested, ctx["locale"], reasons, ctx.get("supplier_omissions", []))
    for _, item in items:
        item["language"] = info
    ctx["help_language"] = info
    return [item for _, item in items], unavailable

def build_item(name, ctx):
    if ctx.get("principal_kind") == "user_session" and name == "documents":
        return {"name":"documents", "form":"children", "children":enumerate_children(name, ctx)}
    if ctx.get("principal_kind") == "user_session" and name == "documents/doc_type":
        return {"name":"documents", "child":"doc_type", "form":"content", "content":context.legacy_content(name, ctx)}
    if ctx.get("principal_kind") == "user_session":
        return compat.build_item(name, ctx)
    items, unavailable = build_bulk([name], ctx)
    if not items:
        raise HelpSupplierError("Unavailable help item: " + name)
    return items[0]

def build_child(name, child, ctx):
    return build_item(name + "/" + child, ctx)

def build_index(ctx):
    if ctx.get("principal_kind") == "user_session":
        return compat.build_index(ctx)
    requested = ctx["locale"]
    index, ctx["locale"] = loader.load_index(requested)
    items = []
    for name in CATALOG_ORDER:
        if decide_visibility(name, ctx).visible:
            children = enumerate_children(name, ctx) if name != "notices" else []
            if name != "notices" and not children:
                continue
            items.append({"name":name, "summary":index["areas"][name], "children_count":len(children)})
    info = metadata(requested, ctx["locale"], ["_index:missing_translation"] if requested != ctx["locale"] else [])
    ctx["help_language"] = info
    return {"items":items, "language":info}
