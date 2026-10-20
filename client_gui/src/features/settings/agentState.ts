// How a system Agent's state reads on screen (mockup ⑩, flowgate.v02.0077), shared by 시스템 Agent
// and the overview's 확인 필요 list. Everything is derived from one GET /system/agents answer
// (services/agent_registry.detail); nothing here sends a request.
import type { AgentCapability, SystemAgent } from './types'
import { relativeTime } from './format'

export const AGENT_CAPS: readonly AgentCapability[] = ['ai_cli', 'ai_api', 'storage']

export type AgentState =
  | 'local' | 'local_disabled' | 'disabled' | 'pending' | 'unregistered' | 'reenroll' | 'revoked' | 'incompatible' | 'online' | 'offline'
export type AgentTone = 'ok' | 'warn' | 'bad' | 'off' | 'info'

export const AGENT_STATE_TONE: Record<AgentState, AgentTone> = {
  local: 'info',
  local_disabled: 'off',
  disabled: 'off',
  pending: 'warn',
  unregistered: 'off',
  reenroll: 'warn',
  revoked: 'bad',
  incompatible: 'bad',
  online: 'ok',
  offline: 'warn',
}

/** One state per Agent, in the order the server's detail decides it (local first, then 사용 중지,
 *  registration, protocol and the heartbeat). Online means a recent compatible heartbeat only. */
export function agentState(a: SystemAgent): AgentState {
  if (a.location === 'local') return a.enabled ? 'local' : 'local_disabled'
  if (!a.enabled) return 'disabled'
  if (a.registration_status === 'unregistered') return a.enrollment_status === 'pending' ? 'pending' : 'unregistered'
  if (a.registration_status === 'revoked') return a.enrollment_status === 'pending' ? 'reenroll' : 'revoked'
  if (a.protocol_compatibility === 'incompatible') return 'incompatible'
  return a.online_status === 'online' ? 'online' : 'offline'
}

/** Allowed here, reported by the Agent as unsupported: allowed but unusable. */
export function capMissing(a: SystemAgent, cap: AgentCapability): boolean {
  return a.location === 'remote' && a.enabled && a.credential_status === 'active'
    && a.configured_capabilities[cap] && a.reported_capabilities[cap] === false
}

/** Worth a look: an enabled remote Agent that is waiting, refused, offline or short of a capability. */
export function needsAttention(a: SystemAgent): boolean {
  if (!a.enabled || a.location === 'local') return false
  const tone = AGENT_STATE_TONE[agentState(a)]
  return tone === 'warn' || tone === 'bad' || AGENT_CAPS.some((cap) => capMissing(a, cap))
}

type Translate = (key: string, values?: Record<string, unknown>) => string

/** The second line under a state: what it rests on, from the same answer. */
export function agentStateSub(a: SystemAgent, t: Translate, locale: string): string {
  const ago = (value: string | null) => relativeTime(value, locale)
  const state = agentState(a)
  const key = `gui.settings.agents.state_sub.${state}`
  switch (state) {
    case 'disabled':
      return t(a.credential_status === 'revoked' ? 'gui.settings.agents.state_sub.disabled_revoked' : key)
    case 'pending':
    case 'reenroll':
      return t(key, { at: ago(a.enrollment_expires_at) })
    case 'incompatible':
      return t(key, { version: a.last_rejected_protocol_version ?? '—', at: ago(a.last_protocol_rejection_at) })
    case 'online':
      return t(key, { at: ago(a.last_seen_at) })
    case 'offline':
      return a.last_seen_at ? t(key, { at: ago(a.last_seen_at) }) : t('gui.settings.agents.state_sub.never')
    default:
      return t(key)
  }
}
