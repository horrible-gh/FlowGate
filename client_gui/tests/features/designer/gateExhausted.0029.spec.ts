// v02gui.0029 (0002-CH turn 35-36): a card approved after its review count ran out is told apart from a reviewed
// approval — the Canvas card's state icon (a yellow check with an exclamation mark) and, on a 확인 card, the box naming
// the cards before it that came through without passing a review. No new state, no "검수 미통과 승인" chip.
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { HEADER_GLYPHS, flowNodeHeaderIcon } from '@/features/designer/center/canvas/flowNodeKinds'
import { cardStateReasonText, cardStateView } from '@/features/designer/center/graph/cardStateView'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import GateExhaustedNotice from '@/features/designer/inspector/features/gateCard/GateExhaustedNotice.vue'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import { exhaustedUpstream, toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const EXHAUSTED = { stage: 'result', doc_id: 'p.m.0001.0003-TR', revision_no: 2, review_count: 3, stop_reason: 'review_count_exhausted' }

function card(id: string, kind: string, docType: string | null, nodeRun: FlowGraphNode['nodeRun'], nodeType = 'document', title = id): FlowGraphNode {
  return { id, kind, title, docType, x: 0, y: 0, nodeRun, definition: { nodeType, docType, paletteItemId: null, label: title, assignee: null } }
}
const run = (extra: Record<string, unknown> = {}) => ({ status: 'done', openedAt: null, finishedAt: null, resultDocId: null, ...extra })

/** R -> t1 (exhausted) -> t2 (reviewed) -> gate; R -> t3 (exhausted) -> gate2; t4 (exhausted) -ref-> gate. */
const GRAPH: FlowGraph = {
  flowId: 'p.m.0001',
  nodes: [
    card('r', 'requirement', 'R', run()),
    card('t1', 'task', 'T', run({ reviewExhaustionApproval: EXHAUSTED }), 'document', '레포트 작성'),
    card('t2', 'task', 'T', run({ reviewExhaustionApproval: null })),
    card('t3', 'task', 'T', run({ reviewExhaustionApproval: EXHAUSTED })),
    card('t4', 'task', 'T', run({ reviewExhaustionApproval: EXHAUSTED })),
    card('gate', 'approval', null, null, 'gate', '확인'),
    card('gate2', 'approval', null, null, 'gate', '확인'),
  ],
  edges: [
    { id: 'e1', source: 'r', target: 't1', edgeClass: 'execution' },
    { id: 'e2', source: 't1', target: 't2', edgeClass: 'execution' },
    { id: 'e3', source: 't2', target: 'gate', edgeClass: 'execution' },
    { id: 'e4', source: 'r', target: 't3', edgeClass: 'execution' },
    { id: 'e5', source: 't3', target: 'gate2', edgeClass: 'execution' },
    { id: 'e6', source: 't4', target: 'gate', edgeClass: 'reference' },
  ],
}

describe('v02gui.0029 approval after review exhaustion', () => {
  it('stays 승인됨 but takes the warning tone and the check-with-exclamation glyph; a reviewed approval is unchanged', () => {
    const exhausted = cardStateView({ status: 'done', reviewExhaustionApproval: EXHAUSTED })!
    expect([exhausted.key, exhausted.phase, exhausted.tone]).toEqual(['approved', 'approved', 'warning'])
    expect(flowNodeHeaderIcon(exhausted)).toEqual({ path: HEADER_GLYPHS.checkAlert, tone: 'warning' })
    const reviewed = cardStateView({ status: 'done', reviewExhaustionApproval: null })!
    expect([reviewed.key, reviewed.tone, reviewed.reason]).toEqual(['approved', 'success', null])
    expect(flowNodeHeaderIcon(reviewed)).toEqual({ path: HEADER_GLYPHS.check, tone: 'success' })
    expect(HEADER_GLYPHS.checkAlert).not.toBe(HEADER_GLYPHS.check)
  })

  it('draws the Canvas card icon yellow with its tooltip, and adds no chip under the title', () => {
    i18n.global.locale.value = 'ko'
    const cardState = cardStateView({ status: 'done', reviewExhaustionApproval: EXHAUSTED })!
    const w = mount(FlowNodeCard, { props: { id: 't1', data: { kind: 'task', title: '레포트', selected: false, state: null, cardState } as any }, global: { plugins: [i18n], stubs: { Handle: true } } })
    const icon = w.get('[data-test="designer-canvas-node-card-state"]')
    expect(icon.classes()).toContain('flow-node-state--warning')
    expect(icon.get('path').attributes('d')).toBe(HEADER_GLYPHS.checkAlert)
    expect(icon.attributes('title')).toContain(cardStateReasonText(cardState, i18n.global.t)!)
    expect(w.find('[data-test="designer-canvas-node-flags"]').exists()).toBe(false)
    expect(w.text()).not.toContain('검수 미통과 승인')
    w.unmount()
  })

  it('lists the done ancestors of a gate approved after exhaustion, along execution edges only', () => {
    expect(exhaustedUpstream(GRAPH, 'gate').map((c) => c.nodeId)).toEqual(['t1'])
    expect(exhaustedUpstream(GRAPH, 'gate')[0]).toEqual({ nodeId: 't1', kind: 'task', docType: 'T', title: '레포트 작성', reviewCount: 3, revisionNo: 2 })
    expect(exhaustedUpstream(GRAPH, 'gate2').map((c) => c.nodeId)).toEqual(['t3'])
    const notDone = { ...GRAPH, nodes: GRAPH.nodes.map((n) => (n.id === 't1' ? { ...n, nodeRun: run({ status: 'review', reviewExhaustionApproval: EXHAUSTED }) } : n)) }
    expect(exhaustedUpstream(notDone, 'gate')).toEqual([])
  })

  it('carries the list on a gate target only', () => {
    const gate = GRAPH.nodes.find((n) => n.id === 'gate')!
    const t2 = GRAPH.nodes.find((n) => n.id === 't2')!
    expect(toInspectorTarget({ projectId: 'p', graph: GRAPH, node: gate })?.exhaustedUpstream?.map((c) => c.nodeId)).toEqual(['t1'])
    expect(toInspectorTarget({ projectId: 'p', graph: GRAPH, node: t2 })).not.toHaveProperty('exhaustedUpstream')
  })

  it.each(['ko', 'en', 'ja'] as const)('the 확인 card box names each card, its review count and revision, and selects it (%s)', (locale) => {
    i18n.global.locale.value = locale
    const request = vi.fn()
    const cards = exhaustedUpstream(GRAPH, 'gate')
    const w = mount(GateExhaustedNotice, { props: { cards }, global: { plugins: [i18n], provide: { [INSPECTOR_HOST as symbol]: { request } } } })
    expect(w.get('[data-test="gate-exhausted-title"]').text()).toContain(i18n.global.t('gui.inspector.gateCard.exhausted.title', { count: 1 }))
    expect(w.get('[data-test="gate-exhausted-open"]').text()).toContain('레포트 작성')
    expect(w.get('[data-test="gate-exhausted-detail"]').text()).toBe(i18n.global.t('gui.inspector.gateCard.exhausted.detail', { count: 3, revision: 2 }))
    expect(i18n.global.te('gui.inspector.gateCard.exhausted.hint', locale)).toBe(true)
    w.get('[data-test="gate-exhausted-open"]').trigger('click')
    expect(request).toHaveBeenCalledWith({ kind: 'select-node', nodeId: 't1' })
    w.unmount()
  })

  it('shows nothing when no card came through without its review', () => {
    const w = mount(GateExhaustedNotice, { props: { cards: [] }, global: { plugins: [i18n] } })
    expect(w.find('[data-test="gate-exhausted-notice"]').exists()).toBe(false)
    w.unmount()
  })
})
