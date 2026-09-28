"""Authenticated machine API for Remote CLI Job pull and result delivery."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from modules.flow_gate.api.v1.agent_routes import err, no_store
from modules.flow_gate.services.agent_registry import AgentError
from modules.flow_gate.services.agent_jobs import AgentJobService

router = APIRouter()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Strict):
    pass


class Lease(Strict):
    lease_token: str = Field(min_length=1)


class Terminal(Lease):
    status: Literal["DONE", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"]
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    diagnostics: str | None
    end_reason: str | None
    duration_seconds: float = Field(ge=0)
    reported_at: datetime


def svc():
    return AgentJobService()


def credential(authorization):
    return authorization[7:] if authorization and authorization.startswith("Bearer ") else None


def run(fn):
    try:
        return fn()
    except AgentError as exc:
        return err(exc)


@router.post("/agent/jobs/claim")
def claim(body: Empty, authorization: str | None = Header(default=None)):
    def work():
        job = svc().claim(credential(authorization))
        if job is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return no_store(job)
    return run(work)


@router.post("/agent/jobs/{job_id}/{attempt_id}/lease")
def lease(job_id: str, attempt_id: str, body: Lease, authorization: str | None = Header(default=None)):
    return run(lambda: no_store(svc().renew(credential(authorization), job_id, attempt_id, body.lease_token)))


@router.post("/agent/jobs/{job_id}/{attempt_id}/started")
def started(job_id: str, attempt_id: str, body: Lease, authorization: str | None = Header(default=None)):
    return run(lambda: no_store(svc().started(credential(authorization), job_id, attempt_id, body.lease_token)))


@router.post("/agent/jobs/{job_id}/{attempt_id}/report")
def report(job_id: str, attempt_id: str, body: Terminal, authorization: str | None = Header(default=None)):
    data = body.model_dump(mode="json", exclude={"lease_token"})
    return run(lambda: no_store(svc().report(
        credential(authorization), job_id, attempt_id, body.lease_token, data,
    )))
