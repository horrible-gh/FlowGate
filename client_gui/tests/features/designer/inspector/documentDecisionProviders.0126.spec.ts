// Provider wiring tests: real provider templates, slot list, decision composable and API adapter.
// Auxiliary settings/test sessions and document IO are fixtures; browser acceptance remains separate.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h, ref, type Component } from 'vue'
import DesignSlots from '@/features/designer/inspector/features/designCard/DesignSlots.vue'
import GateSlots from '@/features/designer/inspector/features/gateCard/GateSlots.vue'
import RequirementSlots from '@/features/designer/inspector/features/requirementCard/RequirementSlots.vue'
import WorkPlanTab from '@/features/designer/inspector/features/workPlan/WorkPlanTab.vue'
import TestSpecTab from '@/features/designer/inspector/features/testSpec/TestSpecTab.vue'
import InspectorSlotList from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { prepareInspectorI18n } from './support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from './support/projectEventStream'

const fixture = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), card: null as any }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: fixture.get, postRequest: fixture.post }))
vi.mock('@/features/document', async original => ({
  ...await original<typeof import('@/features/document')>(),
  settingsNodeRead: vi.fn(async () => ({ fields: { instruction: { value: 'use' } } })),
}))
vi.mock('@/features/designer/inspector/features/workPlan/useWorkPlan', async () => {
  const { ref } = await import('vue')
  return { useWorkPlan: () => ({ view: ref(null), status: ref('ready'), errorKey: ref(null), reload: vi.fn() }) }
})
vi.mock('@/features/designer/inspector/features/testSpec/useTestCard', async () => {
  const { shallowRef } = await import('vue')
  return { useTestCard: () => shallowRef(fixture.card) }
})

const i18n = prepareInspectorI18n()
const refresh = vi.fn()
let wrapper: VueWrapper<any> | null = null
let raw: any
const io = defineComponent({
  props: { docId: String },
  setup: props => () => h('span', { 'data-test': 'provider-io', 'data-doc': props.docId }, [
    h('button', { 'data-test': 'provider-upload' }), h('button', { 'data-test': 'provider-download' }), h('span', { role: 'separator' }),
  ]),
})
function target(type: string, slots: string[], custom = false): InspectorTarget {
  const cardDocuments: FlowGraphCardDocument[] = slots.map(slot => ({
    slot, docId: `doc_${slot === 'scenario' ? 'instruction' : slot}`, docType: type,
    author: 'run', title: slot, status: 'open', docReviewStatus: null,
  }))
  const node: FlowGraphNode = { id: 'gn_provider', kind: custom ? 'custom' : 'task', title: 'Provider', docType: type, x: 0, y: 0,
    definition: { nodeType: custom ? 'custom' : 'document', docType: type, paletteItemId: null, label: 'Provider', assignee: null, rootBound: false }, cardDocuments }
  const graph: FlowGraph = { flowId: 'f_provider', nodes: [node], edges: [], ownership: 'graph', viewMode: 'draft',
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
  raw = { flow_id: graph.flowId, node_key: node.id, context: { run_id: null, graph_revision_no: null, draft_version: 1 }, decision_version: 7,
    documents: Object.fromEntries(cardDocuments.filter(line => line.slot !== 'scenario' && line.slot !== 'delegation')
      .map(line => [line.slot, { doc_id: line.docId, revision_no: 2, doc_review_status: null }])),
    capabilities: Object.fromEntries(slots.map(slot => [slot, { approve: { allowed: true }, reject: { allowed: true } }])) }
  return toInspectorTarget({ projectId: 'p_provider', graph, node })!
}
async function open(component: Component, selected: InspectorTarget) {
  wrapper = mount(component, { props: { target: selected }, global: { plugins: [i18n, createPinia()],
    stubs: { DocumentIoGroup: io }, provide: { [INSPECTOR_HOST as symbol]: { request: refresh } } } })
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.clearAllMocks()
  installProjectEventStream()
  i18n.global.locale.value = 'ko'
  fixture.get.mockImplementation(async () => ({ data: structuredClone(raw) }))
  fixture.post.mockImplementation(async () => ({ data: structuredClone(raw) }))
  fixture.card = null
})
afterEach(() => { wrapper?.unmount(); wrapper = null; removeProjectEventStream() })

