"""Prompt block registry (flowgate.v02test.0061 card 4, design 0004-D section 1 / 2.1).

A block is one shared chunk of AI guidance, defined exactly once. The mention builders
(``mention_service``, ``invoke_mention_service``, ``q_answer_invoke_service``, the provider
transport in ``ai_invoke.worker`` ...) and the help tree (``services.help``) both read the
same definition from here, so the two can no longer drift or duplicate each other.

Card 4 only moves the originals: every renderer returns the exact text its old owner
produced, so no prompt changes. Shortening (brief vs. detail) is card 5.
"""
from .registry import Block, brief, detail, get, names, register

__all__ = ["Block", "brief", "detail", "get", "names", "register"]
