"""Node executor registry and the document node executor (D0004 §2.2, L0009 §2.6.6 ``dispatch_node``).

The registry maps a node type to its executor: ``document``, ``custom`` and ``gate``. A gate node hands
no work out — no token, no mention, ``dispatch = null`` (P0008 §7.1, L0009 §2.6.6); a person decides
it through the gate decision API (``gate_service``, P0008 S6-1). flowgate.v02.0053 T#8: a ``custom`` node
(a Custom card) is a plain document card — handed out by the document executor as the document type its
run writes (``graph_model.run_doc_type``: the work instruction T), whatever Palette category made it.
The document executor hands the work out through the existing token service — called,
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
- flowgate.v02.0056 T#2 (0002-CH turn 5 / 6 §3 / 10 O2): an AI card with its instruction used runs in two
  stages, each one single AI start through the same starter, inside one card. The 0061 ``report_pair``
  policy names this existing instruction_result contract; it adds no executor. The node run records its
  stage (``graph_node_runs.stage``).

  1. instruction (``plan["instruction"]``): whether it runs is decided by the body of the card's instruction,
     not by the document existing (``card_settings_ports.instruction_document``, the v0.2 body judgement):
     no body -> the instruction AI (instruction AI, else run AI, else the assignee) writes the instruction
     type once — into the line's empty document when there is one (``action_scope`` edit, no new document),
     else as a new document; a body -> no AI, the instruction is approved and the result stage starts. Its
     end (``card_settings`` on the AI run's end) approves the written instruction with no review and starts
     the result stage (``start_result_stage``); a failed instruction stage records ``ai_invoke_failed`` on
     the node as an AI start failure does and never reaches the result stage.
  2. result: the node's result as before (the card's report), the instruction attached as a reference
     document (``ref_doc_ids``). Its review and what follows are the card's, as before.

  An instruction approval binds nothing (no node event, no propagation); the result binder takes only the
  result stage's document as the node's result: the stage's end — the move to the result stage, or its failure —
  records the node run's registration boundary (``run_store.mark_result_floor``), so a document the node's tokens
  registered during the instruction stage never binds later, on a contact or a reconciliation; one whose consume
  lands after that end is told by its token's stage (the instruction AI's token is mapped as that stage's by its
  start, ``ai_node_starter``). A run stopped at the next step does not stop either stage
  of a card it already opened. A node with no instruction stage (instruction omitted, a test card, no card
  handler) runs only the result stage.

- flowgate.v02.0111 T#2 (T-A1, NR flowgate.v02.0111.0003 §2 A-1): an AI run that asked questions and ended before its
  document was (fully) submitted is a question wait, never a failure. Its instruction stage is written again on the same
  attempt once the questions are answered (``retry_instruction_stage``: the instruction AI, never the "written: approve
  without AI" shortcut of ``_instruction_job``); a result writer that ended so (``write_failure`` ``question_pending``)
  is retried like one with no result (``retry_result_stage``) and, while its questions are open, is no dispatch error and
  no explicit command's retry (``needs_ai_dispatch``).
- flowgate.v02.0111 T#3 (T-A2, NR flowgate.v02.0111.0003 §2 A-2): the answers' retry of a result writer is taken under
  ``legacy.person_hold`` with ``retry_result_stage`` ``idle_ok``, as the result stage's start after the answers is
  (``start_result_stage`` ``idle_ok``): a run judged idle between the wake and the retry no longer refuses it
  (``attempt_inactive`` ``result_retry`` / ``run_idle``). Without ``idle_ok`` it still refuses an idle run.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.graph_model import run_doc_type
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import token_service

from . import card_settings_ports
from modules.flow_gate.services import graph_execution_hooks

_log = logging.getLogger(__name__)

INSTRUCTION_STAGE, RESULT_STAGE = "instruction", "result"
# 0111 T-A1: the result writer's ends ``retry_result_stage`` writes again after its questions are answered.
RETRYABLE_WRITE_FAILURES = ("no_result", "question_pending")
QUESTION_PENDING = "question_pending"


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


def needs_ai_dispatch(ctx, row: dict) -> bool:
    """An explicit command can retry a finished result writer with no result, or recover a
    default Designer card opened without dispatch (0063 B7). Result reconciliation runs first;
    the legacy no-dispatch repair still excludes staged nodes and explicit assignees."""
    node = ctx.node_rows.get(row["node_key"]) or {}
    # An explicit command may retry a result writer that ended without its result.
    # Hold, live executions and registered results still prevent a new dispatch.
    # 0111 T-A1: not one whose questions are still open (``question_pending``) — its answers retry it once.
    if card_settings_ports.uses_ai(ctx.flow_id, node) \
            and row["node_key"] not in (graph_execution_hooks.call("held_nodes", ctx.flow_id) or ()) \
            and graph_execution_hooks.call("write_failure", row) not in (None, "", QUESTION_PENDING):
        return True
    return (row["status"] == "open" and row.get("stage") is None and row.get("result_doc_id") is None
            and node.get("assignee_kind") is None
            and card_settings_ports.uses_ai(ctx.flow_id, node)
            and run_store.active_attempt_id(ctx.run_id, row["node_key"]) is not None)


def dispatch_document(ctx, row: dict) -> dict:
    ai = card_settings_ports.uses_ai(ctx.flow_id, row)
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
    run_store.map_token(token["token_id"], ctx.run_id, row["node_key"], map_kind=run_store.MAP_DISPATCH)
    if ai:
        stage = INSTRUCTION_STAGE if plan.get("instruction") else RESULT_STAGE
        run_store.set_stage(ctx.run_id, row["node_key"], stage)
        job = _ai_job(ctx, row, plan, provider, issued_to, token, stage)
        graph_execution_hooks.call("request_write", job)
        ctx.jobs.append(lambda: start_ai(job))
    return {"token_issued": True, "mention_id": None, "issued_to": shown}


def _ai_job(ctx, row: dict, plan: dict, provider: Optional[str], issued_to: str, token: dict, stage: str,
            ref_doc_ids: Optional[list] = None) -> dict:
    """The AI start job of a node (``start_ai``): the node row, its card's run plan and the stage."""
    root = ctx.root_document()
    return {"flow_id": ctx.flow_id, "project_id": ctx.project_id, "run_id": ctx.run_id,
            "node_key": row["node_key"], "node": dict(row), "provider_id": provider, "token": token,
            "issued_to": issued_to, "doc_ref": (root or {}).get("doc_id"), "doc_type": row.get("doc_type"),
            "result_doc_type": plan.get("result_doc_type") or row.get("doc_type"),
            "step_timeout_sec": plan.get("step_timeout_sec"), "restart_max": plan.get("restart_max"),
            "card_settings": plan.get("settings") or {}, "stage": stage,
            "instruction": plan.get("instruction") if stage == INSTRUCTION_STAGE else None,
            "action_scope": "new", "target_doc_id": None, "ref_doc_ids": ref_doc_ids}


