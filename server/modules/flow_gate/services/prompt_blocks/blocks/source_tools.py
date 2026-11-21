"""Source tool block: the "Remote project source CRUD" lines every worker mention carries.

Moved verbatim from ``mention_service._remote_source_crud_lines`` (flowgate.v02test.0061 card 4).
The tool list and wording come from ``tool_registry`` (the same judge the permission check uses);
this module only assembles them. Help serves the per-tool detail under ``source_tools/{name}``.
"""
from __future__ import annotations

from typing import Optional

from ..registry import Block, register


def remote_source_crud_lines(
    base: str,
    raw_token: str,
    step_type: Optional[str],
    *,
    action_scope: str = "new",
    locale: str = "ko",
    kind: Optional[str] = None,
) -> list[str]:
    """The five mention lines that survive the 0349 shrink (P0005 [notes] / D0004 D-4).

    Every request format, field description and JSON example the mention used to carry now
    lives behind GET /help/tools — the mention was growing one block per tool and workers
    were reading the top and skipping the rest (R0001). What stays is exactly what a worker
    that never calls help must still know: call help first, do not touch the disk, which
    tools exist, the token, and where the detail is. All five are built from fixed strings
    plus ``kind``, so no lookup failure can empty the section (D0004 D-6).

    ``kind`` comes from tool_registry — the same judge the permission check uses — so the
    mention can no longer advertise a tool the server would refuse. Pass it directly when
    the caller holds a token rather than a step type (see q_answer_invoke_service); leave it
    None to have the step type judged here.
    """
    from modules.flow_gate import template_provision
    from modules.flow_gate.services import tool_registry

    if not raw_token:
        return []
    loc = template_provision.normalize_locale(locale)
    if kind is None:
        kind, _reason = tool_registry.kind_for_step(action_scope, step_type)
    names = tool_registry.tool_names(kind, action_scope)
    if not names:
        # kind=none: sequence-edit / test-run tokens get no tools at all, and there is
        # nothing to advertise. Matches the empty list /help/tools returns them (P0005 normal-3).
        return []
    text = tool_registry.MENTION_LINES[loc]
    return [
        text["first_action"].format(url=f"{base}/help/tools"),
        text["no_disk_edit"],
        f"{text['tools_label']}: " + ", ".join(names),
        f"Authorization: Bearer {raw_token}",
        text["detail"].format(url=f"{base}/help/tools/{{name}}"),
    ]


register(Block("source_tools.crud", brief=remote_source_crud_lines, detail=remote_source_crud_lines,
               help_leaf="source_tools"))
