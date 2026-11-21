"""Flow ownership judgement — a pure function of two facts."""
from __future__ import annotations


def judge_ownership(has_assignment: bool, has_sequence: bool) -> str:
    if has_assignment and has_sequence:
        return "conflict"
    if has_assignment:
        return "graph"
    if has_sequence:
        return "v01"
    return "unassigned"
