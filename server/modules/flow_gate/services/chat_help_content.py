"""Compatibility facade for independently owned chat help suppliers."""
from importlib import import_module
from .help.legacy.compat.chat_content import (ITEMS, TITLES, INTRO,
    conversation_turn_submission_lines, _encoding_probe_paragraph)

def content(name, ctx):
    if name not in ITEMS:
        raise ValueError(name)
    owner = "docs_area" if name in ("chat_turn", "chat_documents") else "cards_area"
    return import_module(f"{__package__}.help.legacy.{owner}.chat_content").content(name, ctx)
