"""Card document lines (flowgate.v02.0053 T#1, 0053 CH agreement on the documents of each section).

Every card placed on the Designer owns a fixed set of document lines ("slots"). Pure: which lines
a node has and which document type fills each one. A line is written by a person (``human``: the
draft is opened and written), filled by a run (``run``: created empty, the run's result takes
its place), or — the instruction of a research / work / apply / design / custom card — written by the
card's instruction AI or a person (``instruction_ai_or_human``, flowgate.v02.0056 T#2: the card run writes
the instruction when its body is empty and keeps a written one). The other modules create, bind and remove
the documents; nothing here touches storage.

- requirement (R / B): requirement · AI delegation (only with AI delegation on; its setting lands
  with T#2)
- work plan (WP): work plan · deploy
- design (D / P / L / DB) · research (N) · work (T / T2) · custom: instruction · report
- test (TS): instruction · scenario · report — the scenario (cases) lives inside the TS document
  (0549 contract 2: no separate case document), so the scenario line shares the instruction document.
  Its instruction stays a person's: the test cards take no instruction AI (TS is reorganized later).
- gate and unknown types: no lines.

flowgate.v02.0056 T#1: the card approval approves the documents of a card's approval lines
(``approval_slots``): the instruction then the report of a research / work / apply / design / custom /
test card (N + NR, T + TR, T2 + TR2, DS + design, TS + TSR), the requirement of R / B and the plan of WP.
The lines a run makes for the record (AI delegation, deploy) and the scenario sharing the TS are not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

REQUIREMENT, DELEGATION, PLAN, DEPLOY = "requirement", "delegation", "plan", "deploy"
INSTRUCTION, SCENARIO, REPORT = "instruction", "scenario", "report"
SLOT_KEYS = (REQUIREMENT, DELEGATION, PLAN, DEPLOY, INSTRUCTION, SCENARIO, REPORT)
HUMAN, RUN = "human", "run"
# The instruction line of a card that runs an instruction stage: its instruction AI or a person writes it.
INSTRUCTED = "instruction_ai_or_human"

# Document title suffix of each line, per locale (the stored document title, not a screen wording).
SLOT_TITLES = {
    "ko": {
        REQUIREMENT: "요구사항", DELEGATION: "AI 위임", PLAN: "작업계획", DEPLOY: "전개",
        INSTRUCTION: "지시서", SCENARIO: "시나리오", REPORT: "레포트",
    },
    "en": {
        REQUIREMENT: "Requirement", DELEGATION: "AI delegation", PLAN: "Work plan", DEPLOY: "Deploy",
        INSTRUCTION: "Instruction", SCENARIO: "Scenario", REPORT: "Report",
    },
}
DEFAULT_LOCALE = "ko"

# Free-form record type for the lines no v0.1 type describes (deploy, AI delegation).
RECORD_DOC_TYPE = "M"
# A custom (palette [+]) card works like research / work (0053 CH §2): work instruction · work report.
CUSTOM_DOC_TYPES = ("T", "TR")
# Instruction / report pair of each document node type (v0.1 AUTO_REPORT_MAP and DS -> design).
_PAIRS = {
    "N": ("N", "NR"), "T": ("T", "TR"), "T2": ("T2", "TR2"),
    "D": ("DS", "D"), "P": ("DS", "P"), "L": ("DS", "L"), "DB": ("DS", "DB"),
}
ROOT_TYPES = frozenset({"R", "B"})


@dataclass(frozen=True)
class CardSlot:
    slot: str
    doc_type: str
    author: str                           # HUMAN | RUN | INSTRUCTED
    shares: Optional[str] = None          # another line of the same card whose document it shows


def card_slots(node_type: Optional[str], doc_type: Optional[str], *, ai_delegation: bool = False) -> tuple:
    """The lines of one card, in display order (tuple[CardSlot, ...])."""
    if node_type == "custom":
        return (CardSlot(INSTRUCTION, CUSTOM_DOC_TYPES[0], INSTRUCTED), CardSlot(REPORT, CUSTOM_DOC_TYPES[1], RUN))
    if node_type != "document":
        return ()
    t = str(doc_type or "").strip().upper()
    if t in ROOT_TYPES:
        lines = [CardSlot(REQUIREMENT, t, HUMAN)]
        if ai_delegation:
            lines.append(CardSlot(DELEGATION, RECORD_DOC_TYPE, RUN))
        return tuple(lines)
    if t == "WP":
        return (CardSlot(PLAN, "WP", HUMAN), CardSlot(DEPLOY, RECORD_DOC_TYPE, RUN))
    if t == "TS":
        return (CardSlot(INSTRUCTION, "TS", HUMAN), CardSlot(SCENARIO, "TS", RUN, shares=INSTRUCTION),
                CardSlot(REPORT, "TSR", RUN))
    if t in _PAIRS:
        instruction, report = _PAIRS[t]
        return (CardSlot(INSTRUCTION, instruction, INSTRUCTED), CardSlot(REPORT, report, RUN))
    return ()


def instruction_and_report(node_type: Optional[str], doc_type: Optional[str]) -> Optional[tuple[str, str]]:
    """(instruction type, report type) of a card whose instruction its instruction AI writes — research,
    work, apply, design and custom cards (N / NR, T / TR, T2 / TR2, DS / design, T / TR) — else None."""
    lines = {s.slot: s for s in card_slots(node_type, doc_type)}
    instruction, report = lines.get(INSTRUCTION), lines.get(REPORT)
    if instruction is None or report is None or instruction.author != INSTRUCTED:
        return None
    return instruction.doc_type, report.doc_type


_APPROVAL_LINES = (REQUIREMENT, PLAN, INSTRUCTION, REPORT)


def approval_slots(node_type: Optional[str], doc_type: Optional[str]) -> tuple:
    """The lines whose documents a card approval approves, instruction first (tuple[CardSlot, ...])."""
    return tuple(s for s in card_slots(node_type, doc_type) if s.slot in _APPROVAL_LINES and not s.shares)


def slot_signature(slot: CardSlot) -> tuple:
    """What a stored line must match to be kept on a re-save (a changed type is a new line)."""
    return (slot.slot, slot.doc_type)


def slot_title(slot: str, locale: Optional[str] = None) -> str:
    return (SLOT_TITLES.get(locale or DEFAULT_LOCALE) or SLOT_TITLES[DEFAULT_LOCALE])[slot]


def author_of(slot: str, doc_type: Optional[str] = None) -> str:
    """Who writes a stored line (``slot`` of ``doc_type``): a test card's instruction (TS) stays a person's."""
    if slot == INSTRUCTION:
        return HUMAN if str(doc_type or "").strip().upper() == "TS" else INSTRUCTED
    return HUMAN if slot in (REQUIREMENT, PLAN) else RUN


def lines_of(node_type: Optional[str], doc_type: Optional[str], lines: list) -> list:
    """The stored lines a node of this kind shows, in stored order.

    Lines are kept per kind: a node retyped in the draft still has the lines of the kind a
    confirmed revision gave it, and each view shows only the lines of the kind it draws. A
    requirement card without a requirement line of its own kind shows the one of the other root
    kind: the root it holds was converted R↔B, or a confirmed Flow keeps the root it bound.
    """
    wanted = {slot_signature(s) for s in card_slots(node_type, doc_type, ai_delegation=True)}
    if (REQUIREMENT, str(doc_type or "").strip().upper()) in wanted and not any(
            (line["slot"], line["doc_type"]) in wanted and line["slot"] == REQUIREMENT for line in lines or []):
        wanted |= {(REQUIREMENT, t) for t in ROOT_TYPES}
    return [line for line in lines or [] if (line["slot"], line["doc_type"]) in wanted]


def is_disposable_draft(doc: Optional[dict]) -> bool:
    """A card's own document that is still an untouched-by-workflow draft (deleted with the card).

    Anything that went further — submitted, reviewed, superseded, cancelled — is kept: v0.1
    removes such documents only by a person's delete / cancel decision.
    """
    if not doc:
        return False
    return (str(doc.get("status") or "") == "draft" and not doc.get("doc_review_status")
            and not doc.get("superseded_by"))
