// What the toolbar and the Flow tabs need to know about the terminal windows (0078 · 0001-R §3.1·§3.3):
// - status: GET /terminal/status of the open Flow (null = not known / not asked: the button is not drawn);
// - openFlows: the Flows that have a terminal window now (their tabs show `>_`);
// - currentOpen: the open Flow has one (the toolbar button is lit with a dot).
// `open` is set by the Host while it is mounted.
import { reactive } from 'vue'
import type { TerminalStatus } from './api'

export const flowTerminalUi = reactive<{
  status: TerminalStatus | null
  openFlows: Record<string, boolean>
  currentOpen: boolean
  open: (() => void) | null
}>({ status: null, openFlows: {}, currentOpen: false, open: null })

/** hidden = not an admin (or not known): no button and no gap; disabled = admin, server setting off (dimmed with a
 *  tooltip); ready = can open; on = the open Flow's window is open. */
export type TerminalButtonState = 'hidden' | 'disabled' | 'ready' | 'on'
export function terminalButtonState(status: TerminalStatus | null, currentOpen: boolean): TerminalButtonState {
  if (!status || status.reason === 'not_admin') return 'hidden'
  if (!status.enabled || status.reason === 'disabled' || !status.allowed) return 'disabled'
  return currentOpen ? 'on' : 'ready'
}
