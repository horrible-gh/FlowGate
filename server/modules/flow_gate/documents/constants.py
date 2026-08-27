"""Shared document workflow constants."""
from __future__ import annotations

# Notes that complete at creation time and never require a review action.
AUTO_COMPLETE_TYPES = frozenset({"M", "CH"})

# Explicit review gates whose state is carried entirely by the database row.
# These types still require a human approval action; they are not auto-complete.
FILELESS_APPROVABLE_TYPES = frozenset({"AC"})

# ── Server-assembled document types (0441 T0004 item 3 / NR0003 recommendation A) ──
# Types the SERVER builds out of a machine result instead of anybody writing them. A TSR
# is assembled by test_run_service.assemble_tsr() from a finished test run, so no human
# and no worker may author one: /documents/next-empty, an inbox action:new submission and
# the ordinary action_scope='new' token hand-off all refuse these types. The official
# assembly path calls document_service directly and goes through none of those gates, so
# it is unaffected.
#
# This is the single source of truth for that policy. workflow_decision_service
# .SERVER_ASSEMBLED_REPORT_TYPES and WORK_PLAN_LOCKED_TYPES below both READ this set
# rather than restating it — before 0441 the same "TSR" literal sat in three files and
# only one of them was ever consulted when a document was actually created.
SERVER_ASSEMBLED_DOC_TYPES = frozenset({"TSR"})


def is_server_assembled_type(type_code: str | None) -> bool:
    """Whether ``type_code`` names a document only the server itself may create."""
    return str(type_code or "").strip().upper() in SERVER_ASSEMBLED_DOC_TYPES


# Records that are intentionally outside workflow_sequence_items result slots.
NON_SLOT_WORKFLOW_TYPES = AUTO_COMPLETE_TYPES | frozenset({"Q", "A", "AC"})
# The work plan (WP) is deliberately **NOT** in this set. D0007 §7 required stating where a
# work plan belongs, and the same document settled it as "an ordinary slot following the
# requirements definition" — in P0009 §7.2's preview example WP occupies item_seq 2. So WP is
# a slot-occupying type, and a WP not attached to a slot shows as an orphan that must be asked where to attach.
# Do not confuse this with HEAD_TYPE_GUARD_EXEMPT_TYPES below: that one relaxes "you may only
# create it while the workflow head is WP", whereas this one means "it occupies no slot at all".

# ── Work plan (WP) — flowgate.default.0395 R0001 / D0007 ─────────────────────
# The advisory plan document: type counts + per-step provider assignment, stored
# as a JSON canonical body. It is NOT auto-complete (D0007 §3.1 decision 4) — it is
# created pending_review and goes through the ordinary review pipeline.
WORK_PLAN_TYPE = "WP"

# Types exempt from the inbox workflow-head type guard. A workflow head pins the
# next expected document type, which is right for step documents. A work plan is
# advisory and may be written at any point in a group's life — D0007 §3.1 decision 5
# explicitly allows several plans per group — so pinning it to the head would make
# the documented AI creation path (P0009 §5.1) fail whenever the group is mid-flow.
HEAD_TYPE_GUARD_EXEMPT_TYPES = AUTO_COMPLETE_TYPES | frozenset({WORK_PLAN_TYPE})

# ── Countable type registry (P0009 §4.1, L0010 §2.1) ─────────────────────────
# "Countable" means the type occupies a quantity box in a work plan. The unit and
# the instruction→result pairing are facts about the workflow, not per-project
# settings, so they live in code — but the *order* the types are listed in is read
# from the document_types table (L0010 §2.1 decision 2), never from this file.
WORK_PLAN_SHEET_TYPES = ("DS", "D", "P", "L", "DB")
WORK_PLAN_SET_TYPES = ("N", "T", "TS")

# instruction code → result code. Mirrors workflow_decision_service.AUTO_REPORT_MAP;
# duplicated here (not imported) so the document layer does not depend on the
# workflow-decision service just to describe a type.
WORK_PLAN_PAIR_MAP = {"N": "NR", "T": "TR", "TS": "TSR"}

WORK_PLAN_TYPE_UNITS: dict[str, str] = {
    **{code: "sheet" for code in WORK_PLAN_SHEET_TYPES},
    **{code: "set" for code in WORK_PLAN_SET_TYPES},
}

# Fallback ordering used only when the type table cannot be read.
WORK_PLAN_COUNTABLE_ORDER = WORK_PLAN_SHEET_TYPES + WORK_PLAN_SET_TYPES

# Every type code a work plan step may carry: the countable ones plus the result
# halves of each set.
WORK_PLAN_STEP_TYPES = frozenset(
    set(WORK_PLAN_COUNTABLE_ORDER) | set(WORK_PLAN_PAIR_MAP.values())
)

# Steps the server assembles itself — no provider, no note (DS0006 §2-7). Same set as
# SERVER_ASSEMBLED_DOC_TYPES above, read from it rather than restated (0441 T0004 item 3):
# "the server builds this row" and "nobody may author this document" are one fact.
WORK_PLAN_LOCKED_TYPES = SERVER_ASSEMBLED_DOC_TYPES

# ── One-line note length cap — canonical (0406 T0022 item 6 / M0019) ─────────
# The same value was pinned separately in work_plan_service, work_plan_sequence_service and
# the screen — all three at 200. The work-plan editor blocked typing at that 200 with no
# warning, and the sequence save path silently cut everything past 200 — the reported "the work plan gets chopped off going in".
# The cap now lives here alone, and both server services plus the screen (via limits in the
# API response) read this value. On overflow the save path rejects with note_too_long instead of truncating.
STEP_NOTE_MAX_CHARS = 1000
