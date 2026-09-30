"""Project AI Group routes (flowgate.v02.0017 T0002 §4-§5).

Group = name/description + members + custom roles + member-role mapping + optional
Supervisor. Runtime slots are a separate sub-resource (`runtime-binding`); team ON/OFF and
the default team are `state`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_group_service as svc

router = APIRouter(tags=['AiGroups'])


class AIGroupCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    description: str | None = None


class AIGroupUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    description: str | None = None
    # Omitted = unchanged, null = clear the Supervisor, member id = designate that member.
    supervisor_member_id: str | None = None


class AIGroupStateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # Omitted/null = unchanged.
    enabled: bool | None = None
    is_default: bool | None = None


class AIGroupMemberInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider_id: str


class AIGroupRoleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    description: str | None = None
    # Evaluation criterion keys the role relies on (member fit). Empty = all criteria.
    criteria: list[str] = []


class AIGroupMemberRolesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    role_ids: list[str]


class AIGroupRuntimeBindingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    work_executor_provider_id: str | None = None
    reviewer_provider_id: str | None = None
    reworker_provider_id: str | None = None


def _error(exc: svc.AIGroupError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={
        'error': {'code': exc.code, 'message': str(exc)}})


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.AIGroupError as exc:
        return _error(exc)


_READ = 'project.settings.read'
_EDIT = 'project.settings.edit'


@router.get('/projects/{project_id}/ai-groups')
def list_ai_groups(project_id: str, user=Depends(require_permission(_READ, 'project_id'))):
    return _call(svc.list_groups, project_id)


@router.get('/projects/{project_id}/ai-groups/{ai_group_id}')
def get_ai_group(project_id: str, ai_group_id: str,
                 user=Depends(require_permission(_READ, 'project_id'))):
    return _call(svc.get_group, project_id, ai_group_id)


@router.post('/projects/{project_id}/ai-groups', status_code=201)
def create_ai_group(project_id: str, body: AIGroupCreate,
                    user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.create_group, project_id, body.model_dump())


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}')
def update_ai_group(project_id: str, ai_group_id: str, body: AIGroupUpdate,
                    user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.update_group, project_id, ai_group_id, body.model_dump(),
                 supervisor_given='supervisor_member_id' in body.model_fields_set)


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}/state')
def set_ai_group_state(project_id: str, ai_group_id: str, body: AIGroupStateInput,
                       user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.set_state, project_id, ai_group_id, body.enabled, body.is_default)


@router.delete('/projects/{project_id}/ai-groups/{ai_group_id}', status_code=204)
def delete_ai_group(project_id: str, ai_group_id: str,
                    user=Depends(require_permission(_EDIT, 'project_id'))):
    try:
        svc.delete_group(project_id, ai_group_id)
    except svc.AIGroupError as exc:
        return _error(exc)
    return Response(status_code=204)


@router.post('/projects/{project_id}/ai-groups/{ai_group_id}/members', status_code=201)
def add_ai_group_member(project_id: str, ai_group_id: str, body: AIGroupMemberInput,
                        user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.add_member, project_id, ai_group_id, body.provider_id)


@router.delete('/projects/{project_id}/ai-groups/{ai_group_id}/members/{member_id}')
def remove_ai_group_member(project_id: str, ai_group_id: str, member_id: str,
                           user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.remove_member, project_id, ai_group_id, member_id)


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}/members/{member_id}/roles')
def set_ai_group_member_roles(project_id: str, ai_group_id: str, member_id: str,
                              body: AIGroupMemberRolesInput,
                              user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.set_member_roles, project_id, ai_group_id, member_id, body.role_ids)


@router.post('/projects/{project_id}/ai-groups/{ai_group_id}/roles', status_code=201)
def create_ai_group_role(project_id: str, ai_group_id: str, body: AIGroupRoleInput,
                         user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.create_role, project_id, ai_group_id, body.model_dump())


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}/roles/{role_id}')
def update_ai_group_role(project_id: str, ai_group_id: str, role_id: str, body: AIGroupRoleInput,
                         user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.update_role, project_id, ai_group_id, role_id, body.model_dump())


@router.delete('/projects/{project_id}/ai-groups/{ai_group_id}/roles/{role_id}')
def delete_ai_group_role(project_id: str, ai_group_id: str, role_id: str,
                         user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.delete_role, project_id, ai_group_id, role_id)


@router.put('/projects/{project_id}/ai-groups/{ai_group_id}/runtime-binding')
def set_ai_group_runtime_binding(project_id: str, ai_group_id: str,
                                 body: AIGroupRuntimeBindingInput,
                                 user=Depends(require_permission(_EDIT, 'project_id'))):
    return _call(svc.set_runtime_binding, project_id, ai_group_id, body.model_dump())
