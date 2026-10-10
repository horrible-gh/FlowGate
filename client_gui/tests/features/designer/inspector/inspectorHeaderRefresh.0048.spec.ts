/**
 * flowgate.v02.0048 T#3 — the Inspector header follows server changes of its target (D#1 §7.3).
 * A `doc_review_status_changed` / `ai_invoke_finished` / `document_explorer_refresh` frame about
 * the target document (or its Flow, with no doc_id) makes the Frame ask for `refresh-flow`; the
 * Center session reads the Flow's Canvas again quietly and the header shows the new state. The
 * Frame keeps no server state of its own. The real hub runs on the support module's fake stream.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { useProjectStore } from '@/features/project'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import { SELECTIONS, prepareInspectorI18n } from './support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from './support/projectEventStream'

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0048'
const TR = 'flowgate.v02.0048.0011-TR'
const ROOT = 'flowgate.v02.0048.0001-R'

/** The v0.1 Flow's Canvas read (P0008 §3 JSON) with the TR's review status as given. */
function v01Raw(trReviewStatus: string, trState = 'blocked') {
  return {
    flow_id: FLOW,
    project_id: 'flowgate',
    ownership: { state: 'v01', sequence_root_doc_id: ROOT, graph_revision_no: null, assigned_at: null },
    projection: 'sequence',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [
      {
        node_key: `doc:${ROOT}`, identity_source: 'document', origin: 'document',
        kind: 'requirement', doc_type: 'R', title: '하단 인스펙터', state: 'done',
        position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: false },
        document: { doc_id: ROOT, type_code: 'R', seq: 1, title: '하단 인스펙터', doc_review_status: 'approved', status: 'closed', supersedes: [] },
        slot: null, graph_node: null, node_run: null,
      },
      {
        node_key: 'slot_t1', identity_source: 'sequence_slot', origin: 'sequence_slot',
        kind: 'task', doc_type: 'TR', title: 'T#1 결과', state: trState,
        position: { x: 260, y: 0, source: 'auto' }, editable: { move: true, structure: false },
        document: { doc_id: TR, type_code: 'TR', seq: 11, title: 'T#1 결과', doc_review_status: trReviewStatus, status: 'open', supersedes: [] },
        slot: { item_id: 4821, item_seq: 11, order: 4, type: 'TR', label: 'T#1 결과', derived_status: trState, protected: false, source_wp_card_id: 'c_t1' },
        graph_node: null, node_run: null,
      },
    ],
    edges: [],
    external_relations: [],
    viewport: null,
    run: null,
  }
}

let stream: ProjectEventStream
let pinia: Pinia
let wrapper: VueWrapper<any> | null = null
let session: ReturnType<typeof createDesignerCenterSession> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  vi.useFakeTimers()
  stream = installProjectEventStream()
  pinia = createPinia()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  // The session's Canvas subscription is released with it, so no hub outlives the case.
  session?.dispose()
  session = null
  removeProjectEventStream()
  vi.useRealTimers()
})

const get = (test: string) => wrapper!.get(`[data-test="${test}"]`)

async function passWindow() {
  await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
  await flushPromises()
}

const WorkspaceStub = defineComponent({ name: 'DesignerWorkspace', render: () => h('section') })

/** Center session on a server source whose Canvas read answers the current `raw`. */
async function selectTask() {
  const server = { raw: v01Raw('pending_review', 'in_progress') }
  const loadFlowCanvas = vi.fn(async () => toFlowCanvas(server.raw))
  // The Canvas change subscription of the app (project store) — the Canvas is on the stream too.
  const store = useProjectStore(pinia)
  const source = createServerFlowGraphSource({
    loadFlowCanvas,
    saveFlowCanvasLayout: vi.fn(),
    subscribeFlowCanvasChanges: (projectId, handlers) => store.subscribeFlowCanvasChanges(projectId, handlers),
  })
  const center = createDesignerCenterSession({ source })
  session = center
  center.setProject('flowgate')
  wrapper = mount(DesignerCenterColumn, {
    attachTo: document.body,
    props: { center, bottomHeight: 300 },
    global: { plugins: [i18n, pinia], stubs: { DesignerWorkspace: WorkspaceStub } },
  })
  center.openFlow({ projectId: 'flowgate', flowId: FLOW, flowName: '0048' })
  await flushPromises()
  center.selectNode(FLOW, 'slot_t1')
  await flushPromises()
  stream.open()
  return { server, loadFlowCanvas, center }
}