def dispatch_custom(ctx, row: dict) -> dict:
    """Custom card executor (flowgate.v02.0053 T#8): the document executor, the node standing for its run type."""
    return dispatch_document(ctx, {**row, "doc_type": run_doc_type(row.get("node_type"), row.get("doc_type"))})


def dispatch_gate(_ctx, _row: dict) -> None:
    """Gate node executor: opening it is the confirmation request; nothing is issued."""
    return None


_EXECUTORS: dict[str, Callable[[Any, dict], Optional[dict]]] = {
    "document": dispatch_document, "custom": dispatch_custom, "gate": dispatch_gate,
}


def start_ai(job: dict) -> None:
    """Post-commit job (outside the guard): start the AI, or record the failure (L0009 E-23). An instruction
    stage job first asks the card's instruction (``_instruction_job``): a written one is approved and the
    result stage started instead."""
    try:
        if _ai_starter is None:
            raise RuntimeError("no AI starter wired for Graph nodes")
        if job.get("stage") == INSTRUCTION_STAGE and not job.get("instruction_retry"):
            started = _instruction_job(job)
            if started is None:
                return                                      # written already: the result stage took over
            job = started
        _ai_starter(job)
    except Exception as exc:  # noqa: BLE001
        _log.warning("graph AI start failed run=%s node=%s: %s", job["run_id"], job["node_key"], exc)
        if not graph_execution_hooks.call("start_failed", job.get("graph_exec_id"), exc):
            record_ai_failure(job)


