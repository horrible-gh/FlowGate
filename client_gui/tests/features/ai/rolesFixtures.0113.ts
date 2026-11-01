import { toMyAiRoles, toCardAiRoles } from '@/features/ai/roles/roleValues'
import type { AiRoleValues } from '@/features/ai/roles/types'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import type { FlowGraphNode, FlowGraph } from '@/features/designer/center/graph/flowGraph'
export const notUsed: AiRoleValues = { instruction: { kind: 'not_used' }, execution: { kind: 'not_used' }, review: { kind: 'not_used' } }
export function accountRaw(completed = false, count = 1) {
  return { setup_completed: completed, selectable_count: count,
    roles: Object.fromEntries(['instruction','execution','review'].map(role => [role, { state: completed ? 'not_used' : 'unset', value: completed ? { kind: 'not_used', provider_id: null } : null, display_name: null, cause: null }])),
    options: [{ id: 'a', name: 'None', exec_type: 'api', kind: 'openai', owner_scope: 'system' }],
  }
}
export const account = (completed = false, count = 1) => toMyAiRoles(accountRaw(completed, count))
export function target(docType = 'T', nodeType = 'document'): InspectorTarget {
  const node: FlowGraphNode = { id: 'gn_1', kind: 'task', title: 'card', x: 0, y: 0, docType,
    definition: { nodeType, docType, paletteItemId: null, label: '', assignee: null, rootBound: false } }
  const graph: FlowGraph = { flowId: 'f', nodes: [node], edges: [], ownership: 'graph', viewMode: 'draft' }
  return toInspectorTarget({ projectId: 'p', graph, node })!
}
export function cardRoles() {
  return toCardAiRoles({ card_init_state: 'legacy', has_legacy_roles: true, options_state: 'ok', options: [{ id: 'a', name: 'None' }], preserved: { run_ai: 'old', review_count: 0, review_basis: 'last' },
    roles: {
      instruction: { selected: null, state: 'assigned_ok', outcome: 'ai', provider_id: 'old', display_name: 'Old AI', source: 'legacy_fields', legacy_flag: true },
      execution: { selected: { kind: 'assigned', provider_id: 'gone' }, state: 'assigned_unavailable', outcome: 'unavailable', provider_id: 'gone', display_name: 'Gone AI', source: 'card_explicit', legacy_flag: false, cause: 'deleted' },
      review: { selected: { kind: 'not_used' }, state: 'not_used', outcome: 'no_review', source: 'card_explicit', legacy_flag: false },
    } })
}
export function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
