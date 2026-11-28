"""Compatibility projection entry point."""
from importlib import import_module

def leaf_content(area, leaf, ctx):
    owner = "compat" if (area, leaf) in (("documents", "doc_type"), ("documents", "group_list")) else (
        "docs_area" if area in ("conversation", "documents", "attachments") else "cards_area")
    return import_module(f"{__package__}.legacy.{owner}.leaves").leaf_content(area, leaf, ctx)
