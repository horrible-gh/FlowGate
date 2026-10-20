"""What a work plan deploys (flowgate.v02.0074 T#4, D0006 §3.3 / §3.4) — pure, no storage.

A deploy target is one WP card id: the steps sharing it (an N / T / TS instruction with its result, or one design
step D / P / L / DB) make one Designer card of the card id's type. Plan order is the order in which each card id first
appears in the steps. DS and T2 / TR2 steps (hidden in the editor, kept for v0.1) make no card; neither does any other
type (``skipped_type``). The card id, not a sequence number, links a card to its step, so reordering the plan or
changing quantities keeps every card on its own step.

A card's instruction text comes from one step's pre-instruction (its attachment and text): the instruction step of
N / T, the design step itself for D / P / L / DB. TS takes none (its instruction is a person's scenario document).
The step note is never a source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DESIGN_TYPES = ("D", "P", "L", "DB")
PAIR_TYPES = ("N", "T", "TS")
CARD_TYPES = frozenset(DESIGN_TYPES + PAIR_TYPES)
INSTRUCTION_TYPES = frozenset(DESIGN_TYPES + ("N", "T"))
FIRST, ADD, CLEANUP = "first", "add", "cleanup"
MODES = (FIRST, ADD, CLEANUP)
NODE_TYPE = "document"


@dataclass
class Target:
    card_id: str
    doc_type: str                               # the card's type: N / T / TS / D / P / L / DB
    order: int
    steps: list = field(default_factory=list)   # the plan steps of the card id, in plan order

    @property
    def instruction_step(self) -> Optional[dict]:
        """The step whose pre-instruction becomes the card's instruction (None for TS)."""
        if self.doc_type not in INSTRUCTION_TYPES:
            return None
        if self.doc_type in DESIGN_TYPES:
            return next((s for s in self.steps if s.get("pair_role") == "single"), None)
        return next((s for s in self.steps if s.get("pair_role") == "instruction"), None)

    @property
    def run_step(self) -> Optional[dict]:
        """The step whose AI runs the card's result (a pair's result step, else the card's one step)."""
        if self.doc_type in ("N", "T"):
            return next((s for s in self.steps if s.get("pair_role") == "result"), None)
        return next((s for s in self.steps if s.get("pair_role") in ("single", "instruction")), None)


def targets(body: dict, type_of_step: Callable[[dict], Optional[str]]) -> tuple[list[Target], list[dict]]:
    """(deploy targets in plan order, the card ids that make no card ``{card_id, doc_type, result, reason}``)."""
    by_card: dict[str, Target] = {}
    skipped: dict[str, dict] = {}
    for step in body.get("steps") or []:
        card_id = step.get("card_id") or step.get("key")
        if not card_id:
            continue
        card_type = str(type_of_step(step) or step.get("type") or "").upper()
        if card_type not in CARD_TYPES:
            skipped.setdefault(card_id, {"card_id": card_id, "doc_type": card_type, "result": "skipped_type",
                                         "reason": "not_a_card_type"})
            continue
        target = by_card.get(card_id)
        if target is None:
            target = by_card[card_id] = Target(card_id, card_type, len(by_card))
        target.steps.append(step)
    return list(by_card.values()), list(skipped.values())


def pre_instruction(target: Target) -> tuple[Optional[str], Optional[dict]]:
    """(text, attachment reference) of the card's instruction source; (None, None) when it has none."""
    step = target.instruction_step
    if step is None:
        return None, None
    text = step.get("pre_instruction_text")
    text = text.strip() if isinstance(text, str) and text.strip() else None
    reference = step.get("pre_instruction_attachment")
    return text, reference if isinstance(reference, dict) else None


def initial_settings(target: Target) -> list[tuple[str, Any]]:
    """``(field, value)`` the new card's settings start with (D0006 §3.4); unset WP values are left out.

    instruction AI: a pair's instruction step AI (N / T); run AI: a pair's result step AI, the design / test step's
    AI; review AI and review count: of that same run step. The instruction use is never set (the instruction
    omission of the WP editor is a written instruction, not ``omit``)."""
    out: list[tuple[str, Any]] = []
    if target.doc_type in ("N", "T"):
        instruction = target.instruction_step or {}
        if instruction.get("provider_id"):
            out.append(("instruction_ai", instruction["provider_id"]))
    run = target.run_step or {}
    if run.get("provider_id"):
        out.append(("run_ai", run["provider_id"]))
    if run.get("reviewer_provider_id"):
        out.append(("review_ai", run["reviewer_provider_id"]))
    count = run.get("review_count")
    if isinstance(count, int) and not isinstance(count, bool) and count != 0:
        out.append(("review_count", count))
    return out


def source_value(wp_doc_id: str, target: Target, wp_revision_no: int, instruction_doc_id: Optional[str] = None,
                 instruction_digest: Optional[str] = None) -> dict:
    """The hidden ``wp_source`` card setting: which plan card the node came from, as what, at which revision, and the
    instruction the deploy wrote (its document and the digest of its file)."""
    return {"wp_doc_id": wp_doc_id, "card_id": target.card_id, "node_type": NODE_TYPE, "doc_type": target.doc_type,
            "wp_revision_no": wp_revision_no, "instruction_doc_id": instruction_doc_id,
            "instruction_digest": instruction_digest}


def is_source(value: Any, wp_doc_id: str) -> bool:
    return isinstance(value, dict) and value.get("wp_doc_id") == wp_doc_id and isinstance(value.get("card_id"), str)


def node_is(node: dict, doc_type: Optional[str]) -> bool:
    """The node is a card of ``doc_type`` (node kind and document type)."""
    return (node.get("node_type") == NODE_TYPE and doc_type is not None
            and str(node.get("doc_type") or "").strip().upper() == doc_type)
