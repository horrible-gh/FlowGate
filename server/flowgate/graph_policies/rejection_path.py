"""Rejection stops that path; unrelated branches continue (0061 T#1).

Only a rejected root or gate is routed. Nothing opens on rejection, and descendants
stay pending: a rejected predecessor cannot propagate approval, and AND joins wait
for every predecessor to be done. No permanent closed state is invented (the existing
apply_decision deliberately rejects close decisions). Revisions of a rejected root
keep the existing contract. Ordinary card rejection stays in review in node_approval.
There is no backward route: cycles are forbidden by graph_validation.
"""
from __future__ import annotations

from flowgate.graph_policies.legacy_adapter import issue

STOP_PATH = "stop_path"
IMPL_VERSION = "1"


class StopRejectedPath:
    key = STOP_PATH

    def route(self, node_key, revision, state) -> tuple[list[str], list[str]]:
        # No opens/closes: the existing dependency and AND-join checks block descendants.
        return [], []

    def validate(self, _graph, occurrence) -> list[dict]:
        # Preserve rejection of cycles even for callers of the legacy validator.
        if occurrence.get("structure") == "cycle":
            return [issue("cycle", occurrence.get("node_keys", ()), occurrence.get("edge_ids", ()))]
        return []


STOP_REJECTED_PATH = StopRejectedPath()
SLOTS = (("rejection_path", STOP_PATH, IMPL_VERSION, STOP_REJECTED_PATH),)
