/**
 * flowgate.v02.0048 T#10 — the kind switch through the real Center session (D#2 §3.4 / §3.5 WP0006 T#10
 * 완료 조건): the Center column (session on a server Graph source answering the Canvas read P0008 §3) with
 * the bottom Inspector and the real registry; only the document calls and the Canvas read are mocked.
 *
 * - unassigned: R -> B. The switch asks `refresh-flow`; the session reads the Canvas again; the old node
 *   key `doc:<…-R>` is gone, so the selection is cleared (the Inspector is empty); the Canvas shows the
 *   root as `doc:<…-B>` of type B. Selecting it, the Inspector agrees with the Canvas (the same document,
 *   the switch back to R), and B -> R closes the loop.
 * - v01 / graph / conflict: the root shows no switch at all (the server refuses there).
 * - a stale Canvas (the Flow became a Graph Flow meanwhile): the server's flat 409 flow_owned_by_graph is
 *   shown, the Canvas is read again, and the new selection shows no switch.
 * - another Flow tab activated while the switch waits: `refresh-flow` names the switch's Flow, so that
 *   Flow is read again at once in its own tab, and the active Flow keeps its read count, Canvas and
 *   selection (with or without a switch on screen); on return the Canvas and the Inspector agree with
 *   the server. A closed tab is not read.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import { prepareInspectorI18n } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../support/projectEventStream'

const convertCall = vi.fn()
const documentRead = vi.fn()
const activityRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaRead = vi.fn()
const attachmentsRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    kindSwitchConvert: (...args: unknown[]) => convertCall(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // flowgate.v02.0064 T#4: the switch reads the document state with its judgement (P0006 §6.1).
    kindSwitchPreviewRead: (...args: unknown[]) => documentRead(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaRead(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsRead(...args),
  }
})

const { toReviewDocument, toReviewActivity, toKindSwitchResult, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0050'
const ROOT_R = `${FLOW}.0001-R`
const ROOT_B = `${FLOW}.0001-B`
const START_KEY = 'gn_start000001'
/** A second unassigned Flow, opened in another tab. */
const OTHER = 'flowgate.v02.0051'
const OTHER_ROOT = `${OTHER}.0001-R`

// --- The server: one Flow, its root document and its ownership ---------------------------------------

const server = {
  ownership: 'unassigned' as string,
  rootId: ROOT_R,
  /** A switch answer refused with this (once). */
  refusal: null as unknown,
}

const typeOf = (docId: string) => docId.slice(-1)

function rootNode() {
  const type = typeOf(server.rootId)
  const graph = server.ownership === 'graph' || server.ownership === 'conflict'
  return {
    // A Graph Flow keys the root by its start Node; every other projection by the document.
    node_key: graph ? START_KEY : `doc:${server.rootId}`,
    identity_source: graph ? 'graph_node' : 'document',
    origin: graph ? 'graph_node' : 'document',
    kind: 'requirement',
    doc_type: type,
    title: '새 요구사항',
    state: 'draft',
    position: { x: 0, y: 0, source: 'auto' },
    editable: { move: true, structure: false },
    document: { doc_id: server.rootId, type_code: type, seq: 1, title: '새 요구사항', doc_review_status: null, status: 'draft', supersedes: [] },
    slot: server.ownership === 'v01' ? { item_id: 1, item_seq: 1, order: 0, type, label: '요구', derived_status: 'done', protected: true, source_wp_card_id: null } : null,
    graph_node: graph ? { node_type: 'document', label: '요구', palette_item_id: 'doc_r', assignee: null, root_bound: true } : null,
    node_run: null,
  }
}

