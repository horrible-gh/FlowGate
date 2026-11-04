// One card resource for both document rows. SSE is a refresh signal; GET is the state.
import { computed, inject, onScopeDispose, reactive, ref, watch } from 'vue'
import { cardDocumentDecisionsRead, cardDocumentDecisionWrite, type CardDocumentDecisions } from '@/features/document'
import { INSPECTOR_HOST } from './inspectorHost'
import { inspectorCardNodeKey, type InspectorTarget } from './inspectorTarget'
import { useInspectorResource } from './useInspectorResource'

// A re-selection or second mounted view cannot submit another decision while this card is pending.
const pending = reactive(new Set<string>())
export function useCardDocumentDecisions(target: () => InspectorTarget | null) {
  const host = inject(INSPECTOR_HOST, null)
  const identity = () => {
    const t = target()
    return t ? JSON.stringify([t.projectId, t.flowId, t.nodeId]) : null
  }
  const scopeKey = () => {
    const t = target(), id = identity(), node = inspectorCardNodeKey(t)
    return t && id && node && ['graph', 'unassigned'].includes(t.ownership ?? '')
      ? JSON.stringify([id, t.settingsContext, t.cardDocuments, t.viewMode]) : null
  }
  const resource = useInspectorResource<CardDocumentDecisions>({
    target: () => {
      const t = target(), key = scopeKey()
      return t && key ? { projectId: t.projectId, flowId: t.flowId, docId: key } : null
    },
    read: (key) => {
      const [id] = JSON.parse(key), [, flow, node] = JSON.parse(id)
      return cardDocumentDecisionsRead(flow, node)
    },
    events: ['doc_review_status_changed', 'document_explorer_refresh', 'graph_canvas_changed',
      'graph_node_state_changed', 'graph_run_status_changed', 'ai_invoke_started', 'ai_invoke_finished'],
    matches: (frame, scope) => frame.groupId === scope.flowId || frame.payload.flow_id === scope.flowId,
  })
  const failure = ref<string | null>(null)
  let epoch = 0
  // Document/context refreshes invalidate GET data, but a POST error still belongs to this card.
  // Changing cards (including away and back) or disposing the scope invalidates POST feedback.
  watch(identity, () => { epoch++; failure.value = null }, { flush: 'sync' })
  onScopeDispose(() => { epoch++ })
  const busy = computed(() => !!identity() && pending.has(identity()!))
  const contextMatches = () => {
    const t = target(), view = resource.data.value, context = t?.settingsContext
    if (!t || !view || view.flow_id !== t.flowId || view.node_key !== t.nodeId) return false
    if (!context) return t.viewMode !== 'revision'
    return (['run_id', 'graph_revision_no', 'draft_version'] as const)
      .every((key) => context[key] === view.context[key])
  }
  function row(slot: string, docId: string | null | undefined) {
    const t = target(), view = resource.data.value
    const visible = !!t && t.nodeType !== null && t.nodeType !== 'gate' && t.ownership !== 'v01'
      && ['instruction', 'report', 'requirement', 'plan'].includes(slot)
    const doc = view?.documents[slot]
    const matches = !!docId && doc?.doc_id === docId
    const fallback = t?.cardDocuments.find((line) => line.slot === slot && line.docId === docId)?.docReviewStatus
    const authoritative = matches && contextMatches()
    const state = authoritative ? doc?.doc_review_status : fallback
    const action = state === 'approved' || state === 'wf_done' ? 'reject' : 'approve'
    const cap = view?.capabilities[slot]?.[action]
    let reason: string | null = null
    if (!docId) reason = 'missing'
    else if (t?.ownership === 'conflict') reason = 'conflict'
    else if (!scopeKey()) reason = 'unavailable'
    else if (busy.value) reason = 'busy'
    else if (resource.status.value === 'error') reason = 'unavailable'
    else if (resource.status.value !== 'ready' || resource.stale.value) reason = 'loading'
    else if (!contextMatches() || !matches) reason = 'stale'
    else if (cap?.allowed !== true) reason = cap?.reason?.code ?? 'unavailable'
    return { visible, action, state, authoritative, reason, disabled: reason !== null } as const
  }
  async function decide(slot: string, docId: string | null | undefined) {
    const t = target(), id = identity(), view = resource.data.value
    const state = row(slot, docId), doc = view?.documents[slot]
    if (!t || !id || !view || !doc || state.disabled || !state.visible) return
    const generation = epoch
    pending.add(id)
    failure.value = null
    try {
      await cardDocumentDecisionWrite(t.flowId, t.nodeId, slot, {
        action: state.action, doc_id: doc.doc_id, expected_revision: doc.revision_no,
        expected_decision_version: view.decision_version, expected_context: view.context,
        request_key: Array.from(crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16).padStart(8, '0')).join(''),
      })
    } catch (error: unknown) {
      if (epoch === generation && identity() === id) {
        const response = (error as { response?: { data?: { error?: { message?: string; code?: string } } } })?.response
        failure.value = response?.data?.error?.message ?? response?.data?.error?.code ?? 'unavailable'
      }
    } finally {
      // The POST view can precede completion/recovery. Read the committed state even after a lost response/409.
      host?.request({ kind: 'refresh-flow', flowId: t.flowId })
      if (epoch === generation && identity() === id) await resource.reload()
      pending.delete(id)
    }
  }
  return { row, decide, failure, busy, resource }
}