def _instruction_job(job: dict) -> Optional[dict]:
    """The instruction stage's start: the job that writes the card's instruction, or None when it has a body
    already (approved without AI; the result stage is started)."""
    attempt_id = job.get("graph_attempt_id")
    found = card_settings_ports.instruction_document(job["flow_id"], job["node"])
    if not found.get("doc_type"):                           # the card has no instruction stage any more
        start_result_stage(job["flow_id"], job["run_id"], job["node_key"], None, job["issued_to"],
                           job.get("project_id"), attempt_id=attempt_id)
        return None
    if found.get("written"):
        _record_instruction(job, found["doc_id"])
        if card_settings_ports.review_instruction(job["flow_id"], job["node"], found["doc_id"],
                                                  job["issued_to"], job["run_id"], attempt_id):
            return None
        # 0061 T#7: a job of an attempt that is no longer the node's active one approves nothing (recorded); the
        # approval and the result stage's commit run under the Stop gate's shared hold, judged for the job's attempt.
        jobs: list = []
        with attempt_hold(job["run_id"], job["node_key"], attempt_id, "instruction_approval") as owned:
            if not owned:
                return None
            if not card_settings_ports.approve_instruction(job["flow_id"], job["node"], found["doc_id"],
                                                           job["issued_to"]):
                _log.info("graph instruction left to the card approval run=%s node=%s doc=%s",
                          job["run_id"], job["node_key"], found["doc_id"])
            start_result_stage(job["flow_id"], job["run_id"], job["node_key"], found["doc_id"], job["issued_to"],
                               job.get("project_id"), attempt_id=attempt_id, defer=jobs)
        for queued in jobs:                                 # the result stage's AI start, after the hold
            queued()
        return None
    target = found.get("doc_id")
    if target:
        _record_instruction(job, target)
    stage = job.get("instruction") or {}
    return {**job, "provider_id": stage.get("provider_id") or job["provider_id"],
            "result_doc_type": found["doc_type"], "action_scope": "edit" if target else "new",
            "target_doc_id": target, "ref_doc_ids": [job["doc_ref"]] if target and job.get("doc_ref") else None}


def _record_instruction(job: dict, doc_id: str) -> None:
    """The instruction document the stage writes into (its line's empty document)."""
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    def body() -> None:
        row = _open_row(job["run_id"], job["node_key"])
        if row is not None and row.get("stage") == INSTRUCTION_STAGE:
            if graph_execution_hooks.call("node_refused", job["run_id"], job["node_key"], "instruction_record",
                                          job.get("graph_attempt_id")):
                return                                      # 0061 T#7: a stopping run / not the job's attempt
            run_store.set_stage(job["run_id"], job["node_key"], INSTRUCTION_STAGE, doc_id)

    with_flow_guard(job["flow_id"], body)


def attempt_owns(run_id: str, node_key: str, attempt_id: Optional[str], operation: str) -> bool:
    """0061 T#7, outside a guard: the attempt a job or an AI run was made for (``attempt_id``; None: not known, the
    node's active attempt judges) is still the node's active attempt and may write. A refusal is recorded."""
    return not graph_execution_hooks.call("node_refused", run_id, node_key, operation, attempt_id)


@contextmanager
def attempt_hold(run_id: str, node_key: str, attempt_id: Optional[str], operation: str, **options) -> Iterator[bool]:
    """0061 T#7, outside a guard: ``attempt_owns`` judged under the Stop gate's shared hold, kept for the body — an
    automatic approval and the step after it commit before a Stop's ``stopping`` does, or are refused after it.
    Yields whether the body may go on (a refusal is recorded after the hold). No Graph handler: True.
    ``options``: the Graph's own (0083 WP-4, a person's result stage: ``idle_ok``, ``also``)."""
    hold = graph_execution_hooks.call("node_hold", run_id, node_key, attempt_id, operation, **options)
    if hold is None:
        yield True
        return
    with hold as owned:
        yield bool(owned)


def _open_row(run_id: str, node_key: str) -> Optional[dict]:
    """The node run, while its run goes on and the node is open (re-read inside the guard, L0009 §2.1.1)."""
    run = run_store.run_by_id(run_id)
    if run is None or run_store.completed(run):
        return None
    row = next((r for r in run_store.node_runs(run_id) if r["node_key"] == node_key), None)
    return row if row is not None and row["status"] == "open" else None


