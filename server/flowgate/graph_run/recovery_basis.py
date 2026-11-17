"""Read-only recovery inputs. No provisioning, reconciliation, fetch or Git mutation.

An explicit retry opens a new input boundary for that card: predecessor results,
or the observed group HEAD when there are no predecessors. Existing attempts
keep their pinned input; normal command windows keep their existing semantics.
"""
import hashlib
import json
from pathlib import Path

from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_read_snapshot
from flowgate.gate_decisions import legacy_adapter
from . import workspace


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def unknown(reason):
    return {"available": False, "commit": None, "reason": reason}


def git_facts(group):
    try:
        slot = legacy_adapter.git_slot(group.get("project_id"), group["group_id"])
        if not slot.get("ok"):
            return unknown(slot.get("reason") or "git_unavailable")
        return {"available": True, "ref": "refs/heads/" + slot["group_branch"],
                "commit": slot["group_head"], "work_base_ref": slot.get("work_base"),
                "work_base_commit": slot.get("work_base_sha")}
    except Exception:
        return unknown("git_read_failed")


def candidate(group, run, node_key, head):
    if not head["available"]:
        return unknown(head["reason"])
    if not run or node_key is None:
        return {"available": True, "commit": head["commit"], "ref": head["ref"],
                "source": "group_head", "input_attempt_ids": [], "predecessors": []}
    row = {**run, "attempt_id": "", "node_key": node_key}
    try:
        inputs = workspace._inputs(row)
        root = workspace.git._base_root_of(group["project_id"])
        if not root:
            return unknown("checkout_unavailable")
        shas = list(dict.fromkeys(i[2] for i in inputs))
        if any(workspace.git._rev_parse(Path(root), sha + "^{commit}") != sha for sha in shas):
            return unknown("input_missing")
        heads = [sha for sha in shas if not any(other != sha and
                 workspace.git._is_ancestor(Path(root), sha, other) for other in shas)]
        details = {"input_attempt_ids": [i[1] for i in inputs], "predecessors": [list(i) for i in inputs]}
        if len(heads) > 1:
            return {**unknown("join_requires_merge"), **details}
        return {"available": True, "commit": heads[0] if heads else head["commit"],
                "ref": heads[0] if heads else head["ref"],
                "source": "predecessors" if heads else "group_head", **details}
    except Exception:
        return unknown("input_read_failed")


def view(group, node_key=None):
    def read():
        flow_id = group["group_id"]
        run = run_store.current_run(flow_id)
        latest = definition_store.latest_revision_no(flow_id)
        draft = definition_store.draft(flow_id)
        revision = int(run["revision_no"]) if run else latest
        record = definition_store.revision(flow_id, revision) if revision else draft
        node = next((n for n in (record or {}).get("nodes", []) if n["node_key"] == node_key), None)
        if node_key is not None and node is None:
            raise GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key})
        aid = run_store.active_attempt_id(run["run_id"], node_key) if run and node_key else None
        attempt = workspace.attempt(aid) if aid else None
        row = next((n for n in run_store.node_runs(run["run_id"]) if n["node_key"] == node_key), {}) if run else {}
        head = git_facts(group)
        proposed = candidate(group, run, node_key, head)
        fixed = ({"available": True, "commit": attempt["input_commit"],
                  "source": attempt.get("input_source"), "attempt_id": aid}
                 if attempt and attempt.get("input_commit") else {**unknown("not_prepared"), "attempt_id": aid})
        from flowgate.card_settings import repository
        settings = repository.settings_version(flow_id, node_key) if node_key else None
        versions = ({n["node_key"]: repository.settings_version(flow_id, n["node_key"])
                     for n in (record or {}).get("nodes", [])} if node_key is None else None)
        window = workspace._base_window(run["run_id"])[0] if run else None
        context = {"flow_id": flow_id, "node_key": node_key, "run_id": run["run_id"] if run else None,
                   "run_status": run["status"] if run else None, "revision_no": revision,
                   "latest_revision_no": latest, "draft_version": (draft or {}).get("draft_version"),
                   "attempt_id": aid, "node_status": row.get("status"), "settings_version": settings,
                   "settings_versions": versions,
                   "base_window": window, "group_head": head, "new_attempt": proposed}
        return {"flow_id": flow_id, "node_key": node_key, "group_head": head,
                "new_attempt": proposed, "existing_attempt": fixed, "context": context,
                "context_digest": digest(context)}
    return with_read_snapshot(read)


def require_context(group, expected, node_key=None):
    actual = view(group, node_key)
    if expected != actual["context"]:
        raise GraphApiError(409, "recovery_context_changed", {"flow_id": group["group_id"],
                            "node_key": node_key, "current": actual})
    if not actual["new_attempt"]["available"]:
        raise GraphApiError(409, "recovery_basis_unavailable", {"reason": actual["new_attempt"]["reason"]})
    return actual
