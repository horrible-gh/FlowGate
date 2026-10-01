"""AI Group validation, CRUD and the canonical nested read model (flowgate.v02.0017 T0002 §4).

An AI Group is a team the user composes: a name, an optional description, members (AI
connections), user-defined roles and a many-to-many member/role mapping, plus an optional
Supervisor member. None of these is required -- an empty group is valid.

The concrete runtime slots a continuous run needs (work_executor / reviewer / reworker) are a
separate layer (`runtime_binding`). They only pre-fill the Start dialog; the run itself keeps
the concrete providers it was started with and records the group id as provenance only, so
editing a group never changes a run that already started.

Groups belong to no project (CH0006 (4)): there is one list for the whole system, built from the
system AI every project can run. A team can be switched OFF (not selectable at Start) and one
team can be the default (pre-selected at Start). A member's fit is the mean, over its roles, of its latest
evaluation scores on the criteria each role relies on (all criteria when a role names none).
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid

from modules.flow_gate.db import ai_evaluations
from modules.flow_gate.db import ai_groups as repo
from modules.flow_gate.db import ai_providers
from modules.flow_gate.settings import ai_settings_service

RUNTIME_SLOTS = repo.RUNTIME_SLOTS
# Same keys as ai_evaluation_service.CRITERION_KEYS (not imported: that module pulls in the runtime).
CRITERION_KEYS = ("instruction_understanding", "task_decomposition", "reasoning", "execution",
                  "verification", "reporting")
MAX_ROLES_PER_GROUP = 50
MAX_MEMBERS_PER_GROUP = 50


class AIGroupError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        super().__init__(code)


def _name(name, *, required_code: str = 'ai_group_name_required',
          invalid_code: str = 'ai_group_validation_failed', max_len: int = 200) -> tuple[str, str, str]:
    if not isinstance(name, str) or not name.strip():
        raise AIGroupError(required_code)
    name = name.strip()
    if len(name) > max_len:
        raise AIGroupError(invalid_code)
    key = unicodedata.normalize('NFKC', name).casefold()
    if not key or len(key.encode('utf-8')) > 1024:
        raise AIGroupError(invalid_code)
    return name, key, hashlib.sha256(key.encode('utf-8')).hexdigest()


def _description(value, invalid_code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2000:
        raise AIGroupError(invalid_code)
    return value.strip() or None


def _registry_provider(provider_id: str) -> dict | None:
    return ai_providers.get_row(None, provider_id)


def _available() -> dict[str, dict]:
    """The AI a group may use: every enabled system AI (the chain is the same in every project)."""
    return {p['id']: p for p in ai_settings_service.resolve_global()['providers']}


def _connection(provider_id: str, available: dict, registry: dict) -> dict:
    """AI connection reference as shown inside a group. Never hides or replaces a connection:
    available = in the effective chain, unavailable = registered but off/not effective,
    broken = no longer registered anywhere."""
    if provider_id not in registry:
        registry[provider_id] = _registry_provider(provider_id)
    provider = registry[provider_id]
    if provider_id in available:
        status = 'available'
    elif provider is not None:
        status = 'unavailable'
    else:
        status = 'broken'
    return {'provider_id': provider_id,
            'provider_name': (available.get(provider_id) or provider or {}).get('name'),
            'is_available': status == 'available', 'status': status}


def _role_criteria(role: dict) -> list[str]:
    try:
        value = json.loads(role.get('criteria') or '[]')
    except (TypeError, ValueError):
        return []
    return [key for key in CRITERION_KEYS if isinstance(value, list) and key in value]


def _fit(role_ids: list[str], roles: dict[str, dict], scores: dict[str, int]) -> int | None:
    """Mean over the member's roles of its criterion scores the role relies on (0-100)."""
    values = []
    for role_id in role_ids:
        keys = _role_criteria(roles[role_id]) or list(CRITERION_KEYS)
        picked = [scores[key] for key in keys if scores.get(key) is not None]
        if picked:
            values.append(sum(picked) / len(picked))
    return round(sum(values) / len(values)) if values else None


def _status(members: list[dict], binding: dict) -> str:
    if any(not m['is_available'] for m in members) or any(
            slot is not None and not slot['is_available'] for slot in binding.values()):
        return 'attention'
    return 'empty' if not members else 'ok'


