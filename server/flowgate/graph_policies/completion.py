"""Completion = all run targets done, with no active attempt (0061 T#1).

Candidate exhaustion is never success: join/approval/rejection waits stay incomplete.
The caller supplies has_active_attempts in RunState (T#2: run_apply.read_run_state reads graph_node_attempts).
Pure judgement, including a separate no_targets reason for an empty scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flowgate.graph_validation.run_scope import run_scope

ALL_TARGETS_DONE = "all_targets_done"
IMPL_VERSION = "1"


@dataclass(frozen=True)
class CompletionResult:
    complete: bool
    reason: str


class AllTargetsDone:
    key = ALL_TARGETS_DONE

    def evaluate(self, revision, state, *, has_active_attempts: Optional[bool] = None) -> CompletionResult:
        targets = run_scope(revision).targets
        if not targets:
            return CompletionResult(False, "no_targets")
        active = state.has_active_attempts if has_active_attempts is None else has_active_attempts
        if active:
            return CompletionResult(False, "active_attempts")
        if any(state.node(key) is None or state.node(key).status != "done" for key in targets):
            return CompletionResult(False, "targets_not_done")
        return CompletionResult(True, "all_targets_done")

    def is_complete(self, revision, state) -> bool:
        return self.evaluate(revision, state).complete


ALL_DONE = AllTargetsDone()
SLOTS = (("completion", ALL_TARGETS_DONE, IMPL_VERSION, ALL_DONE),)