describe('0126 each provider forwards the selected card to the real decision rows', () => {
  it.each([
    { name: 'Design', component: DesignSlots, type: 'D', slots: ['instruction', 'report'], custom: false, rows: ['instruction', 'report'] },
    { name: 'Gate custom', component: GateSlots, type: 'X', slots: ['instruction', 'report'], custom: true, rows: ['instruction', 'report'] },
    { name: 'Requirement', component: RequirementSlots, type: 'R', slots: ['requirement', 'delegation'], custom: false, rows: ['document'] },
    { name: 'WorkPlan', component: WorkPlanTab, type: 'WP', slots: ['plan'], custom: false, rows: ['plan'] },
  ])('$name keeps target, row icons, and the clicked slot/doc identity', async ({ component, type, slots, custom, rows }) => {
    const selected = target(type, slots, custom)
    await open(component, selected)
    expect(wrapper!.getComponent(InspectorSlotList).props('target')).toEqual(selected)
    expect(fixture.get).toHaveBeenCalledWith('/api/v1/flows/f_provider/nodes/gn_provider/documents/decisions')
    expect(wrapper!.findAll('[data-test="inspector-document-decision"]')).toHaveLength(rows.length)
    for (const key of rows) {
      const row = wrapper!.get(`[data-slot="${key}"]`)
      const button = row.get('[data-test="inspector-document-decision"]')
      expect(button.attributes('disabled')).toBeUndefined()
      expect(row.findAll('button').at(-1)!.attributes('data-test')).toBe('inspector-document-decision')
      expect(row.find('[data-test="inspector-slot-history"]').exists()).toBe(true)
      expect(row.find('[data-test="inspector-slot-open"]').exists()).toBe(true)
      await button.trigger('click'); await flushPromises()
      const slot = key === 'document' ? 'requirement' : key
      expect(fixture.post).toHaveBeenLastCalledWith(`/api/v1/flows/f_provider/nodes/gn_provider/documents/${slot}/decision`,
        expect.objectContaining({ doc_id: `doc_${slot}`, expected_revision: 2, expected_decision_version: 7 }))
    }
    if (slots.includes('delegation')) {
      expect(wrapper!.get('[data-slot="delegation"]').find('[data-test="inspector-document-decision"]').exists()).toBe(false)
    }
  })
})

it('TestSpecTab renders decisions only on instruction/report and preserves edit, run, cancel, history and scenario actions', async () => {
  const selected = target('TS', ['instruction', 'scenario', 'report'])
  const run = vi.fn(), cancel = vi.fn(), rewriteAll = vi.fn(), openWindow = vi.fn()
  const availability = ref({ cancel: { visible: false, enabled: true, reason: null } })
  fixture.card = {
    view: ref(null), presentation: ref({ stage: 3, hasScenario: true, canFollow: false, resultState: { key: 'ready' }, stateKey: 'state.ready', tone: 'info', pending: 0 }),
    instructionOmitted: ref(false), writing: ref(null), reviewHistory: { data: ref({ reviews: [], rejections: [] }) },
    test: { status: ref('ready'), errorKey: ref(null), form: ref('graph'), runState: ref({ key: 'ready', done: 0, total: 1 }),
      counts: ref(null), availability, history: ref([{ runId: 'old-run', statusKey: 'state.passed', at: '2026-11-04' }]), busy: ref(null), failure: ref(null) },
    can: () => ({ enabled: true, reason: null }), openReason: () => null, openWindow, run, cancel, rewriteAll, reload: vi.fn(), reportRecentlyConfirmed: ref(false),
  }
  await open(TestSpecTab, selected)
  const instruction = wrapper!.get('[data-slot="instruction"]'), scenario = wrapper!.get('[data-slot="scenario"]'), report = wrapper!.get('[data-slot="report"]')
  expect(wrapper!.findAll('[data-test="inspector-document-decision"]')).toHaveLength(2)
  expect(scenario.find('[data-test="inspector-document-decision"]').exists()).toBe(false)
  expect(instruction.get('[data-test="test-card-brief"]').text()).toBe('✎')
  expect(report.get('[data-test="test-spec-run"]').text()).toBe('▶')
  expect(report.get('[data-test="test-card-run-history"]').text()).toBe('↺')
  expect(scenario.find('[data-test="test-card-review-history"]').exists()).toBe(true)
  expect(scenario.find('[data-test="test-card-scenario-open"]').exists()).toBe(true)
  await instruction.get('[data-test="test-card-brief"]').trigger('click')
  expect(openWindow).toHaveBeenCalledWith('brief')
  await scenario.get('[data-test="test-card-scenario-ai"]').trigger('click')
  expect(rewriteAll).toHaveBeenCalledTimes(1)
  await report.get('[data-test="test-spec-run"]').trigger('click')
  expect(run).toHaveBeenCalledTimes(1)
  for (const [slot, row] of [['instruction', instruction], ['report', report]] as const) {
    const b = row.get('[data-test="inspector-document-decision"]')
    expect(b.attributes('disabled')).toBeUndefined()
    expect(b.attributes('title')).toBe(b.attributes('aria-label'))
    expect(row.findAll('button').at(-1)!.attributes('data-test')).toBe('inspector-document-decision')
    await b.trigger('click'); await flushPromises()
    expect(fixture.post).toHaveBeenLastCalledWith(`/api/v1/flows/f_provider/nodes/gn_provider/documents/${slot}/decision`, expect.objectContaining({ doc_id: `doc_${slot}` }))
  }
  availability.value = { cancel: { visible: true, enabled: true, reason: null } }
  await flushPromises()
  expect(report.find('[data-test="test-spec-run"]').exists()).toBe(false)
  expect(report.get('[data-test="test-spec-cancel"]').text()).toBe('■')
  await report.get('[data-test="test-spec-cancel"]').trigger('click')
  expect(cancel).toHaveBeenCalledTimes(1)
  expect(report.find('[data-test="test-card-run-history"]').exists()).toBe(true)
  expect(scenario.find('[data-test="inspector-document-decision"]').exists()).toBe(false)
})