def _responses(rows: list[dict], available: dict | None = None) -> list[dict]:
    if not rows:
        return []
    available = _available() if available is None else available
    ids = [row['ai_group_id'] for row in rows]
    registry: dict[str, dict | None] = {}
    members_by_group: dict[str, list[dict]] = {gid: [] for gid in ids}
    for member in repo.list_members(ids):
        members_by_group[member['ai_group_id']].append(member)
    roles_by_group: dict[str, list[dict]] = {gid: [] for gid in ids}
    for role in repo.list_roles(ids):
        roles_by_group[role['ai_group_id']].append(role)
    roles_of_member: dict[str, list[str]] = {}
    members_of_role: dict[str, list[str]] = {}
    for mapping in repo.list_mappings(ids):
        roles_of_member.setdefault(mapping['member_id'], []).append(mapping['role_id'])
        members_of_role.setdefault(mapping['role_id'], []).append(mapping['member_id'])
    bindings_by_group: dict[str, dict[str, str]] = {gid: {} for gid in ids}
    for binding in repo.list_bindings(ids):
        bindings_by_group[binding['ai_group_id']][binding['slot']] = binding['provider_id']
    # Latest completed evaluation of each member connection (the team is built from evaluated AI).
    latest = {row['target_provider_id']: {'evaluation_id': row['evaluation_id'],
                                          'total_score': ai_evaluations.final_score(row),
                                          'finished_at': row['finished_at']}
              for row in ai_evaluations.latest_completed(
                  [m['provider_id'] for group in members_by_group.values() for m in group])}
    criterion_scores = {eid: {s['criterion_key']: s['score'] for s in rows}
                        for eid, rows in ai_evaluations.scores(
                            [item['evaluation_id'] for item in latest.values()]).items()}

    result = []
    for row in rows:
        gid = row['ai_group_id']
        role_rows = {role['role_id']: role for role in roles_by_group[gid]}
        members = []
        for member in members_by_group[gid]:
            member_roles = [rid for rid in roles_of_member.get(member['member_id'], []) if rid in role_rows]
            evaluation = latest.get(member['provider_id'])
            members.append({
                'member_id': member['member_id'],
                **_connection(member['provider_id'], available, registry),
                'role_ids': member_roles,
                'latest_evaluation': evaluation,
                'fit_score': (_fit(member_roles, role_rows, criterion_scores.get(evaluation['evaluation_id'], {}))
                              if evaluation else None),
                'created_at': member['created_at'],
            })
        member_ids = {m['member_id'] for m in members}
        roles = [{
            'role_id': role['role_id'], 'name': role['name'], 'description': role['description'],
            'criteria': _role_criteria(role), 'position': role['position'],
            'member_ids': [mid for mid in members_of_role.get(role['role_id'], []) if mid in member_ids],
        } for role in roles_by_group[gid]]
        supervisor = next((m for m in members if m['member_id'] == row.get('supervisor_member_id')), None)
        binding = {slot: (_connection(bindings_by_group[gid][slot], available, registry)
                          if slot in bindings_by_group[gid] else None) for slot in RUNTIME_SLOTS}
        result.append({
            'ai_group_id': gid,
            'name': row['name'], 'description': row['description'],
            'enabled': bool(row.get('enabled', 1)), 'is_default': row.get('default_key') is not None,
            'members': members, 'roles': roles,
            'supervisor': ({'member_id': supervisor['member_id'], 'provider_id': supervisor['provider_id'],
                            'provider_name': supervisor['provider_name'],
                            'is_available': supervisor['is_available'], 'status': supervisor['status'],
                            'latest_evaluation': supervisor['latest_evaluation']}
                           if supervisor else None),
            'runtime_binding': binding,
            'member_count': len(members), 'role_count': len(roles),
            'status': _status(members, binding),
            'created_at': row['created_at'], 'updated_at': row['updated_at'],
        })
    return result


def response(row: dict, available: dict | None = None) -> dict:
    return _responses([row], available)[0]


def list_groups() -> dict:
    return {'items': _responses(repo.list_all())}


def _group_row(ai_group_id: str) -> dict:
    row = repo.get(ai_group_id)
    if row is None:
        raise AIGroupError('ai_group_not_found', 404)
    return row


def get_group(ai_group_id: str) -> dict:
    return response(_group_row(ai_group_id))


