"""How AI-facing text names a Flow or a card to the user: number and name together.

The user does not know where ``0068`` is, and two Flows of one module can share a name (``깃`` is F52 and
F68), so a Flow is written ``F68(깃)`` and a card ``F77-12(card title)`` -- never a number or a stored id
alone. This is wording for sentences shown to the user only: API calls keep taking the stored ids
(group / flow id, node key, document id), and those stay listed next to the display ids.
Pure: no database, no framework.
"""
from __future__ import annotations

from typing import Optional

from . import card_ids

# One sentence for the chat system guidance and the Graph state read; English like the rest of the prompt.
NAMING_RULE = ("Naming: when you mention a Flow or card to the user, write its number with its name, e.g. "
               "Flow F68(깃), card F77-12(card title) or \"F77(ID)'s card 12\". Never use the group number "
               "(0068) or a document id alone: Flows can share a name. API calls keep the stored ids "
               "(flow_id/group_id, node_key, doc_id).")


def _one_line(value: Optional[str]) -> str:
    return " ".join(str(value or "").split())


def flow_name(group_id: Optional[str], title: Optional[str]) -> Optional[str]:
    """``F77(ID)`` (``F77`` without a title); None for an id without a group number."""
    label = card_ids.flow_label(group_id)
    if label is None:
        return None
    name = _one_line(title)
    return f"{label}({name})" if name else label


def card_name(card_ref: Optional[dict], title: Optional[str]) -> Optional[str]:
    """``F77-12(card title)`` from a ``card_ref`` (``F77-12`` without a title); None while unnumbered."""
    short = (card_ref or {}).get("short")
    if not short:
        return None
    name = _one_line(title)
    return f"{short}({name})" if name else short


def bound_flow_line(group_id: str, title: Optional[str]) -> str:
    """The chat's bound Flow: its stored id (for API calls) and how to name it to the user."""
    ref = card_ids.flow_ref(group_id)
    if ref is None:
        return f"Bound flow_id: {group_id}."
    return f"Bound flow_id: {group_id} = {flow_name(group_id, title)} (full id {ref['full']})."
