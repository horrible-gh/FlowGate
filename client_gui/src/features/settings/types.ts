// Environment Settings (환경 설정) domain types — mirrors the server's
// /api/v1/workspaces and /api/v1/system/{users,security} payloads (flowgate.v02.0020).

export type WorkspaceRole = 'owner' | 'admin' | 'member'
export type ProjectAccess = 'admin' | 'dev' | 'review' | 'none'
export type UserState = 'active' | 'invited' | 'disabled'
export type WorkspaceStatus = 'active' | 'disabled'
export type AiMode = 'all' | 'allow'
export type ProjectCreatePolicy = 'owner' | 'owner_admin' | 'all'
export type ResourceType = 'provider' | 'agent'

export interface WorkspaceOwner {
  user_id: string
  username: string
  email: string | null
}

export interface WorkspaceSummary {
  workspace_id: string
  name: string
  status: WorkspaceStatus
  is_default: boolean
  member_count: number
  project_count: number
  ai_policy_mode: 'off' | 'allowlist'
  owner: WorkspaceOwner | null
  my_role: WorkspaceRole | null
  can_manage: boolean
  can_admin: boolean
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceSummary[]
  can_create: boolean
}

export interface WorkspaceMember {
  user_id: string
  username: string
  email: string | null
  role: WorkspaceRole
  state: UserState
  is_system_admin: boolean
  last_login_at: string | null
  joined_at: string | null
  project_count: number
}

export interface WorkspaceProject {
  project_id: string
  project_name: string
  is_active: boolean
}

export interface AccessException {
  project_id: string
  user_id: string
  access: ProjectAccess
}

export interface AllowEntry {
  resource_type: ResourceType
  resource_id: string
  name: string
  available: boolean
  status: string
  source: string
}

export interface WorkspacePolicy {
  ai_mode: AiMode
  allowlist: AllowEntry[]
  project_create: ProjectCreatePolicy
}

export interface WorkspaceDetail extends WorkspaceSummary {
  members: WorkspaceMember[]
  projects: WorkspaceProject[]
  active_project_count: number
  access: {
    defaults: Record<WorkspaceRole, ProjectAccess>
    exceptions: AccessException[]
  }
  policy: WorkspacePolicy
}

export interface MemberCandidate {
  user_id: string
  username: string
  email: string | null
  state: UserState
}

export interface PolicyResource {
  resource_type: ResourceType
  resource_id: string
  name: string
  enabled: boolean
}

export interface SystemUserWorkspace {
  workspace_id: string
  name: string
  role: WorkspaceRole
}

export interface SystemUser {
  user_id: string
  username: string
  email: string | null
  is_admin: boolean
  state: UserState
  tfa_enabled: boolean
  workspaces: SystemUserWorkspace[]
  last_login_at: string | null
  created_at: string | null
}

export interface SystemUsersSummary {
  total: number
  active: number
  invited: number
  disabled: number
  admins: number
  tfa_missing: number
  tfa_enabled: number
  no_workspace: number
}

export interface SystemUsersResponse {
  users: SystemUser[]
  summary: SystemUsersSummary
  workspaces: { workspace_id: string; name: string }[]
}

export interface CreateUserInput {
  name: string
  email: string
  start: 'invite' | 'password'
  is_admin: boolean
  workspace_id: string | null
}

/** A one-time credential the administrator hands over: an invitation link or a temporary
 *  password. The server never sends mail (no transport is configured). */
export interface UserCredentialResult {
  user: SystemUser
  invite_path?: string
  invite_expires_at?: string
  temp_password?: string
}

export type TfaScope = 'off' | 'admin' | 'all'
export type IdleTimeout = '30m' | '2h' | '8h' | '7d'

export interface SecurityPolicy {
  tfa_scope: TfaScope
  tfa_grace_days: 0 | 7 | 14 | 30
  recovery_codes: boolean
  password_min_length: 8 | 10 | 12
  lock_attempts: 3 | 5 | 10
  lock_minutes: 5 | 15 | 60
  idle_timeout: IdleTimeout
  logout_on_tighten: boolean
  tfa_policy_changed_at?: string | null
}

export interface SecurityPerson {
  user_id: string
  username: string
  is_admin: boolean
  tfa_enabled: boolean
  state: UserState
}

export interface SecurityResponse {
  policy: SecurityPolicy
  people: SecurityPerson[]
  signed_out?: number
}