def _validated(data: dict, own_id: str | None = None) -> dict:
    name, key, digest = _name(data.get('name'))
    description = _description(data.get('description'), 'ai_group_validation_failed')
    same = repo.get_by_name_hash(digest)
    if same is not None:
        if same['name_key'] != key:
            raise AIGroupError('ai_group_hash_collision', 500)
        if same['ai_group_id'] != own_id:
            raise AIGroupError('ai_group_name_conflict', 409)
    return {'name': name, 'name_key': key, 'name_key_hash': digest, 'description': description}


def _write_error(exc: Exception, row: dict) -> None:
    same = repo.get_by_name_hash(row['name_key_hash'])
    if same is not None:
        if same['name_key'] == row['name_key']:
            raise AIGroupError('ai_group_name_conflict', 409) from exc
        raise AIGroupError('ai_group_hash_collision', 500) from exc
    raise exc


def create_group(data: dict) -> dict:
    """Name and optional description only. No member, role or provider is required."""
    row = _validated(data)
    row.update(ai_group_id='aig_' + uuid.uuid4().hex)
    try:
        repo.create(row)
    except Exception as exc:
        _write_error(exc, row)
    return get_group(row['ai_group_id'])


def update_group(ai_group_id: str, data: dict, *, supervisor_given: bool = False) -> dict:
    _group_row(ai_group_id)
    row = _validated(data, ai_group_id)
    supervisor = data.get('supervisor_member_id')
    if supervisor_given and supervisor is not None:
        if not isinstance(supervisor, str) or repo.get_member(ai_group_id, supervisor) is None:
            raise AIGroupError('ai_group_supervisor_not_member')
    try:
        repo.update(ai_group_id, row)
    except Exception as exc:
        _write_error(exc, row)
    if supervisor_given:
        repo.set_supervisor(ai_group_id, supervisor)
    return get_group(ai_group_id)


def set_state(ai_group_id: str, enabled, is_default) -> dict:
    """Team ON/OFF and default team. An OFF team cannot be the default."""
    row = _group_row(ai_group_id)
    for value in (enabled, is_default):
        if value is not None and not isinstance(value, bool):
            raise AIGroupError('ai_group_validation_failed')
    will_be_enabled = bool(row.get('enabled', 1)) if enabled is None else enabled
    if is_default and not will_be_enabled:
        raise AIGroupError('ai_group_default_disabled')
    repo.set_state(ai_group_id, enabled=enabled, is_default=is_default)
    return get_group(ai_group_id)


def delete_group(ai_group_id: str) -> None:
    _group_row(ai_group_id)
    repo.delete(ai_group_id)


# ── Members ──────────────────────────────────────────────────────────────────

def add_member(ai_group_id: str, provider_id) -> dict:
    _group_row(ai_group_id)
    if not isinstance(provider_id, str) or not provider_id.strip() or provider_id != provider_id.strip():
        raise AIGroupError('ai_group_member_invalid')
    # Only a connection that can run now may be added. Existing members that later
    # become unavailable stay in the group and are shown as such (never auto-removed).
    if provider_id not in _available():
        raise AIGroupError('ai_group_provider_unavailable' if _registry_provider(provider_id)
                           else 'ai_group_provider_not_found')
    if repo.get_member_by_provider(ai_group_id, provider_id) is not None:
        raise AIGroupError('ai_group_member_exists', 409)
    if len(repo.list_members([ai_group_id])) >= MAX_MEMBERS_PER_GROUP:
        raise AIGroupError('ai_group_member_limit')
    try:
        repo.add_member(ai_group_id, 'aigm_' + uuid.uuid4().hex, provider_id)
    except Exception as exc:
        if repo.get_member_by_provider(ai_group_id, provider_id) is not None:
            raise AIGroupError('ai_group_member_exists', 409) from exc
        raise
    return get_group(ai_group_id)


def remove_member(ai_group_id: str, member_id: str) -> dict:
    _group_row(ai_group_id)
    member = repo.get_member(ai_group_id, member_id)
    if member is None:
        raise AIGroupError('ai_group_member_not_found', 404)
    repo.remove_member(ai_group_id, member_id, member['provider_id'])
    return get_group(ai_group_id)


def set_member_roles(ai_group_id: str, member_id: str, role_ids) -> dict:
    _group_row(ai_group_id)
    if repo.get_member(ai_group_id, member_id) is None:
        raise AIGroupError('ai_group_member_not_found', 404)
    if not isinstance(role_ids, list) or not all(isinstance(r, str) for r in role_ids):
        raise AIGroupError('ai_group_validation_failed')
    known = {role['role_id'] for role in repo.list_roles([ai_group_id])}
    unique = list(dict.fromkeys(role_ids))
    if any(role_id not in known for role_id in unique):
        raise AIGroupError('ai_group_role_not_found', 404)
    repo.set_member_roles(ai_group_id, member_id, unique)
    return get_group(ai_group_id)


