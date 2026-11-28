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


@dataclass(frozen=True)
class MentionFacts:
    mode: str
    kind: str = "chat"
    doc_type: str = ""
    attachment_request: bool = False
    unanswered: bool | None = None
    current_run: bool | None = None
    question_policy: bool = False
    design_template: bool = False
    authoring_guide: bool = False


def select_mention(facts):
    """New-path display order; migrated_leaves defines the runtime fallback boundary."""
    if facts.mode.startswith("chat."):
        first = ["notices", "conversation/read", "conversation/turn", "conversation/encoding"]
        more = ["documents/list", "documents/partial", "documents/unreadable",
                "attachments/link", "source_tools", "cards/state", "cards/ai_options"]
        if facts.mode in ("chat.execute", "chat.edit", "chat.edit_once"):
            more.append("conversation/commands")
        if facts.mode in ("chat.edit", "chat.edit_once"):
            more.extend(WRITE_CHAT)
    elif facts.mode in ("card.read", "card.edit") and facts.kind in ("new", "first_write", "edit", "rework"):
        first = ["notices", "submit/format"]
        if facts.mode == "card.edit":
            first.append("submit/changed_files")
        first.append("documents/access")
        if facts.design_template:
            first.append("design_template/" + facts.doc_type)
        more = ["documents/partial", "documents/unreadable", "attachments/read",
                "question/register", "question/precheck", "source_tools", "cards/state"]
        if facts.authoring_guide:
            more.append("authoring_guide/" + facts.doc_type)
        if facts.mode == "card.edit" and facts.doc_type == "TR":
            more.append("submit/self_check")
    else:
        raise ValueError("unsupported_mention_mode_or_kind")
    if facts.attachment_request:
        more.append("attachments/upload_request")
    if facts.mode.startswith("chat."):
        if facts.unanswered is not False:
            more.append("cards/questions")
        if facts.current_run is not False:
            more.append("cards/recovery")
        if facts.question_policy:
            more.append("question/policy")
    return tuple(first), tuple(more)


def transition_leaves(mode):
    """Minimum cross-situation coverage; add applicable dynamic type leaves in probes."""
    facts = MentionFacts(mode=mode, kind="chat" if mode.startswith("chat.") else "rework",
                         doc_type="TR", attachment_request=True, question_policy=True)
    first, more = select_mention(facts)
    return first + more


def migrated_leaves(facts):
    """D-0012 sections 3-3/3-7: guidance discovery is optional at render time.

    Source-tool discovery and AI options carry no relocated rule here. Card
    state is chat-tool guidance, not a migrated card-document instruction.
    Conditional migrated rules stay required whenever selected by facts.
    The deployment probe still covers every selected leaf (section 3-7.2).
    """
    first, more = select_mention(facts)
    optional = {"source_tools", "cards/ai_options"}
    if facts.mode.startswith("card."):
        optional.add("cards/state")
    return tuple(name for name in first + more if name not in optional)
