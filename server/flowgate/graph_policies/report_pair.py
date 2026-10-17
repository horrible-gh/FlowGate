"""The report_pair slot names the existing within-card instruction/result contract.

services.node_dispatch.dispatch_document / start_ai / start_result_stage remain the
executor: write an absent instruction (or use its body), then start the result stage
with that instruction as reference. The instruction's approval never completes the
node; only its result is bound and approved. Omitted instructions keep result-only
execution. This slot adds no executor or dispatch path.

Standalone report cards on Canvas (NR/TR/TR2/TSR) remain validation errors, including
outside run_scope. Deciding the internal pair does not authorize those graph nodes.
"""
from __future__ import annotations

from flowgate.graph_policies.legacy_adapter import issue

INSTRUCTION_RESULT = "instruction_result"
IMPL_VERSION = "1"


class InstructionResult:
    key = INSTRUCTION_RESULT

    def validate(self, _graph, occurrence) -> list[dict]:
        if occurrence.get("structure") == "report_pair":
            return [issue("rule_undecided", occurrence.get("node_keys", ()), occurrence.get("edge_ids", ()),
                          rule_ref="Q-5", structure="report_pair")]
        return []


INSTRUCTION_RESULT_PAIR = InstructionResult()
SLOTS = (("report_pair", INSTRUCTION_RESULT, IMPL_VERSION, INSTRUCTION_RESULT_PAIR),)
