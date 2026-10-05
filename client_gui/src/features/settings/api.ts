// Environment Settings API adapter (flowgate.v02.0020). Reuses the `@shared/api` transport.
// The routes answer {"detail": {"code"}}; a known code is shown as gui.settings.errors.<code>.

import { deleteRequest, extractApiErrorMessage, getRequest, patchRequest, postRequest, putRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import i18n from '@shared/i18n'
import type {
  AiMode,
  CreateUserInput,
  MemberCandidate,
  PolicyResource,
  ProjectAccess,
  ProjectCreatePolicy,
  ResourceType,
  SecurityPolicy,
  SecurityResponse,
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

export class SettingsRequestError extends Error {
  constructor(message: string, readonly code: string | null) {
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
    throw new SettingsRequestError(message, code)
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

export const createSystemUser = (input: CreateUserInput) =>
  call(() => postRequest<UserCredentialResult>(`${API}/system/users`, input), 'save_failed')

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

// ── System › 보안 (시안 14) ───────────────────────────────────────────────────

export const getSecurity = () => call(() => getRequest<SecurityResponse>(`${API}/system/security`), 'load_failed')

export const saveSecurity = (patch: Partial<SecurityPolicy>) =>
  call(() => putRequest<SecurityResponse>(`${API}/system/security`, patch), 'save_failed')
