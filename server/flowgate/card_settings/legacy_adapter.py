"""Reuse boundary of the card settings (flowgate.v02.0053 T#2, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never
changed: the Graph API envelope (user JWT, RBAC, error shape), the Flow load / disposal check and the
Flow guard, the active run's node states, the AI repeat count choices (the same set the AI invoke
dialogs and the engine use), the AI connection and AI Team rows and the project's registered document
types. The review stage (``review_stage``) also takes from here: the Flow's engine ownership, which
node an AI run or a result document belongs to, the project default reviewer, the v0.1 document review
loop (started as the AI invoke route starts it, its review hops told the card's review basis, and its
state) and the v0.1 approval as the unmanned chain makes it, followed by the Graph contact a person's
approval reaches, and whether the AI run of a node still holds the Flow's group AI lease (flowgate.v02.0056
T#1). The source tools' step type judgement takes the open run node a token was mapped to, and the type a node's run
writes and binds when its card changes nothing (``graph_model.run_doc_type``, flowgate.v02.0083 T#2).
flowgate.v02.0056 T#3: an AI run's node result is the one registered with that run's own tokens
(``node_of_ai_run``), so the review stage never takes a result someone else registered as the run's.

flowgate.v02.0056 T#2 (the instruction stage of a card run): which open node in its instruction stage an AI
run worked for, the card instruction documents that node's tokens registered, and the Graph's own two steps
that follow the stage — the result stage's start (``node_dispatch.start_result_stage``) and the stage's
failure record (``node_dispatch.instruction_stage_failed``, the AI start failure path).

flowgate.v02.0061 T#2: after the review stage followed a node's AI run, the Graph run is judged again
(``run_service.settle_if_needed``, as before a run read).

flowgate.v02.0063 (the card's question wait, ``question_wait``): an ended AI run as its durable row tells it, the node
whose result writer ended with no result, the card's node row by its key, whether the Flow's AI is busy (the group
lease or a Graph execution), the Graph's retry of a result writer and the wake of an idle run.

flowgate.v02.0083 WP-4 (a person's work plan reviewed by AI): the result of a node in review is also a person's work plan
card whose card reviews it (``node_of_result``: an AI node, or a person's plan node with a review count — so a passed
review of it is approved as an AI card's is), and the person's result stage reads the Flow's active run and a node run of
it, the node's active attempt and the node an attempt is of, the run's issuer, whether that run may take the stage
(``wake_graph_run``'s conditions: not stopped, no Time Machine operation unfinished), whether a verdict already answers a
document's current submission (and whether it passed it), and the span of an explicit review request
(``graph_execution_hooks.explicit_request``, Q439). Its steps run under the Stop gate's shared hold (``person_hold``: the
run-end stage's ``attempt_hold`` taking an idle run too, those conditions judged again under the hold).

flowgate.v02.0098 T#1 (D0004 §3.1 / §3.2): the check card's merge target branch takes the Git merge target judgement
(``merge_target.judge_check_target``: one exact name, read-only, never cached) for a save, and the cached candidate
view (``merge_target.check_target_view``) and its invalidation for the settings response. Both are scoped by the
authenticated group alone: its ``group_id`` and its own ``project_id``.

flowgate.v02.0105 T#2 (NR flowgate.v02.0101.0003 §3.9 B-1 / B-3 / B-4, Q #511 o3 — 0083 T#4 개정): an AI card's automatic
approval is made under the server's automatic approval authority (``AUTOMATION_APPROVE_PERMISSIONS``: ``document.approve``
alone), not the run issuer's permissions; the issuer stays the record's actor (a real user) and an empty issuer approves
nothing. A person's work plan result keeps the issuer's authority (0083 WP-4). Why an automatic approval was not made
(``record_approval_wait``) and that the system's authority made one (``record_approval_authority``) are added as run status
events with the status unchanged, told by their cause (as ``fencing.record``); existing records are never changed. The
reconciliation of a lost run-end stage reads the node's result AI run (``result_ai_run``), its card's stored review count
as stored (``repository.stored``) and the result's review loops (``review_loops``).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from modules.flow_gate.db import ai_groups as db_ai_groups
from modules.flow_gate.db import ai_invoke_document_review_loops as db_review_loops
from modules.flow_gate.db import ai_providers as db_ai_providers
from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.api import envelope, v01_contact
from modules.flow_gate.graph_workflow.domain.graph_model import run_doc_type
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.projection.run_documents import registered_documents
from modules.flow_gate.graph_workflow.services import canvas_service, card_settings_ports, graph_service, node_dispatch
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.services.git import merge_target as git_merge_target
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.settings import ai_execution_policy_service

__all__ = ["GraphApiError", "GitServiceError", "judge_merge_target", "merge_target_view", "invalidate_merge_targets",
           "MERGE_TARGET_UNFIT", "MERGE_TARGET_GIT_UNAVAILABLE", "with_flow_guard", "registered_doc_types", "authenticate", "authorize", "invalid",
           "locale_of", "run", "load_flow", "require_writable", "repeat_choices", "ai_exists", "team_exists",
           "node_running", "graph_owned", "project_of", "node_of_ai_run", "node_of_result", "open_node_of_token",
           "run_doc_type",
           "node_ai_run_active", "instruction_node_of_ai_run", "registered_instructions", "start_result_stage",
           "instruction_stage_failed", "attempt_of_ai_run", "attempt_owns", "attempt_hold", "active_run_id",
           "invalidated_documents", "ai_run_record", "no_result_node_of_ai_run", "node_of_key", "nodes_of_ai_run",
           "flow_ai_busy", "retry_result_stage", "wake_graph_run", "open_questions_of_ai_run", "open_ai_questions",
           "askers_answered_by", "ask_responder",
           "default_reviewer", "last_rejection", "start_review_loop", "review_loop_row", "review_loop_active",
           "approve", "settle_graph_run",
           "person_plan_reviewed", "active_run", "node_run", "active_attempt_id", "run_issuer", "person_stage_open",
           "person_hold", "attempt_node", "submission_judged", "submission_passed", "explicit_request",
           "AUTOMATION", "AUTOMATION_APPROVE_PERMISSIONS", "record_approval_wait", "record_approval_authority",
           "approval_waits", "approval_wait_reason", "node_runs", "result_ai_run", "stage_ai_run", "review_loops"]

_log = logging.getLogger(__name__)

OPEN_NODE_STATUS = "open"
REVIEW_NODE_STATUS = "review"
STOPPED_LOOP_STAGE = "stopped"
LOCALE = "ko"
APPROVE_PERMISSION = "document.approve"
# The TR2 approval attempt key of a card approval (the unmanned chain's own is "auto:").
TR2_REQUEST_KEY_PREFIX = "card:"
TR2_REQUEST_KEY_LEN = 48
# flowgate.v02.0105 T#2 (B-4): an AI card's automatic approval authority — approval alone, no other permission.
AUTOMATION = "automation"
AUTOMATION_APPROVE_PERMISSIONS = frozenset({APPROVE_PERMISSION})
SYSTEM_AUTHORITY = "system"
# flowgate.v02.0105 T#2 (B-1): why an automatic approval was not made (the run event's reason; ``review_not_passed:`` and
# ``run_not_clean:`` take the loop's stop reason / the AI run's stop code or end reason after the colon).
ISSUER_LACKS_APPROVE = "issuer_lacks_approve"           # a person's work plan result only (0083 WP-4)
ISSUER_MISSING = "issuer_missing"
APPROVAL_REFUSED = "approval_refused"
REVIEW_NOT_STARTED = "review_not_started"
REVIEW_NOT_PASSED = "review_not_passed:"
RUN_NOT_CLEAN = "run_not_clean:"
REVIEW_RESULT_UNKNOWN = "review_result_unknown"
# graph_run_events admits only the run / node status events (migration 144): a run status event, the status unchanged.
APPROVAL_WAIT_CAUSE = "auto_approval_skipped"
APPROVAL_AUTHORITY_CAUSE = "auto_approval_authority"


def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict, required: str) -> None:
    envelope.authorize(user_id, group, required)


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def locale_of(request) -> str:
    """The request's X-Locale as the Graph envelope normalizes it (ko / ja / en, else ko)."""
    return envelope.locale_of(request)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def require_writable(flow_id: str) -> None:
    graph_service.require_writable(flow_id)


