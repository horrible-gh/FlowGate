import { shallowReactive, reactive } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { mergeCardKey } from './mergeModel'

export interface MergeWindowEntry {
  key: string
  target: () => InspectorTarget
  refresh: () => void
  minimized: boolean
  raise: number
}
export const mergeWindows = shallowReactive<MergeWindowEntry[]>([])
export function openMergeWindow(target: () => InspectorTarget, refresh: () => void = () => undefined): void {
  const current = target()
  const key = mergeCardKey(current.flowId, current.nodeId)
  const existing = mergeWindows.find(win => win.key === key)
  if (existing) {
    existing.target = target
    existing.refresh = refresh
    existing.minimized = false
    existing.raise++
    return
  }
  mergeWindows.push(reactive({ key, target, refresh, minimized: false, raise: 0 }))
}
export function closeMergeWindow(key: string): void {
  const index = mergeWindows.findIndex(win => win.key === key)
  if (index >= 0) mergeWindows.splice(index, 1)
}
