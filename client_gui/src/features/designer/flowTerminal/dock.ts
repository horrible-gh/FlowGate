// Where a Flow terminal is drawn (0078 · 0015-T A): the floating window (모달리스) or filling the whole Designer area
// under the Flow tab strip (디자이너 — the Canvas and the bottom Inspector · Minimap places, all of it; nothing split).
// - `designer[flowId]`: the Flow's terminal fills the Designer. Remembered per Flow for as long as the Host lives; a
//   terminal opens floating the first time, and [✕] (the shell ends) forgets it, so the next one opens floating too.
// Moving between the two never ends the shell: the window moves the same screen (Teleport), nothing is made again.
import { reactive } from 'vue'

export interface TerminalPlaceState {
  designer: Record<string, boolean>
}

export function createTerminalPlaceState(): TerminalPlaceState {
  return reactive({ designer: {} }) as TerminalPlaceState
}

/** [⇲ 디자이너로]: the terminal fills the Designer area. */
export function toDesigner(state: TerminalPlaceState, flowId: string): void {
  state.designer[flowId] = true
}

/** [⇱ 모달리스로]: back to the floating window. */
export function toModeless(state: TerminalPlaceState, flowId: string): void {
  delete state.designer[flowId]
}

/** [✕] or a closed Flow tab forgets the place: the next terminal of that Flow opens floating. */
export const forgetFlow = toModeless

/** The terminal of `flowId` fills the Designer now: it is open, in designer mode and there is a place to fill. */
export function isDesignerNow(state: TerminalPlaceState, flowId: string | null, open: boolean, hasTarget: boolean): boolean {
  return !!flowId && open && hasTarget && !!state.designer[flowId]
}
