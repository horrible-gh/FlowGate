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

/** POST /system/users. flowgate.v02.0076 T#3: the screen no longer picks a workspace, so the
 *  body carries no workspace_id at all (not even an empty one); the server's field stays. */
export interface CreateUserInput {
  name: string
  email: string
  start: 'invite' | 'password'
  is_admin: boolean
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

// ── 시스템 › 일반 (flowgate.v02.0076 T#3, D §2.5) ─────────────────────────────

/** GET /system/storage-root (storage/paths.describe_storage_root). Read only here. */
export interface SystemStorageRoot {
  project_id: string | null
  /** The stored system_settings.storage_root; null when unset. */
  configured_value: string | null
  project_override: string | null
  effective_value: string
  effective_source: 'env' | 'project_override' | 'system_setting' | 'default'
  env_override: boolean
}

/** One row of GET /system/settings `settings` and PATCH `updated`. */
export interface SystemSettingRow {
  setting_key: string
  setting_value: string | null
  value_type: string
}

export type ScratchUnit = 'minute' | 'hour' | 'day'

// ── 시스템 › 시스템 Agent (flowgate.v02.0076 T#4, D §2.6) ─────────────────────

export type AgentLocation = 'local' | 'remote'
export type AgentCapability = 'ai_cli' | 'ai_api' | 'storage'
export type AgentCapabilities = Record<AgentCapability, boolean>

/** One Agent of GET /system/agents (services/agent_registry.detail); also the answer of POST, PATCH
 *  and credential/revoke. Holds no secret: the enrollment token is only in AgentEnrollment. */
export interface SystemAgent {
  agent_id: string
  name: string
  enabled: boolean
  location: AgentLocation
  /** 'agent_pull' for remote, null for local. */
  connection_mode: 'agent_pull' | null
  local_executable_path: string | null
  configured_capabilities: AgentCapabilities
  /** null until the Agent reported (heartbeat); local never reports. */
  reported_capabilities: Record<AgentCapability, boolean | null>
  effective_capabilities: AgentCapabilities
  registration_status: 'registered' | 'revoked' | 'unregistered'
  credential_status: 'active' | 'revoked' | 'none'
  has_registration_history: boolean
  enrollment_status: 'pending' | 'none'
  enrollment_expires_at: string | null
  online_status: 'online' | 'offline' | 'not_applicable'
  last_seen_at: string | null
  agent_version: string | null
  protocol_version: number | null
  protocol_compatibility: 'unknown' | 'compatible' | 'incompatible'
  last_rejected_protocol_version: number | null
  last_protocol_rejection_at: string | null
  os: string | null
  architecture: string | null
}

/** POST /system/agents: every field (the route has no defaults). */
export interface SystemAgentCreate {
  name: string
  enabled: boolean
  location: AgentLocation
  connection_mode: 'agent_pull' | null
  local_executable_path: string | null
  configured_capabilities: AgentCapabilities
}

/** PATCH /system/agents/{aid}: only the changed fields; location / connection_mode are never sent. */
export type SystemAgentChanges = Partial<Pick<SystemAgent, 'name' | 'enabled' | 'local_executable_path' | 'configured_capabilities'>>

/** POST /system/agents/{aid}/enrollments (201): the token exists only in this answer. */
export interface AgentEnrollment {
  agent_id: string
  enrollment_token: string
  expires_at: string
}
