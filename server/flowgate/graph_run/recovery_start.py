"""Optional inspected source context for the existing chat start command."""
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from . import recovery_basis as basis, recovery_store as store, workspace


class CheckedStart:
    def __init__(self, group, expected, confirmed=None):
        self.group, self.expected, self.confirmed = group, expected, confirmed

    def before(self):
        current = basis.view(self.group, self.expected.get("node_key"))
        expected = dict(self.expected)
        if self.confirmed is not None:
            expected.update(draft_version=None, latest_revision_no=self.confirmed)
            if expected.get("run_id") is None:
                expected["revision_no"] = self.confirmed
        if current["context"] != expected:
            raise GraphApiError(409, "recovery_context_changed", {"current": current})
        if not current["new_attempt"]["available"]:
            raise GraphApiError(409, "recovery_basis_unavailable", {"reason": current["new_attempt"]["reason"]})
        fixed = current["existing_attempt"]
        if fixed["available"] and fixed["commit"] != current["new_attempt"]["commit"]:
            raise GraphApiError(409, "recovery_refused", {"reason": "existing_attempt_requires_retry"})
        store.require_available()

    def created(self, ctx):
        # A new run has no predecessor results. Pin only its initial command window.
        value = self.expected["group_head"]
        store.pin(ctx.run_id, self.group["group_id"], {"commit": value["commit"]})

    def continued(self, run):
        key = self.expected.get("node_key")
        if not key:
            raise GraphApiError(422, "invalid_request", {"reason": "node_context_required_for_resume"})
        aid = run_store.active_attempt_id(run["run_id"], key)
        if not aid:
            raise GraphApiError(409, "recovery_refused", {"reason": "card_not_opened"})
        row = workspace.attempt(aid)
        selected = self.expected["new_attempt"]
        if row.get("input_commit") and row["input_commit"] != selected["commit"]:
            raise GraphApiError(409, "recovery_refused", {"reason": "existing_attempt_requires_retry"})
        pinned = store.input_of(aid)
        if pinned and pinned != selected:
            raise GraphApiError(409, "recovery_context_changed")
        if not pinned:
            store.pin(aid, self.group["group_id"], selected)
