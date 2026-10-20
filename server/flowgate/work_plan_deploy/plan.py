"""WP-to-card deployment; v0.2 paired stage contract is in CARD_STAGES.md.

Each card ID owns an instruction/result pair. Design instructions retain the DS document type
for storage compatibility, but are real card-owned steps; standalone legacy DS records deploy
no card. Older single-step plans remain readable. Stage settings and edit inputs are independent.
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
            return next((s for s in self.steps if s.get("pair_role") == "instruction"), None) or next(
                (s for s in self.steps if s.get("pair_role") == "single"), None)
        return next((s for s in self.steps if s.get("pair_role") == "instruction"), None)

    @property
    def run_step(self) -> Optional[dict]:
        """The step whose AI runs the card's result (a pair's result step, else the card's one step)."""
        if self.doc_type in ("N", "T") + DESIGN_TYPES:
            result = next((s for s in self.steps if s.get("pair_role") == "result"), None)
            if result is not None:
                return result
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
    if instruction_mode(target) == "omit" or step is None:
        return None, None
    text = step.get("pre_instruction_text")
    text = text.strip() if isinstance(text, str) and text.strip() else None
    reference = step.get("pre_instruction_attachment")
    return text, reference if isinstance(reference, dict) else None


def instruction_mode(target: Target) -> Optional[str]:
    """Explicit instruction choice of an N / T card (its instruction step) or a D / P / L / DB card (its design
    step: the 설계 지시 line); None leaves pre-existing card settings alone."""
    step = target.instruction_step or {}
    mode = step.get("instruction_mode")
    return mode if target.doc_type in ("N", "T") + DESIGN_TYPES and mode in ("use", "omit") else None


def initial_settings(target: Target) -> list[tuple[str, Any]]:
    """``(field, value)`` the new card's settings start with (D0006 §3.4); unset WP values are left out.

    instruction AI: a pair's instruction step AI (N / T); run AI: a pair's result step AI, the design / test step's
    AI; review AI and review count: of that same run step. An explicit instruction_mode maps to the
    existing card execution setting; legacy plans retain their defaults."""
    out: list[tuple[str, Any]] = []
    mode = instruction_mode(target)
    if mode is not None:
        out.append(("instruction", mode))
    if target.doc_type in INSTRUCTION_TYPES:
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
    instruction = target.instruction_step or {}
    if instruction.get("pair_role") == "instruction":
        out.extend([
            ("instruction_review_count", instruction.get("review_count") or 0),
            ("instruction_note", instruction.get("note") or ""),
            ("run_note", run.get("note") or ""),
        ])
        if instruction.get("reviewer_provider_id"):
            out.append(("instruction_review_ai", instruction["reviewer_provider_id"]))
    if run.get("_run_prompt") is not None:
        out.append(("run_pre_instruction", run["_run_prompt"]))
    return out


def stage_settings(target: Target) -> list[tuple[str, Any]]:
    """A paired card's explicit settings, including clears, for pre-start redeploys."""
    instruction, result = target.instruction_step or {}, target.run_step or {}
    if instruction.get("pair_role") != "instruction":
        return []
    return [("instruction_ai", instruction.get("provider_id")),
            ("instruction_review_ai", instruction.get("reviewer_provider_id")),
            ("instruction_review_count", instruction.get("review_count") or 0),
            ("instruction_note", instruction.get("note") or ""),
            ("run_ai", result.get("provider_id")), ("review_ai", result.get("reviewer_provider_id")),
            ("review_count", result.get("review_count") or 0), ("run_note", result.get("note") or ""),
            ("run_pre_instruction", result.get("_run_prompt") or "")]


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
