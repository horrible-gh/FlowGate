/**
 * flowgate.v02.0135 T#4 (review rework) — cards shown again after SEVERAL Canvas writes lost their answer, whatever
 * order this tab's own change events then arrive in. The real path: the project store's Canvas / Graph loaders ->
 * the server Graph source -> the Center session (what the Designer shows); only `@shared/api` is an in-memory server
 * here, and its change events go through the real event filter (`changedFlowOf`, flowCanvasEvents.ts) before they
 * reach the session — the same frames the SSE stream carries, with this tab's `origin_client_id`.
 *
 * The stg shape (0135 TR §5.2): the write is committed minutes after the GUI gave up at 30 s; the re-read after the
 * failure shows the Flow without it; the commit's own event used to be dropped as "already applied", so the card was
 * gone from the screen until F5. Before this rework one lost write was remembered per Flow and the first own event
 * of any kind (a title, an earlier layout) used it up.
 */
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useProjectStore } from '@/features/project'
import { CANVAS_CLIENT_ID } from '@/features/project/flowCanvasApi'
import { changedFlowOf } from '@/features/project/flowCanvasEvents'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, readPaletteDragData, writePaletteDragData } from '@/features/designer/palette/paletteCatalog'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  putRequest,
  patchRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

type Mode = 'answer' | 'lost'
interface Card { key: string; title: string; docType: string | null; palette: string | null; x: number; y: number }
interface ServerFlow { draftVersion: number; cards: Card[]; edges: Array<{ id: string; source: string; target: string }> }

const FLOW = 'p.ui.0135'
const timeout = () => Object.assign(new Error('timeout of 30000ms exceeded'), { isAxiosError: true, code: 'ECONNABORTED' })

