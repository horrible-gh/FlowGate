// Docking a Flow terminal into the Designer's bottom Dock (0078 · 0001-R §3.1-3 rev 3, 0009-T).
// - `docked[flowId]`: the Flow's terminal is wanted in the bottom Dock (remembered per Flow for this browser session;
//   a Flow that was never docked opens floating). [새로 열기] and opening again after [닫기] keep it.
// - `pane[flowId]`: what the left 7 of the bottom Dock shows for that Flow while its terminal is docked.
// The bottom Dock shows the Inspector | 터미널 switch only for the open Flow whose terminal is open *and* docked;
// anything else is the Inspector alone, as before.
import { reactive } from 'vue'

export type BottomPane = 'inspector' | 'terminal'

export interface TerminalDockState {
  docked: Record<string, boolean>
  pane: Record<string, BottomPane>
}

export function createTerminalDockState(): TerminalDockState {
  return reactive({ docked: {}, pane: {} }) as TerminalDockState
}

/** [아래에 붙이기]: the terminal goes into the bottom Dock and its tab is chosen. */
export function dockFlow(state: TerminalDockState, flowId: string): void {
  state.docked[flowId] = true
  state.pane[flowId] = 'terminal'
}

/** [떼기]: back to the floating window. */
export function undockFlow(state: TerminalDockState, flowId: string): void {
  delete state.docked[flowId]
  delete state.pane[flowId]
}

/** A closed Flow tab forgets its terminal place. */
export const forgetFlow = undockFlow

export function selectBottomPane(state: TerminalDockState, flowId: string, pane: BottomPane): void {
  if (state.docked[flowId]) state.pane[flowId] = pane
}

/** The terminal of `flowId` is drawn in the bottom Dock: it is open, docked and there is a Dock to go to. */
export function isDockedNow(state: TerminalDockState, flowId: string, open: boolean, hasTarget: boolean): boolean {
  return open && hasTarget && !!state.docked[flowId]
}

/** What the bottom Dock's left part shows for the open Flow: null = the Inspector alone (no switch). */
export function bottomPaneOf(state: TerminalDockState, flowId: string | null, open: boolean): BottomPane | null {
  if (!flowId || !open || !state.docked[flowId]) return null
  return state.pane[flowId] ?? 'terminal'
}