/** The other Flow's Canvas: an unassigned Flow whose root is an R document (it shows a switch too). */
function otherCanvasRaw() {
  const raw = canvasRaw()
  const node = raw.nodes[0]
  return {
    ...raw,
    flow_id: OTHER,
    ownership: { state: 'unassigned', sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
    projection: 'documents',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [
      {
        ...node,
        node_key: `doc:${OTHER_ROOT}`,
        identity_source: 'document',
        origin: 'document',
        doc_type: 'R',
        document: { ...node.document, doc_id: OTHER_ROOT, type_code: 'R' },
        slot: null,
        graph_node: null,
      },
    ],
  }
}

function canvasRaw() {
  const graph = server.ownership === 'graph' || server.ownership === 'conflict'
  return {
    flow_id: FLOW,
    project_id: 'flowgate',
    ownership: {
      state: server.ownership,
      sequence_root_doc_id: server.ownership === 'v01' || server.ownership === 'conflict' ? server.rootId : null,
      graph_revision_no: graph ? 1 : null,
      assigned_at: null,
    },
    projection: graph ? 'graph' : server.ownership === 'v01' ? 'sequence' : 'documents',
    view: graph ? { mode: 'revision', revision_no: 1, draft_version: null } : null,
    graph: { draft: null, latest_revision_no: graph ? 1 : null, root_binding: null },
    nodes: [rootNode()],
    edges: [],
    external_relations: [],
    viewport: null,
    run: null,
  }
}

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

function serve() {
  documentRead.mockImplementation(async (docId: string) => {
    if (docId !== server.rootId && docId !== OTHER_ROOT) throw axiosError(404, { detail: `Document not found: ${docId}` })
    return toReviewDocument({ doc_id: docId, group_id: docId === OTHER_ROOT ? OTHER : FLOW, type_code: typeOf(docId), title: '새 요구사항', status: 'draft', doc_review_status: null, group_disposed: false })
  })
  activityRead.mockImplementation(async (groupId: string) => toReviewActivity({ ok: true, active: false, group_id: groupId }))
  convertCall.mockImplementation(async (docId: string, newType: string) => {
    if (server.refusal) {
      const error = server.refusal
      server.refusal = null
      throw error
    }
    // The server's rule after D#2 §4.2: only an unassigned Flow converts.
    if (server.ownership === 'graph') throw axiosError(409, { error: 'flow_owned_by_graph', doc_id: docId, group_id: FLOW, graph_revision_no: 1 })
    if (server.ownership === 'conflict') throw axiosError(409, { error: 'flow_ownership_conflict', doc_id: docId, group_id: FLOW, graph_revision_no: 1 })
    if (server.ownership === 'v01') throw axiosError(409, { detail: 'Cannot convert root type after the workflow decision was taken.' })
    if (docId !== server.rootId) throw axiosError(404, { detail: `Document not found: ${docId}` })
    server.rootId = docId.replace(/-[RB]$/, `-${newType}`)
    return toKindSwitchResult({ data: { doc_id: server.rootId, project_id: 'flowgate', group_id: FLOW, type_code: newType, seq: 1, title: '새 요구사항', status: 'draft' } }, docId)
  })
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail({ doc_id: docId, group_id: FLOW, type_code: typeOf(docId), title: '새 요구사항' }))
  relationsRead.mockImplementation(async () => [])
  qaRead.mockImplementation(async (docId: string) => toQaThread({ qa: { doc_id: docId, status: null, items: [] } }))
  attachmentsRead.mockImplementation(async (docId: string) => toAttachmentsList({ doc_id: docId, items: [] }))
}

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let wrapper: VueWrapper<any> | null = null
let session: ReturnType<typeof createDesignerCenterSession> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  installProjectEventStream()
  pinia = createPinia()
  server.ownership = 'unassigned'
  server.rootId = ROOT_R
  server.refusal = null
  for (const mock of [convertCall, documentRead, activityRead, detailRead, relationsRead, qaRead, attachmentsRead]) mock.mockReset()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  session?.dispose()
  session = null
  removeProjectEventStream()
  document.body.innerHTML = ''
})

const WorkspaceStub = defineComponent({ name: 'DesignerWorkspace', render: () => h('section') })

async function openFlow() {
  const loadFlowCanvas = vi.fn(async (flowId: string) => toFlowCanvas(flowId === OTHER ? otherCanvasRaw() : canvasRaw()))
  const source = createServerFlowGraphSource({ loadFlowCanvas, saveFlowCanvasLayout: vi.fn() })
  const center = createDesignerCenterSession({ source })
  session = center
  center.setProject('flowgate')
  wrapper = mount(DesignerCenterColumn, {
    attachTo: document.body,
    props: { center, bottomHeight: 300 },
    global: { plugins: [i18n, pinia], stubs: { DesignerWorkspace: WorkspaceStub } },
  })
  center.openFlow({ projectId: 'flowgate', flowId: FLOW, flowName: '0050' })
  await flushPromises()
  return { center, loadFlowCanvas }
}