/** The Graph slice of the server. A 'lost' write is stored only on commit() (minutes later on stg), then announced. */
function createServer() {
  const flows: Record<string, ServerFlow> = {}
  const pending: Array<() => Record<string, unknown>> = []
  const draftModes: Mode[] = []
  const layoutModes: Mode[] = []
  let keyNo = 0
  const canvasOf = (flowId: string) => {
    const flow = flows[flowId]
    return {
      ok: true, flow_id: flowId, project_id: 'p',
      ownership: { state: 'unassigned', sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
      projection: 'documents', view: null,
      graph: {
        draft: flow.draftVersion > 0
          ? { draft_version: flow.draftVersion, base_revision_no: null, updated_at: 't', updated_by: 'u', confirmable: true, blocked_reason: null }
          : null,
        latest_revision_no: null, root_binding: null,
      },
      nodes: flow.cards.map((card) => ({
        node_key: card.key, identity_source: 'graph_node', origin: 'draft_node', kind: card.docType === 'R' ? 'requirement' : 'task',
        doc_type: card.docType, title: card.title, state: 'draft', position: { x: card.x, y: card.y, source: 'saved' },
        editable: { move: true, structure: true }, document: null, slot: null, node_run: null,
        graph_node: { node_type: 'document', label: card.title, palette_item_id: card.palette, assignee: null, root_bound: false },
      })),
      edges: flow.edges.map((e) => ({ edge_id: e.id, edge_class: 'execution', origin: 'graph', source: e.source, target: e.target, relation: null, condition: null, editable: true })),
      external_relations: [], viewport: null, run: null,
      identity: { sequence_tag: null, rematched: false, retired_node_keys: [] },
      collapsed: { applied: ['conversation'], ignored_keys: [], hidden_counts: {} },
    }
  }
  const server = {
    flows, draftModes, layoutModes,
    events: [] as Array<Record<string, unknown>>,
    /** Stores the oldest lost write and returns its own change event payload (as canvas_events.py publishes it). */
    commit(): Record<string, unknown> {
      return pending.shift()!()
    },
    pendingCount: () => pending.length,
    async get(url: string): Promise<{ data: unknown }> {
      const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
      if (canvas) return { data: canvasOf(decodeURIComponent(canvas[1])) }
      throw Object.assign(new Error('no route'), { response: { status: 404, data: {} } })
    },
    async put(url: string, body: Record<string, any>): Promise<{ data: unknown }> {
      const draft = /^\/api\/v1\/flows\/([^/]+)\/graph\/draft$/.exec(url)
      if (draft) {
        const flowId = decodeURIComponent(draft[1])
        const keyMap: Record<string, string> = {}
        const store = () => {
          const flow = flows[flowId]
          if (flow.draftVersion !== body.draft_version) {
            throw Object.assign(new Error('conflict'), { response: { status: 409, data: { error: { code: 'draft_version_conflict' } } } })
          }
          flow.cards = body.nodes.map((n: Record<string, any>, i: number) => {
            const kept = n.node_key ? flow.cards.find((c) => c.key === n.node_key) : undefined
            const key = n.node_key ?? (keyMap[n.client_key] = `gn_${++keyNo}`)
            return { key, title: n.label, docType: n.doc_type, palette: n.palette_item_id, x: kept?.x ?? 260 * i, y: kept?.y ?? 0 }
          })
          flow.edges = body.edges.map((e: Record<string, any>) => ({ id: e.edge_id ?? (keyMap[e.client_key] = `ge_${++keyNo}`), source: e.source, target: e.target }))
          flow.draftVersion += 1
          return flow.draftVersion
        }
        if ((draftModes.shift() ?? 'answer') === 'lost') {
          pending.push(() => ({ flow_id: flowId, reason: 'draft', draft_version: store(), node_keys: [], origin_client_id: CANVAS_CLIENT_ID }))
          throw timeout()
        }
        const version = store()
        server.events.push({ flow_id: flowId, reason: 'draft', draft_version: version, node_keys: [], origin_client_id: CANVAS_CLIENT_ID })
        return { data: { flow_id: flowId, draft_version: version, key_map: keyMap, removed_node_keys: [], removed_edge_ids: [] } }
      }
      const layout = /^\/api\/v1\/flows\/([^/]+)\/canvas\/layout$/.exec(url)
      if (layout) {
        const flowId = decodeURIComponent(layout[1])
        const store = () => {
          const keys: string[] = []
          for (const p of body.positions) {
            const card = flows[flowId].cards.find((c) => c.key === p.node_key)
            if (card) Object.assign(card, { x: p.x, y: p.y }) && keys.push(card.key)
          }
          return keys
        }
        if ((layoutModes.shift() ?? 'answer') === 'lost') {
          pending.push(() => ({ flow_id: flowId, reason: 'layout', draft_version: null, node_keys: store(), origin_client_id: CANVAS_CLIENT_ID }))
          throw timeout()
        }
        const keys = store()
        server.events.push({ flow_id: flowId, reason: 'layout', draft_version: null, node_keys: keys, origin_client_id: CANVAS_CLIENT_ID })
        return { data: { flow_id: flowId, saved_positions: keys.length, viewport_saved: false, ignored: [] } }
      }
      throw Object.assign(new Error('no route'), { response: { status: 404, data: {} } })
    },
  }
  return server
}

let server: ReturnType<typeof createServer>
let handlers: Parameters<NonNullable<FlowGraphSource['watchChanges']>>[1] | null = null
const sessions: Array<ReturnType<typeof createDesignerCenterSession>> = []

beforeEach(() => {
  server = createServer()
  handlers = null
  getRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockImplementation((url: string) => server.get(url))
  putRequest.mockImplementation((url: string, body: Record<string, unknown>) => server.put(url, body))
})

afterEach(() => {
  while (sessions.length) sessions.pop()!.dispose()
})

/** A change frame as the project event stream delivers it, through the real Canvas filter. */
function deliver(payload: Record<string, unknown>) {
  const flowId = changedFlowOf(JSON.stringify({ type: 'graph_canvas_changed', group_id: payload.flow_id, payload }))
  if (flowId) handlers?.changed(flowId)
  return flowId
}

async function settle() {
  for (let i = 0; i < 4; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 30))
    await flushPromises()
  }
}

function dataTransfer(itemId: string, label: string) {
  const store = new Map<string, string>()
  const transfer = { setData: (k: string, v: string) => store.set(k, v), getData: (k: string) => store.get(k) ?? '', effectAllowed: 'none' }
  const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === itemId)!
  writePaletteDragData(transfer as unknown as DataTransfer, paletteItemDefinition(item, label))
  return transfer as unknown as DataTransfer
}

