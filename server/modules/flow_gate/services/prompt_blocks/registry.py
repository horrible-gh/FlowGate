"""The block registry: one entry per shared chunk of AI guidance.

Block = name + brief renderer (what a mention carries) + detail renderer (what the help
leaf serves) + help leaf path + exposure condition. Block modules under ``blocks/``
register themselves on import and never import each other; the only shared code is this
registry (and, for exposure, ``services.help.visibility``).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Every block module, imported on first registry use. Adding a module here is the only
# step needed to make its blocks visible.
BLOCK_MODULES: tuple[str, ...] = (
    "attachments",
    "cards",
    "chat_mode",
    "conversation",
    "documents",
    "source_tools",
    "submit",
)


def _always(_ctx: Optional[dict] = None) -> bool:
    return True


@dataclass(frozen=True)
class Block:
    name: str
    brief: Callable[..., Any]
    detail: Callable[..., Any]
    help_leaf: Optional[str] = None
    visible: Callable[[Optional[dict]], bool] = _always


_REGISTRY: dict[str, Block] = {}
_loaded = False


def register(block: Block) -> Block:
    existing = _REGISTRY.get(block.name)
    if existing is not None and existing != block:
        raise ValueError(f"prompt block registered twice: {block.name}")
    _REGISTRY[block.name] = block
    return block


def _load() -> None:
    global _loaded
    if _loaded:
        return
    for module in BLOCK_MODULES:
        importlib.import_module(f"{__package__}.blocks.{module}")
    _loaded = True


def get(name: str) -> Block:
    _load()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown prompt block: {name}") from None


def names() -> tuple[str, ...]:
    _load()
    return tuple(sorted(_REGISTRY))


def brief(name: str, *args: Any, **kwargs: Any) -> Any:
    """What a mention carries for this block."""
    return get(name).brief(*args, **kwargs)


def detail(name: str, *args: Any, **kwargs: Any) -> Any:
    """What the help leaf serves for this block."""
    return get(name).detail(*args, **kwargs)
