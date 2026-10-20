// Environment Settings API adapter (flowgate.v02.0020). Reuses the `@shared/api` transport.
// The routes answer {"detail": {"code"}}; a known code is shown as gui.settings.errors.<code>.

import { deleteRequest, extractApiErrorMessage, getRequest, patchRequest, postRequest, putRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import i18n from '@shared/i18n'
import type {
  AgentEnrollment,
  AiMode,
  CreateUserInput,
  MemberCandidate,
  PolicyResource,
  ProjectAccess,
  ProjectCreatePolicy,
  ProjectGitConfigView,
  ProjectStorageRoot,
  ResourceType,
  SecurityPolicy,
  ScratchUnit,
  SecurityResponse,
  SystemAgent,
  SystemAgentChanges,
  SystemAgentCreate,
  SystemSettingRow,
  SystemStorageRoot,
  SystemUser,
  SystemUsersResponse,
  UserCredentialResult,
  WorkspaceDetail,
  WorkspaceListResponse,
  WorkspaceRole,
} from './types'

const API = '/api/v1'
const ws = (id: string) => `${API}/workspaces/${encodeURIComponent(id)}`
const user = (id: string) => `${API}/system/users/${encodeURIComponent(id)}`
const agent = (id: string) => `${API}/system/agents/${encodeURIComponent(id)}`
const project = (id: string) => `${API}/projects/${encodeURIComponent(id)}`

export class SettingsRequestError extends Error {
  /** `status` is the HTTP status when there was a response (401 / 403 / 500 …), else null. */
  constructor(message: string, readonly code: string | null, readonly status: number | null = null) {
    super(message)
    this.name = 'SettingsRequestError'
  }
}

function codeOf(error: unknown): string | null {
  const { code, status } = normalizeApiError(error)
  // require_permission answers a bare 403 "Forbidden" without a code.
  return code ?? (status === 403 ? 'forbidden' : null)
}

async function call<T>(request: () => Promise<{ data: T }>, fallbackKey: string): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    const code = codeOf(error)
    const key = code ? `gui.settings.errors.${code}` : ''
    const fallback = String(i18n.global.t(`gui.settings.errors.${fallbackKey}`))
    const message = key && i18n.global.te(key) ? String(i18n.global.t(key)) : extractApiErrorMessage(error, fallback)
    throw new SettingsRequestError(message, code, normalizeApiError(error).status)
  }
}

// ── Workspaces (시안 11 / 12) ────────────────────────────────────────────────

export const listWorkspaces = () =>
  call(() => getRequest<WorkspaceListResponse>(`${API}/workspaces`), 'load_failed')

export const createWorkspace = (name: string, ownerUserId: string | null) =>
  call(() => postRequest<WorkspaceDetail>(`${API}/workspaces`, { name, owner_user_id: ownerUserId }), 'save_failed')

export const getWorkspace = (id: string) => call(() => getRequest<WorkspaceDetail>(ws(id)), 'load_failed')

export const updateWorkspace = (id: string, patch: { name?: string; status?: 'active' | 'disabled' }) =>
  call(() => patchRequest<WorkspaceDetail>(ws(id), patch), 'save_failed')

export const deleteWorkspace = (id: string) =>
  call(() => deleteRequest<{ ok: boolean }>(ws(id)), 'save_failed')

export const listMemberCandidates = async (id: string, q: string) =>
  (await call(() => getRequest<{ users: MemberCandidate[] }>(`${ws(id)}/member-candidates`, { q }), 'load_failed')).users

export const addMember = (id: string, userId: string, role: Exclude<WorkspaceRole, 'owner'>) =>
  call(() => postRequest<WorkspaceDetail>(`${ws(id)}/members`, { user_id: userId, role }), 'save_failed')

export const changeMemberRole = (id: string, userId: string, role: Exclude<WorkspaceRole, 'owner'>) =>
  call(() => patchRequest<WorkspaceDetail>(`${ws(id)}/members/${encodeURIComponent(userId)}`, { role }), 'save_failed')

