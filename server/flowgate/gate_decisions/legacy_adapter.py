"""Reuse boundary of the gate decision intake (flowgate.v02.0085 T#2, D0006 §2.2 / §4.1).

Everything ``flowgate.gate_decisions`` takes from ``modules.flow_gate`` goes through here, called only - never changed:
the Graph API envelope (user JWT, RBAC, error shape, locale), the Flow load, the run guard and its checks as the gate
decision has always made them (``gate_service`` / ``run_service``: run status, engine ownership, gate node, policy
snapshot), the resolver and ``apply_decision`` (node event, attempt, propagation, settlement), the run read, the
operation job store (``gate_publish``) and the local Git facts the confirm's checks read (the project's Git config,
the group's worktree slot, its work base branch, the ``origin`` remote). No Git command here changes anything and none
waits for a lock or the network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.domain.frontier import ProgrammingError, RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import RULE_REF, PolicyUndecided, implementation, policies_of
from modules.flow_gate.graph_workflow.services import canvas_service, gate_service, run_service
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.run_apply import (
    apply_decision, emit_run_status, load_context, read_run_state, settle_run,
)
from modules.flow_gate.graph_workflow.store import definition_store, run_store

__all__ = ["GraphApiError", "ProgrammingError", "PolicyUndecided", "RULE_REF", "RunEvent", "resolve", "policies_of",
           "apply_decision", "emit_run_status", "load_context", "read_run_state", "settle_run", "run_store",
           "read_ownership", "GATE_POLICY", "GATE_EVENTS", "authenticate", "authorize", "invalid", "run",
           "client_id_of", "load_flow", "run_flow_id", "guarded_run", "missing_policies", "ownership_error",
           "gate_undecided", "run_payload", "node_types", "insert_gate_publish", "notify_job", "get_job",
           "cancel_unstarted_job", "git_slot", "STATUS_KEY", "gate_rule_decided"]

GATE_POLICY = gate_service.GATE_POLICY
GATE_EVENTS = gate_service.GATE_EVENTS
STATUS_KEY = envelope.STATUS_KEY


# ── the Graph API envelope ───────────────────────────────────────────────────

def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict, required: str) -> None:
    envelope.authorize(user_id, group, required)


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def client_id_of(request) -> Optional[str]:
    return envelope.client_id_of(request)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def run_flow_id(run_id: str) -> str:
    return run_service.run_flow_id(run_id)


# ── the run guard and the gate decision's existing checks ────────────────────

def guarded_run(run_id: str, body_of) -> dict:
    """``body_of(run, jobs)`` inside the run's Flow guard (404 ``run_not_found``); the post-commit jobs run after it."""
    return run_service._guarded_run(run_id, body_of)


def missing_policies(ctx, entries: dict, extra: frozenset) -> list[dict]:
    return run_service._missing(ctx, entries, extra=extra)


def ownership_error(flow_id: str, own) -> GraphApiError:
    return run_service._ownership_error(flow_id, own)


def gate_undecided(flow_id: str, revision_no: int, missing: list[dict]) -> GraphApiError:
    return gate_service._gate_undecided(flow_id, revision_no, missing)


def run_payload(run_id: str) -> Optional[dict]:
    return run_service.run_payload(run_id, with_nodes=True)


def gate_rule_decided(run_id: str) -> bool:
    """Whether the run's own snapshot holds a decided, registered ``gate_node`` rule (else its decision is refused:
    ``run_policy_undecided``, or 500 for an unregistered entry)."""
    entry = run_store.policy_entries(run_id).get(GATE_POLICY) or {}
    value = entry.get("value")
    if not value or value == "undecided":
        return False
    return implementation(GATE_POLICY, str(value), str(entry.get("impl_version"))) is not None


def node_types(flow_id: str, revision_no: int) -> dict[str, str]:
    """``{node key: node type}`` of the run's revision."""
    record = definition_store.revision(flow_id, int(revision_no)) or {"nodes": []}
    return {n["node_key"]: n.get("node_type") for n in record["nodes"]}


# ── the operation job store ──────────────────────────────────────────────────

def insert_gate_publish(project_id: str, req: dict, *, group_id: str, target_key: str, payload: dict,
                        requested_by: str) -> dict:
    from modules.flow_gate.services.git import job_store
    return job_store.insert_gate_publish(project_id, req, group_id=group_id, target_key=target_key, payload=payload,
                                         requested_by=requested_by)


def notify_job(job: Optional[dict]) -> None:
    from modules.flow_gate.services.git import job_store
    job_store.notify_created(job)


def get_job(job_id: Optional[str]) -> Optional[dict]:
    if not job_id:
        return None
    from modules.flow_gate.services.git import job_store
    return job_store.get_job(job_id)


def cancel_unstarted_job(job_id: str) -> bool:
    from modules.flow_gate.services.git import job_store
    return job_store.cancel_unstarted_gate_publish(job_id, job_store.GATE_CANCEL_NO_EFFECT)


# ── the local Git facts of a confirm's checks (D0007 §7.1) ───────────────────

def _rev(root: Path, ref: str) -> Optional[str]:
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd=root)
    sha = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    return sha or None


def git_slot(project_id: Optional[str], group_id: str) -> dict:
    """The group's Git slot as a confirm needs it: ``{"ok": False, "reason": …}`` when there is none, else
    ``{"ok": True, "group_branch", "group_head", "work_base", "work_base_sha", "origin"}``. ``work_base`` is the group's
    stored work base branch, else the project's base branch (``resolve_group_work_base_ref``). ``origin`` says whether
    the checkout has an ``origin`` remote; its URL is never read into the result (it may hold credentials)."""
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git.group_work_base import resolve_group_work_base_ref

    if not project_id:
        return {"ok": False, "reason": "no_project"}
    cfg = gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": False, "reason": "git_disabled"}
    state = gs.db_git.get_state(group_id)
    branch = ((state or {}).get("branch") or "").strip()
    if not state or not branch or not int(state.get("worktree_registered") or 0):
        return {"ok": False, "reason": "group_slot_missing"}
    root = gs._base_root_of(project_id)
    if root is None or not (Path(root) / ".git").exists():
        return {"ok": False, "reason": "checkout_unavailable"}
    head = _rev(Path(root), f"refs/heads/{branch}")
    if head is None:
        return {"ok": False, "reason": "group_branch_missing"}
    work_base = resolve_group_work_base_ref(project_id, group_id, config=cfg)
    base_sha = _rev(Path(root), f"refs/heads/{work_base}") if work_base else None
    origin = gs._run_git(["remote", "get-url", "origin"], cwd=Path(root)).returncode == 0
    return {"ok": True, "group_branch": branch, "group_head": head, "work_base": work_base,
            "work_base_sha": base_sha, "origin": bool(origin)}