def repeat_choices() -> tuple[int, ...]:
    """-1 (until it passes / restarts until done), 0 (none), 1..the configured max."""
    return ai_execution_policy_service.repeat_count_choices(allow_zero=True)


def ai_exists(provider_id: str) -> bool:
    """An AI connection row with this id (ids are global, whoever owns the row)."""
    return provider_id in db_ai_providers.all_provider_ids()


def team_exists(ai_group_id: str) -> bool:
    return db_ai_groups.get(ai_group_id) is not None


# ── the check card's merge target (flowgate.v02.0098 T#1) ────────────────────

MERGE_TARGET_UNFIT = git_merge_target.CHECK_TARGET_UNFIT
MERGE_TARGET_GIT_UNAVAILABLE = git_merge_target.CHECK_GIT_UNAVAILABLE


def judge_merge_target(group: dict, branch: str) -> dict:
    """Judge ``branch`` as the group's check card merge target now (no cache); ``GitServiceError`` when it is not.
    A group whose worktree is not provisioned yet is judged as the candidate list judges it (T0012)."""
    return git_merge_target.judge_check_target(group.get("project_id"), group["group_id"], branch,
                                               require_slot=False)


def merge_target_view(group: dict, selected: Optional[str], refresh: bool = False) -> dict:
    """The candidate list, default target and their states for the settings response (never raises for Git)."""
    return git_merge_target.check_target_view(group.get("project_id"), group["group_id"], selected, refresh=refresh)


def invalidate_merge_targets(group: dict) -> None:
    git_merge_target.invalidate_check_targets(group.get("project_id"), group["group_id"])


def node_running(flow_id: str, node_key: str) -> bool:
    """The node is open in the Flow's active run (its start already took the settings), or in review
    while the card's AI review works on its result (that review follows them)."""
    run_row: Optional[dict] = run_store.active_run(flow_id)
    if run_row is None:
        return False
    for r in run_store.node_runs(run_row["run_id"]):
        if r["node_key"] != node_key:
            continue
        if r["status"] == OPEN_NODE_STATUS:
            return True
        if r["status"] == REVIEW_NODE_STATUS and r.get("result_doc_id"):
            return review_loop_active(r["result_doc_id"])
    return False


# ── the review stage ─────────────────────────────────────────────────────────

def graph_owned(flow_id: str) -> bool:
    return read_ownership(flow_id).state == "graph"


def project_of(flow_id: str) -> Optional[str]:
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


def _module_of(flow_id: str) -> Optional[str]:
    parts = flow_id.split(".")
    return parts[1] if len(parts) > 2 and parts[1] != "none" else None


def _ai_node_in_review(run_row: dict, node_key: str, person_reviewed: bool = False) -> Optional[tuple[dict, str]]:
    """(the run revision's node row, its result) when the AI node is in review with a result. ``person_reviewed``
    (0083 WP-4): a person's work plan node whose card reviews its result too (``person_plan_reviewed``)."""
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == node_key), None)
    if row is None or row["status"] != REVIEW_NODE_STATUS or not row.get("result_doc_id"):
        return None
    record = definition_store.revision(run_row["group_id"], int(run_row["revision_no"])) or {}
    node = next((n for n in record.get("nodes") or [] if n["node_key"] == node_key), None)
    if node is None:
        return None
    if not card_settings_ports.uses_ai(run_row["group_id"], node) and not (
            person_reviewed and person_plan_reviewed(run_row["group_id"], node)):
        return None
    return node, row["result_doc_id"]


def node_of_ai_run(flow_id: str, ai_run_id: str) -> Optional[tuple[dict, str]]:
    """The AI node of the Flow's active run that this AI run wrote the result of (its tokens carry the
    run id and were mapped to the node), when that node is in review: (node row, result doc id).

    flowgate.v02.0056 T#3: the result must be the run's own — registered with one of those tokens, as their
    ``token_consumed`` records show (the evidence the run reconciliation binds from). A result someone else
    registered while the run went on (a person's, with the node's dispatch token) is not this run's to review
    or approve: None, and it waits for the card approval."""
    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id:
        return None
    mapped = get_store()._fetch_all(
        "SELECT m.node_key, m.token_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND t.ai_run_id = ?", [run_row["run_id"], ai_run_id])
    tokens: dict[str, list[str]] = {}
    for r in mapped or []:
        tokens.setdefault(r["node_key"], []).append(r["token_id"])
    for key, token_ids in tokens.items():
        found = _ai_node_in_review(run_row, key)
        if found is not None and _registered_with(flow_id, found[1], token_ids):
            return found
    return None


def _registered_with(flow_id: str, doc_id: str, token_ids: list[str]) -> bool:
    """``doc_id`` was registered with one of ``token_ids`` (their ``token_consumed`` records)."""
    return any(d["doc_id"] == doc_id for d in registered_documents(flow_id, token_ids))


def _lease_held(lease: Optional[dict]) -> bool:
    """An unexpired lease row (read only: the run reads that ask may sit inside a Flow guard, so the
    expired row is left for the lease's own recovery instead of being reclaimed here)."""
    if not lease or not lease.get("run_id") or not lease.get("expires_at"):
        return False
    try:
        expires = datetime.fromisoformat(str(lease["expires_at"]))
    except ValueError:
        return False
    return (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc)


