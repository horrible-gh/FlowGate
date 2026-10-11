/**
 * flowgate.v02.0053 T#5 — the card document lines T#1 adds to the Canvas read (`card_documents` of a Graph /
 * draft node) and to the draft save answer (node key -> lines) reach the Designer in camelCase. A node or an
 * answer without them carries no field, so the 0045 / 0048 shapes stay as they were.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const putRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: (...args: unknown[]) => putRequest(...args),
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { toCardDocuments, toFlowCanvas } from '@/features/project/flowCanvasApi'
import { saveFlowGraphDraft } from '@/features/project/flowGraphApi'

/** Lines as the server writes them (flowgate/card_documents/lines.py lines_by_node). */
const RAW_LINES = [
  { slot: 'instruction', doc_type: 'TS', author: 'human', doc_id: 'f.0002-TS', title: '테스트 · 지시서', status: 'draft', doc_review_status: null },
  { slot: 'scenario', doc_type: 'TS', author: 'run', doc_id: 'f.0002-TS', title: '테스트 · 지시서', status: 'draft', doc_review_status: null },
  { slot: 'report', doc_type: 'TSR', author: 'run', doc_id: null, title: null, status: null, doc_review_status: null },
]

const LINES = [
  { slot: 'instruction', docType: 'TS', author: 'human', docId: 'f.0002-TS', title: '테스트 · 지시서', status: 'draft', docReviewStatus: null },
  { slot: 'scenario', docType: 'TS', author: 'run', docId: 'f.0002-TS', title: '테스트 · 지시서', status: 'draft', docReviewStatus: null },
  { slot: 'report', docType: 'TSR', author: 'run', docId: null, title: null, status: null, docReviewStatus: null },
]

const node = (key: string, extra: Record<string, unknown>) => ({
  node_key: key, identity_source: 'graph_node', origin: 'draft_node', kind: 'test', doc_type: 'TS', title: key, state: 'draft',
  position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: true },
  document: null, slot: null, graph_node: { node_type: 'document', label: key, palette_item_id: null, assignee: null, root_bound: false },
  ...extra,
})

beforeEach(() => {
  putRequest.mockReset()
})

describe('card lines in the Canvas read', () => {
  it('a Graph / draft node keeps its lines in order; a node without the field carries none', () => {
    const canvas = toFlowCanvas({
      flow_id: 'f',
      ownership: { state: 'unassigned' },
      nodes: [node('gn_ts', { card_documents: RAW_LINES }), node('gn_g', { card_documents: [] }), node('doc:r', {})],
      edges: [],
    })
    expect(canvas.nodes[0].cardDocuments).toEqual(LINES)
    expect(canvas.nodes[1].cardDocuments).toEqual([])
    expect(canvas.nodes[2]).not.toHaveProperty('cardDocuments')
  })

  it('drops entries without a slot and reads wrong values as null', () => {
    expect(toCardDocuments([null, 'x', { doc_id: 'a' }, { slot: 'report', doc_type: 3, author: 'run', doc_id: 7 }])).toEqual([
      { slot: 'report', docType: null, author: 'run', docId: null, title: null, status: null, docReviewStatus: null },
    ])
    expect(toCardDocuments(undefined)).toEqual([])
  })
})

describe('card lines in the draft save answer', () => {
  it('node key -> lines when the server answers them', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, draft_version: 1, key_map: { tmp_a: 'gn_ts' }, removed_node_keys: [], removed_edge_ids: [], card_documents: { gn_ts: RAW_LINES } } })
    const result = await saveFlowGraphDraft('f', { draftVersion: 0, nodes: [], edges: [] })
    expect(result).toEqual({ draftVersion: 1, keyMap: { tmp_a: 'gn_ts' }, removedNodeKeys: [], removedEdgeIds: [], cardDocuments: { gn_ts: LINES } })
  })

  it('an answer without card_documents (or a malformed one) carries no field', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, draft_version: 2, key_map: {} } })
    expect(await saveFlowGraphDraft('f', { draftVersion: 1, nodes: [], edges: [] })).not.toHaveProperty('cardDocuments')
    putRequest.mockResolvedValue({ data: { ok: true, draft_version: 3, key_map: {}, card_documents: ['x'] } })
    expect(await saveFlowGraphDraft('f', { draftVersion: 2, nodes: [], edges: [] })).not.toHaveProperty('cardDocuments')
  })
})
