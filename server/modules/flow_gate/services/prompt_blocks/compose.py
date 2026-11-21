"""Assemble shared briefs and invocation-specific text, preserving terminal_prompt."""
from .matrix import Facts, select


def guidance(base, raw_token, *, kind="new", can_write=False, unanswered=None,
             current_run=None, context=None):
    from modules.flow_gate.services.help.visibility import decide_visibility
    facts = Facts(kind, can_write, unanswered, current_run)
    ctx = context or {
        "principal_kind": "worker", "action_scope": {
            "chat": "chat", "review": "review", "workflow": "workflow_decide",
            "sequence": "workflow_sequence_edit", "answer": "edit",
        }.get(kind, "edit" if kind in ("edit", "first_write", "rework") else "new"),
        "doc_type": None, "source_mode": None, "tool_kind": "none",
    }
    leaves = [leaf for leaf in select(facts) if decide_visibility(leaf, ctx).visible]
    return "\n".join(["## Help", "---", *[
        f"GET {base.rstrip('/')}/help/items/{leaf}" for leaf in leaves],
        f"Authorization: Bearer {raw_token}"])


def compose(sections, *, kind, locale="ko", **facts):
    from modules.flow_gate.services import terminal_prompt
    if isinstance(sections, str):
        sections = [sections]
    # Caller-owned values, guards and policy text are opaque; never rewrite them.
    return terminal_prompt.prepare("\n\n".join(section for section in sections if section),
                                  kind=kind, locale=locale, **facts)