def node_ai_run_active(flow_id: str, node_key: str) -> bool:
    """Requested/running Graph executions keep the card working too: they use attempt leases,
    not the legacy group lease. Retain the group-lease check for ordinary document AI runs."""
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return False
    attempt_id = run_store.active_attempt_id(run_row["run_id"], node_key)
    if attempt_id:
        from modules.flow_gate.services import graph_execution_hooks
        counts = graph_execution_hooks.call("execution_wire", run_row["run_id"], flow_id, attempt_id) or {}
        if counts.get("running_execution_count", 0) or counts.get("requested_execution_count", 0):
            return True
    lease = db_leases.get(flow_id)
    if not _lease_held(lease):
        return False
    mapped = get_store()._fetch_one(
        "SELECT m.node_key FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND m.node_key = ? AND t.ai_run_id = ?",
        [run_row["run_id"], node_key, lease["run_id"]])
    return mapped is not None


def node_of_result(flow_id: str, doc_id: Optional[str]) -> Optional[dict]:
    """The node of the Flow's active run whose result in review is ``doc_id`` (its node row): an AI node, or — 0083
    WP-4 — a person's work plan node whose card reviews its result (``person_plan_reviewed``), so the review stage
    approves a passed review of it and the node moves on as an AI card's does."""
    run_row = run_store.active_run(flow_id)
    row = run_store.node_run_by_result(run_row["run_id"], doc_id) if run_row and doc_id else None
    found = _ai_node_in_review(run_row, row["node_key"], person_reviewed=True) if row else None
    return found[0] if found else None


def person_plan_reviewed(flow_id: str, node: dict) -> bool:
    """0083 WP-4: ``node`` is a work plan card a person writes whose card reviews its result (review count not 0)."""
    from flowgate.card_settings import fields, repository  # noqa: PLC0415 — the card's own settings

    if not node.get("node_key") or not repository.available() or card_settings_ports.uses_ai(flow_id, node):
        return False
    if fields.card_kind(node.get("node_type"), node.get("doc_type")) != fields.PLAN:
        return False
    return fields.after_result(fields.PLAN, repository.values(flow_id, node["node_key"])) == fields.REVIEW


def open_node_of_token(token_rec: dict) -> Optional[tuple[str, dict]]:
    """(Flow, the run revision's node row) of the node a token was mapped to (``graph_run_token_map``:
    the node's dispatch token, and every token its AI start or a retry of it minted), while that run is
    its own Flow's active run and the node is still open; else None. The node row carries the node run's
    card run stage as ``run_stage`` (flowgate.v02.0056 T#2)."""
    token_id, flow_id = token_rec.get("token_id"), token_rec.get("group_id")
    mapped = run_store.node_run_by_token(token_id) if token_id and flow_id else None
    if mapped is None:
        return None
    run_row = run_store.active_run(flow_id)
    if run_row is None or run_row["run_id"] != mapped["run_id"]:
        return None
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == mapped["node_key"]), None)
    if row is None or row["status"] != OPEN_NODE_STATUS:
        return None
    record = definition_store.revision(flow_id, int(run_row["revision_no"])) or {}
    node = next((n for n in record.get("nodes") or [] if n["node_key"] == mapped["node_key"]), None)
    return (flow_id, {**node, "run_stage": row.get("stage")}) if node is not None else None


# ── the instruction stage (flowgate.v02.0056 T#2) ────────────────────────────

def instruction_node_of_ai_run(flow_id: str, ai_run_id: str) -> Optional[tuple[dict, dict]]:
    """(the run revision's node row, its node run) of the AI node of the Flow's active run that this AI run
    worked for (its tokens carry the run id and were mapped to the node) while that node is open in its
    card's instruction stage; else None."""
    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id:
        return None
    mapped = get_store()._fetch_all(
        "SELECT m.node_key FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND t.ai_run_id = ?", [run_row["run_id"], ai_run_id])
    record = definition_store.revision(flow_id, int(run_row["revision_no"])) or {}
    for key in dict.fromkeys(r["node_key"] for r in mapped or []):
        row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == key), None)
        if row is None or row["status"] != OPEN_NODE_STATUS or row.get("stage") != node_dispatch.INSTRUCTION_STAGE:
            continue
        node = next((n for n in record.get("nodes") or [] if n["node_key"] == key), None)
        if node is not None and card_settings_ports.uses_ai(flow_id, node):
            return node, row
    return None


def registered_instructions(flow_id: str, run_id: str, node_key: str, doc_type: str) -> list[dict]:
    """The live documents of ``doc_type`` registered with the node's tokens in the run, the latest first."""
    docs = registered_documents(flow_id, run_store.token_ids(run_id, node_key))   # in registration order
    wanted = str(doc_type or "").strip().upper()
    return [d for d in reversed(docs) if str(d.get("type_code") or "").strip().upper() == wanted
            and d.get("status") != "cancelled" and not d.get("superseded_by")]


def start_result_stage(flow_id: str, run_id: str, node_key: str, instruction_doc_id: Optional[str],
                       issued_to: str, attempt_id: Optional[str] = None, defer: Optional[list] = None,
                       idle_ok: bool = False) -> None:
    """``idle_ok`` (flowgate.v02.0105 T#2 B-3): an idle run takes it too — only under ``person_hold``."""
    node_dispatch.start_result_stage(flow_id, run_id, node_key, instruction_doc_id, issued_to, project_of(flow_id),
                                     attempt_id=attempt_id, defer=defer, idle_ok=idle_ok)


def instruction_stage_failed(flow_id: str, run_id: str, node_key: str, attempt_id: Optional[str] = None) -> None:
    node_dispatch.instruction_stage_failed(flow_id, run_id, node_key, project_of(flow_id), attempt_id=attempt_id)


def attempt_of_ai_run(run: dict) -> Optional[str]:
    """0061 T#7: the attempt the AI run's Graph execution was made for (None: no Graph execution)."""
    from modules.flow_gate.services import graph_execution_hooks  # noqa: PLC0415
    return graph_execution_hooks.call("execution_attempt", run.get("graph_exec_id"))


def attempt_owns(run_id: str, node_key: str, attempt_id: Optional[str], operation: str) -> bool:
    """0061 T#7: that attempt is still the node's active one and may write (a refusal is recorded)."""
    return node_dispatch.attempt_owns(run_id, node_key, attempt_id, operation)


def attempt_hold(run_id: str, node_key: str, attempt_id: Optional[str], operation: str):
    """0061 T#7: ``attempt_owns`` judged under the Stop gate's shared hold, kept for the ``with`` body (an automatic
    approval and its commits). Yields whether the body may go on."""
    return node_dispatch.attempt_hold(run_id, node_key, attempt_id, operation)