type Center = Awaited<ReturnType<typeof openFlow>>['center']

/** The Graph the session shows for a Flow (the same object until the Flow is read again). */
function graphOf(center: Center, flowId: string) {
  const load = center.state.graphs[flowId]
  if (load?.status !== 'ready') throw new Error(`Flow not ready: ${load?.status}`)
  return load.graph
}

/** The Canvas as the session shows it: the Flow's nodes with their key, type and document. */
function canvasNodes(center: Center, flowId = FLOW) {
  return graphOf(center, flowId).nodes.map((node) => ({ id: node.id, docType: node.docType, docId: node.document?.docId ?? null }))
}

async function select(center: Center, nodeId: string, flowId = FLOW) {
  center.selectNode(flowId, nodeId)
  await flushPromises()
}

/** How many times the session read the given Flow's Canvas. */
const readsOf = (loadFlowCanvas: Awaited<ReturnType<typeof openFlow>>['loadFlowCanvas'], flowId: string) =>
  loadFlowCanvas.mock.calls.filter(([id]) => id === flowId).length

/** Holds the next switch answer (as the server would answer it) until the returned function is called. */
function holdNextSwitch() {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  const answer = convertCall.getMockImplementation()!
  convertCall.mockImplementationOnce(async (...args: unknown[]) => {
    await gate
    return answer(...args)
  })
  return release
}

/** Opens the other Flow in a new tab (it becomes the active Flow). */
async function openOther(center: Center) {
  center.openFlow({ projectId: 'flowgate', flowId: OTHER, flowName: '0051' })
  await flushPromises()
}

async function activate(center: Center, flowId: string) {
  center.activateFlow(flowId)
  await flushPromises()
}

const find = (test: string) => wrapper!.find(`[data-test="${test}"]`)

/** flowgate.v02.0051 stage 2: the switch is the header 종류 배지 ▾ — open its menu and pick the other kind (0015-T T2: no confirmation). */
async function pickOther() {
  await find('kind-switch-open').trigger('click')
  await flushPromises()
  const to = find('kind-switch-open').attributes('data-to')
  document.body.querySelector<HTMLButtonElement>(`[data-test="kind-switch-item-${to}"]`)?.click()
  await flushPromises()
}

/** flowgate.v02.0070 (0015-T T2): picking the other kind is the switch — no confirmation opens on the way. */
async function switchKind() {
  await pickOther()
  expect(document.body.querySelectorAll('.ui-confirm-actions button')).toHaveLength(0)
}

describe('unassigned Flow: the switch, the Canvas and the Inspector agree (D#2 §3.4)', () => {
  it('R -> B -> R', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_R}`, docType: 'R', docId: ROOT_R }])
    await select(center, `doc:${ROOT_R}`)
    expect(find('designer-inspector-title').text()).toBe('새 요구사항')
    expect(find('kind-switch-open').text()).toBe('요구사항 ▾')
    expect(loadFlowCanvas).toHaveBeenCalledTimes(1)

    await switchKind()
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    // refresh-flow -> the session read the Canvas again: the root is now the B document.
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_B}`, docType: 'B', docId: ROOT_B }])
    // The old node key is gone: nothing is selected, the Inspector is empty (D#1 §10.2).
    expect(center.activeSelection.value).toBeNull()
    expect(center.state.views[FLOW].selectedNodeId).toBeNull()
    expect(find('designer-inspector-empty').exists()).toBe(true)
    expect(find('inspector-kind-switch').exists()).toBe(false)

    // Selecting the Canvas node, the Inspector shows that very document.
    detailRead.mockClear()
    documentRead.mockClear()
    await select(center, `doc:${ROOT_B}`)
    expect(center.activeSelection.value?.node.document?.docId).toBe(ROOT_B)
    expect(documentRead).toHaveBeenCalledWith(ROOT_B)
    // flowgate.v02.0052: the card slot leads the 문서 tab; the common documents detail is not read beneath it.
    expect(detailRead).not.toHaveBeenCalled()
    expect(documentRead).not.toHaveBeenCalledWith(ROOT_R)
    expect(find('kind-switch-open').attributes('data-to')).toBe('R')
    expect(find('kind-switch-open').text()).toBe('버그 ▾')

    await switchKind()
    expect(convertCall).toHaveBeenLastCalledWith(ROOT_B, 'R')
    expect(loadFlowCanvas).toHaveBeenCalledTimes(3)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_R}`, docType: 'R', docId: ROOT_R }])
    expect(center.activeSelection.value).toBeNull()
    await select(center, `doc:${ROOT_R}`)
    expect(find('kind-switch-open').attributes('data-to')).toBe('B')
  })

  it('a refused switch keeps the selection and the Canvas as they are when the Flow did not move', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    server.refusal = axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } })
    await switchKind()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(1)
    expect(center.activeSelection.value?.node.id).toBe(`doc:${ROOT_R}`)
    expect(find('kind-switch-line').text()).toBe('AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.')
  })

  it('a refusal that keeps the node key (children) reads the Canvas again and keeps the selection with its reason', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    server.refusal = axiosError(409, { detail: 'Cannot convert root type: child documents already reference it.' })
    await switchKind()
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_R}`, docType: 'R', docId: ROOT_R }])
    expect(center.activeSelection.value?.node.id).toBe(`doc:${ROOT_R}`)
    expect(find('kind-switch-line').text()).toBe('이 문서를 가리키는 하위 문서가 있어 전환이 거절되었습니다. Canvas를 다시 읽었습니다.')
  })
})

