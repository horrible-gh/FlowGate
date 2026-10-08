"""Run routes (P0008 §7 S5-1~S5-5). Thin: no judgement lives here."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.domain.run_request import parse_page, parse_reason, parse_start
from modules.flow_gate.graph_workflow.services import canvas_service, run_service

from . import envelope

router = APIRouter(prefix="/api/v1", tags=["GraphRun"])


@router.post("/flows/{flow_id}/runs")
def post_flow_run(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        revision_no, errors = parse_start(body)
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        return run_service.start_run(group, user_id, revision_no)

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


def _control(request: Request, run_id: str, body: Any, action) -> dict:
    user_id = envelope.authenticate(request)
    envelope.client_id_of(request)
    reason, errors = parse_reason(body)
    if errors:
        raise envelope.invalid(request, errors)
    group = canvas_service.load_flow(run_service.run_flow_id(run_id))
    envelope.authorize(user_id, group, "write")
    return action(group, run_id, reason, user_id)


@router.post("/runs/{run_id}/stop")
def post_run_stop(run_id: str, request: Request, body: Any = Body(default=None)):
    return envelope.run(request, lambda: _control(request, run_id, body, run_service.stop_run))


@router.post("/runs/{run_id}/resume")
def post_run_resume(run_id: str, request: Request, body: Any = Body(default=None)):
    return envelope.run(request, lambda: _control(request, run_id, body, run_service.resume_run))