def active_run_id(flow_id: str) -> Optional[str]:
    run_row = run_store.active_run(flow_id)
    return run_row["run_id"] if run_row else None


def invalidated_documents(flow_id: str, node_key: str) -> set[str]:
    """0061 T#7 (0017-TR question 440: kept as history, never the new attempt's evidence): the documents registered
    (new or edit) with the tokens of the node's invalidated attempts in the Flow's active run — an attempt a Stop, a
    lost lease or a failed worktree ended — and with none of the node's live tokens. A node opened again never takes
    them as its instruction, its approval or its result."""
    if not node_key or not get_store().table_exists("graph_node_attempts"):
        return set()
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return set()
    tokens = run_store.invalidated_token_ids(run_row["run_id"], node_key)
    if not tokens:
        return set()
    old = {d["doc_id"] for d in registered_documents(flow_id, tokens)}
    live = run_store.token_ids(run_row["run_id"], node_key)
    return old - {d["doc_id"] for d in registered_documents(flow_id, live)} if live and old else old


# ── the question wait (flowgate.v02.0063) ────────────────────────────────────

def ai_run_record(ai_run_id: str) -> Optional[dict]:
    """An ended AI run as its durable row tells it, with the Graph execution it ran for (``graph_exec_id``) and, for a
    review loop, its loop row (``document_review_loop``); None when no row was written."""
    from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: PLC0415

    row = db_runs.get(ai_run_id) if ai_run_id else None
    if not row:
        return None
    found = get_store()._fetch_one(
        "SELECT exec_id FROM graph_attempt_runs WHERE ai_run_id = ? ORDER BY created_at DESC LIMIT 1",
        [ai_run_id]) if get_store().table_exists("graph_attempt_runs") else None
    return {**row, "run_id": ai_run_id, "graph_exec_id": (found or {}).get("exec_id"),
            "document_review_loop": review_loop_row(ai_run_id)}


def no_result_node_of_ai_run(flow_id: str, ai_run_id: str) -> Optional[tuple[dict, dict]]:
    """(the run revision's node row, its node run) of the AI node of the Flow's active run whose active attempt's
    latest execution is ``ai_run_id``'s result write and ended with no result (``write_failure`` ``no_result``)."""
    from modules.flow_gate.services import graph_execution_hooks  # noqa: PLC0415

    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id or not get_store().table_exists("graph_attempt_runs"):
        return None
    found = get_store()._fetch_one(
        "SELECT a.node_key, a.attempt_id FROM graph_attempt_runs e JOIN graph_node_attempts a "
        "ON a.attempt_id = e.attempt_id WHERE e.run_id = ? AND e.ai_run_id = ? AND e.kind = 'write' "
        "AND a.status = 'active'", [run_row["run_id"], ai_run_id])
    if found is None:
        return None
    latest = get_store()._fetch_one(
        "SELECT ai_run_id FROM graph_attempt_runs WHERE attempt_id = ? ORDER BY created_at DESC, exec_id DESC LIMIT 1",
        [found["attempt_id"]])
    if (latest or {}).get("ai_run_id") != ai_run_id:
        return None
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == found["node_key"]), None)
    if row is None or graph_execution_hooks.call("write_failure", row) != "no_result":
        return None
    node = node_of_key(flow_id, found["node_key"])
    return (node, row) if node is not None else None


def node_of_key(flow_id: str, node_key: str) -> Optional[dict]:
    """The node row of the Flow's active run revision with this key."""
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return None
    record = definition_store.revision(flow_id, int(run_row["revision_no"])) or {}
    return next((n for n in record.get("nodes") or [] if n["node_key"] == node_key), None)


def nodes_of_ai_run(flow_id: str, ai_run_id: str) -> list[dict]:
    """The node rows of the Flow's active run revision this AI run worked for (its tokens were mapped to them)."""
    run_row = run_store.active_run(flow_id)
    if run_row is None or not ai_run_id:
        return []
    keys = sorted(run_store.nodes_of_ai_runs(run_row["run_id"], [ai_run_id]))
    return [n for n in (node_of_key(flow_id, k) for k in keys) if n is not None]


def open_questions_of_ai_run(flow_id: str, ai_run_id: Optional[str]) -> list[dict]:
    """The questions ``ai_run_id`` asked in the Flow still unanswered, oldest first (``anchor_doc_id``: the document
    they hang on). Raises on a failed read."""
    from modules.flow_gate.services.ai_invoke.finalize import open_questions_raised_by_run  # noqa: PLC0415

    return open_questions_raised_by_run(flow_id, ai_run_id)


def open_ai_questions(flow_id: str) -> list[dict]:
    """Every unanswered AI-asked question of the Flow, oldest first (``anchor_doc_id`` as above). Raises on a failed
    read."""
    from modules.flow_gate.db import question_items as db_question_items  # noqa: PLC0415
    from modules.flow_gate.db import questions as db_questions  # noqa: PLC0415

    found: list[dict] = []
    for doc_id in db_questions.list_open_doc_ids_by_group(flow_id) or []:
        container = db_questions.get_container_by_doc(doc_id)
        for row in (db_question_items.list_unanswered(container["id"]) if container else None) or []:
            if (row.get("asker_kind") or "") == "ai" and row.get("asker_ai_run_id"):
                found.append({**row, "anchor_doc_id": doc_id})
    found.sort(key=lambda row: int(row.get("id") or 0))
    return found


def askers_answered_by(responder_run_id: str) -> set[str]:
    """The AI runs that asked the questions ``responder_run_id`` was the responder of."""
    from modules.flow_gate.db import question_items as db_question_items  # noqa: PLC0415

    return {item["asker_ai_run_id"] for item in db_question_items.list_by_responder_run(responder_run_id) or []
            if item.get("asker_ai_run_id")}


def ask_responder(anchor_doc_id: str, item_id: int, issued_to: str, provider_id: Optional[str],
                  provider_source: str) -> Optional[dict]:
    """Start the responder AI run of one question, as the Q&A panel's [AI 답변 요청] does
    (``q_answer_invoke_service.dispatch_answer_run``; a refusal is recorded on the item and raised)."""
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.services.ai_node_starter import api_base_url  # noqa: PLC0415
    from modules.flow_gate.services import q_answer_invoke_service  # noqa: PLC0415

    doc = db_documents.get_by_id(anchor_doc_id)
    if doc is None:
        return None
    item = q_answer_invoke_service.resolve_item(anchor_doc_id, item_id)
    return q_answer_invoke_service.dispatch_answer_run(
        doc={**doc, "doc_id": anchor_doc_id}, item=item, issued_to=issued_to, api_base_url=api_base_url(),
        provider_id=provider_id, provider_source=provider_source, actor_user_id=issued_to)