describe('v01 / graph / conflict: the root shows no switch (D#2 E1 / E2)', () => {
  it.each([
    ['v01', `doc:${ROOT_R}`],
    ['graph', START_KEY],
    ['conflict', START_KEY],
  ] as const)('%s', async (ownership, nodeKey) => {
    server.ownership = ownership
    const { center } = await openFlow()
    expect(canvasNodes(center)).toEqual([{ id: nodeKey, docType: 'R', docId: ROOT_R }])
    await select(center, nodeKey)
    expect(center.activeSelection.value?.graph.ownership).toBe(ownership)
    expect(find('designer-inspector-title').text()).toBe('새 요구사항')
    expect(find('inspector-kind-switch').exists()).toBe(false)
    expect(convertCall).not.toHaveBeenCalled()
  })
})

describe('a stale Canvas: the Flow became a Graph Flow meanwhile', () => {
  it('the flat 409 flow_owned_by_graph is shown, the Canvas is read again, and the new selection has no switch', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    server.ownership = 'graph'
    await pickOther()
    // The refusal is read before the Canvas answer moves the selection.
    await vi.waitFor(() => expect(convertCall).toHaveBeenCalledTimes(1))
    await flushPromises()
    expect(server.rootId).toBe(ROOT_R)
    expect(loadFlowCanvas).toHaveBeenCalledTimes(2)
    // The root is now the Graph start Node: the old key is gone, so nothing is selected.
    expect(canvasNodes(center)).toEqual([{ id: START_KEY, docType: 'R', docId: ROOT_R }])
    expect(center.activeSelection.value).toBeNull()
    await select(center, START_KEY)
    expect(center.activeSelection.value?.graph.ownership).toBe('graph')
    expect(find('inspector-kind-switch').exists()).toBe(false)
  })
})

