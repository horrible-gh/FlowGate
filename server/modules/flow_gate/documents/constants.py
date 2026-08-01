"""Shared document workflow constants."""
from __future__ import annotations

# Notes that complete at creation time and never require a review action.
AUTO_COMPLETE_TYPES = frozenset({"M", "CH"})

# Explicit review gates whose state is carried entirely by the database row.
# These types still require a human approval action; they are not auto-complete.
FILELESS_APPROVABLE_TYPES = frozenset({"AC"})

# Records that are intentionally outside workflow_sequence_items result slots.
NON_SLOT_WORKFLOW_TYPES = AUTO_COMPLETE_TYPES | frozenset({"Q", "A", "AC"})