def independent_question_responder(project_id: Optional[str], asker_provider_id: Optional[str],
                                  candidates: list[tuple[Optional[str], str]]) -> tuple[Optional[str], str]:
    """Use the common enabled-provider and actual-asker independence policy."""
    from modules.flow_gate.services.ai_invoke import review as v01_review  # noqa: PLC0415

    return v01_review.independent_question_responder(project_id, asker_provider_id, candidates)


def mark_question_responder_failed(item_id: int, issued_to: Optional[str]) -> bool:
    """Leave an automatically unassignable question open for a human in the existing Q&A panel."""
    from modules.flow_gate.services import q_service  # noqa: PLC0415

    return q_service.mark_responder_failed(
        item_id, "no_independent_responder",
        "No independent enabled responder could be established; awaiting a human answer.",
        actor_user_id=issued_to,
    )


def flow_ai_busy(flow_id: str) -> bool:
    """An AI run holds the Flow's group lease, or a Graph execution of the Flow is requested / running: a new ordinary
    AI run (a question's responder) would be refused (``run_in_progress``) now."""
    if _lease_held(db_leases.get(flow_id)):
        return True
    if not get_store().table_exists("graph_attempt_runs"):
        return False
    return get_store()._fetch_one(
        "SELECT 1 AS present FROM graph_attempt_runs e JOIN graph_runs r ON r.run_id = e.run_id "
        "WHERE r.group_id = ? AND e.status IN ('requested', 'running') LIMIT 1", [flow_id]) is not None


def retry_result_stage(flow_id: str, run_id: str, node_key: str, issued_to: str,
                       attempt_id: Optional[str] = None) -> bool:
    return node_dispatch.retry_result_stage(flow_id, run_id, node_key, issued_to, project_of(flow_id),
                                            attempt_id=attempt_id)


def wake_graph_run(flow_id: str) -> bool:
    """The Flow's run may take up a card's step again: it is running, or idle and woken to running here (cause
    ``work_resumed``, reason ``question_answered``). False for a paused, stopping or stopped run and while a Time
    Machine operation is unfinished: the card waits for the next command or a person."""
    from flowgate.graph_time_machine.operation import busy  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.services import run_apply  # noqa: PLC0415

    def body() -> bool:
        run_row = run_store.active_run(flow_id)
        if run_row is None:
            return False
        if run_row["status"] == run_store.RUNNING:
            return True
        if run_row["status"] != run_store.IDLE or run_row.get("idle_reason") == "stopped" or busy(flow_id):
            return False
        ctx = run_apply.load_context(run_row, project_of(flow_id), None)
        if run_store.set_run_status(run_row["run_id"], run_store.IDLE, run_store.RUNNING) != 1:
            return False
        ctx.run["idle_reason"] = None
        run_apply.emit_run_status(ctx, run_store.IDLE, run_store.RUNNING, "work_resumed", "question_answered")
        return True

    return bool(with_flow_guard(flow_id, body))


def default_reviewer(project_id: Optional[str]) -> Optional[str]:
    """The project default reviewer: the fallback of the v0.1 review gate (first enabled AI)."""
    from modules.flow_gate.services.ai_invoke import review as v01_review  # noqa: PLC0415 — AI stack on use

    return v01_review.resolve_reviewer(None, None, project_id)


def last_rejection(doc_id: str) -> Optional[str]:
    """The reason of the document's last rejection (its rejection history, else the latest reason
    column), or None when it was never rejected."""
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history  # noqa: PLC0415

    doc = db_documents.get_by_id(doc_id) or {}
    history = parse_rejection_history(doc.get("rejection_history"))
    reason = (history[-1].get("reason") if history else None) or doc.get("rejection_reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def start_review_loop(project_id: Optional[str], flow_id: str, doc_id: str, issued_to: str,
                      loop: dict, basis_note: Callable[[Optional[str]], str],
                      node_key: Optional[str] = None, graph_exec_id: Optional[str] = None,
                      attempt_id: Optional[str] = None) -> Optional[dict]:
    """Start the v0.1 document review loop on ``doc_id`` as the AI invoke route starts it (single
    run, ``action_scope`` review, its stage-aware issuer: a review token, an edit token for a
    rework hop). A refusal is logged and the result left to a person (returns None).

    Every review hop's mention -- the first one, a retry's reissue and the review after a rework all
    come from this issuer -- opens with ``basis_note(the document's last rejection)``: the review
    basis the card chose, said with the last rejection as it is when the hop is issued. The engine
    adds its own one-line criteria name only when it switches stages, never to the first hop.

    flowgate.v02.0056 T#3: every hop, review and rework, also carries the memos of the card ``node_key`` turned
    on for AI reading (``card_memo_hooks``), read when the hop is issued.

    flowgate.v02.0061 T#7: ``attempt_id`` — the attempt of the node's run whose end starts the loop — must still be
    the one the document's node is worked on by (``execution.request_loop`` refuses and records otherwise)."""
    from modules.flow_gate.services import ai_invoke_service, card_memo_hooks, invoke_mention_service  # noqa: PLC0415
    from modules.flow_gate.services import workflow_decision_service  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.services.ai_node_starter import api_base_url  # noqa: PLC0415

    def _issue(ai_run_id: Optional[str] = None) -> dict:
        if getattr(_issue, "loop_stage", None) == ai_invoke_service.REWORK_HOP_KIND:
            issued = invoke_mention_service.issue_rework_request(
                doc_id=doc_id, issued_to=issued_to, api_base_url=api_base_url(), locale=LOCALE, ai_run_id=ai_run_id)
            from modules.flow_gate.services import graph_execution_hooks
            live = ai_invoke_service.get_run_record(ai_run_id) if ai_run_id else None
            graph_execution_hooks.call("map_token", (live or {}).get("graph_exec_id") or graph_exec_id, issued["token_id"])
            return {**issued, "mention": card_memo_hooks.prepend_memo_section(
                issued.get("mention") or "", flow_id, node_key=node_key)}
        issued = workflow_decision_service.request_review(
            doc_id=doc_id, issued_to=issued_to, api_base_url=api_base_url(), ref_doc_ids=None, locale=LOCALE,
            ai_run_id=ai_run_id)
        from modules.flow_gate.services import graph_execution_hooks
        live = ai_invoke_service.get_run_record(ai_run_id) if ai_run_id else None
        graph_execution_hooks.call("map_token", (live or {}).get("graph_exec_id") or graph_exec_id, issued["token_id"])
        mention = issued.get("mention") or ""
        if mention:
            mention = card_memo_hooks.prepend_memo_section(mention, flow_id, node_key=node_key)
            mention = invoke_mention_service.prepend_messages_section(
                mention, [basis_note(last_rejection(doc_id))], LOCALE)
        return {"raw_token": issued["token"], "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"], "mention": mention}

    from flowgate.graph_run import execution
    try:
        graph_exec_id = execution.request_loop(project_id, flow_id, doc_id, issued_to, loop,
                                                basis_note(last_rejection(doc_id)), node_key, graph_exec_id,
                                                attempt_id=attempt_id)
        return ai_invoke_service.start_run(
            graph_exec_id=graph_exec_id,
            project_id=project_id or "", module=_module_of(flow_id), group_id=flow_id, doc_ref=doc_id,
            action_scope="review", mode="single", continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale=None, issued_to=issued_to,
            api_base_url=api_base_url(), mention_builder=lambda _raw, _scratch: None, issue_builder=_issue,
            document_review_loop=dict(loop),
        )
    except Exception as exc:  # noqa: BLE001 -- the result stays in review for a person
        execution.start_failed(graph_exec_id, exc)
        _log.warning("card review loop start failed flow=%s doc=%s", flow_id, doc_id, exc_info=True)
        return None


