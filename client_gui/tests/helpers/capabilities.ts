import { setCurrentUser, type CurrentUser } from '@/core/auth/currentUser'
import {
  parseCapabilities,
  resetCapabilities,
  setProjectCapabilities,
  setSystemCapabilities,
  setWorkspaceCapabilityList,
} from '@shared/permissions/capabilities'

// Test helpers for the server's capability answers (rbac/capabilities.py shapes). A screen reads these from the
// shared store; a test that needs "an administrator" gives the store what the server would have sent.

export const SYSTEM_ACTIONS = [
  'system.settings.read', 'system.settings.manage', 'system.user.read', 'system.user.create',
  'system.user.update', 'system.user.disable', 'system.user.assign_admin', 'system.workspace.create',
] as const

export const PROJECT_ACTIONS = [
  'project.view', 'project.document.read', 'project.document.create', 'project.document.update',
  'project.document.delete', 'project.document.approve', 'project.document.reject', 'project.group.read',
  'project.group.manage', 'project.run', 'project.git.read', 'project.git.write', 'project.git.merge',
  'project.storage.write', 'project.settings.read', 'project.settings.edit', 'project.document_type.manage',
  'project.numbering.manage', 'project.member.read', 'project.member.manage',
] as const

type Wire = Record<string, { allowed: boolean; reason?: string; minimum_role?: string; rule?: string }>

/** The wire shape of one block: every action in `all` allowed except those in `refused` (action -> refusal). */
export function wireBlock(
  all: readonly string[],
  role: string | null,
  refused: Record<string, { reason: string; minimum_role?: string; rule?: string }> = {},
) {
  const actions: Wire = {}
  for (const action of all) actions[action] = refused[action] ? { allowed: false, ...refused[action] } : { allowed: true }
  return { role, sources: [{ kind: role ? 'direct' : 'none', role }], actions }
}

/** A system administrator, as /auth/me reports one. */
export function grantSystemAdmin(): void {
  setSystemCapabilities(parseCapabilities(wireBlock(SYSTEM_ACTIONS, 'system_admin')))
}

/** A person with no system action at all (an ordinary member). */
export function grantSystemNothing(): void {
  const refused = Object.fromEntries(SYSTEM_ACTIONS.map((a) => [a, { reason: 'system_admin_only' }]))
  setSystemCapabilities(parseCapabilities(wireBlock(SYSTEM_ACTIONS, null, refused)))
}

/** One project's answer for a role. `refused` lists the actions that role may not do. */
export function grantProject(
  projectId: string,
  role: string,
  refused: Record<string, { reason: string; minimum_role?: string; rule?: string }> = {},
): void {
  setProjectCapabilities(projectId, parseCapabilities(wireBlock(PROJECT_ACTIONS, role, refused)))
}

export { resetCapabilities }

/**
 * `setCurrentUser` for a test that describes a person by `isAdmin`: the person is set, and the store gets the system
 * answer the server would have sent for that kind of person (an administrator: every system action; anyone
 * else: none). Production code never derives one from the other — only fixtures do.
 */
export function setCurrentUserWithCaps(user: CurrentUser | null): void {
  setCurrentUser(user)
  if (!user) resetCapabilities()
  else if (user.isAdmin) grantSystemAdmin()
  else grantSystemNothing()
}

export const WORKSPACE_ACTIONS = [
  'workspace.view', 'workspace.member.manage', 'workspace.team.manage', 'workspace.admin', 'workspace.project.create',
] as const

/** The default workspace's answer for [New project]: allowed, or refused (a plain member of a closed policy). */
export function grantNewProject(allowed: boolean): void {
  const refused = allowed ? {} : { 'workspace.project.create': { reason: 'role_limit' } }
  setWorkspaceCapabilityList([{
    workspace_id: 'ws_default', is_default: true, capabilities: wireBlock(WORKSPACE_ACTIONS, 'member', refused),
  }])
}