describe('header state after a document change event (through the Center session)', () => {
  it('a review status change of the target document reads the Flow again and the header shows the new status', async () => {
    const { server, loadFlowCanvas } = await selectTask()
    expect(get('designer-inspector-doc-status').text()).toBe('검수 대기')
    expect(get('designer-inspector-state').text()).toBe('진행 중')
    expect(loadFlowCanvas).toHaveBeenCalledTimes(1)

    server.raw = v01Raw('approved', 'done')
    stream.emit('doc_review_status_changed', { docId: TR, groupId: FLOW, payload: { doc_review_status: 'approved' } })
    expect(get('designer-inspector-doc-status').text()).toBe('검수 대기')
    await passWindow()

    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    const status = get('designer-inspector-doc-status')
    expect(status.text()).toBe('승인됨')
    expect(status.attributes('data-value')).toBe('approved')
    expect(get('designer-inspector-state').text()).toBe('완료')
  })

  it('a burst of events is one read again; an event of another document is none', async () => {
    const { server, loadFlowCanvas } = await selectTask()
    stream.emit('doc_review_status_changed', { docId: ROOT, groupId: FLOW })
    stream.emit('doc_review_status_changed', { docId: 'flowgate.v02.0049.0004-TR', groupId: 'flowgate.v02.0049' })
    await passWindow()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(1)

    server.raw = v01Raw('rejected')
    stream.emit('ai_invoke_finished', { groupId: FLOW })
    stream.emit('document_explorer_refresh', { docId: TR, groupId: FLOW })
    stream.emit('doc_review_status_changed', { docId: TR, groupId: FLOW })
    await passWindow()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    expect(get('designer-inspector-doc-status').text()).toBe('반려됨')
  })

  it('the Canvas and the Inspector share one project stream; each hears its own events', async () => {
    const { server, loadFlowCanvas } = await selectTask()
    expect(stream.connections()).toHaveLength(1)
    const [connection] = stream.connections()
    expect(connection.listened('graph_canvas_changed')).toBe(1)
    expect(connection.listened('doc_review_status_changed')).toBe(1)

    // A Canvas change is the Canvas's own read again; the header follows it without an Inspector request.
    server.raw = v01Raw('revised')
    stream.emit('graph_canvas_changed', { groupId: FLOW })
    await passWindow()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    expect(get('designer-inspector-doc-status').text()).toBe('수정됨')
  })
})

describe('Frame — refresh-flow from the target events', () => {
  function mountFrame(props: Record<string, unknown>) {
    wrapper = mount(DesignerInspector, { attachTo: document.body, props, global: { plugins: [i18n, pinia] } })
    stream.open()
    return wrapper
  }
  const refreshes = () => (wrapper!.emitted('host') ?? []).filter(([intent]) => (intent as { kind: string }).kind === 'refresh-flow').length

  it('asks once per window for each event name of the header', async () => {
    const target = SELECTIONS.v01.node.document!.docId
    mountFrame({ selection: SELECTIONS.v01 })
    for (const type of ['doc_review_status_changed', 'ai_invoke_finished', 'document_explorer_refresh']) {
      stream.emit(type, { docId: target })
      await passWindow()
    }
    expect(refreshes()).toBe(3)
  })

  it('a Graph Flow Node: frames of its document; a gate without a document: Flow-level frames only', async () => {
    const w = mountFrame({ selection: SELECTIONS.graph })
    stream.emit('doc_review_status_changed', { docId: SELECTIONS.graph.node.document!.docId })
    await passWindow()
    expect(refreshes()).toBe(1)

    await w.setProps({ selection: SELECTIONS.graphGate })
    stream.emit('doc_review_status_changed', { docId: SELECTIONS.graph.node.document!.docId })
    await passWindow()
    expect(refreshes()).toBe(1)
    stream.emit('ai_invoke_finished', { groupId: SELECTIONS.graphGate.graph.flowId })
    await passWindow()
    expect(refreshes()).toBe(2)
  })

  it('asks nothing for other events, with no selection, when unselected, or after a reconnect', async () => {
    const w = mountFrame({ selection: SELECTIONS.v01 })
    stream.emit('qna_q_registered', { docId: SELECTIONS.v01.node.document!.docId })
    stream.reconnect()
    await passWindow()
    expect(refreshes()).toBe(0)

    await w.setProps({ selection: null })
    stream.emit('ai_invoke_finished', { groupId: SELECTIONS.v01.graph.flowId })
    await passWindow()
    await w.setProps({ selection: SELECTIONS.v01, unselected: true })
    stream.emit('doc_review_status_changed', { docId: SELECTIONS.v01.node.document!.docId })
    await passWindow()
    expect(refreshes()).toBe(0)
  })

  it('a pending request for the previous Node is dropped when another Node is selected', async () => {
    const w = mountFrame({ selection: SELECTIONS.v01 })
    stream.emit('doc_review_status_changed', { docId: SELECTIONS.v01.node.document!.docId })
    await w.setProps({ selection: SELECTIONS.unassigned })
    await passWindow()
    expect(refreshes()).toBe(0)
  })

  it('unmounting the Frame leaves the project stream', async () => {
    mountFrame({ selection: SELECTIONS.v01 })
    expect(stream.connections()).toHaveLength(1)
    wrapper!.unmount()
    wrapper = null
    expect(stream.connections()).toHaveLength(0)
  })
})