export const removeMember = (id: string, userId: string) =>
  call(() => deleteRequest<WorkspaceDetail>(`${ws(id)}/members/${encodeURIComponent(userId)}`), 'save_failed')

export const setDefaultAccess = (id: string, defaults: Partial<Record<WorkspaceRole, ProjectAccess>>) =>
  call(() => putRequest<WorkspaceDetail>(`${ws(id)}/access/defaults`, defaults), 'save_failed')

export const setProjectAccess = (id: string, projectId: string, userId: string, access: ProjectAccess | 'default') =>
  call(
    () => putRequest<WorkspaceDetail>(`${ws(id)}/access/exceptions`, { project_id: projectId, user_id: userId, access }),
    'save_failed',
  )

export const setWorkspacePolicy = (id: string, patch: { ai_mode?: AiMode; project_create?: ProjectCreatePolicy }) =>
  call(() => putRequest<WorkspaceDetail>(`${ws(id)}/policy`, patch), 'save_failed')

export const listPolicyResources = (id: string) =>
  call(() => getRequest<{ providers: PolicyResource[]; agents: PolicyResource[] }>(`${ws(id)}/policy/resources`), 'load_failed')

export const allowResource = (id: string, resourceType: ResourceType, resourceId: string) =>
  call(
    () => postRequest<WorkspaceDetail>(`${ws(id)}/policy/allowlist`, { resource_type: resourceType, resource_id: resourceId }),
    'save_failed',
  )

export const revokeResource = (id: string, resourceType: ResourceType, resourceId: string) =>
  call(
    () => deleteRequest<WorkspaceDetail>(
      `${ws(id)}/policy/allowlist/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`,
    ),
    'save_failed',
  )

// ── System › 사용자 (시안 13) ─────────────────────────────────────────────────

export const listSystemUsers = () =>
  call(() => getRequest<SystemUsersResponse>(`${API}/system/users`), 'load_failed')

/** Only the four fields the server reads; workspace_id is never sent (flowgate.v02.0076 T#3). */
export const createSystemUser = (input: CreateUserInput) =>
  call(
    () => postRequest<UserCredentialResult>(`${API}/system/users`, {
      name: input.name, email: input.email, start: input.start, is_admin: input.is_admin,
    }),
    'save_failed',
  )

export const setSystemAdmin = (id: string, isAdmin: boolean) =>
  call(() => putRequest<SystemUser>(`${user(id)}/admin`, { is_admin: isAdmin }), 'save_failed')

export const disableSystemUser = (id: string) =>
  call(() => postRequest<SystemUser>(`${user(id)}/disable`, {}), 'save_failed')

export const enableSystemUser = (id: string) =>
  call(() => postRequest<SystemUser>(`${user(id)}/enable`, {}), 'save_failed')

export const resendInvite = (id: string) =>
  call(() => postRequest<UserCredentialResult>(`${user(id)}/invite`, {}), 'save_failed')

export const resetPassword = (id: string) =>
  call(() => postRequest<UserCredentialResult>(`${user(id)}/password-reset`, {}), 'save_failed')

export const resetTfa = (id: string) =>
  call(() => postRequest<SystemUser>(`${user(id)}/tfa-reset`, {}), 'save_failed')

// ── System › 일반 (flowgate.v02.0076 T#3, D §2.5) ─────────────────────────────
// Two read contracts — the Root of GET /system/storage-root (never /system/settings/storage-root)
// and the rows of GET /system/settings — and two separate saves on PATCH /system/settings: the
// Scratch value/unit pair together in one request (the server writes it in one transaction), and
// ai_repeat_count_max alone. Nothing else rides along; storage_root is never in `updates`.

export const getSystemStorageRoot = () =>
  call(() => getRequest<SystemStorageRoot>(`${API}/system/storage-root`), 'load_failed')

export const getSystemSettings = async () =>
  (await call(() => getRequest<{ settings?: SystemSettingRow[] }>(`${API}/system/settings`), 'load_failed')).settings

/** Both keys as the strings the API stores — never converted to minutes. */
export const saveScratchRetention = async (value: string, unit: ScratchUnit) =>
  (await call(
    () => patchRequest<{ updated?: SystemSettingRow[] }>(`${API}/system/settings`, {
      updates: { scratch_ttl_value: value, scratch_ttl_unit: unit },
    }),
    'save_failed',
  )).updated