def review_loop_row(ai_run_id: str) -> Optional[dict]:
    row = db_review_loops.get(ai_run_id)
    return dict(row) if row else None


def review_loop_active(doc_id: str) -> bool:
    """A document review loop is still working on ``doc_id`` (not stopped)."""
    if not get_store().table_exists("ai_invoke_document_review_loops"):
        return False
    return any(r.get("current_stage") != STOPPED_LOOP_STAGE for r in db_review_loops.list_by_document(doc_id) or [])


def _retryable_tr2(exc: Exception) -> bool:
    """A TR2 refusal the unmanned chain retries once with a new attempt key (``settle_completed_step``)."""
    from modules.flow_gate.documents import tr2_service  # noqa: PLC0415
    from modules.flow_gate.documents.tr2_errors import TR2_ERRORS  # noqa: PLC0415

    return (isinstance(exc, tr2_service.Tr2ValidationError) and exc.code != "tr2_in_progress"
            and exc.code in TR2_ERRORS and bool(TR2_ERRORS[exc.code].retryable))


def approve(flow_id: str, doc_id: str, actor_user_id: str, authority: Optional[str] = None, *,
            node_key: Optional[str] = None, attempt_id: Optional[str] = None) -> bool:
    """The v0.1 approval of ``doc_id`` as the unmanned chain makes it (``inbox_routes.settle_completed_step``),
    then the TR approval hook every approval entry point calls and the Graph contact a person's approval
    reaches. True when approved; any refusal is logged, its reason recorded (B-1, ``record_approval_wait``) and the
    result left to a person (False).

    - Authority: ``authority`` ``AUTOMATION`` (an AI card's result, flowgate.v02.0105 T#2 B-4, Q #511 o3 — 0083 T#4
      개정): the server's automatic approval authority (``AUTOMATION_APPROVE_PERMISSIONS``), never the issuer's
      permissions; an empty issuer approves nothing (``issuer_missing``) and the system's authority is recorded beside
      the approval. None (a person's work plan result, 0083 WP-4): the run issuer's own permissions, resolved as the
      approve button resolves them; without ``document.approve`` nothing is attempted (``issuer_lacks_approve``).
    - Actor and principal: the issuer — never the author of the result — as the Flow's server-owned completion context
      (``system_principal``, the one the unmanned approval passes), so the group mutation guard admits it as it admits
      that approval.
    - Revision: the document's revision as read here (``expected_revision``).
    - TR2: its own approval (precheck, source lock, apply, validation, commit) under an attempt key of
      the document and revision, retried once with a new key on a retryable TR2 refusal.
    """
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.db import users as db_users  # noqa: PLC0415
    from modules.flow_gate.services import tr_commit_service  # noqa: PLC0415
    from modules.flow_gate.services.mutation_policy import system_principal  # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import transition_document_review  # noqa: PLC0415
    from modules.flow_gate.workflow.routers.workflow import _get_user_permissions  # noqa: PLC0415

    automation = authority == AUTOMATION
    if automation:
        if not actor_user_id:
            _log.info("card approval left to a person flow=%s doc=%s: no run issuer", flow_id, doc_id)
            record_approval_wait(flow_id, node_key, doc_id, ISSUER_MISSING, attempt_id=attempt_id)
            return False
        permissions = set(AUTOMATION_APPROVE_PERMISSIONS)
    else:
        actor = db_users.get_by_id(actor_user_id) or {"user_id": actor_user_id, "is_admin": 0}
        permissions = _get_user_permissions(actor)
        if APPROVE_PERMISSION not in permissions:
            _log.info("card approval left to a person flow=%s doc=%s: the issuer lacks %s",
                      flow_id, doc_id, APPROVE_PERMISSION)
            record_approval_wait(flow_id, node_key, doc_id, ISSUER_LACKS_APPROVE, attempt_id=attempt_id)
            return False
    doc = db_documents.get_by_id(doc_id) or {}
    revision = int(doc.get("revision_no") or 0)
    tr2 = str(doc.get("type_code") or "").upper() == "TR2"
    result = None
    for attempt in range(2 if tr2 else 1):
        request_key = None
        if tr2:
            digest = hashlib.sha256(f"{doc_id}:{revision}:{attempt}".encode("utf-8")).hexdigest()
            request_key = TR2_REQUEST_KEY_PREFIX + digest[:TR2_REQUEST_KEY_LEN]
        try:
            result = transition_document_review(
                doc_id=doc_id, action="approve", actor_user_id=actor_user_id, user_permissions=permissions,
                locale=LOCALE, mutation_principal=system_principal(user_id=actor_user_id, group_id=flow_id),
                expected_revision=revision, request_key=request_key)
            break
        except Exception as exc:  # noqa: BLE001 — the result stays in review for a person
            if tr2 and attempt == 0 and _retryable_tr2(exc):
                continue
            _log.warning("card approval refused flow=%s doc=%s", flow_id, doc_id, exc_info=True)
            record_approval_wait(flow_id, node_key, doc_id, APPROVAL_REFUSED, attempt_id=attempt_id)
            return False
    if automation:
        record_approval_authority(flow_id, node_key, doc_id, actor_user_id, attempt_id=attempt_id)
    tr_commit_service.on_document_approved(doc_id, result)
    v01_contact.after_review_decided(flow_id, doc_id, "approve")
    return True


# ── why an automatic approval was not made (flowgate.v02.0105 T#2 B-1 / B-4) ──

