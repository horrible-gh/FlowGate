"""Run routes (P0008 §7 S5-1~S5-5, S6-1). Thin: no judgement lives here."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.domain.run_request import (
    parse_gate_decision, parse_gate_intent, parse_mode, parse_page, parse_reason, parse_start,
)
from modules.flow_gate.graph_workflow.services import canvas_service, gate_service, run_service

from . import envelope

router = APIRouter(prefix="/api/v1", tags=["GraphRun"])


@router.post("/flows/{flow_id}/runs")
def post_flow_run(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        revision_no, errors = parse_start(body)
        mode, mode_errors = parse_mode(body)                    # flowgate.v02.0061 T#2: step / full
        errors = errors or mode_errors
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        return run_service.start_run(group, user_id, revision_no, mode)

    return envelope.run(request, handle, status=201)


@router.get("/flows/{flow_id}/runs/current")
def get_flow_current_run(flow_id: str, request: Request):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "read")
        return run_service.current_run(group)

    return envelope.run(request, handle)


@router.get("/flows/{flow_id}/runs")
def get_flow_runs(flow_id: str, request: Request, limit: Optional[str] = None, offset: Optional[str] = None):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        (lim, off), errors = parse_page(limit, offset)
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "read")
        return run_service.list_runs(group, lim, off)

    return envelope.run(request, handle)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        group = canvas_service.load_flow(run_service.run_flow_id(run_id))
        envelope.authorize(user_id, group, "read")
        return run_service.get_run(run_id)

    return envelope.run(request, handle)


def _control(request: Request, run_id: str, body: Any, action, with_mode: bool = False) -> dict:
    user_id = envelope.authenticate(request)
    envelope.client_id_of(request)
    reason, errors = parse_reason(body)
    mode, mode_errors = parse_mode(body) if with_mode else (None, [])
    errors = errors or mode_errors
    if errors:
        raise envelope.invalid(request, errors)
    group = canvas_service.load_flow(run_service.run_flow_id(run_id))
    envelope.authorize(user_id, group, "write")
    if with_mode:
        return action(group, run_id, reason, user_id, mode)
    return action(group, run_id, reason, user_id)


@router.post("/runs/{run_id}/pause")
def post_run_pause(run_id: str, request: Request, body: Any = Body(default=None)):
    """flowgate.v02.0061 T#2 Pause (running -> paused), moved here from /stop by T#7."""
    return envelope.run(request, lambda: _control(request, run_id, body, run_service.pause_run))


@router.post("/runs/{run_id}/stop")
def post_run_stop(run_id: str, request: Request, body: Any = Body(default=None)):
    """flowgate.v02.0061 T#7 Stop: stopping, the AI processes ended, then idle (stopped)."""
    return envelope.run(request, lambda: _control(request, run_id, body, run_service.stop_run))


@router.post("/runs/{run_id}/nodes/{node_key}/recover")
def post_card_recover(run_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    """0083 WP-2: the same authentication and write permission as Stop / Resume."""
    from modules.flow_gate.graph_workflow.services.run_state_ports import recover_card
    return envelope.run(request, lambda: _control(
        request, run_id, body,
        lambda group, rid, reason, user: recover_card(group, rid, node_key, reason, user)))


@router.post("/runs/{run_id}/resume")
def post_run_resume(run_id: str, request: Request, body: Any = Body(default=None)):
    """flowgate.v02.0061 T#2: a paused / idle run goes on; ``mode`` (step / full) makes it a new command."""
    return envelope.run(request, lambda: _control(request, run_id, body, run_service.resume_run, with_mode=True))


@router.post("/runs/{run_id}/nodes/{node_key}/gate-decision")
def post_gate_decision(run_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    """flowgate.v02.0085 T#2 (D0007 §7.2): a new client also sends ``request_id``, ``expected_attempt_id`` and
    ``expected_settings_version``; the answer is 200 (rejected / confirmed at once) or 202 (the confirm's Git apply or
    archive accepted as a job). The actor is the authenticated user, never a body field."""
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        (decision, comment), errors = parse_gate_decision(body)
        intent, intent_errors = parse_gate_intent(body)
        errors = errors + intent_errors
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(run_service.run_flow_id(run_id))
        envelope.authorize(user_id, group, "write")
        return gate_service.decide_gate(group, run_id, node_key, decision, comment, user_id, intent)

    return envelope.run(request, handle)
