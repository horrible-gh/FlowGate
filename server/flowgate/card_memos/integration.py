"""Card memo port handler: how the AI mentions of ``modules.flow_gate`` reach this package (D0006 §2.3 / §4.1).

``modules.flow_gate.services.card_memo_hooks`` defines the port; the composition root (``startup.py``) calls
:func:`register` once at startup.

    memo_section   a card's AI starts (its instruction / result stage, its review and rework hops), the
                   per-document AI invoke of a card document, a Q&A answer on one: the "User memos" section
                   of the card's memos turned on for AI reading (``memos.memo_section``)
"""
from __future__ import annotations

from modules.flow_gate.services import card_memo_hooks as hooks

from flowgate.card_memos import memos


def register() -> None:
    hooks.register(hooks.MEMO_SECTION, memos.memo_section)


def unregister() -> None:
    for name in hooks.HOOK_NAMES:
        hooks.unregister(name)