def _run_event(flow_id: str, cause: str, reason: str, extra: dict) -> None:
    """A run status event of the Flow's active run with the status unchanged, told by ``cause`` (as ``fencing.record``):
    no node event, no SSE. Inside the caller's transaction when there is one, else in its own Flow guard. Added only —
    never raises, and a failed record changes nothing of the step it tells."""
    from modules.flow_gate.db.connection import in_transaction  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.services import run_events  # noqa: PLC0415

    def write() -> None:
        run_row = run_store.active_run(flow_id)
        if run_row is None:
            return
        seq = int(run_row["last_event_seq"]) + 1
        payload = {"flow_id": flow_id, "run_id": run_row["run_id"], "revision_no": run_row.get("revision_no"),
                   "event_seq": seq, "from": run_row["status"], "to": run_row["status"], "cause": cause,
                   "reason": reason, **extra}
        run_store.append_event(run_row["run_id"], seq, run_events.RUN_STATUS, payload, node_key=None,
                               from_status=run_row["status"], to_status=run_row["status"], cause=cause,
                               result_doc_id=None, canvas_state=None, actor_user_id=None, reason=reason)

    try:
        if in_transaction():
            write()
        else:
            with_flow_guard(flow_id, write)
    except Exception:  # noqa: BLE001
        _log.warning("card approval record not written flow=%s cause=%s reason=%s", flow_id, cause, reason,
                     exc_info=True)


def _node_key_of(flow_id: str, node_key: Optional[str], doc_id: Optional[str]) -> Optional[str]:
    """``node_key``, else the node of the Flow's active run whose result is ``doc_id``."""
    if node_key or not doc_id or not run_store.available():
        return node_key
    run_row = run_store.active_run(flow_id)
    row = run_store.node_run_by_result(run_row["run_id"], doc_id) if run_row else None
    return row["node_key"] if row else None


def record_approval_wait(flow_id: str, node_key: Optional[str], doc_id: Optional[str], reason: str, *,
                         attempt_id: Optional[str] = None, stage: str = "result") -> None:
    """B-1: why the automatic approval of the node's ``doc_id`` (its result, or its instruction: ``stage``) was not made,
    added to the Flow's active run. Nothing when the document is no node's of that run."""
    key = _node_key_of(flow_id, node_key, doc_id)
    if not key or not doc_id:
        return
    _run_event(flow_id, APPROVAL_WAIT_CAUSE, reason,
               {"node_key": key, "doc_id": doc_id, "attempt_id": attempt_id, "stage": stage})


def record_approval_authority(flow_id: str, node_key: Optional[str], doc_id: str, issuer: str, *,
                              attempt_id: Optional[str] = None, stage: str = "result") -> None:
    """B-4: the automatic approval of the node's ``doc_id`` was made under the system's authority, for ``issuer`` (the
    approval record's actor)."""
    key = _node_key_of(flow_id, node_key, doc_id)
    if not key:
        return
    _run_event(flow_id, APPROVAL_AUTHORITY_CAUSE, SYSTEM_AUTHORITY,
               {"node_key": key, "doc_id": doc_id, "attempt_id": attempt_id, "stage": stage,
                "auto_approval_authority": SYSTEM_AUTHORITY, "issuer": issuer})


def approval_waits(run_id: str, node_key: str, doc_id: Optional[str], attempt_id: Optional[str] = None) -> list[str]:
    """B-1 reasons recorded on the run for the node's ``doc_id``, oldest first — of ``attempt_id`` (a reason recorded for
    another attempt of the node is not this one's; one recorded with no attempt counts)."""
    from modules.flow_gate.graph_workflow.services import run_events  # noqa: PLC0415

    if not doc_id or not run_store.available():
        return []
    found = []
    for row in run_store._all("SELECT payload_json FROM graph_run_events WHERE run_id = ? AND event_type = ? AND cause = ? "
                              "ORDER BY event_seq", [run_id, run_events.RUN_STATUS, APPROVAL_WAIT_CAUSE]):
        try:
            data = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("node_key") != node_key or data.get("doc_id") != doc_id:
            continue
        if attempt_id and data.get("attempt_id") and data["attempt_id"] != attempt_id:
            continue
        if isinstance(data.get("reason"), str) and data["reason"]:
            found.append(data["reason"])
    return found


def approval_wait_reason(flow_id: str, row: dict) -> Optional[str]:
    """B-2: the latest B-1 reason of the node run ``row``'s result (its current attempt), or None."""
    doc_id = row.get("result_doc_id")
    if not doc_id or not row.get("run_id"):
        return None
    reasons = approval_waits(row["run_id"], row["node_key"], doc_id, run_store.active_attempt_id(row["run_id"], row["node_key"]))
    return reasons[-1] if reasons else None


# ── a lost run-end stage (flowgate.v02.0105 T#2 B-3) ─────────────────────────

def node_runs(run_id: str) -> list[dict]:
    return run_store.node_runs(run_id)


def _node_ai_runs(flow_id: str, run_id: str, node_key: str) -> list[tuple[str, dict]]:
    """(token id, ended AI run record) of the node's live tokens that carry an AI run, mapping order."""
    out = []
    for token_id in run_store.token_ids(run_id, node_key):
        found = get_store()._fetch_one("SELECT ai_run_id FROM tokens WHERE token_id = ?", [token_id])
        record = ai_run_record((found or {}).get("ai_run_id")) if found and found.get("ai_run_id") else None
        if record is not None and record.get("group_id") == flow_id:
            out.append((token_id, record))
    return out


def _work_run(record: dict) -> bool:
    return record.get("mode") == "single" and not record.get("document_review_loop")


def result_ai_run(flow_id: str, run_id: str, node_key: str, doc_id: str) -> Optional[dict]:
    """The latest ``new`` AI run of the node that registered its result ``doc_id`` with its own token (its durable row,
    ``ai_run_record``), or None — a result a person registered has none."""
    found = None
    for token_id, record in _node_ai_runs(flow_id, run_id, node_key):
        if _work_run(record) and record.get("action_scope") == "new" and _registered_with(flow_id, doc_id, [token_id]):
            found = record
    return found


def stage_ai_run(flow_id: str, run_id: str, node_key: str) -> Optional[dict]:
    """The latest single AI run of the node's live tokens that is no review loop (its instruction stage's writer while the
    node is open in that stage), or None."""
    found = None
    for _token_id, record in _node_ai_runs(flow_id, run_id, node_key):
        if _work_run(record) and record.get("action_scope") in ("new", "edit"):
            found = record
    return found


def review_loops(doc_id: str) -> list[dict]:
    """The document review loops of ``doc_id``, the latest first (started, then created)."""
    if not get_store().table_exists("ai_invoke_document_review_loops"):
        return []
    rows = [dict(r) for r in db_review_loops.list_by_document(doc_id) or []]
    return sorted(rows, key=lambda r: (str(r.get("started_at") or ""), str(r.get("created_at") or "")), reverse=True)


def settle_graph_run(flow_id: str) -> None:
    """The Flow's Graph run is judged again (flowgate.v02.0061 T#2): running while the review its card stage started
    works, idle when nothing works any more. Never raises (a failure only skips it, as before a run read)."""
    from modules.flow_gate.graph_workflow.services import run_service  # noqa: PLC0415 — the run stack on use

    try:
        run_service.settle_if_needed(flow_id, project_of(flow_id))
    except Exception:  # noqa: BLE001 — the stage's own end is not failed by the run's judgement
        _log.warning("graph run settle after the card stage failed flow=%s", flow_id, exc_info=True)


