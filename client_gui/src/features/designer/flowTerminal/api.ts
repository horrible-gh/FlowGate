// The terminal server contract shared with the "터미널 서버" card (0005-T: 서버·화면 공통 약속). The screen never puts
// the login token in a URL: POST /sessions returns a one-time ticket and only that goes into the WebSocket address.
import { deleteRequest, getBaseUrl, getRequest, postRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'

export type TerminalDenyReason = 'not_admin' | 'disabled'
export interface TerminalStatus {
  enabled: boolean
  allowed: boolean
  reason: TerminalDenyReason | null
  max_sessions: number
  active_sessions: number
  flow_session_id: string | null
}
export interface TerminalSession {
  session_id: string
  cwd: string
  ticket: string
  ticket_expires_in: number
}

const BASE = '/api/v1/terminal'

export async function readTerminalStatus(flowId: string): Promise<TerminalStatus> {
  return (await getRequest<TerminalStatus>(`${BASE}/status`, { flow_id: flowId })).data
}
export async function openTerminalSession(flowId: string, cols: number, rows: number): Promise<TerminalSession> {
  return (await postRequest<TerminalSession>(`${BASE}/sessions`, { flow_id: flowId, cols, rows })).data
}
export async function closeTerminalSession(sessionId: string): Promise<void> {
  await deleteRequest(`${BASE}/sessions/${encodeURIComponent(sessionId)}`)
}

/** ws(s)://<api base>/api/v1/terminal/ws?ticket=… — the scheme follows the API base (https → wss). A relative base
 *  (e.g. "/flowgate") is read against the page address. */
export function terminalSocketUrl(ticket: string, base: string = getBaseUrl(), page: string = location.href): string {
  const url = new URL(`${base.replace(/\/+$/, '')}${BASE}/ws`, page)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.search = `?ticket=${encodeURIComponent(ticket)}`
  return url.toString()
}

/** Why POST /sessions was refused, by the contract's codes; anything else is `failed`. */
export type OpenFailure = 'limit' | 'exists' | 'not_admin' | 'disabled' | 'failed'
export function openFailureOf(error: unknown): OpenFailure {
  const { code } = normalizeApiError(error)
  if (code === 'terminal_session_limit') return 'limit'
  if (code === 'terminal_flow_session_exists') return 'exists'
  if (code === 'terminal_not_admin') return 'not_admin'
  if (code === 'terminal_disabled') return 'disabled'
  return 'failed'
}