describe("another Flow tab is activated while the switch waits (refresh-flow names the switch's Flow)", () => {
  it("success, the other Flow showing a switch too: only the switch's Flow is read, at once", async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    const release = holdNextSwitch()
    await switchKind()
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')

    await openOther(center)
    await select(center, `doc:${OTHER_ROOT}`, OTHER)
    expect(center.state.activeFlowId).toBe(OTHER)
    expect(find('kind-switch-open').attributes('data-to')).toBe('B')
    const otherGraph = graphOf(center, OTHER)
    release()
    await flushPromises()
    expect(server.rootId).toBe(ROOT_B)
    // The switch's Flow is read again in its own (inactive) tab: the old key is gone there.
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_B}`, docType: 'B', docId: ROOT_B }])
    expect(center.state.views[FLOW].selectedNodeId).toBeNull()
    // The active Flow is not read, and keeps its Canvas and its selection.
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
    expect(graphOf(center, OTHER)).toBe(otherGraph)
    expect(center.state.activeFlowId).toBe(OTHER)
    expect(center.activeSelection.value?.node.id).toBe(`doc:${OTHER_ROOT}`)
    expect(find('kind-switch-open').attributes('data-to')).toBe('B')
    expect(find('kind-switch-line').exists()).toBe(false)

    // Back on the switch's Flow: already up to date, nothing is read on return.
    await activate(center, FLOW)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(center.activeSelection.value).toBeNull()
    expect(find('designer-inspector-empty').exists()).toBe(true)
    await select(center, `doc:${ROOT_B}`)
    expect(find('kind-switch-open').attributes('data-to')).toBe('R')

    // Switching tabs again reads neither Flow.
    await activate(center, OTHER)
    await activate(center, FLOW)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
    expect(convertCall).toHaveBeenCalledTimes(1)
  })

  it('success, nothing selected in the other Flow (no switch on screen): the other Flow is not read and keeps its state', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    const release = holdNextSwitch()
    await switchKind()

    await openOther(center)
    expect(center.activeSelection.value).toBeNull()
    expect(find('inspector-kind-switch').exists()).toBe(false)
    const otherGraph = graphOf(center, OTHER)
    release()
    await flushPromises()
    expect(server.rootId).toBe(ROOT_B)
    // Only the switch's Flow is read; the active one keeps its read count, Canvas and (empty) selection.
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
    expect(graphOf(center, OTHER)).toBe(otherGraph)
    expect(canvasNodes(center, OTHER)).toEqual([{ id: `doc:${OTHER_ROOT}`, docType: 'R', docId: OTHER_ROOT }])
    expect(center.state.activeFlowId).toBe(OTHER)
    expect(center.state.views[OTHER].selectedNodeId).toBeNull()
    expect(find('designer-inspector-empty').exists()).toBe(true)

    await activate(center, FLOW)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_B}`, docType: 'B', docId: ROOT_B }])
    expect(center.activeSelection.value).toBeNull()
    expect(find('designer-inspector-empty').exists()).toBe(true)
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
  })

  it("a refusal that moves the Flow (graph 409) reads the switch's Flow, not the active one", async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    server.ownership = 'graph'
    const release = holdNextSwitch()
    await switchKind()

    await openOther(center)
    await select(center, `doc:${OTHER_ROOT}`, OTHER)
    const otherGraph = graphOf(center, OTHER)
    release()
    await flushPromises()
    expect(server.rootId).toBe(ROOT_R)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(canvasNodes(center)).toEqual([{ id: START_KEY, docType: 'R', docId: ROOT_R }])
    expect(center.state.views[FLOW].selectedNodeId).toBeNull()
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
    expect(graphOf(center, OTHER)).toBe(otherGraph)
    expect(center.activeSelection.value?.node.id).toBe(`doc:${OTHER_ROOT}`)
    // The refusal belongs to the Node it was made for: the other Flow's root shows no reason.
    expect(find('kind-switch-line').exists()).toBe(false)

    await activate(center, FLOW)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(center.activeSelection.value).toBeNull()
    await select(center, START_KEY)
    expect(find('inspector-kind-switch').exists()).toBe(false)
  })

  it('the selection cleared in the same Flow while the switch waits: that Flow is still read', async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    const release = holdNextSwitch()
    await switchKind()
    center.selectNode(FLOW, null)
    await flushPromises()
    expect(find('inspector-kind-switch').exists()).toBe(false)
    release()
    await flushPromises()
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(2)
    expect(canvasNodes(center)).toEqual([{ id: `doc:${ROOT_B}`, docType: 'B', docId: ROOT_B }])
    expect(center.activeSelection.value).toBeNull()
  })

  it("the switch's Flow tab closed while the switch waits: no Flow is read", async () => {
    const { center, loadFlowCanvas } = await openFlow()
    await select(center, `doc:${ROOT_R}`)
    const release = holdNextSwitch()
    await switchKind()
    await openOther(center)
    const otherGraph = graphOf(center, OTHER)
    center.closeFlow(FLOW)
    await flushPromises()
    release()
    await flushPromises()
    expect(server.rootId).toBe(ROOT_B)
    expect(readsOf(loadFlowCanvas, FLOW)).toBe(1)
    expect(readsOf(loadFlowCanvas, OTHER)).toBe(1)
    expect(graphOf(center, OTHER)).toBe(otherGraph)
    expect(center.state.activeFlowId).toBe(OTHER)
  })
})
