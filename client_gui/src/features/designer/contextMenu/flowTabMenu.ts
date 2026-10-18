// flowgate.v02.0060 — ③ Flow tab menu (0006-D rev2 §5.3 ③): close / close others / close all for the
// right-clicked tab id (not the active tab); the blank tab strip has close all only.

import { action as a, makeMenu } from './model'
import type { PartInput, TabTarget } from './types'
export interface TabAvailability { readonly openTabIds: readonly string[] }
export function flowTabMenu({ target, available: v, connected }: PartInput<TabTarget, TabAvailability>) {
  const present = target.flowId !== null && v.openTabIds.includes(target.flowId)
  return makeMenu(target, [
    a('tab.close', 'close', '×', !present),
    a('tab.closeOthers', 'closeOthers', '⊟', !present || !v.openTabIds.some((id) => id !== target.flowId)),
    a('tab.closeAll', 'closeAll', '☒', v.openTabIds.length === 0),
  ], connected)
}