def start_result_stage(flow_id: str, run_id: str, node_key: str, instruction_doc_id: Optional[str],
                       issued_to: str, project_id: Optional[str] = None, *, attempt_id: Optional[str] = None,
                       defer: Optional[list] = None, idle_ok: bool = False) -> None:
    """flowgate.v02.0056 T#2: the node's instruction stage is over (its instruction written or found written,
    and approved — or left to the card approval); its run AI writes the node's result now, the instruction
    attached as a reference. Only a node still open in its instruction stage moves on (a repeated call is a
    no-op), whether the run goes on or is stopped at the next step (a card already opened finishes both
    stages). The start is a post-commit job like the dispatch's: a refused start records ``ai_invoke_failed``.
    0061 T#7: ``attempt_id`` is the attempt whose instruction stage ended (the job's, the instruction AI run's); a
    node opened again since (another active attempt) is left alone and the refusal recorded. ``defer``: the caller
    runs the post-commit AI start itself (after the Stop gate's hold it took, ``attempt_hold``).
    flowgate.v02.0105 T#2 (B-3): ``idle_ok`` — an idle run (waiting for a person) takes the result stage too, for a
    caller holding ``legacy.person_hold`` (the run read's reconciliation of a lost instruction stage end): its Stop /
    Time Machine conditions were judged under that hold, and the execution's request takes the run back to running
    (``request_write``'s settlement), so its AI start can acquire."""
    from . import run_apply                                   # noqa: PLC0415 — run_apply imports this module
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    project = project_id or (db_groups.get_by_id(flow_id) or {}).get("project_id")
    jobs: list = []
    refusal = {"idle_ok": True} if idle_ok else {}

    def body() -> None:
        row = _open_row(run_id, node_key)
        if row is None or row.get("stage") != INSTRUCTION_STAGE:
            return
        if graph_execution_hooks.call("node_refused", run_id, node_key, "result_stage", attempt_id, **refusal):
            return                                          # 0061 T#7: a late instruction end starts nothing
        ctx = run_apply.load_context(run_store.run_by_id(run_id), project, None, [])
        node = ctx.node_rows.get(node_key)
        if node is None:
            return
        if node.get("node_type") == "custom":                 # handed out as ``dispatch_custom`` does
            node = {**node, "doc_type": run_doc_type(node.get("node_type"), node.get("doc_type"))}
        plan = card_settings_ports.run_settings(flow_id, node)
        provider = plan.get("provider_id") or node.get("assignee_provider_id")
        tokens = run_store.token_ids(run_id, node_key)
        run_store.set_stage(run_id, node_key, RESULT_STAGE, instruction_doc_id)
        run_store.mark_result_floor(run_id, node_key)          # the instruction stage's documents stay behind
        if instruction_doc_id:
            run_apply.fill_line(ctx, node_key, instruction_doc_id)   # its line, also after a lost contact
        job = _ai_job(ctx, node, plan, provider, issued_to, {"token_id": tokens[0] if tokens else None},
                      RESULT_STAGE, [instruction_doc_id] if instruction_doc_id else None)
        graph_execution_hooks.call("request_write", job)
        jobs.append(lambda: start_ai(job))

    with_flow_guard(flow_id, body)
    if defer is not None:
        defer.extend(jobs)
        return
    for job in jobs:
        job()


