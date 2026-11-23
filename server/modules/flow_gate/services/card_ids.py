"""Display ids of Flows and cards (``F77``, ``F77-12``): names shown on screen and lookup aliases.

Stored ids never change: the group id (``<project>.<module>.<number>``), document ids, the work plan card id and the
graph node key stay what they are. A Flow number is the group number without its leading zeros, derived here
from the group id and never stored. A card number is stored per (Flow, node key) by ``card_numbers``.

The forms of a card id, by where it is written:

* the same Flow: ``#12``;
* another Flow of the same module: ``F77-12``;
* another module: ``v02test.F77-12``;
* the full id (copy, search, links): ``flowgate.v02test.F77-12`` (module key, never its display name).

Parsing ignores case and leading zeros (``f0077-012`` is ``F77-12``); a bare number is a Flow number candidate.
Pure: no database, no framework.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

FLOW_PREFIX = "F"
LOCAL_PREFIX = "#"

_SEGMENT = r"[A-Za-z0-9_\-]+"
_REF_RE = re.compile(
    rf"^(?:(?P<first>{_SEGMENT})\.)??(?:(?P<second>{_SEGMENT})\.)?"
    r"[Ff](?P<flow>\d{1,9})(?:-(?P<card>\d{1,9}))?$"
)
_BARE_FLOW_RE = re.compile(r"^\d{1,9}$")
_LOCAL_RE = re.compile(r"^#(?P<card>\d{1,9})$")


@dataclass(frozen=True)
class ParsedRef:
    """What a typed id names. ``flow_no`` is None only for a local ``#12`` (its Flow is the caller's)."""

    flow_no: Optional[int]
    card_no: Optional[int] = None
    project: Optional[str] = None
    module: Optional[str] = None
    bare: bool = False                  # digits only: a Flow number candidate, never a card

    @property
    def kind(self) -> str:
        return "card" if self.card_no is not None else "flow"


def split_group_id(group_id: Optional[str]) -> Optional[tuple[str, str, int]]:
    """``project.module.NNNN`` -> (project, module, number), else None."""
    parts = (group_id or "").split(".")
    if len(parts) < 3 or not parts[-1].isdigit() or not parts[-2] or not all(parts[:-2]):
        return None
    return ".".join(parts[:-2]), parts[-2], int(parts[-1])


def flow_label(group_id: Optional[str]) -> Optional[str]:
    """A group id ending in ``.0077`` -> ``F77`` (None for an id without a group number)."""
    split = split_group_id(group_id)
    return f"{FLOW_PREFIX}{split[2]}" if split else None


def flow_ref(group_id: Optional[str]) -> Optional[dict]:
    """The display ids of a Flow, or None."""
    split = split_group_id(group_id)
    if split is None:
        return None
    project, module, number = split
    label = f"{FLOW_PREFIX}{number}"
    return {"flow_no": number, "label": label, "module_ref": f"{module}.{label}",
            "full": f"{project}.{module}.{label}"}


def card_ref(group_id: Optional[str], card_no: Optional[int]) -> Optional[dict]:
    """The display ids of card ``card_no`` of a Flow, or None (no number yet, or no group number)."""
    split = split_group_id(group_id)
    if split is None or card_no is None:
        return None
    project, module, number = split
    short = f"{FLOW_PREFIX}{number}-{int(card_no)}"
    return {"card_no": int(card_no), "local": f"{LOCAL_PREFIX}{int(card_no)}", "short": short,
            "module_ref": f"{module}.{short}", "full": f"{project}.{module}.{short}"}


def parse(text: Optional[str], *, allow_bare: bool = True, allow_local: bool = True) -> Optional[ParsedRef]:
    """A typed Flow / card id, or None when ``text`` is no such id (a stored id is never one)."""
    value = (text or "").strip()
    if not value or len(value) > 300:
        return None
    if allow_local:
        local = _LOCAL_RE.fullmatch(value)
        if local:
            return ParsedRef(flow_no=None, card_no=int(local["card"]))
    if allow_bare and _BARE_FLOW_RE.fullmatch(value):
        return ParsedRef(flow_no=int(value), bare=True)
    match = _REF_RE.fullmatch(value)
    if not match:
        return None
    first, second = match["first"], match["second"]
    project, module = (first, second) if second is not None else (None, first)
    flow_no = int(match["flow"])
    card = match["card"]
    if flow_no < 1 or (card is not None and int(card) < 1):
        return None
    return ParsedRef(flow_no=flow_no, card_no=int(card) if card is not None else None,
                     project=project, module=module)


def same_key(left: Optional[str], right: Optional[str]) -> bool:
    """Project / module keys compared the way a typed id is read (case-insensitive)."""
    return (left or "").lower() == (right or "").lower()
