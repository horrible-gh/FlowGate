"""Node executor registry and the document node executor (D0004 §2.2, L0009 §2.6.6 ``dispatch_node``).

The registry maps a node type to its executor: ``document`` and ``gate``. A gate node hands no
work out — no token, no mention, ``dispatch = null`` (P0008 §7.1, L0009 §2.6.6); a person decides
it through the gate decision API (``gate_service``, P0008 S6-1). A ``custom`` node has no executor
(Q-11). The document executor hands the work out through the existing token service — called,
never changed — with the v0.1 next-step token shape (``action_scope="new"``, ``doc_ref`` = the
Flow's root document, here the bound root's live end), records token -> node run (the result
binder's evidence) and, for an AI assignee, queues the AI start after the commit.

- The issue runs under a savepoint: a failing statement must not poison the guard transaction
  (PostgreSQL), so a failed issue is rolled back to the savepoint and reported as
  ``dispatch.token_issued = false`` with ``error_code`` — the run goes on (L0009 E-22).
- ``issued_to`` of the token is a user (``workflow_events.actor_user_id`` is a users FK): the human
  assignee, else the run's actor; an AI node's token carries the provider in ``provider_id`` and the
  dispatch block names the provider (P0008 §7.1).
- v0.1 keeps no mention record (a mention is text built on demand from the token), so
  ``mention_id`` stays null.
- The AI start is ``ai_node_starter.start_ai_invoke`` (the existing AI invoke service, single mode,
  once per node — D0004 §3.4 / §3.5). ``set_ai_starter`` swaps it (tests; ``None`` = not wired).
- flowgate.v02.0053 T#2: an AI node reads its card's run settings here, inside the run step's guard
  (``card_settings_ports.run_settings``): the card's run AI replaces the assignee provider (token and
  start), its time limit / restarts ride the job to the start, and with the instruction omitted the
  AI writes the card's report instead of the node's own type (``result_doc_type``; the result binder
  asks the same port which type it binds). With no setting (or no handler) every value is the one
  used before.
  When the start raises, or nothing is wired, the open node records ``ai_invoke_failed`` in a new
  guard (L0009 E-23); the token stays valid for a person.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import token_service

from . import card_settings_ports

_log = logging.getLogger(__name__)


def _start_with_ai_invoke(job: dict) -> Any:
    from .ai_node_starter import start_ai_invoke              # noqa: PLC0415 — loads the v0.1 AI stack on use

    return start_ai_invoke(job)


_ai_starter: Optional[Callable[[dict], Any]] = _start_with_ai_invoke


def set_ai_starter(starter: Optional[Callable[[dict], Any]]) -> Optional[Callable[[dict], Any]]:
    """Wire the AI start (``starter(job)``, see ``dispatch_document``). Returns the old one."""
    global _ai_starter
    old, _ai_starter = _ai_starter, starter
    return old


@contextmanager
def _savepoint(name: str = "graph_dispatch"):
    store = get_store()
    store._execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        store._execute(f"ROLLBACK TO SAVEPOINT {name}")
        store._execute(f"RELEASE SAVEPOINT {name}")
        raise
    store._execute(f"RELEASE SAVEPOINT {name}")


def dispatch(ctx, node_key: str) -> Optional[dict]:
    """``dispatch_node``: the executor of the node's type, or ``None`` (no executor: no dispatch)."""
    row = ctx.node_rows.get(node_key) or {}
    executor = _EXECUTORS.get(row.get("node_type"))
    return executor(ctx, row) if executor else None


def dispatch_document(ctx, row: dict) -> dict:
    ai = row.get("assignee_kind") == "ai"
    plan = card_settings_ports.run_settings(ctx.flow_id, row) if ai else {}
    provider = (plan.get("provider_id") or row.get("assignee_provider_id")) if ai else None
    user = row.get("assignee_user_id") if row.get("assignee_kind") == "human" else None
    issued_to = user or ctx.run.get("started_by") or ctx.actor or ""
    shown = provider if ai and provider else issued_to
    root = ctx.root_document()
    try:
        with _savepoint():
            token = token_service.issue(
                project=ctx.project_id or "", group_id=ctx.flow_id, action_scope="new",
                doc_ref=(root or {}).get("doc_id"), issued_to=issued_to, provider_id=provider,
                continuation_target_seq=None,                       # no unmanned chain (D0004 §3.5, Q-9)
            )
    except Exception as exc:  # noqa: BLE001 — L0009 E-22: the run goes on, the node stays open
        _log.warning("graph node dispatch failed run=%s node=%s: %s", ctx.run_id, row.get("node_key"), exc)
        return {"token_issued": False, "mention_id": None, "issued_to": shown, "error_code": "token_issue_failed"}
    run_store.map_token(token["token_id"], ctx.run_id, row["node_key"])
    if ai:
        job = {"flow_id": ctx.flow_id, "project_id": ctx.project_id, "run_id": ctx.run_id,
               "node_key": row["node_key"], "provider_id": provider, "token": token,
               "issued_to": issued_to, "doc_ref": (root or {}).get("doc_id"), "doc_type": row.get("doc_type"),
               "result_doc_type": plan.get("result_doc_type") or row.get("doc_type"),
               "step_timeout_sec": plan.get("step_timeout_sec"), "restart_max": plan.get("restart_max"),
               "card_settings": plan.get("settings") or {}}
        ctx.jobs.append(lambda: start_ai(job))
    return {"token_issued": True, "mention_id": None, "issued_to": shown}


def dispatch_gate(_ctx, _row: dict) -> None:
    """Gate node executor: opening it is the confirmation request; nothing is issued."""
    return None


_EXECUTORS: dict[str, Callable[[Any, dict], Optional[dict]]] = {"document": dispatch_document, "gate": dispatch_gate}


def start_ai(job: dict) -> None:
    """Post-commit job (outside the guard): start the AI, or record the failure (L0009 E-23)."""
    try:
        if _ai_starter is None:
            raise RuntimeError("no AI starter wired for Graph nodes")
        _ai_starter(job)
    except Exception as exc:  # noqa: BLE001
        _log.warning("graph AI start failed run=%s node=%s: %s", job["run_id"], job["node_key"], exc)
        record_ai_failure(job)


def record_ai_failure(job: dict) -> None:
    from . import run_apply                                   # noqa: PLC0415 — run_apply imports this module
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    def body() -> None:
        run = run_store.run_by_id(job["run_id"])              # re-read inside the guard (L0009 §2.1.1)
        if run is None or run["status"] == "completed":
            return
        row = next((r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == job["node_key"]), None)
        if row is None or row["status"] != "open":
            return
        if job["token"]["token_id"] not in run_store.token_ids(run["run_id"], job["node_key"]):
            return
        dispatch = {**(run_apply.dispatch_block(row) or {}), "error_code": "ai_invoke_failed"}
        run_store.set_dispatch(run["run_id"], job["node_key"], dispatch)
        ctx = run_apply.load_context(run, job.get("project_id"), None)
        run_apply.emit_node_state(ctx, job["node_key"], "open", "open", "dispatch_failed",
                                  row.get("result_doc_id"), dispatch)

    try:
        with_flow_guard(job["flow_id"], body)
    except Exception:  # noqa: BLE001 — a post-commit job never fails the request that queued it
        _log.exception("graph AI failure record failed run=%s node=%s", job["run_id"], job["node_key"])
