import type { CapabilityDecision } from './capabilities'
import type { Translate } from '../apiErrors'

// One line that says why buttons are off. Buttons refused for the same reason share one line; buttons
// refused for different reasons give one line each (reviewer: [Edit] and [Run] are `role_limit`, [Approve] is on).
// The words come from the server's reason value plus what it sent with it — never from a role guess.

export interface DeniedAction {
  readonly action: string
  readonly decision: CapabilityDecision
}

const KNOWN = new Set([
  'not_member', 'role_limit', 'context_rule', 'token_scope',
  'system_admin_only', 'workspace_disabled', 'project_archived', 'account_inactive',
])

function roleName(id: string | null, t: Translate): string {
  if (!id) return ''
  const key = `gui.permissions.role.${id}`
  const word = t(key)
  return word === key ? id : word
}

export type DenialScope = 'project' | 'workspace' | 'system'

function line(decision: CapabilityDecision, role: string | null, t: Translate, scope: DenialScope): string {
  const reason = decision.reason && KNOWN.has(decision.reason) ? decision.reason : 'unknown'
  // Workspace roles (owner / admin / member) have no "minimum role" sentence; the wording names the workspace.
  if (scope === 'workspace' && (reason === 'not_member' || reason === 'role_limit')) {
    return t(`gui.permissions.reason_workspace.${reason}`)
  }
  if (reason === 'role_limit') {
    const params = { role: roleName(role, t), minimum: roleName(decision.minimumRole, t) }
    return t(role ? (decision.minimumRole ? 'gui.permissions.reason.role_limit' : 'gui.permissions.reason.role_limit_plain')
      : 'gui.permissions.reason.not_member', params)
  }
  if (reason === 'context_rule') {
    const key = `gui.permissions.rule.${decision.rule ?? ''}`
    const word = t(key)
    return word === key ? t('gui.permissions.reason.context_rule') : word
  }
  return t(`gui.permissions.reason.${reason}`)
}

/**
 * The lines to show for the refused `actions` of one target. Actions that are allowed are ignored, so a
 * caller can pass every action its row uses and get nothing back when all of them are on.
 */
export function denialLines(
  actions: readonly DeniedAction[],
  role: string | null,
  t: Translate,
  scope: DenialScope = 'project',
): string[] {
  const lines: string[] = []
  for (const { decision } of actions) {
    if (decision.allowed) continue
    const text = line(decision, role, t, scope)
    if (!lines.includes(text)) lines.push(text)
  }
  return lines
}
