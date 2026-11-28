// Environment Settings API adapter. Reuses the `@shared/api` transport.
// The routes answer {"detail": {"code"}}; a known code is shown as gui.settings.errors.<code>.

import { deleteRequest, extractApiErrorMessage, getRequest, patchRequest, postRequest, putRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import i18n from '@shared/i18n'
import type {
  AgentEnrollment,
  AiMode,
  ChangeAnswer,
  ChangeMode,
  ChangePreview,
  ChangeRequest,
  CreateUserInput,
  MemberCandidate,
  PolicyResource,
  ProjectAccess,
  ProjectCreatePolicy,
  ProjectDirectMember,
  ProjectGitConfigView,
  ProjectMemberCandidates,
  ProjectStorageRoot,
  ProjectTeamRole,
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
  SystemUserDetail,
  SystemUsersResponse,
  Team,
  TeamDetail,
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

// ── Workspaces (mockup 11 / 12) ────────────────────────────────────────────────

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

// ── Access changes (v02test.0090): preview first, then apply with the revision ────────────
// Every change of who may do what — workspace members and defaults, teams, project roles — goes
// through the server's common change path. The same method, path and body are sent twice: first with
// `?preview=true` (nothing is written; the answer is { revision, impact }), then with
// `?revision=<that number>` (applied; the answer is { revision, result }). When someone changed
// access in between the server answers 409 revision_conflict and the screen previews again.

/** The path of one access-change call in the given mode. */
function changePath(path: string, mode: ChangeMode): string {
  return `${path}${'preview' in mode ? '?preview=true' : `?revision=${encodeURIComponent(String(mode.revision))}`}`
}

export const isPreview = <T>(answer: ChangeAnswer<T>): answer is ChangePreview => 'impact' in answer

export const addMember = (id: string, userId: string, role: Exclude<WorkspaceRole, 'owner'>): ChangeRequest<WorkspaceDetail> => (mode) =>
  call(() => postRequest<ChangeAnswer<WorkspaceDetail>>(changePath(`${ws(id)}/members`, mode), { user_id: userId, role }), 'save_failed')

export const changeMemberRole = (id: string, userId: string, role: Exclude<WorkspaceRole, 'owner'>): ChangeRequest<WorkspaceDetail> => (mode) =>
  call(() => patchRequest<ChangeAnswer<WorkspaceDetail>>(changePath(`${ws(id)}/members/${encodeURIComponent(userId)}`, mode), { role }), 'save_failed')

export const removeMember = (id: string, userId: string): ChangeRequest<WorkspaceDetail> => (mode) =>
  call(() => deleteRequest<ChangeAnswer<WorkspaceDetail>>(changePath(`${ws(id)}/members/${encodeURIComponent(userId)}`, mode)), 'save_failed')

export const setDefaultAccess = (id: string, defaults: Partial<Record<WorkspaceRole, ProjectAccess>>): ChangeRequest<WorkspaceDetail> => (mode) =>
  call(() => putRequest<ChangeAnswer<WorkspaceDetail>>(changePath(`${ws(id)}/access/defaults`, mode), defaults), 'save_failed')

export const setProjectAccess = (id: string, projectId: string, userId: string, access: ProjectAccess | 'default'): ChangeRequest<WorkspaceDetail> => (mode) =>
  call(
    () => putRequest<ChangeAnswer<WorkspaceDetail>>(changePath(`${ws(id)}/access/exceptions`, mode), { project_id: projectId, user_id: userId, access }),
    'save_failed',
  )

// ── Teams (설정 › 팀) ─────────────────────────────────────────────────────────────
const teamsOf = (workspaceId: string) => `${ws(workspaceId)}/teams`
const team = (workspaceId: string, teamId: string) => `${teamsOf(workspaceId)}/${encodeURIComponent(teamId)}`

export const listTeams = async (workspaceId: string) =>
  (await call(() => getRequest<{ teams: Team[] }>(teamsOf(workspaceId)), 'load_failed')).teams

export const getTeam = (workspaceId: string, teamId: string) =>
  call(() => getRequest<TeamDetail>(team(workspaceId, teamId)), 'load_failed')

export const createTeam = (workspaceId: string, name: string): ChangeRequest<Team> => (mode) =>
  call(() => postRequest<ChangeAnswer<Team>>(changePath(teamsOf(workspaceId), mode), { name }), 'save_failed')

export const renameTeam = (workspaceId: string, teamId: string, name: string): ChangeRequest<Team> => (mode) =>
  call(() => patchRequest<ChangeAnswer<Team>>(changePath(team(workspaceId, teamId), mode), { name }), 'save_failed')

export const disableTeam = (workspaceId: string, teamId: string): ChangeRequest<Team> => (mode) =>
  call(() => postRequest<ChangeAnswer<Team>>(changePath(`${team(workspaceId, teamId)}/disable`, mode), {}), 'save_failed')

export const addTeamMember = (workspaceId: string, teamId: string, userId: string): ChangeRequest<unknown> => (mode) =>
  call(() => putRequest<ChangeAnswer<unknown>>(changePath(`${team(workspaceId, teamId)}/members/${encodeURIComponent(userId)}`, mode), {}), 'save_failed')

export const removeTeamMember = (workspaceId: string, teamId: string, userId: string): ChangeRequest<unknown> => (mode) =>
  call(() => deleteRequest<ChangeAnswer<unknown>>(changePath(`${team(workspaceId, teamId)}/members/${encodeURIComponent(userId)}`, mode)), 'save_failed')

// ── Project members (설정 › 프로젝트 › 멤버): the /rbac routes ───────────────────────
const rbacProject = (projectId: string) => `/rbac/projects/${encodeURIComponent(projectId)}`

export const listProjectMembers = (projectId: string) =>
  call(() => getRequest<ProjectDirectMember[]>(`${rbacProject(projectId)}/members`), 'load_failed')

export const listProjectTeamRoles = async (projectId: string) =>
  (await call(() => getRequest<{ teams: ProjectTeamRole[] }>(`${rbacProject(projectId)}/team-roles`), 'load_failed')).teams

/** Only for someone who may manage the project's members (403 otherwise). */
export const getProjectMemberCandidates = (projectId: string) =>
  call(() => getRequest<ProjectMemberCandidates>(`${rbacProject(projectId)}/member-candidates`), 'load_failed')

export const assignProjectUserRole = (projectId: string, userId: string, roleId: string): ChangeRequest<unknown> => (mode) =>
  call(
    () => postRequest<ChangeAnswer<unknown>>(changePath(`/rbac/users/${encodeURIComponent(userId)}/roles`, mode), { project_id: projectId, role_id: roleId }),
    'save_failed',
  )

export const revokeProjectUserRole = (projectId: string, userId: string): ChangeRequest<unknown> => (mode) =>
  call(
    () => deleteRequest<ChangeAnswer<unknown>>(changePath(`/rbac/users/${encodeURIComponent(userId)}/roles/${encodeURIComponent(projectId)}`, mode)),
    'save_failed',
  )

export const assignProjectTeamRole = (projectId: string, teamId: string, roleId: string): ChangeRequest<unknown> => (mode) =>
  call(
    () => putRequest<ChangeAnswer<unknown>>(changePath(`${rbacProject(projectId)}/team-roles/${encodeURIComponent(teamId)}`, mode), { role_id: roleId }),
    'save_failed',
  )

export const revokeProjectTeamRole = (projectId: string, teamId: string): ChangeRequest<unknown> => (mode) =>
  call(() => deleteRequest<ChangeAnswer<unknown>>(changePath(`${rbacProject(projectId)}/team-roles/${encodeURIComponent(teamId)}`, mode)), 'save_failed')

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

// ── System › "사용자" (mockup 13) ─────────────────────────────────────────────────

export const listSystemUsers = () =>
  call(() => getRequest<SystemUsersResponse>(`${API}/system/users`), 'load_failed')

/** One person with the teams they are in and the final role on each project (v02test.0090). */
export const getSystemUser = (id: string) =>
  call(() => getRequest<SystemUserDetail>(user(id)), 'load_failed')

/** Only the four fields the server reads; workspace_id is never sent. */
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

// ── System › "일반" (flowgate.v02.0076 T#3, D §2.5) ─────────────────────────────
// Two read contracts — the Root of GET /system/storage-root (never /system/settings/storage-root)
// and the rows of GET /system/settings — and separate saves on PATCH /system/settings: the
// Scratch value/unit pair together in one request (the server writes it in one transaction), and
// attach_read_max_mb alone. Nothing else rides along; storage_root is never in `updates`.
// (v02test.0096 F96-11: ai_repeat_count_max is no longer edited from this screen.)

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

/** AI attachment read ceiling in whole MB (attach_read_max_mb, 1~64), alone. */
export const saveAttachReadMaxMb = async (value: string) =>
  (await call(
    () => patchRequest<{ updated?: SystemSettingRow[] }>(`${API}/system/settings`, { updates: { attach_read_max_mb: value } }),
    'save_failed',
  )).updated

// ── System › System Agent ─────────────────────
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
      remote_workdir: input.remote_workdir,
      workspace_cleanup_enabled: input.workspace_cleanup_enabled,
      workspace_cleanup_retention_hours: input.workspace_cleanup_retention_hours,
      max_concurrent_jobs: input.max_concurrent_jobs,
    }),
    'save_failed',
  )

