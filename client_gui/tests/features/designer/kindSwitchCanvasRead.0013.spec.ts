/**
 * flowgate.v02test.0013 — R↔B switch delay: one Canvas read for the switch's answer and its own Canvas event.
 *
 * A switch that changes draft cards is followed by two signals of one commit: the switch's refresh after the answer
 * (Host intent `refresh-flow`) and the `graph_canvas_changed` (`draft`) event. Both used to start a whole Canvas read
 * (Canvas + Graph definition + archive, then the run), the first one's answer thrown away. The switch now names itself
 * (`x-canvas-client-id`, one id per switch), the event carries that id as `origin_client_id`, and the Center reads once
 * for the pair — whichever comes first — while every other change still reads as before:
 *  - the event layer hands the event's origin id to the Center (this tab's own Canvas id is still dropped);
 *  - answer -> event and event -> answer: one read; without an id, or with ids that differ: a read each;
 *  - another tab's events (all carrying that tab's id) never pair with each other;
 *  - a read that started before the switch is not taken for its result (the answer reads again);
 *  - a failed first read lets the second signal of the pair read;
 *  - the switch sends its id and names it in its refresh.
 */
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowGraphChangeSignal } from '@/features/designer/center/graph/flowGraphSource'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { newKindSwitchId } from '@/features/designer/inspector/features/kindSwitch/useKindSwitch'
import { kindSwitchConvert } from '@/features/document'
import { CANVAS_CLIENT_ID } from '@/features/project/flowCanvasApi'
import { subscribeFlowCanvasChanges } from '@/features/project/flowCanvasEvents'

const P = 'p'
const F = 'p.v02test.0013'
const G = 'p.v02test.0014'

// ── the event layer ───────────────────────────────────────────────────────────

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static all: FakeEventSource[] = []
  readyState = FakeEventSource.CONNECTING
  private listeners = new Map<string, Array<(event: Event) => void>>()
  constructor(readonly url: string) { FakeEventSource.all.push(this) }
  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }
  close() { this.readyState = FakeEventSource.CLOSED }
  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const frame = (flowId: string, origin: string | null, reason = 'draft') => ({
  event_type: 'graph_canvas_changed',
  payload: { flow_id: flowId, reason, actor_user_id: 'u_kim', origin_client_id: origin, draft_version: 3, node_keys: [] },
  project: P,
  group_id: flowId,
  doc_id: null,
})

