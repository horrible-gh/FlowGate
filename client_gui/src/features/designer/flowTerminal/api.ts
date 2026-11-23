// The terminal server contract shared with the "터미널 서버" card (0005-T: 서버·화면 공통 약속). The screen never puts
// the login token in a URL: POST /sessions returns a one-time ticket and only that goes into the WebSocket address.
import { deleteRequest, getBaseUrl, getRequest, postRequest, putRequest } from '@shared/api'
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

/* ── 시스템 설정 › 실행 자원 › 웹 터미널 (0015-T B, 서버·화면 공통 약속 2~7; administrators only) ── */
export type TerminalPlatform = 'windows' | 'linux' | 'darwin'
export type TerminalInstallStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type TerminalInstallStage = 'download' | 'verify' | 'extract' | 'test' | 'apply'
export interface TerminalInstall {
  install_id: string
  status: TerminalInstallStatus
  stage: TerminalInstallStage | null
  /** 0 ~ 1 */
  progress: number
  bytes_done: number | null
  bytes_total: number | null
  error_code: string | null
  message: string | null
}
export interface TerminalBundle { name: string; version: string; size_bytes: number }
export interface TerminalSystem {
  platform: TerminalPlatform
  bundle_required: boolean
  installed: boolean
  enabled: boolean
  open_sessions: number
  /** null on Linux / macOS (nothing to get). */
  bundle: TerminalBundle | null
  /** The running or the last installation; null = none yet. */
  install: TerminalInstall | null
}
export type TerminalShellTest =
  | { ok: true; shell: string }
  | { ok: false; error_code: string; message: string }

export async function readTerminalSystem(): Promise<TerminalSystem> {
  return (await getRequest<TerminalSystem>(`${BASE}/system`)).data
}
export async function startTerminalInstall(): Promise<TerminalInstall> {
  return (await postRequest<TerminalInstall>(`${BASE}/system/installs`, {})).data
}
export async function readTerminalInstall(installId: string): Promise<TerminalInstall> {
  return (await getRequest<TerminalInstall>(`${BASE}/system/installs/${encodeURIComponent(installId)}`)).data
}
export async function cancelTerminalInstall(installId: string): Promise<void> {
  await deleteRequest(`${BASE}/system/installs/${encodeURIComponent(installId)}`)
}
export async function deleteTerminalBundle(): Promise<void> {
  await deleteRequest(`${BASE}/system/bundle`)
}
export async function setTerminalEnabled(enabled: boolean): Promise<{ enabled: boolean; closed_sessions: number }> {
  return (await putRequest<{ enabled: boolean; closed_sessions: number }>(`${BASE}/system/enabled`, { enabled })).data
}
export async function testTerminalShell(): Promise<TerminalShellTest> {
  return (await postRequest<TerminalShellTest>(`${BASE}/system/test`, {})).data
}
/** The contract's error code of a refused system call (`terminal_install_running`, `terminal_enabled`, …), or null. */
export function terminalErrorCode(error: unknown): string | null {
  return normalizeApiError(error).code || null
}

/** Why POST /sessions was refused, by the contract's codes; anything else is `failed`. */
export type OpenFailure = 'limit' | 'exists' | 'not_admin' | 'disabled' | 'no_cwd' | 'failed'
export function openFailureOf(error: unknown): OpenFailure {
  const { code } = normalizeApiError(error)
  if (code === 'terminal_session_limit') return 'limit'
  if (code === 'terminal_flow_session_exists') return 'exists'
  if (code === 'terminal_not_admin') return 'not_admin'
  if (code === 'terminal_disabled') return 'disabled'
  // 0011-T 할 일 4: no worktree and no branch checkout on the server — retrying will not help, so say so.
  if (code === 'terminal_cwd_unavailable') return 'no_cwd'
  return 'failed'
}
