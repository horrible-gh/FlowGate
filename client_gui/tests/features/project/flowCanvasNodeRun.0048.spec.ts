/**
 * flowgate.v02.0048 T#1 — the Canvas read keeps a Graph node's `node_run` for the Inspector
 * (D#1 §4.2). flowCanvasApi.0045.spec stays untouched: a node without a run carries no field.
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { toFlowCanvas } from '@/features/project/flowCanvasApi'

const node = (key: string, nodeRun: unknown) => ({
  node_key: key, identity_source: 'graph_node', origin: 'graph_revision', kind: 'task', doc_type: 'TR', title: key, state: 'in_progress',
  position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: false },
  document: null, slot: null, graph_node: { node_type: 'document', label: key, palette_item_id: null, assignee: null, root_bound: false },
  node_run: nodeRun,
})

describe('toFlowCanvas — node_run', () => {
  it('keeps status / opened_at / finished_at / result_doc_id in camelCase', () => {
    const canvas = toFlowCanvas({
      flow_id: 'f',
      ownership: { state: 'graph' },
      nodes: [node('a', { status: 'completed', opened_at: '2026-10-09T01:00:00Z', finished_at: '2026-10-09T02:00:00Z', result_doc_id: 'f.0004-TR', attempt: 2 })],
      run: { run_id: 'r1', status: 'running' },
      edges: [],
    })
    expect(canvas.nodes[0].nodeRun).toEqual({ status: 'completed', openedAt: '2026-10-09T01:00:00Z', finishedAt: '2026-10-09T02:00:00Z', resultDocId: 'f.0004-TR' })
    // The top-level run stays dropped (no 0048 reader).
    expect(canvas).not.toHaveProperty('run')
  })

  it('reads missing / malformed values as null and leaves the field out without a run', () => {
    const canvas = toFlowCanvas({
      flow_id: 'f',
      ownership: { state: 'graph' },
      nodes: [node('a', { status: 7, opened_at: null }), node('b', null), node('c', 'oops')],
      edges: [],
    })
    expect(canvas.nodes[0].nodeRun).toEqual({ status: null, openedAt: null, finishedAt: null, resultDocId: null })
    expect(canvas.nodes[1]).not.toHaveProperty('nodeRun')
    expect(canvas.nodes[2]).not.toHaveProperty('nodeRun')
  })
})