export const updateSystemAgent = (id: string, changes: SystemAgentChanges) => {
  const body: SystemAgentChanges = {}
  if (changes.max_concurrent_jobs !== undefined) body.max_concurrent_jobs = changes.max_concurrent_jobs
  if (changes.name !== undefined) body.name = changes.name
  if (changes.enabled !== undefined) body.enabled = changes.enabled
  if (changes.remote_workdir !== undefined) body.remote_workdir = changes.remote_workdir
  if (changes.workspace_cleanup_enabled !== undefined) body.workspace_cleanup_enabled = changes.workspace_cleanup_enabled
  if (changes.workspace_cleanup_retention_hours !== undefined) body.workspace_cleanup_retention_hours = changes.workspace_cleanup_retention_hours
  return call(() => patchRequest<SystemAgent>(agent(id), body), 'save_failed')
}

export const issueAgentEnrollment = (id: string) =>
  call(() => postRequest<AgentEnrollment>(`${agent(id)}/enrollments`, {}), 'action_failed')

export const revokeAgentCredential = (id: string) =>
  call(() => postRequest<SystemAgent>(`${agent(id)}/credential/revoke`, {}), 'action_failed')

export const deleteSystemAgent = (id: string) =>
  call(() => deleteRequest<unknown>(agent(id)), 'action_failed')

