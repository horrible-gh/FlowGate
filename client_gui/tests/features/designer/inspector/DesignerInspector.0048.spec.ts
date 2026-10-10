/**
 * flowgate.v02.0048 T#1 — the Inspector data path and Frame (D#1 §4 / §5).
 *
 * 1. A server Canvas read (P0008 §3 JSON) of a v0.1 Flow and of a Graph Flow goes through the
 *    real normalizer (toFlowCanvas), the server Graph source, the Center session and the Center
 *    column: selecting a Node shows its real document state in the Inspector header.
 * 2. The Frame lays out a fixture registry: tabs only where a registration contributes, Content
 *    parts stacked in `order`, header meta / actions, the tab number, tab selection rules, and a
 *    Node without a document showing the header only.
 * 3. Host intents travel Frame -> Dock -> Center column (-> Shell).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, h, inject } from 'vue'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { createServerFlowGraphSource, toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import type { InspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import DesignerBottomDock from '@/features/designer/shell/DesignerBottomDock.vue'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import { SELECTIONS, fixtureRegistry, prepareInspectorI18n, probePart, registration } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((t) => t.text())
const activeTabLabel = (w: VueWrapper<any>) => w.get('[data-test="designer-inspector-tabs"] [role="tab"][aria-selected="true"]').text()
const sections = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-section"]').map((s) => s.attributes('data-key'))

async function clickTab(w: VueWrapper<any>, label: string) {
  const tab = w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((t) => t.text() === label)
  if (!tab) throw new Error(`tab not rendered: ${label}`)
  await tab.trigger('click')
}

// --- 1. Server Canvas read -> Inspector header ------------------------------------------------

const V01_RAW = {
  flow_id: 'flowgate.v02.0048',
  project_id: 'flowgate',
  ownership: { state: 'v01', sequence_root_doc_id: 'flowgate.v02.0048.0001-R', graph_revision_no: null, assigned_at: null },
  projection: 'sequence',
  view: null,
  graph: { draft: null, latest_revision_no: null, root_binding: null },
  nodes: [
    {
      node_key: 'doc:flowgate.v02.0048.0001-R', identity_source: 'document', origin: 'document',
      kind: 'requirement', doc_type: 'R', title: '하단 인스펙터', state: 'done',
      position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: false },
      document: { doc_id: 'flowgate.v02.0048.0001-R', type_code: 'R', seq: 1, title: '하단 인스펙터', doc_review_status: 'approved', status: 'closed', supersedes: [] },
      slot: null, graph_node: null, node_run: null,
    },
    {
      node_key: 'slot_t1', identity_source: 'sequence_slot', origin: 'sequence_slot',
      kind: 'task', doc_type: 'TR', title: 'T#1 결과', state: 'blocked',
      position: { x: 260, y: 0, source: 'auto' }, editable: { move: true, structure: false },
      document: { doc_id: 'flowgate.v02.0048.0011-TR', type_code: 'TR', seq: 11, title: 'T#1 결과', doc_review_status: 'rejected', status: 'open', supersedes: ['flowgate.v02.0048.0009-TR'] },
      slot: { item_id: 4821, item_seq: 11, order: 4, type: 'TR', label: 'T#1 결과', derived_status: 'rejected', protected: false, source_wp_card_id: 'c_t1' },
      graph_node: null, node_run: null,
    },
  ],
  edges: [],
  external_relations: [],
  viewport: null,
  run: null,
}

const GRAPH_RAW = {
  flow_id: 'flowgate.v02.0049',
  project_id: 'flowgate',
  ownership: { state: 'graph', sequence_root_doc_id: null, graph_revision_no: 2, assigned_at: '2026-10-09T00:00:00Z' },
  projection: 'graph',
  view: { mode: 'revision', revision_no: 2, draft_version: null },
  graph: { draft: null, latest_revision_no: 2, root_binding: { node_key: 'gn_req', doc_id: 'flowgate.v02.0049.0001-R', bound_at_revision_no: 1 } },
  nodes: [
    {
      node_key: 'gn_tr', identity_source: 'graph_node', origin: 'graph_revision',
      kind: 'task', doc_type: 'TR', title: '구현 레포트', state: 'in_progress',
      position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: false },
      document: { doc_id: 'flowgate.v02.0049.0004-TR', type_code: 'TR', seq: 4, title: '구현 레포트', doc_review_status: 'pending_review', status: 'open', supersedes: [] },
      slot: null,
      graph_node: { node_type: 'document', label: '구현 레포트', palette_item_id: 'doc_tr', assignee: null, root_bound: false },
      node_run: { status: 'waiting_review', opened_at: '2026-10-09T01:00:00Z', finished_at: null, result_doc_id: 'flowgate.v02.0049.0004-TR' },
    },
    {
      node_key: 'gn_gate', identity_source: 'graph_node', origin: 'graph_revision',
      kind: 'approval', doc_type: null, title: '확인', state: 'draft',
      position: { x: 260, y: 0, source: 'saved' }, editable: { move: true, structure: false },
      document: null, slot: null,
      graph_node: { node_type: 'gate', label: '확인', palette_item_id: 'gate', assignee: null, root_bound: false },
      node_run: null,
    },
  ],
  edges: [],
  external_relations: [],
  viewport: null,
  run: { run_id: 'r1', status: 'running' },
}

describe('server Canvas read keeps document / slot / node run on the Graph Node', () => {
  it('v0.1 Flow', () => {
    const graph = toFlowGraph(toFlowCanvas(V01_RAW))
    expect(graph.nodes[1]).toMatchObject({
      document: { docId: 'flowgate.v02.0048.0011-TR', typeCode: 'TR', seq: 11, docReviewStatus: 'rejected', status: 'open', supersedes: ['flowgate.v02.0048.0009-TR'] },
      slot: { itemId: 4821, itemSeq: 11, order: 4, type: 'TR', label: 'T#1 결과', derivedStatus: 'rejected', protected: false, sourceWpCardId: 'c_t1' },
      nodeRun: null,
    })
  })

  it('Graph Flow', () => {
    const graph = toFlowGraph(toFlowCanvas(GRAPH_RAW))
    expect(graph.nodes[0].nodeRun).toEqual({ status: 'waiting_review', openedAt: '2026-10-09T01:00:00Z', finishedAt: null, resultDocId: 'flowgate.v02.0049.0004-TR' })
    expect(graph.nodes[1]).toMatchObject({ document: null, slot: null, nodeRun: null, definition: { nodeType: 'gate' } })
  })
})

const WorkspaceStub = defineComponent({ name: 'DesignerWorkspace', render: () => h('section', { 'data-test': 'designer-workspace' }) })

function serverSession(raws: Record<string, Record<string, unknown>>) {
  const loadFlowCanvas = vi.fn(async (flowId: string) => toFlowCanvas(raws[flowId]))
  const source = createServerFlowGraphSource({ loadFlowCanvas, saveFlowCanvasLayout: vi.fn() })
  const center = createDesignerCenterSession({ source })
  center.setProject('flowgate')
  return { center, loadFlowCanvas }
}

async function mountColumn(center: ReturnType<typeof createDesignerCenterSession>) {
  wrapper?.unmount()
  wrapper = mount(DesignerCenterColumn, {
    attachTo: document.body,
    props: { center, bottomHeight: 300 },
    global: { plugins: [i18n], stubs: { DesignerWorkspace: WorkspaceStub } },
  })
  await flushPromises()
  return wrapper
}

async function selectThroughSession(raw: typeof V01_RAW | typeof GRAPH_RAW, nodeId: string) {
  const { center } = serverSession({ [raw.flow_id]: raw })
  const w = await mountColumn(center)
  center.openFlow({ projectId: 'flowgate', flowId: raw.flow_id, flowName: 'F' })
  await flushPromises()
  center.selectNode(raw.flow_id, nodeId)
  await flushPromises()
  return { w, center }
}

describe('Node selection shows the real document state in the header', () => {
  it('ownership v01: review status of the bound document, the slot Node title and kind', async () => {
    const { center } = serverSession({ 'flowgate.v02.0048': V01_RAW })
    const w = await mountColumn(center)
    center.openFlow({ projectId: 'flowgate', flowId: 'flowgate.v02.0048', flowName: '0048' })
    await flushPromises()
    center.selectNode('flowgate.v02.0048', 'slot_t1')
    await flushPromises()
    expect(center.activeSelection.value).toMatchObject({ projectId: 'flowgate', graph: { flowId: 'flowgate.v02.0048', ownership: 'v01' }, node: { id: 'slot_t1' } })
    expect(get(w, 'designer-inspector-kind').text()).toBe('작업')
    expect(get(w, 'designer-inspector-title').text()).toBe('T#1 결과')
    const status = get(w, 'designer-inspector-doc-status')
    expect(status.text()).toBe('반려됨')
    expect(status.attributes('data-value')).toBe('rejected')
    expect(status.attributes('title')).toBe('문서 상태')
    expect(get(w, 'designer-inspector-state').text()).toBe('보류')
    expect(w.find('[data-test="designer-inspector-run-status"]').exists()).toBe(false)

    center.selectNode('flowgate.v02.0048', 'doc:flowgate.v02.0048.0001-R')
    await flushPromises()
    expect(get(w, 'designer-inspector-doc-status').text()).toBe('승인됨')
  })

  it('ownership graph: the bound document status and the node run status; a gate shows neither', async () => {
    const { center } = serverSession({ 'flowgate.v02.0049': GRAPH_RAW })
    const w = await mountColumn(center)
    center.openFlow({ projectId: 'flowgate', flowId: 'flowgate.v02.0049', flowName: '0049' })
    await flushPromises()
    center.selectNode('flowgate.v02.0049', 'gn_tr')
    await flushPromises()
    expect(get(w, 'designer-inspector-title').text()).toBe('구현 레포트')
    expect(get(w, 'designer-inspector-doc-status').text()).toBe('검수 대기')
    const run = get(w, 'designer-inspector-run-status')
    expect(run.text()).toBe('waiting_review')
    expect(run.attributes('title')).toBe('실행 상태')

    center.selectNode('flowgate.v02.0049', 'gn_gate')
    await flushPromises()
    expect(get(w, 'designer-inspector-title').text()).toBe('확인')
    expect(w.find('[data-test="designer-inspector-doc-status"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-inspector-run-status"]').exists()).toBe(false)
  })

  it('an unknown review status shows as sent; en shows its own wording', async () => {
    const raw = { ...V01_RAW, nodes: [{ ...V01_RAW.nodes[0], document: { ...V01_RAW.nodes[0].document, doc_review_status: 'wf_done' } }] }
    const { w } = await selectThroughSession(raw, 'doc:flowgate.v02.0048.0001-R')
    expect(get(w, 'designer-inspector-doc-status').text()).toBe('wf_done')
    i18n.global.locale.value = 'en'
    const { w: en } = await selectThroughSession(V01_RAW, 'slot_t1')
    expect(get(en, 'designer-inspector-doc-status').text()).toBe('Rejected')
    expect(get(en, 'designer-inspector-doc-status').attributes('title')).toBe('Document status')
  })
})

// --- 2. Frame with a fixture registry -----------------------------------------------------------

const COUNT_TARGETS: (string | null)[] = []
const qnaCount = (target: { value: InspectorTarget | null }) => {
  return computed(() => {
    COUNT_TARGETS.push(target.value?.docId ?? null)
    return target.value ? 3 : null
  })
}

const REGISTRY = fixtureRegistry([
  registration({ key: 'documents', order: 500, tab: { key: 'documents', component: probePart('documents') } }),
  registration({ key: 'workPlan', order: 410, cardKinds: ['plan'], tab: { key: 'documents', component: probePart('workPlan') } }),
  registration({ key: 'qna', order: 700, tab: { key: 'qna', component: probePart('qna'), count: qnaCount } }),
  registration({ key: 'settings', order: 900, ownerships: ['v01'], cardKinds: ['task', 'research', 'design', 'test'], tab: { key: 'settings', component: probePart('settings') } }),
  registration({ key: 'actions', order: 600, headerAction: probePart('actions') }),
  registration({ key: 'badge', order: 100, requiresDocument: false, headerMeta: probePart('badge') }),
])

function mountFrame(props: Record<string, unknown>, registry: InspectorRegistry = REGISTRY) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props: { registry, ...props }, global: { plugins: [i18n] } })
  return wrapper
}

describe('DesignerInspector — registry layout', () => {
  it('shows four tabs in fixed order and keeps the contributed Q&A number', () => {
    const w = mountFrame({ selection: SELECTIONS.v01 })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A (3)', '첨부', '설정'])
    expect(activeTabLabel(w)).toBe('문서')
    expect(get(w, 'designer-inspector-body').attributes('data-tab')).toBe('documents')
    expect(sections(w)).toEqual(['documents'])
    expect(get(w, 'probe-documents').attributes('data-doc')).toBe('flowgate.v02.0048.0011-TR')
  })

  it('stacks the Content parts of one tab in `order`', () => {
    const wp = { ...SELECTIONS.unassigned, node: { ...SELECTIONS.unassigned.node, document: { ...SELECTIONS.unassigned.node.document!, typeCode: 'WP' } } }
    const w = mountFrame({ selection: wp })
    // flowgate.v02.0052: card slots lead the 문서 tab; the common documents section is not repeated beneath them.
    expect(sections(w)).toEqual(['workPlan'])
    expect(tabLabels(w)).toEqual(['문서', 'Q&A (3)', '첨부', '설정'])
  })

  it('puts header meta before the state chip and header actions in the actions area', () => {
    const w = mountFrame({ selection: SELECTIONS.graph })
    const header = get(w, 'designer-inspector-header')
    const order = [...header.element.querySelectorAll('[data-test]')].map((el) => el.getAttribute('data-test'))
    expect(order.indexOf('probe-badge')).toBeLessThan(order.indexOf('designer-inspector-doc-status'))
    expect(order.indexOf('designer-inspector-doc-status')).toBeLessThan(order.indexOf('designer-inspector-state'))
    expect(get(w, 'designer-inspector-actions').get('[data-test="probe-actions"]').exists()).toBe(true)
    // graph ownership: no settings registration applies.
    expect(tabLabels(w)).toEqual(['문서', 'Q&A (3)', '첨부', '설정'])
  })

  it('a Node without a document keeps the four-tab frame and an empty document state', () => {
    const w = mountFrame({ selection: SELECTIONS.graphGate })
    expect(get(w, 'designer-inspector').attributes('data-selected')).toBe('true')
    expect(get(w, 'designer-inspector-title').text()).toBe('확인 게이트')
    expect(get(w, 'probe-badge').exists()).toBe(true)
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    expect(w.find('[data-test="designer-inspector-actions"]').exists()).toBe(false)
  })

  it('with no registration a document Node keeps four tabs and an empty state', () => {
    const w = mountFrame({ selection: SELECTIONS.v01 }, fixtureRegistry([]))
    expect(get(w, 'designer-inspector-doc-status').text()).toBe('검수 대기')
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    expect(w.find('[data-test="designer-inspector-actions"]').exists()).toBe(false)
  })

  it('keeps the selected tab across Nodes, including an empty settings tab', async () => {
    const w = mountFrame({ selection: SELECTIONS.v01 })
    await clickTab(w, '설정')
    expect(sections(w)).toEqual(['settings'])
    await w.setProps({ selection: null })
    await w.setProps({ selection: SELECTIONS.v01 })
    expect(activeTabLabel(w)).toBe('설정')
    // A documentless Node keeps four tabs and the selected settings state.
    await w.setProps({ selection: SELECTIONS.graphGate })
    await w.setProps({ selection: SELECTIONS.v01 })
    expect(activeTabLabel(w)).toBe('설정')
    await w.setProps({ selection: SELECTIONS.graph })
    expect(activeTabLabel(w)).toBe('설정')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('disabled')
    await w.setProps({ selection: SELECTIONS.v01 })
    expect(activeTabLabel(w)).toBe('설정')
    await clickTab(w, 'Q&A (3)')
    await w.setProps({ selection: SELECTIONS.unassigned })
    expect(activeTabLabel(w)).toBe('Q&A (3)')
    expect(sections(w)).toEqual(['qna'])
  })

  it('gives a tab number supplier the target only while its registration applies', async () => {
    const counted = fixtureRegistry([
      registration({ key: 'qna', order: 700, cardKinds: ['task'], tab: { key: 'qna', component: probePart('qna'), count: qnaCount } }),
      registration({ key: 'documents', order: 500, tab: { key: 'documents', component: probePart('documents') } }),
    ])
    COUNT_TARGETS.length = 0
    const w = mountFrame({ selection: SELECTIONS.v01 }, counted)
    expect(tabLabels(w)).toEqual(['문서', 'Q&A (3)', '첨부', '설정'])
    await w.setProps({ selection: SELECTIONS.graph })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(COUNT_TARGETS).toContain('flowgate.v02.0048.0011-TR')
    expect(COUNT_TARGETS).not.toContain('flowgate.v02.0049.0007-TS')
  })

  it('merges the registry strings so a feature part can use them', () => {
    const Part = defineComponent({
      props: { target: { type: Object, required: true } },
      setup: () => () => h('p', { 'data-test': 'worded' }, i18n.global.t('gui.inspector.worded.title')),
    })
    const registry = fixtureRegistry([
      registration({ key: 'worded', order: 1, tab: { key: 'documents', component: Part }, messages: { ko: { title: '단어' }, en: { title: 'Word' }, ja: { title: '単語' } } }),
    ])
    const w = mountFrame({ selection: SELECTIONS.v01 }, registry)
    expect(get(w, 'worded').text()).toBe('단어')
  })
})

// --- 3. Host intents --------------------------------------------------------------------------

function intentPart(intent: InspectorHostIntent) {
  return defineComponent({
    name: `Intent_${intent.kind}`,
    props: { target: { type: Object, required: true } },
    setup() {
      const host = inject(INSPECTOR_HOST)!
      return () => h('button', { 'data-test': `intent-${intent.kind}`, onClick: () => host.request(intent) }, intent.kind)
    },
  })
}

describe('Host intents', () => {
  it('leave the Frame as the `host` event', async () => {
    const registry = fixtureRegistry([registration({ key: 'go', order: 1, headerAction: intentPart({ kind: 'select-node', nodeId: 'x' }) })])
    const w = mountFrame({ selection: SELECTIONS.v01 }, registry)
    await get(w, 'intent-select-node').trigger('click')
    expect(w.emitted('host')).toEqual([[{ kind: 'select-node', nodeId: 'x' }]])
  })

  it('select-node / refresh-flow are carried out by the Center column, show-tool goes up to the Shell', async () => {
    const { center, loadFlowCanvas } = serverSession({ 'flowgate.v02.0048': V01_RAW })
    center.openFlow({ projectId: 'flowgate', flowId: 'flowgate.v02.0048', flowName: '0048' })
    await flushPromises()
    center.selectNode('flowgate.v02.0048', 'doc:flowgate.v02.0048.0001-R')
    wrapper = mount(DesignerCenterColumn, {
      attachTo: document.body,
      props: { center, bottomHeight: 300 },
      global: { plugins: [i18n], stubs: { DesignerWorkspace: WorkspaceStub } },
    })
    await flushPromises()
    const inspector = wrapper.findComponent(DesignerInspector)
    const reads = loadFlowCanvas.mock.calls.length

    inspector.vm.$emit('host', { kind: 'select-node', nodeId: 'slot_t1' })
    await flushPromises()
    expect(center.activeSelectedNode.value?.id).toBe('slot_t1')
    expect(get(wrapper, 'designer-inspector-title').text()).toBe('T#1 결과')

    inspector.vm.$emit('host', { kind: 'refresh-flow' })
    await flushPromises()
    expect(loadFlowCanvas.mock.calls.length).toBe(reads + 1)
    // A quiet read: the Graph stayed on screen and the selection is kept.
    expect(center.activeSelectedNode.value?.id).toBe('slot_t1')

    inspector.vm.$emit('host', { kind: 'show-tool', tool: 'git' })
    expect(wrapper.emitted('show-tool')).toEqual([['git']])
  })

  it('the Dock hands the Inspector event to the Center column it sits in (provide / inject), not as its own event', async () => {
    const { center } = serverSession({ 'flowgate.v02.0048': V01_RAW })
    center.openFlow({ projectId: 'flowgate', flowId: 'flowgate.v02.0048', flowName: '0048' })
    await flushPromises()
    center.selectNode('flowgate.v02.0048', 'slot_t1')
    wrapper = mount(DesignerCenterColumn, {
      attachTo: document.body,
      props: { center, bottomHeight: 300 },
      global: { plugins: [i18n], stubs: { DesignerWorkspace: WorkspaceStub } },
    })
    await flushPromises()
    wrapper.findComponent(DesignerInspector).vm.$emit('host', { kind: 'show-tool', tool: 'git' })
    expect(wrapper.findComponent({ name: 'DesignerBottomDock' }).emitted('host')).toBeUndefined()
    expect(wrapper.emitted('show-tool')).toEqual([['git']])
  })

  it('a Dock mounted without the Center column gives the Inspector no selection and drops its intents', async () => {
    wrapper = mount(DesignerBottomDock, {
      attachTo: document.body,
      props: { height: 300, selectedNode: null },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(get(wrapper, 'designer-inspector').attributes('data-selected')).toBe('false')
    expect(() => wrapper.findComponent(DesignerInspector).vm.$emit('host', { kind: 'refresh-flow' })).not.toThrow()
  })
})

describe('Center session — refreshFlow', () => {
  it('reads an open Flow again in the background and ignores a closed one', async () => {
    const { center, loadFlowCanvas } = serverSession({ 'flowgate.v02.0048': V01_RAW })
    center.openFlow({ projectId: 'flowgate', flowId: 'flowgate.v02.0048', flowName: '0048' })
    await flushPromises()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(1)
    center.refreshFlow('flowgate.v02.0048')
    expect(center.activeGraphLoad.value?.status).toBe('ready')
    await flushPromises()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    center.refreshFlow('flowgate.v02.0099')
    await flushPromises()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
  })
})
