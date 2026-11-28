"""Opt-in mention assembly. Templates and activation evidence are separate assets.

Callers own facts and localized value collection; this module never fetches a token
or writes rollout state. A rejected attempt always returns the untouched legacy text.
"""
from copy import deepcopy
from dataclasses import dataclass, field

from ..help import loader
from ..help.fill import fill
from ..help.locale import normalize_locale
from .matrix import select_mention, transition_leaves, migrated_leaves

MODES = ("chat.read", "chat.execute", "chat.edit", "chat.edit_once", "card.read", "card.edit")


@dataclass
class MentionResult:
    text: str
    template: str
    locale: str
    values: dict = field(default_factory=dict, repr=False)
    fallback_reason: str | None = None
    language_reasons: list = field(default_factory=list)


def render_body(template, spec, values, routes):
    """Drop absent optional lines before filling; injected braces stay opaque."""
    required = spec.get("required", [])
    missing = [key for key in required if values.get(key) is None or values.get(key) == ""]
    if missing:
        raise loader.HelpContentError("missing_values:" + ",".join(missing))
    lines = []
    for line in template.splitlines():
        names = [name for route, name in loader.TOKEN.findall(line) if not route]
        if any(name not in required and (values.get(name) is None or values.get(name) == "") for name in names):
            continue
        lines.append(line)
    body, _ = fill("\n".join(lines), spec, values, routes, values["base_url"].rstrip("/"))
    return body


# A chat token is never bound to a card, so `{node_key}` stays a path slot the AI
# fills from cards/state. It is not missing context in a chat mode.
CHAT_UNBOUND = frozenset({"node_key"})


def unbound_for(mode):
    return CHAT_UNBOUND if str(mode).startswith("chat.") else frozenset()


def probe_covers(token_mode, mode):
    """Chat help visibility depends only on the chat scope (not read/edit), so a
    probe made with any chat-mode token is evidence for every chat mode. Card
    modes still need a probe token of the exact mode."""
    if token_mode == mode:
        return True
    return token_mode in MODES and str(token_mode).startswith("chat.") and str(mode).startswith("chat.")


def unresolved_context(name, body, unbound=frozenset()):
    """Match manifest-declared placeholders in decoded response string values.

    Original user fields are opaque; their brace text is not a context contract.
    Unknown contracts fail closed instead of silently using a fixed name list.
    ``unbound`` names are slots the caller's mode never binds (see CHAT_UNBOUND).
    """
    import re
    spec = loader.definition(name)
    if spec is None:
        raise loader.HelpContentError("unknown_help_contract:" + name)
    names = {key for key, kind in spec.get("placeholders", {}).items() if kind != "original"} - set(unbound)
    pattern = re.compile(r"\{\{?([A-Za-z_][\w./-]*)\}?\}")
    return any(match in names for value in loader.strings(body) for match in pattern.findall(value))


def help_selection(facts, context, locale):
    """Use the very same visibility, suppliers and language fallback as help HTTP."""
    from ..help.render import build_bulk
    first, more = select_mention(facts)
    ctx = dict(context, locale=locale)
    from ..help.tree import HelpSupplierError
    try:
        items, unavailable = build_bulk(list(first + more), ctx)
    except HelpSupplierError as exc:
        raise loader.HelpContentError("help_supplier_failed") from exc
    missing = {row["name"] for row in unavailable}
    required = set(migrated_leaves(facts))
    blocked = missing & required
    if blocked:
        raise loader.HelpContentError("unavailable_help:" + ",".join(sorted(blocked)))
    # Inspect decoded values, using each leaf's manifest contract.
    for item in items:
        name = item["name"] + ("/" + item["child"] if item.get("child") else "")
        if unresolved_context(name, item.get("content", item.get("children", {})), unbound_for(facts.mode)):
            if name in required:
                raise loader.HelpContentError("unresolved_help_context:" + name)
            missing.add(name)
    first = tuple(name for name in first if name not in missing)
    more = tuple(name for name in more if name not in missing)
    applied = ctx.get("locale", locale)
    return first, more, applied