def retry_result_stage(flow_id: str, run_id: str, node_key: str, issued_to: str, project_id: Optional[str] = None, *,
                       attempt_id: Optional[str] = None, idle_ok: bool = False,
                       defer: Optional[list] = None) -> bool:
    """flowgate.v02.0063: the node's result writer ended with no result after asking questions, and they are answered
    now: its run AI writes the result again on the same attempt (a new execution, the instruction attached as at the
    result stage's start). Only a node still open in its result stage whose writer ended with no result
    (``write_failure``; 0111 T-A1: ``no_result`` or ``question_pending``) and that is not held moves on; ``attempt_id``
    must still be the node's active attempt. True when the AI start was requested (it runs after the guard).
    flowgate.v02.0111 T#3 (T-A2, NR flowgate.v02.0111.0003 §2 A-2): ``idle_ok`` — an idle run takes it too, for a caller
    holding ``legacy.person_hold`` (the answers' continuation): a run judged idle between the wake and this retry is no
    refusal, and the execution's request takes it back to running. ``defer``: the caller runs the AI start after its
    hold."""
    from . import run_apply                                   # noqa: PLC0415 — run_apply imports this module
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    project = project_id or (db_groups.get_by_id(flow_id) or {}).get("project_id")
    jobs: list = []
    refusal = {"idle_ok": True} if idle_ok else {}

    def body() -> None:
        row = _open_row(run_id, node_key)
        if row is None or row.get("stage") != RESULT_STAGE or row.get("result_doc_id"):
            return
        if graph_execution_hooks.call("write_failure", row) not in RETRYABLE_WRITE_FAILURES \
                or node_key in (graph_execution_hooks.call("held_nodes", flow_id) or ()):
            return
        if graph_execution_hooks.call("node_refused", run_id, node_key, "result_retry", attempt_id, **refusal):
            return
        ctx = run_apply.load_context(run_store.run_by_id(run_id), project, None, [])
        node = ctx.node_rows.get(node_key)
        if node is None:
            return
        if node.get("node_type") == "custom":                 # handed out as ``dispatch_custom`` does
            node = {**node, "doc_type": run_doc_type(node.get("node_type"), node.get("doc_type"))}
        plan = card_settings_ports.run_settings(flow_id, node)
        provider = plan.get("provider_id") or node.get("assignee_provider_id")
        tokens = run_store.token_ids(run_id, node_key)
        instruction_doc_id = row.get("instruction_doc_id")
        job = _ai_job(ctx, node, plan, provider, issued_to, {"token_id": tokens[0] if tokens else None},
                      RESULT_STAGE, [instruction_doc_id] if instruction_doc_id else None)
        graph_execution_hooks.call("request_write", job)
        jobs.append(lambda: start_ai(job))

    with_flow_guard(flow_id, body)
    if defer is not None:
        defer.extend(jobs)
        return bool(jobs)
    for job in jobs:
        job()
    return bool(jobs)


def retry_instruction_stage(flow_id: str, run_id: str, node_key: str, issued_to: str, asker_ai_run_id: Optional[str],
                            target_doc_id: Optional[str] = None, project_id: Optional[str] = None, *,
                            attempt_id: Optional[str] = None, defer: Optional[list] = None) -> bool:
    """flowgate.v02.0111 T#2 (T-A1): the instruction stage's AI run ``asker_ai_run_id`` asked questions and they are
    answered now, but its instruction is not written (or it ended asking, ``question_pending``): the instruction AI
    writes it again on the same attempt — a new execution, never a new attempt. Inside the Flow guard, only once: the node
    is still open in its instruction stage with no result; its active attempt has no requested / running execution and
    its latest execution is the asker's instruction write, its questions all answered (``instruction_continuation_due``:
    the same answers told twice find the first retry's execution and request nothing, an earlier asker answered again
    after a later run of the stage finds that run's execution); and ``attempt_id`` may write, an idle run too (``node_refused``
    ``idle_ok`` — the caller holds ``person_hold``). The job is the instruction AI's (the card's instruction AI, else its
    run AI, else the assignee), ``action_scope`` edit into ``target_doc_id`` (else the instruction line's document) when
    there is one, else new; ``_instruction_job``'s "written: approved without AI" shortcut is never taken — what the
    asking run wrote never read the answers. ``defer``: the caller runs the AI start after its hold. True when it was
    requested."""
    from . import run_apply                                   # noqa: PLC0415 — run_apply imports this module
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    project = project_id or (db_groups.get_by_id(flow_id) or {}).get("project_id")
    jobs: list = []

    def body() -> None:
        row = _open_row(run_id, node_key)
        if row is None or row.get("stage") != INSTRUCTION_STAGE or row.get("result_doc_id"):
            return
        if not graph_execution_hooks.call("instruction_continuation_due", row, asker_ai_run_id):
            return
        if graph_execution_hooks.call("node_refused", run_id, node_key, "instruction_retry", attempt_id, idle_ok=True):
            return
        ctx = run_apply.load_context(run_store.run_by_id(run_id), project, None, [])
        node = ctx.node_rows.get(node_key)
        if node is None:
            return
        if node.get("node_type") == "custom":                 # handed out as ``dispatch_custom`` does
            node = {**node, "doc_type": run_doc_type(node.get("node_type"), node.get("doc_type"))}
        found = card_settings_ports.instruction_document(flow_id, node)
        if not found.get("doc_type"):
            return                                          # the card has no instruction stage any more
        plan = card_settings_ports.run_settings(flow_id, node)
        provider = plan.get("provider_id") or node.get("assignee_provider_id")
        tokens = run_store.token_ids(run_id, node_key)
        job = _ai_job(ctx, node, plan, provider, issued_to, {"token_id": tokens[0] if tokens else None},
                      INSTRUCTION_STAGE)
        target = target_doc_id or found.get("doc_id")
        if target:
            run_store.set_stage(run_id, node_key, INSTRUCTION_STAGE, target)
        stage = job.get("instruction") or {}
        job.update(provider_id=stage.get("provider_id") or job["provider_id"], result_doc_type=found["doc_type"],
                   action_scope="edit" if target else "new", target_doc_id=target,
                   ref_doc_ids=[job["doc_ref"]] if target and job.get("doc_ref") else None, instruction_retry=True)
        graph_execution_hooks.call("request_write", job)
        jobs.append(lambda: start_ai(job))

    with_flow_guard(flow_id, body)
    if defer is not None:
        defer.extend(jobs)
        return bool(jobs)
    for job in jobs:
        job()
    return bool(jobs)


