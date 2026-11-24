// The WebSocket frames of the terminal (0005-T): JSON text frames both ways.
export type ExitReason = 'closed' | 'idle_timeout' | 'shell_exit' | 'error'
export type ServerMessage =
  | { type: 'output'; data: string }
  | { type: 'busy'; value: boolean }
  | { type: 'card_running'; value: boolean }
  /** `message`: why the shell could not start (e.g. pywinpty missing → `pip install -r server/requirements.txt`). */
  | { type: 'exit'; reason: ExitReason; code: number | null; message?: string }

const EXIT_REASONS: readonly string[] = ['closed', 'idle_timeout', 'shell_exit', 'error']

/** One server frame, or null for anything that is not a frame of the contract (it is ignored). */
export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null
  let value: unknown
  try { value = JSON.parse(raw) } catch { return null }
  if (!value || typeof value !== 'object') return null
  const { type, data, value: flag, reason, code, message } = value as Record<string, unknown>
  if (type === 'output' && typeof data === 'string') return { type: 'output', data }
  if (type === 'busy' && typeof flag === 'boolean') return { type: 'busy', value: flag }
  if (type === 'card_running' && typeof flag === 'boolean') return { type: 'card_running', value: flag }
  if (type === 'exit') {
    const known = typeof reason === 'string' && EXIT_REASONS.includes(reason)
    const exit: Extract<ServerMessage, { type: 'exit' }> = { type: 'exit', reason: known ? reason as ExitReason : 'error', code: typeof code === 'number' ? code : null }
    if (typeof message === 'string' && message) exit.message = message
    return exit
  }
  return null
}

export const inputFrame = (data: string) => JSON.stringify({ type: 'input', data })
export const resizeFrame = (cols: number, rows: number) =>
  JSON.stringify({ type: 'resize', cols: Math.max(1, Math.floor(cols)), rows: Math.max(1, Math.floor(rows)) })