# ── Custom roles ─────────────────────────────────────────────────────────────

def _role_fields(ai_group_id: str, data: dict, own_id: str | None = None) -> dict:
    name, key, digest = _name(data.get('name'), required_code='ai_group_role_name_required',
                              invalid_code='ai_group_role_invalid', max_len=100)
    description = _description(data.get('description'), 'ai_group_role_invalid')
    criteria = data.get('criteria') or []
    if not isinstance(criteria, list) or any(key not in CRITERION_KEYS for key in criteria):
        raise AIGroupError('ai_group_role_invalid')
    same = repo.get_role_by_name_hash(ai_group_id, digest)
    if same is not None and same['role_id'] != own_id:
        raise AIGroupError('ai_group_role_name_conflict', 409)
    return {'name': name, 'name_key': key, 'name_key_hash': digest, 'description': description,
            'criteria': json.dumps([k for k in CRITERION_KEYS if k in criteria]) if criteria else None}


def create_role(ai_group_id: str, data: dict) -> dict:
    _group_row(ai_group_id)
    row = _role_fields(ai_group_id, data)
    if len(repo.list_roles([ai_group_id])) >= MAX_ROLES_PER_GROUP:
        raise AIGroupError('ai_group_role_limit')
    row.update(role_id='aigr_' + uuid.uuid4().hex, ai_group_id=ai_group_id,
               position=repo.next_role_position(ai_group_id))
    try:
        repo.create_role(row)
    except Exception as exc:
        if repo.get_role_by_name_hash(ai_group_id, row['name_key_hash']) is not None:
            raise AIGroupError('ai_group_role_name_conflict', 409) from exc
        raise
    return get_group(ai_group_id)


def update_role(ai_group_id: str, role_id: str, data: dict) -> dict:
    _group_row(ai_group_id)
    if repo.get_role(ai_group_id, role_id) is None:
        raise AIGroupError('ai_group_role_not_found', 404)
    row = _role_fields(ai_group_id, data, role_id)
    try:
        repo.update_role(ai_group_id, role_id, row)
    except Exception as exc:
        same = repo.get_role_by_name_hash(ai_group_id, row['name_key_hash'])
        if same is not None and same['role_id'] != role_id:
            raise AIGroupError('ai_group_role_name_conflict', 409) from exc
        raise
    return get_group(ai_group_id)


def delete_role(ai_group_id: str, role_id: str) -> dict:
    _group_row(ai_group_id)
    if repo.get_role(ai_group_id, role_id) is None:
        raise AIGroupError('ai_group_role_not_found', 404)
    repo.delete_role(ai_group_id, role_id)
    return get_group(ai_group_id)


# ── Runtime slot binding ──────────────────────────────────────────────────────

def set_runtime_binding(ai_group_id: str, data: dict) -> dict:
    """Bind runtime slots to group members. Separate from team roles: a slot is what a
    continuous run executes with, a role is how the user describes the team."""
    _group_row(ai_group_id)
    member_providers = {m['provider_id'] for m in repo.list_members([ai_group_id])}
    available = _available()
    bindings: dict[str, str | None] = {}
    for slot in RUNTIME_SLOTS:
        value = data.get(slot + '_provider_id')
        if value is None or value == '':
            bindings[slot] = None
            continue
        if not isinstance(value, str) or value != value.strip():
            raise AIGroupError('ai_group_invalid_binding')
        if value not in member_providers:
            raise AIGroupError('ai_group_binding_not_member')
        if value not in available:
            raise AIGroupError('ai_group_provider_unavailable')
        bindings[slot] = value
    if bindings['work_executor'] is None and (bindings['reviewer'] or bindings['reworker']):
        raise AIGroupError('ai_group_work_executor_required')
    repo.replace_bindings(ai_group_id, bindings)
    return get_group(ai_group_id)


def validate_start(ai_group_id: str) -> None:
    # Provenance only. The concrete provider fields of the start request remain
    # authoritative, and a group edit after start never reaches the run.
    row = repo.get(ai_group_id)
    if row is None:
        raise AIGroupError('ai_group_not_found', 409)
    if not row.get('enabled', 1):
        raise AIGroupError('ai_group_disabled', 409)
