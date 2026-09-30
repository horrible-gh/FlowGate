"""Project AI Group CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_group_service as svc

router = APIRouter(tags=['AiGroups'])


class AIGroupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    description: str | None = None
    work_executor_provider_id: str
    reviewer_provider_id: str | None = None
    reworker_provider_id: str | None = None


def _error(exc: svc.AIGroupError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={
        'error': {'code': exc.code, 'message': str(exc)}})


@router.get('/projects/{project_id}/ai-groups')
def list_ai_groups(project_id: str, user=Depends(require_permission('project.settings.read', 'project_id'))):
    try:
        return svc.list_groups(project_id)
    except svc.AIGroupError as exc:
        return _error(exc)


@router.get('/projects/{project_id}/ai-groups/{ai_group_id}')
def get_ai_group(project_id: str, ai_group_id: str,
                 user=Depends(require_permission('project.settings.read', 'project_id'))):
    try:
        return svc.get_group(project_id, ai_group_id)
    except svc.AIGroupError as exc:
        return _error(exc)


@router.post('/projects/{project_id}/ai-groups', status_code=201)
def create_ai_group(project_id: str, body: AIGroupInput,
                    user=Depends(require_permission('project.settings.edit', 'project_id'))):
    try:
        return svc.create_group(project_id, body.model_dump())
    except svc.AIGroupError as exc:
        return _error(exc)


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}')
def update_ai_group(project_id: str, ai_group_id: str, body: AIGroupInput,
                    user=Depends(require_permission('project.settings.edit', 'project_id'))):
    try:
        return svc.update_group(project_id, ai_group_id, body.model_dump())
    except svc.AIGroupError as exc:
        return _error(exc)


@router.delete('/projects/{project_id}/ai-groups/{ai_group_id}', status_code=204)
def delete_ai_group(project_id: str, ai_group_id: str,
                    user=Depends(require_permission('project.settings.edit', 'project_id'))):
    try:
        svc.delete_group(project_id, ai_group_id)
    except svc.AIGroupError as exc:
        return _error(exc)
    return Response(status_code=204)