describe('the Canvas event hands its origin id to the Center', () => {
  beforeEach(() => {
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    sessionStorage.clear()
  })

  it("a switch's event carries its id; an event without one reports the Flow alone; this tab's own is dropped", () => {
    const calls: unknown[][] = []
    const stop = subscribeFlowCanvasChanges(P, { changed: (...args: unknown[]) => calls.push(args), resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('graph_canvas_changed', frame(F, 'kind_abc'))
    es.emit('graph_canvas_changed', frame(F, null))
    es.emit('graph_canvas_changed', frame(F, CANVAS_CLIENT_ID))
    expect(calls).toEqual([[F, { originClientId: 'kind_abc' }], [F]])
    stop()
  })
})

// ── the Center session ────────────────────────────────────────────────────────

interface CountingSource extends FlowGraphSource {
  graphReads: string[]
  archiveReads: number
  /** When set, each Canvas read waits until the test answers it. */
  manual: boolean
  pending: Array<{ flowId: string; resolve: () => void; reject: (error: Error) => void }>
  changed: ((flowId: string, signal?: FlowGraphChangeSignal) => void) | null
}

const graphOf = (flowId: string, title: string): FlowGraph => ({
  flowId,
  ownership: 'unassigned',
  viewMode: 'draft',
  draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  nodes: [{ id: 'doc:root', kind: 'requirement', title, x: 0, y: 0, movable: true, structureEditable: true }],
  edges: [],
} as unknown as FlowGraph)

function countingSource(): CountingSource {
  let title = 'v0'
  const source: CountingSource = {
    isSample: false,
    graphReads: [],
    archiveReads: 0,
    manual: false,
    pending: [],
    changed: null,
    loadGraph: (_projectId: string, flowId: string) => {
      source.graphReads.push(flowId)
      const answer = graphOf(flowId, title)
      if (!source.manual) return Promise.resolve(answer)
      return new Promise<FlowGraph>((resolve, reject) => {
        source.pending.push({ flowId, resolve: () => resolve(answer), reject })
      })
    },
    loadRun: async () => null,
    watchChanges: (_projectId, handlers) => {
      source.changed = handlers.changed
      return () => { source.changed = null }
    },
  } as unknown as CountingSource
  ;(source as unknown as { retitle: (t: string) => void }).retitle = (t: string) => { title = t }
  return source
}

const settle = async () => { for (let i = 0; i < 6; i += 1) await flushPromises() }

let center: DesignerCenterSession | null = null
afterEach(() => {
  center?.dispose()
  center = null
})

async function openFlows(source: CountingSource, ...flowIds: string[]) {
  center = createDesignerCenterSession({ source, readArchive: async () => { source.archiveReads += 1; return { archived: false } } })
  center.setProject(P)
  for (const flowId of flowIds) center.openFlow({ projectId: P, flowId, flowName: flowId })
  await settle()
  source.graphReads = []
  source.archiveReads = 0
  return center
}

const shownTitle = (session: DesignerCenterSession, flowId: string) => {
  const load = session.state.graphs[flowId]
  return load?.status === 'ready' ? (load.graph as FlowGraph).nodes[0]?.title : null
}

describe('one Canvas read for a switch answer and its own Canvas event', () => {
  it('answer first, then the event with the same id: one read (was two)', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    session.refreshFlow(F, { originClientId: 'kind_1' })
    await settle()
    source.changed!(F, { originClientId: 'kind_1' })
    await settle()
    expect(source.graphReads).toEqual([F])
    expect(source.archiveReads).toBe(1)
  })

  it('event first, then the answer: one read', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    source.changed!(F, { originClientId: 'kind_2' })
    session.refreshFlow(F, { originClientId: 'kind_2' })
    await settle()
    expect(source.graphReads).toEqual([F])
  })

  it('the event arriving while the answer\'s read is still on its way starts nothing, and that read is shown', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    source.manual = true
    ;(source as unknown as { retitle: (t: string) => void }).retitle('switched')
    session.refreshFlow(F, { originClientId: 'kind_3' })
    source.changed!(F, { originClientId: 'kind_3' })
    await settle()
    expect(source.pending.map((p) => p.flowId)).toEqual([F])
    source.pending[0].resolve()
    await settle()
    expect(shownTitle(session, F)).toBe('switched')
  })

  it('without an id (no draft card changed, no event) the answer reads as before; ids that differ each read', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    session.refreshFlow(F)
    await settle()
    session.refreshFlow(F, { originClientId: 'kind_4' })
    await settle()
    source.changed!(F, { originClientId: 'kind_other' })
    await settle()
    expect(source.graphReads).toEqual([F, F, F])
  })

  it("another tab's events never pair with each other: every one reads", async () => {
    const source = countingSource()
    await openFlows(source, F)
    source.changed!(F, { originClientId: 'tab_other' })
    await settle()
    source.changed!(F, { originClientId: 'tab_other' })
    await settle()
    source.changed!(F)
    await settle()
    expect(source.graphReads).toEqual([F, F, F])
  })

  it('an id pairs once: a later signal with it reads again', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    session.refreshFlow(F, { originClientId: 'kind_5' })
    source.changed!(F, { originClientId: 'kind_5' })
    await settle()
    source.changed!(F, { originClientId: 'kind_5' })
    await settle()
    expect(source.graphReads).toEqual([F, F])
  })

  it('a read that started before the switch is not taken for its result: the answer reads again', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    source.manual = true
    source.changed!(F)                                  // an earlier change: its read is still on its way
    ;(source as unknown as { retitle: (t: string) => void }).retitle('switched')
    session.refreshFlow(F, { originClientId: 'kind_6' })
    source.changed!(F, { originClientId: 'kind_6' })
    await settle()
    expect(source.pending.map((p) => p.flowId)).toEqual([F, F])
    source.pending[1].resolve()
    source.pending[0].resolve()                         // the older answer comes last and is dropped
    await settle()
    expect(shownTitle(session, F)).toBe('switched')
  })

  it('a failed first read lets the second signal of the pair read', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    source.manual = true
    session.refreshFlow(F, { originClientId: 'kind_7' })
    await settle()
    source.pending[0].reject(new Error('offline'))
    await settle()
    source.manual = false
    source.changed!(F, { originClientId: 'kind_7' })
    await settle()
    expect(source.graphReads).toEqual([F, F])
  })

  it('the switch names its Flow: another Flow tab active reads only that Flow, once', async () => {
    const source = countingSource()
    const session = await openFlows(source, F, G)
    session.activateFlow(G)
    session.refreshFlow(F, { originClientId: 'kind_8' })
    source.changed!(F, { originClientId: 'kind_8' })
    await settle()
    expect(source.graphReads).toEqual([F])
  })

  it('a closed Flow forgets its ids', async () => {
    const source = countingSource()
    const session = await openFlows(source, F)
    session.refreshFlow(F, { originClientId: 'kind_9' })
    await settle()
    session.closeFlow(F)
    session.openFlow({ projectId: P, flowId: F, flowName: F })
    await settle()
    source.changed!(F, { originClientId: 'kind_9' })
    await settle()
    expect(source.graphReads).toEqual([F, F, F])
  })
})

// ── the switch's call ─────────────────────────────────────────────────────────

describe('the switch sends its own id', () => {
  beforeEach(() => patchRequest.mockReset())

  it('as x-canvas-client-id, and without one exactly as before', async () => {
    const row = { data: { doc_id: `${F}.0001-B`, type_code: 'B', group_id: F, project_id: P } }
    patchRequest.mockResolvedValue({ data: row })
    await kindSwitchConvert(`${F}.0001-R`, 'B', 'kind_abc')
    await kindSwitchConvert(`${F}.0001-R`, 'B')
    const path = `/api/v1/documents/${encodeURIComponent(`${F}.0001-R`)}/root-type`
    expect(patchRequest.mock.calls).toEqual([
      [path, { new_type: 'B' }, { headers: { 'x-canvas-client-id': 'kind_abc' } }],
      [path, { new_type: 'B' }],
    ])
  })

  it('one new id per switch, in the P0008 §1.1 format and never this tab\'s Canvas id', () => {
    const a = newKindSwitchId()
    const b = newKindSwitchId()
    expect(a).toMatch(/^[A-Za-z0-9_-]{1,64}$/)
    expect(a).not.toBe(b)
    expect(a).not.toBe(CANVAS_CLIENT_ID)
  })
})