# ── a person's work plan result (flowgate.v02.0083 WP-4) ────────────────────

def active_run(flow_id: str) -> Optional[dict]:
    return run_store.active_run(flow_id)


def node_run(run_id: str, node_key: str) -> Optional[dict]:
    return next((r for r in run_store.node_runs(run_id) if r["node_key"] == node_key), None)


def active_attempt_id(run_id: str, node_key: str) -> Optional[str]:
    return run_store.active_attempt_id(run_id, node_key)


def run_issuer(run_row: dict) -> str:
    """The Graph run's issuer (its starter), as a node dispatch issues an AI card's run to it
    (``node_dispatch.dispatch_document``) — never the author of the result: the actor of the run's automatic approvals,
    and the authority of a person's work plan result's (0083 WP-4). flowgate.v02.0105 T#2 (B-4, Q #511 o3): an AI card's
    are made under the server's automatic approval authority instead (``approve`` ``AUTOMATION``)."""
    return run_row.get("started_by") or ""


def person_stage_open(flow_id: str, run_row: dict) -> bool:
    """The run may take a person's result stage (its review's start, its automatic approval): running, paused or idle
    — on the conditions ``wake_graph_run`` wakes an idle run on: not stopped (``idle_reason`` stopped), and no Time
    Machine operation unfinished. A stopping run takes nothing."""
    return _person_stage_refusal(flow_id, run_row) is None


def _person_stage_refusal(flow_id: str, run_row: Optional[dict]) -> Optional[str]:
    """Why the run may not take a person's result stage now (``person_stage_open``), or None."""
    from flowgate.graph_time_machine.operation import busy  # noqa: PLC0415

    if run_row is None:
        return "run_not_active"
    status = run_row.get("status")
    if status not in (run_store.RUNNING, run_store.PAUSED, run_store.IDLE):
        return "run_" + str(status)
    if status == run_store.IDLE and run_row.get("idle_reason") == "stopped":
        return "run_stopped"
    return "time_machine_in_progress" if busy(flow_id) else None


def person_hold(flow_id: str, run_id: str, node_key: str, attempt_id: Optional[str], operation: str):
    """0083 WP-4: a person's result stage — its review's start, its automatic approval — judged as ``attempt_hold``
    judges the run-end stage, under the Stop gate's shared hold kept for the ``with`` body (the approval's transition, its
    TR hook and its Graph contact; the review's request), with an idle run taken too (Q439: a person's own step) and the
    stage's conditions judged again under that hold: ``run_id`` still the Flow's active run, not stopped, no Time Machine
    operation unfinished (``person_stage_open``). A Stop and a Time Machine rewind take the gate exclusively, so they commit
    before the judgement — which then refuses — or after the body. Yields whether the body may go on; a refusal is
    recorded (operation ``operation``)."""
    def closed() -> Optional[str]:
        active = run_store.active_run(flow_id)
        if active is None or active["run_id"] != run_id:
            return "run_not_active"
        return _person_stage_refusal(flow_id, active)

    return node_dispatch.attempt_hold(run_id, node_key, attempt_id, operation, idle_ok=True, also=closed)


def attempt_node(attempt_id: Optional[str]) -> Optional[tuple[str, str]]:
    """(run id, node key) of a node attempt, or None."""
    if not attempt_id or not get_store().table_exists("graph_node_attempts"):
        return None
    row = get_store()._fetch_one("SELECT run_id, node_key FROM graph_node_attempts WHERE attempt_id = ?", [attempt_id])
    return (row["run_id"], row["node_key"]) if row else None


def submission_judged(doc_id: str) -> bool:
    """A review verdict already answers the document's current submission: the latest review of its current revision
    — unless that was an ``issues`` the document was rejected for (its ``rejection_history`` names the review) and the
    document was put back in review since (``mark_revised``): that is a new submission of the same revision."""
    from modules.flow_gate.db import document_reviews as db_reviews  # noqa: PLC0415
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415
    from modules.flow_gate.services.ai_invoke import review as v01_review  # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history  # noqa: PLC0415

    doc = db_documents.get_by_id(doc_id) or {}
    revision = int(doc.get("revision_no") or 0)
    reviews = [r for r in db_reviews.list_by_doc(doc_id) or [] if int(r.get("revision_no") or 0) == revision]
    if not reviews:
        return False
    latest = max(reviews, key=lambda r: int(r.get("id") or 0))
    if str(latest.get("verdict") or "").lower() != "issues":
        return True                                 # passed or held: decided (a person approves what is left)
    slot = {"doc_id": doc_id, "rejection_history": parse_rejection_history(doc.get("rejection_history"))}
    resubmitted = (v01_review._review_already_rejected(latest, slot, None)
                   and doc.get("doc_review_status") != "rejected")
    return not resubmitted


def submission_passed(doc_id: str) -> bool:
    """0083 WP-4: the latest review of the document's current revision passed it — a pass of an older revision (the
    document revised in place while its review worked) does not answer the current submission."""
    from modules.flow_gate.db import document_reviews as db_reviews  # noqa: PLC0415
    from modules.flow_gate.db import documents as db_documents  # noqa: PLC0415

    revision = int((db_documents.get_by_id(doc_id) or {}).get("revision_no") or 0)
    reviews = [r for r in db_reviews.list_by_doc(doc_id) or [] if int(r.get("revision_no") or 0) == revision]
    latest = max(reviews, key=lambda r: int(r.get("id") or 0)) if reviews else {}
    return str(latest.get("verdict") or "").lower() == "pass"


def explicit_request():
    """The span of a person's explicit review request (Q439, ``graph_execution_hooks.explicit_request``): a review hop
    requested inside it is also taken on an idle run, which it takes back to running."""
    from modules.flow_gate.services import graph_execution_hooks  # noqa: PLC0415

    return graph_execution_hooks.explicit_request()


def instruction_node_of_document(flow_id: str, doc_id: str) -> Optional[tuple[dict, dict]]:
    """The active instruction stage owning this document; result stages cannot consume this review."""
    run = run_store.active_run(flow_id)
    if run is None or not doc_id:
        return None
    record = definition_store.revision(flow_id, int(run["revision_no"])) or {}
    for row in run_store.node_runs(run["run_id"]):
        if row.get("stage") != "instruction" or row.get("status") != OPEN_NODE_STATUS or row.get("instruction_doc_id") != doc_id:
            continue
        node = next((n for n in record.get("nodes") or [] if n["node_key"] == row["node_key"]), None)
        if node:
            return node, row
    return None