// Personal management keeps the same DTOs; ownership is assigned by the server.
const personalAgent = (id: string) => `${API}/me/agents/${encodeURIComponent(id)}`
export const listPersonalAgents = async () =>
  (await call(() => getRequest<{ agents?: SystemAgent[] }>(`${API}/me/agents`), 'load_failed')).agents
export const createPersonalAgent = (input: SystemAgentCreate) =>
  call(() => postRequest<SystemAgent>(`${API}/me/agents`, {
    name: input.name, remote_workdir: input.remote_workdir,
    workspace_cleanup_enabled: input.workspace_cleanup_enabled,
    workspace_cleanup_retention_hours: input.workspace_cleanup_retention_hours,
    max_concurrent_jobs: input.max_concurrent_jobs,
  }), 'save_failed')
export const updatePersonalAgent = (id: string, changes: SystemAgentChanges) => {
  const body: SystemAgentChanges = {}
  if (changes.max_concurrent_jobs !== undefined) body.max_concurrent_jobs = changes.max_concurrent_jobs
  if (changes.name !== undefined) body.name = changes.name
  if (changes.enabled !== undefined) body.enabled = changes.enabled
  if (changes.remote_workdir !== undefined) body.remote_workdir = changes.remote_workdir
  if (changes.workspace_cleanup_enabled !== undefined) body.workspace_cleanup_enabled = changes.workspace_cleanup_enabled
  if (changes.workspace_cleanup_retention_hours !== undefined) body.workspace_cleanup_retention_hours = changes.workspace_cleanup_retention_hours
  return call(() => patchRequest<SystemAgent>(personalAgent(id), body), 'save_failed')
}
export const issuePersonalAgentEnrollment = (id: string) =>
  call(() => postRequest<AgentEnrollment>(`${personalAgent(id)}/enrollments`, {}), 'action_failed')
export const deletePersonalAgent = (id: string) =>
  call(() => deleteRequest<unknown>(personalAgent(id)), 'action_failed')

// ── Project lookup · "시스템" › "전체 프로젝트" (flowgate.v02.0076 T#5, D §2.3/§2.4/§2.8) ──────
// Three existing GETs and nothing else: the project list (status=all; the "활성" filter is the
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

// ── System › "보안" (mockup 14) ───────────────────────────────────────────────────

export const getSecurity = () => call(() => getRequest<SecurityResponse>(`${API}/system/security`), 'load_failed')

export const saveSecurity = (patch: Partial<SecurityPolicy>) =>
  call(() => putRequest<SecurityResponse>(`${API}/system/security`, patch), 'save_failed')
