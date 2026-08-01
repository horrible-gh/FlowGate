"""Shared document workflow constants."""
from __future__ import annotations

# Notes that complete at creation time and never require a review action.
AUTO_COMPLETE_TYPES = frozenset({"M", "CH"})