def instruction_stage_failed(flow_id: str, run_id: str, node_key: str, project_id: Optional[str] = None, *,
                             attempt_id: Optional[str] = None) -> None:
    """flowgate.v02.0056 T#2: the instruction stage's AI run failed (stopped, or ended without a written
    instruction): the node records it as an AI start failure does (``ai_invoke_failed``, L0009 E-23) and
    the result stage does not start. The node's token stays valid for a person; what the failed stage registered
    stays behind the node run's registration boundary (``record_ai_failure``)."""
    tokens = run_store.token_ids(run_id, node_key)
    if not tokens:
        return
    record_ai_failure({"flow_id": flow_id, "run_id": run_id, "node_key": node_key, "token": {"token_id": tokens[0]},
                       "project_id": project_id or (db_groups.get_by_id(flow_id) or {}).get("project_id"),
                       "graph_attempt_id": attempt_id})


def record_ai_failure(job: dict) -> None:
    from . import run_apply                                   # noqa: PLC0415 — run_apply imports this module
    from .flow_guard import with_flow_guard                   # noqa: PLC0415

    jobs: list = []

    def body() -> None:
        run = run_store.run_by_id(job["run_id"])              # re-read inside the guard (L0009 §2.1.1)
        if run is None or run_store.completed(run):
            return
        row = next((r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == job["node_key"]), None)
        if row is None or row["status"] != "open":
            return
        if job["token"]["token_id"] not in run_store.token_ids(run["run_id"], job["node_key"]):
            return
        if graph_execution_hooks.call("node_refused", run["run_id"], job["node_key"], "ai_failure_record",
                                      job.get("graph_attempt_id")):
            return                                          # 0061 T#7: a stopping run's / an old attempt's failure
        dispatch = {**(run_apply.dispatch_block(row) or {}), "error_code": "ai_invoke_failed"}
        run_store.set_dispatch(run["run_id"], job["node_key"], dispatch)
        if row.get("stage") == INSTRUCTION_STAGE:              # T#2: a person's result binds, not the stage's
            run_store.mark_result_floor(run["run_id"], job["node_key"])
        ctx = run_apply.load_context(run, job.get("project_id"), None, jobs)
        run_apply.emit_node_state(ctx, job["node_key"], "open", "open", "dispatch_failed",
                                  row.get("result_doc_id"), dispatch)
        run_apply.settle_run(ctx)                             # 0061 T#2: nothing else works -> idle (error)

    try:
        with_flow_guard(job["flow_id"], body)
    except Exception:  # noqa: BLE001 — a post-commit job never fails the request that queued it
        _log.exception("graph AI failure record failed run=%s node=%s", job["run_id"], job["node_key"])
        return
    for queued in jobs:                                       # what the settle opened (post-commit)
        try:
            queued()
        except Exception:  # noqa: BLE001
            _log.exception("graph run post-commit job failed run=%s", job["run_id"])
