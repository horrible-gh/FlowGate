import { reactive } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
const panels = reactive(new Map<string, { open: boolean }>())
export function gateMemoPanel(target: InspectorTarget) {
  const key = `${target.flowId}\n${target.nodeId}`
  if (!panels.has(key)) panels.set(key, { open: false })
  return panels.get(key)!
}
