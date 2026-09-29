"""AI Group validation, CRUD, and canonical nested read model."""
from __future__ import annotations

import hashlib
import unicodedata
import uuid

from modules.flow_gate.db import ai_groups as repo
from modules.flow_gate.db import ai_providers, projects
from modules.flow_gate.settings import ai_settings_service


class AIGroupError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        super().__init__(code)


def _project(project_id: str) -> None:
    if projects.get_by_id(project_id) is None:
        raise AIGroupError('project_not_found', 404)


def _name(name: str) -> tuple[str, str, str]:
    if not isinstance(name, str) or not name.strip():
        raise AIGroupError('ai_group_name_required')
    name = name.strip()
    if len(name) > 200:
        raise AIGroupError('ai_group_validation_failed')
    key = unicodedata.normalize('NFKC', name).casefold()
    if not key or len(key.encode('utf-8')) > 1024:
        raise AIGroupError('ai_group_validation_failed')
    return name, key, hashlib.sha256(key.encode('utf-8')).hexdigest()


def _registry_provider(project_id: str, provider_id: str) -> dict | None:
    return (ai_providers.get_row(project_id, provider_id)
            or ai_providers.get_row(None, provider_id))


def _available(project_id: str) -> dict[str, dict]:
    return {p['id']: p for p in ai_settings_service.resolve_effective(project_id)['providers']}


def _role(project_id: str, provider_id: str | None, available: dict) -> dict | None:
    if provider_id is None:
        return None
    provider = _registry_provider(project_id, provider_id)
    return {'provider_id': provider_id,
            'provider_name': provider['name'] if provider else None,
            'is_available': provider_id in available}


def response(row: dict, available: dict | None = None) -> dict:
    project_id = row['project_id']
    available = _available(project_id) if available is None else available
    return {
        'ai_group_id': row['ai_group_id'], 'project_id': project_id,
        'name': row['name'], 'description': row['description'],
        'work_executor': _role(project_id, row['work_executor_provider_id'], available),
        'reviewer': _role(project_id, row['reviewer_provider_id'], available),
        'reworker': _role(project_id, row['reworker_provider_id'], available),
        'created_at': row['created_at'], 'updated_at': row['updated_at'],
    }


def list_groups(project_id: str) -> dict:
    _project(project_id)
    available = _available(project_id)
    return {'items': [response(row, available) for row in repo.list_for_project(project_id)]}


def get_group(project_id: str, ai_group_id: str) -> dict:
    _project(project_id)
    row = repo.get(project_id, ai_group_id)
    if row is None:
        raise AIGroupError('ai_group_not_found', 404)
    return response(row)


def _validated(project_id: str, data: dict, own_id: str | None = None) -> dict:
    _project(project_id)
    name, key, digest = _name(data.get('name'))
    description = data.get('description')
    if description is not None and (not isinstance(description, str) or len(description) > 2000):
        raise AIGroupError('ai_group_validation_failed')
    same = repo.get_by_name_hash(project_id, digest)
    if same is not None:
        if same['name_key'] != key:
            raise AIGroupError('ai_group_hash_collision', 500)
        if same['ai_group_id'] != own_id:
            raise AIGroupError('ai_group_name_conflict', 409)
    available = _available(project_id)
    roles = {}
    for field in ('work_executor_provider_id', 'reviewer_provider_id', 'reworker_provider_id'):
        value = data.get(field)
        if value is None and field != 'work_executor_provider_id':
            roles[field] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise AIGroupError('ai_group_work_executor_required' if field == 'work_executor_provider_id'
                               else 'ai_group_invalid_role')
        if value != value.strip():
            raise AIGroupError('ai_group_invalid_role')
        if value not in available:
            raise AIGroupError('ai_group_provider_unavailable' if _registry_provider(project_id, value)
                               else 'ai_group_provider_not_found')
        roles[field] = value
    return {'name': name, 'name_key': key, 'name_key_hash': digest,
            'description': description, **roles}


def _write_error(exc: Exception, project_id: str, row: dict) -> None:
    same = repo.get_by_name_hash(project_id, row['name_key_hash'])
    if same is not None:
        if same['name_key'] == row['name_key']:
            raise AIGroupError('ai_group_name_conflict', 409) from exc
        raise AIGroupError('ai_group_hash_collision', 500) from exc
    raise exc


def create_group(project_id: str, data: dict) -> dict:
    row = _validated(project_id, data)
    row.update(project_id=project_id, ai_group_id='aig_' + uuid.uuid4().hex)
    try:
        repo.create(row)
    except Exception as exc:
        _write_error(exc, project_id, row)
    return get_group(project_id, row['ai_group_id'])


def update_group(project_id: str, ai_group_id: str, data: dict) -> dict:
    _project(project_id)
    if repo.get(project_id, ai_group_id) is None:
        raise AIGroupError('ai_group_not_found', 404)
    row = _validated(project_id, data, ai_group_id)
    try:
        repo.update(project_id, ai_group_id, row)
    except Exception as exc:
        _write_error(exc, project_id, row)
    return get_group(project_id, ai_group_id)


def delete_group(project_id: str, ai_group_id: str) -> None:
    _project(project_id)
    if repo.get(project_id, ai_group_id) is None:
        raise AIGroupError('ai_group_not_found', 404)
    repo.delete(project_id, ai_group_id)


def validate_start(project_id: str, ai_group_id: str) -> None:
    # Preset existence only. Concrete provider fields remain authoritative.
    if repo.get(project_id, ai_group_id) is None:
        raise AIGroupError('ai_group_not_found', 409)