def render_mention(legacy, *, facts, values, context, locale="ko", localized_values=None, root=None):
    """Return a prepared prompt plus inspectable metadata (which contains secrets).

    localized_values maps locale -> system text/material headings collected by the
    owning builder. Always provide ko, including when the requested locale differs.
    No result values should be logged or written to transition evidence.
    """
    from .. import terminal_prompt
    locale = normalize_locale(locale)
    result = MentionResult(legacy, "mention/" + facts.mode, locale)
    try:
        if facts.mode not in MODES:
            raise loader.HelpContentError("unsupported_mode")
        state = loader.read_json("mention_rollout", root).get(facts.mode, {})
        if not state.get("enabled"):
            result.fallback_reason = "switch_disabled"
            return result
        record = state.get("verification") or {}
        first, more = select_mention(facts)
        if not (record.get("passed") is True and record.get("mode") == facts.mode
                and probe_covers(record.get("token_mode"), facts.mode) and record.get("checked_at")
                and set(first + more + transition_leaves(facts.mode)).issubset(record.get("items", {}))
                and all(all(row.get(k) is True for k in ("visible", "response", "context"))
                        for row in record.get("items", {}).values())):
            raise loader.HelpContentError("verification_missing_or_failed")
        # A mode is not ready until all three owned templates are real assets.
        for lang in ("ko", "en", "ja"):
            asset = loader.read_json(lang + "/mention/" + facts.mode, root)
            if asset.get("status") == "pending":
                raise loader.HelpContentError("template_pending:" + lang)
        item, applied, reasons = loader.load_mention(facts.mode, locale, root)
        if item is None:
            raise loader.HelpContentError("template_pending")
        first, more, help_locale = help_selection(facts, context, applied)
        localized_values = localized_values or {}
        spec = loader.definition("mention/" + facts.mode, root)
        system_required = [key for key in spec.get("required", [])
                           if spec.get("placeholders", {}).get(key) == "system" and key != "task_block"]
        localized = localized_values.get(applied, {})
        if help_locale != applied or applied not in localized_values or any(not localized.get(key) for key in system_required):
            reasons.append("missing_localized_values_or_help")
            applied = "ko"
        if applied not in localized_values or any(not localized_values[applied].get(key) for key in system_required):
            raise loader.HelpContentError("missing_korean_values")
        item, applied, why = loader.load_mention(facts.mode, applied, root)
        reasons.extend(why)
        if applied != locale:
            first, more, _ = help_selection(facts, context, applied)
        body, task = terminal_prompt.split(legacy)
        task = task or terminal_prompt.specification(facts.kind, applied)
        task["locale"] = applied
        filled = dict(values, **localized_values[applied])
        filled.update(task_block=terminal_prompt.render(task), help_first=", ".join(first), help_more=", ".join(more))
        spec = loader.definition("mention/" + facts.mode, root)
        if not isinstance(item["body"], str) or not item["body"].startswith("{{task_block}}\n") or item["body"].count("{{task_block}}") != 1:
            raise loader.HelpContentError("task_must_be_first_and_unique")
        routes = loader.read_json("routes", root)
        rendered = render_body(item["body"], spec, filled, routes)
        task["mention"] = {"template": item["body"], "spec": spec, "values": filled, "routes": routes}
        task["placement"] = "first"
        result.text = terminal_prompt._with_spec(rendered, task)
        result.values = deepcopy(filled)
        result.locale = applied
        result.language_reasons = reasons
        return result
    except (loader.HelpContentError, ValueError, KeyError, TypeError) as exc:
        result.fallback_reason = str(exc)
        return result


def provider_prompt(prompt, workspace=None):
    """Resolve only provider-dependent workspace; keep task metadata for API handoff."""
    from .. import terminal_prompt
    body, spec = terminal_prompt.split(prompt)
    if not spec or "mention" not in spec:
        return None
    spec["mention"]["values"]["workspace"] = workspace
    return terminal_prompt._with_spec(body, spec)
