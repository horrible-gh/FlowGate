"""Invocation x source mode x stored card state; guidance never grants permission."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Facts:
    kind: str
    can_write: bool = False
    unanswered: bool | None = None
    current_run: bool | None = None


# One selection table. Optional rows are selected by facts, then by help visibility.
MATRIX = {
    "new": ("documents/unreadable", "attachments/read", "question/register", "question/precheck", "submit/format"),
    "edit": ("documents/unreadable", "attachments/read", "question/register", "question/precheck", "submit/format"),
    "first_write": ("documents/unreadable", "attachments/read", "question/register", "question/precheck", "submit/format"),
    "rework": ("documents/unreadable", "attachments/read", "question/register", "question/precheck", "submit/format"),
    "review": ("documents/unreadable", "attachments/read", "question/register", "question/precheck", "submit/format"),
    "workflow": ("documents/unreadable", "question/register", "question/precheck", "submit/format"),
    "sequence": ("documents/doc_type", "question/register", "question/precheck", "submit/format"),
    "work_plan": ("submit/format",),
    "answer": ("documents/unreadable", "question/answers"),
    "failure_origin": (),
    "prompt_copy": (),
    "chat": ("documents/unreadable", "cards/state", "cards/ai_options"),
}
WRITE_CHAT = ("documents/edit", "documents/edit_by_id", "cards/place", "cards/roles", "cards/run")


def select(facts):
    leaves = list(MATRIX[facts.kind])
    if facts.kind == "chat":
        if facts.can_write:
            leaves.extend(WRITE_CHAT)
        if facts.unanswered is not False:
            leaves.append("cards/questions")
        if facts.current_run is not False:
            leaves.append("conversation/recovery")
    return tuple(leaves)
