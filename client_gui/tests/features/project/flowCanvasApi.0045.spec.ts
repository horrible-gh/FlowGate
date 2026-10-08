/**
 * flowgate.v02.0045 T0011 (S1+S2) — Flow Canvas transport in features/project: GET canvas
 * normalization (P0008 §2.6 / §2.7 / §3), PUT layout request / response shape (P0008 §4), the
 * store loaders, and the public surface staying runtime-free (types only).
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  putRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID, fetchFlowCanvas, saveFlowCanvasLayout } from '@/features/project/flowCanvasApi'
import { useProjectStore } from '@/features/project'

const RAW = {
  ok: true,
  server_ts: '2026-10-08T05:31:02.007+00:00',
  flow_id: 'flowgate.v02.0048',
  project_id: 'flowgate',
  ownership: { state: 'v01', sequence_root_doc_id: 'flowgate.v02.0048.0001-R', graph_revision_no: null, assigned_at: null },
  projection: 'sequence',
  view: null,
  graph: { draft: null, latest_revision_no: null, root_binding: null },
  nodes: [
    {
      node_key: 'doc:flowgate.v02.0048.0001-R', identity_source: 'document', origin: 'document',
      kind: 'requirement', doc_type: 'R', title: 'Explorer 정렬 개선', state: 'in_progress',
      position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: false },
      document: { doc_id: 'flowgate.v02.0048.0001-R', type_code: 'R', seq: 1, title: 'Explorer 정렬 개선', doc_review_status: 'wf_in_progress', status: 'open', supersedes: [] },
      slot: null, graph_node: null, node_run: null,
    },
    {
      node_key: 'slot_q7m2x1', identity_source: 'sequence_slot', origin: 'sequence_slot',
      kind: 'design', doc_type: 'D', title: '정렬 기본설계', state: 'draft',
      position: { x: 780, y: 0, source: 'auto' }, editable: { move: true, structure: false },
      document: null,
      slot: { item_id: 4820, item_seq: 5, order: 3, type: 'D', label: '정렬 기본설계', derived_status: 'pending', protected: false, source_wp_card_id: 'c_k0dtcr9pq80j' },
      graph_node: null, node_run: null,
    },
  ],
  edges: [{ edge_id: 'seq:doc:flowgate.v02.0048.0001-R>slot_q7m2x1', edge_class: 'execution', origin: 'sequence', source: 'doc:flowgate.v02.0048.0001-R', target: 'slot_q7m2x1', relation: null, condition: null, editable: false }],
  external_relations: [{ node_key: 'slot_q7m2x1', relation: 'triggered_by', other_doc_id: 'x.0001-D', other_flow_id: 'x', other_title: 'Other' }],
  viewport: { x: -40, y: 12, zoom: 0.85 },
  run: null,
  identity: { sequence_tag: 'seq12-r9-i3', rematched: true, retired_node_keys: ['slot_old'] },
  collapsed: { applied: ['memo'], ignored_keys: ['archive'], hidden_counts: { memo: 1 } },
}

beforeEach(() => {
  getRequest.mockReset()
  putRequest.mockReset()
})

describe('fetchFlowCanvas', () => {
  it('reads GET /api/v1/flows/{id}/canvas and normalizes to camelCase', async () => {
    getRequest.mockResolvedValue({ data: RAW })
    const canvas = await fetchFlowCanvas('flowgate.v02.0048')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/flows/flowgate.v02.0048/canvas', {}, {
      headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID },
    })
    expect(canvas.ownership).toEqual({ state: 'v01', sequenceRootDocId: 'flowgate.v02.0048.0001-R', graphRevisionNo: null, assignedAt: null })
    expect(canvas.nodes[0]).toEqual({
      nodeKey: 'doc:flowgate.v02.0048.0001-R', identitySource: 'document', origin: 'document', kind: 'requirement',
      docType: 'R', title: 'Explorer 정렬 개선', state: 'in_progress', position: { x: 0, y: 0, source: 'saved' },
      editable: { move: true, structure: false },
      document: { docId: 'flowgate.v02.0048.0001-R', typeCode: 'R', seq: 1, title: 'Explorer 정렬 개선', docReviewStatus: 'wf_in_progress', status: 'open', supersedes: [] },
      slot: null,
    })
    expect(canvas.nodes[1].slot).toEqual({
      itemId: 4820, itemSeq: 5, order: 3, type: 'D', label: '정렬 기본설계', derivedStatus: 'pending', protected: false, sourceWpCardId: 'c_k0dtcr9pq80j',
    })
    expect(canvas.edges[0]).toEqual({
      edgeId: 'seq:doc:flowgate.v02.0048.0001-R>slot_q7m2x1', edgeClass: 'execution', origin: 'sequence',
      source: 'doc:flowgate.v02.0048.0001-R', target: 'slot_q7m2x1', relation: null, editable: false,
    })
    expect(canvas.externalRelations).toEqual([{ nodeKey: 'slot_q7m2x1', relation: 'triggered_by', otherDocId: 'x.0001-D', otherFlowId: 'x', otherTitle: 'Other' }])
    expect(canvas.viewport).toEqual({ x: -40, y: 12, zoom: 0.85 })
    expect(canvas.identity).toEqual({ sequenceTag: 'seq12-r9-i3', rematched: true, retiredNodeKeys: ['slot_old'] })
    expect(canvas.collapsed).toEqual({ applied: ['memo'], ignoredKeys: ['archive'], hiddenCounts: { memo: 1 } })
  })

  it('treats a broken viewport as none and keeps open keys untouched', async () => {
    getRequest.mockResolvedValue({ data: { ...RAW, viewport: { x: 0, y: 0, zoom: 0 }, nodes: [{ ...RAW.nodes[0], kind: 'future_kind', state: 'future_state' }] } })
    const canvas = await fetchFlowCanvas('f')
    expect(canvas.viewport).toBeNull()
    expect([canvas.nodes[0].kind, canvas.nodes[0].state]).toEqual(['future_kind', 'future_state'])
  })

  it('rejects with a displayable message and keeps the transport error as cause', async () => {
    const transport = new Error('404')
    getRequest.mockRejectedValue(transport)
    await expect(fetchFlowCanvas('f')).rejects.toMatchObject({ message: 'Failed to load the flow canvas.', cause: transport })
  })
})

describe('x-canvas-client-id (P0008 §1.1)', () => {
  it('is one opaque id of the allowed shape, the same for every request of this tab', async () => {
    expect(CANVAS_CLIENT_ID).toMatch(/^[A-Za-z0-9_-]{1,64}$/)
    getRequest.mockResolvedValue({ data: RAW })
    await fetchFlowCanvas('a')
    await fetchFlowCanvas('b')
    expect(getRequest.mock.calls.map((call) => call[2].headers['x-canvas-client-id'])).toEqual([CANVAS_CLIENT_ID, CANVAS_CLIENT_ID])
  })
})

describe('saveFlowCanvasLayout', () => {
  it('PUTs snake_case positions / viewport and normalizes the result', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, flow_id: 'f', saved_positions: 1, viewport_saved: false, ignored: [{ node_key: 'tmp_9', reason: 'unknown_node_key' }] } })
    const result = await saveFlowCanvasLayout('flowgate.v02.0051', { positions: [{ nodeKey: 'doc:a', x: 320, y: 60 }], viewport: null })
    expect(putRequest).toHaveBeenCalledWith('/api/v1/flows/flowgate.v02.0051/canvas/layout', {
      positions: [{ node_key: 'doc:a', x: 320, y: 60 }], viewport: null,
    }, { headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } })
    expect(result).toEqual({ savedPositions: 1, viewportSaved: false, ignored: [{ nodeKey: 'tmp_9', reason: 'unknown_node_key' }] })
  })
})

describe('project store Canvas loaders', () => {
  it('loadFlowCanvas / saveFlowCanvasLayout go through the same transport', async () => {
    setActivePinia(createPinia())
    getRequest.mockResolvedValue({ data: RAW })
    putRequest.mockResolvedValue({ data: { saved_positions: 0, viewport_saved: true, ignored: [] } })
    const store = useProjectStore()
    expect((await store.loadFlowCanvas('flowgate.v02.0048')).flowId).toBe('flowgate.v02.0048')
    expect(await store.saveFlowCanvasLayout('f', { positions: [], viewport: { x: 1, y: 2, zoom: 1 } })).toEqual({
      savedPositions: 0, viewportSaved: true, ignored: [],
    })
  })
})
