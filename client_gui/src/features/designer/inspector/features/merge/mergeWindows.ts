import { shallowReactive, reactive } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { mergeCardKey } from './mergeModel'

/**
 * flowgate.v02test.0084 (F84-8): [병합 취소] of a merge the AI resolved — the Designer's Graph Time Machine of the card
 * (its merge is reverted on the Flow branch by a new commit, the card and the cards that took its result go back to
 * before their run). The window only asks; the Designer owns the run state and the Time Machine.
 */
export interface MergeUndoHost {
  /** Why it cannot run now (a card menu reason: run_not_idle, run_locked, run_unknown, busy), `unavailable`, or null. */
  blocked(): string | null
  /** The cards it takes back (this one first) and the done cards it leaves as they are, by title. */
  impact(): { readonly back: readonly string[]; readonly keep: readonly string[] }
  /** Runs it; whether it was done (the Designer shows the outcome). */
  run(): Promise<boolean>
}

export interface MergeWindowEntry {
  key: string
  target: () => InspectorTarget
  refresh: () => void
  minimized: boolean
  raise: number
  undo: MergeUndoHost | null
}
export const mergeWindows = shallowReactive<MergeWindowEntry[]>([])
export function openMergeWindow(target: () => InspectorTarget, refresh: () => void = () => undefined, undo: MergeUndoHost | null = null): void {
  const current = target()
  const key = mergeCardKey(current.flowId, current.nodeId)
  const existing = mergeWindows.find(win => win.key === key)
  if (existing) {
    existing.target = target
    existing.refresh = refresh
    existing.undo = undo ?? existing.undo
    existing.minimized = false
    existing.raise++
    return
  }
  mergeWindows.push(reactive({ key, target, refresh, minimized: false, raise: 0, undo }))
}
export function closeMergeWindow(key: string): void {
  const index = mergeWindows.findIndex(win => win.key === key)
  if (index >= 0) mergeWindows.splice(index, 1)
}