const SEED = { nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구사항', kind: 'requirement' }

async function open(flow: ServerFlow, options: { seed?: boolean; beforeOpen?: () => void } = {}) {
  server.flows[FLOW] = flow
  setActivePinia(createPinia())
  const store = useProjectStore()
  const serverSource = createServerFlowGraphSource({
    loadFlowCanvas: store.loadFlowCanvas,
    saveFlowCanvasLayout: store.saveFlowCanvasLayout,
    saveFlowGraphDraft: store.saveFlowGraphDraft,
    validateFlowGraphDraft: store.validateFlowGraphDraft,
    confirmFlowGraphRevision: store.confirmFlowGraphRevision,
  })
  const source: FlowGraphSource = {
    ...serverSource,
    watchChanges: (_projectId, next) => {
      handlers = next
      return () => {
        handlers = null
      }
    },
  }
  const center = createDesignerCenterSession({
    source,
    layoutSaveDelayMs: 5,
    draftSaveDelayMs: 5,
    readDrop: (transfer) => {
      const definition = readPaletteDragData(transfer)
      return definition ? paletteNodeSpec(definition) : null
    },
  })
  sessions.push(center)
  center.setProject('p')
  options.beforeOpen?.()
  center.openFlow({ projectId: 'p', flowId: FLOW, flowName: FLOW }, options.seed ? { seedNode: SEED } : undefined)
  await settle()
  return center
}

function screen(center: ReturnType<typeof createDesignerCenterSession>) {
  const load = center.state.graphs[FLOW]
  if (load?.status !== 'ready') throw new Error(`not ready: ${load?.status}`)
  const graph = load.graph as FlowGraph
  return {
    cards: graph.nodes.map((n) => ({ id: n.id, title: n.title, x: n.x, y: n.y })).sort((a, b) => a.id.localeCompare(b.id)),
    edges: graph.edges.map((e) => [e.source, e.target]),
  }
}

function stored() {
  const flow = server.flows[FLOW]
  return {
    cards: flow.cards.map((c) => ({ id: c.key, title: c.title, x: c.x, y: c.y })).sort((a, b) => a.id.localeCompare(b.id)),
    edges: flow.edges.map((e) => [e.source, e.target]),
  }
}

describe('cards stay on screen after Canvas writes that lost their answer (flowgate.v02.0135 T#4)', () => {
  it('new Flow: the seed card save is lost, a title event arrives first, then the late commit shows the card', async () => {
    // the Shell opens a just created Flow with its 요구사항 seed card; its first draft save gets no answer
    const center = await open({ draftVersion: 0, cards: [], edges: [] }, { seed: true, beforeOpen: () => server.draftModes.push('lost') })
    // the re-read after the failed save: not committed yet -> the card is gone from the screen (the stg symptom)
    expect(screen(center).cards).toEqual([])
    expect(server.pendingCount()).toBe(1)

    // an own event of something else comes first (review: it used to use the recovery up)
    expect(deliver({ flow_id: FLOW, reason: 'title', node_keys: ['gn_x'], draft_version: null, origin_client_id: CANVAS_CLIENT_ID })).toBeNull()
    expect(deliver(server.commit())).toBe(FLOW) // the late commit (stg: +2.5 min) is read again
    await settle()
    expect(screen(center)).toEqual(stored())
    expect(screen(center).cards).toEqual([{ id: 'gn_1', title: '요구사항', x: 0, y: 0 }])
  })

  it('existing Flow: a lost move and a lost card add, the move commit arrives first, then the draft commit', async () => {
    const center = await open({
      draftVersion: 1,
      cards: [{ key: 'gn_r', title: '요구사항', docType: 'R', palette: null, x: 0, y: 0 }],
      edges: [],
    })
    expect(screen(center).cards).toEqual([{ id: 'gn_r', title: '요구사항', x: 0, y: 0 }])

    server.layoutModes.push('lost')
    center.moveNode(FLOW, 'gn_r', 300, 200)
    await settle()
    server.draftModes.push('lost')
    expect(center.dropOnCanvas(FLOW, dataTransfer('task', '구현'), 600, 320)).toBe(true)
    await settle()
    expect(server.pendingCount()).toBe(2)
    // what the screen shows after the failed saves: the server state before either write
    expect(screen(center).cards).toEqual([{ id: 'gn_r', title: '요구사항', x: 0, y: 0 }])

    expect(deliver(server.commit())).toBe(FLOW) // layout commit first: read (the draft is still not stored)
    await settle()
    expect(screen(center).cards).toEqual([{ id: 'gn_r', title: '요구사항', x: 300, y: 200 }])

    expect(deliver(server.commit())).toBe(FLOW) // the draft commit after it is still read (review: it was dropped)
    await settle()
    expect(screen(center)).toEqual(stored())
    expect(screen(center).cards).toEqual([
      { id: 'gn_1', title: '구현', x: 260, y: 0 },
      { id: 'gn_r', title: '요구사항', x: 300, y: 200 },
    ])

    // nothing is left waiting: a later answered save's own event is dropped again (applied from its answer)
    center.moveNode(FLOW, 'gn_1', 500, 40)
    await settle()
    expect(deliver(server.events.at(-1)!)).toBeNull()
    expect(screen(center)).toEqual(stored())
  })
})