export const saveRepeatCountMax = async (value: string) =>
  (await call(
    () => patchRequest<{ updated?: SystemSettingRow[] }>(`${API}/system/settings`, { updates: { ai_repeat_count_max: value } }),
    'save_failed',
  )).updated

// ── System › 시스템 Agent (flowgate.v02.0076 T#4, D §2.6) ─────────────────────
// Only the /system/agents management family of api/v1/agent_routes.py (the one the old
// client/src/settings AgentsView used). The Agent runtime routes /agent/enrollments and
// /agent/heartbeat are never called from here. Errors come as {"ok":false,"error":{"code"}} and go
// through the same code → gui.settings.errors.<code> mapping. Bodies are built field by field so
// nothing else rides along: PATCH never carries location / connection_mode, and the two actions
// without input send the route's Empty model as {} (the body itself is required).

export const listSystemAgents = async () =>
  (await call(() => getRequest<{ agents?: SystemAgent[] }>(`${API}/system/agents`), 'load_failed')).agents

export const createSystemAgent = (input: SystemAgentCreate) =>
  call(
    () => postRequest<SystemAgent>(`${API}/system/agents`, {
      name: input.name,
      enabled: input.enabled,
      location: input.location,
      connection_mode: input.connection_mode,
      local_executable_path: input.local_executable_path,
      configured_capabilities: { ...input.configured_capabilities },
    }),
    'save_failed',
  )

export const updateSystemAgent = (id: string, changes: SystemAgentChanges) => {
  const body: SystemAgentChanges = {}
  if (changes.name !== undefined) body.name = changes.name
  if (changes.enabled !== undefined) body.enabled = changes.enabled
  if (changes.local_executable_path !== undefined) body.local_executable_path = changes.local_executable_path
  if (changes.configured_capabilities !== undefined) body.configured_capabilities = { ...changes.configured_capabilities }
  return call(() => patchRequest<SystemAgent>(agent(id), body), 'save_failed')
}

export const issueAgentEnrollment = (id: string) =>
  call(() => postRequest<AgentEnrollment>(`${agent(id)}/enrollments`, {}), 'action_failed')

export const revokeAgentCredential = (id: string) =>
  call(() => postRequest<SystemAgent>(`${agent(id)}/credential/revoke`, {}), 'action_failed')

export const deleteSystemAgent = (id: string) =>
  call(() => deleteRequest<unknown>(agent(id)), 'action_failed')

// ── 프로젝트 조회 · 시스템 › 전체 프로젝트 (flowgate.v02.0076 T#5, D §2.3/§2.4/§2.8) ──────
// Three existing GETs and nothing else: the project list (status=all; the 활성 filter is the
// screen's own display rule, not a server permission filter), one project's storage Root and one
// project's Git config. No settings / paths GET, no /system/settings/storage-root, no legacy
// /projects · /list/projects, and no write of any kind (settings / paths PATCH, storage migrate,
// Git PUT / DELETE / test-connection / provision / reconnect / finalize). The answers are returned
// as sent; the views check their shape.

export const listAllProjects = async () =>
  (await call(() => getRequest<{ projects?: unknown } | null>(`${API}/projects`, { status: 'all' }), 'load_failed'))?.projects

export const getProjectStorageRoot = (id: string) =>
  call(() => getRequest<ProjectStorageRoot>(`${project(id)}/storage-root`), 'load_failed')

export const getProjectGitConfig = (id: string) =>
  call(() => getRequest<ProjectGitConfigView>(`${project(id)}/git/config`), 'load_failed')

// ── System › 보안 (시안 14) ───────────────────────────────────────────────────

export const getSecurity = () => call(() => getRequest<SecurityResponse>(`${API}/system/security`), 'load_failed')

export const saveSecurity = (patch: Partial<SecurityPolicy>) =>
  call(() => putRequest<SecurityResponse>(`${API}/system/security`, patch), 'save_failed')
