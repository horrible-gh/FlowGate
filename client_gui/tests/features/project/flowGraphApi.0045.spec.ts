/**
 * flowgate.v02.0045 T0013 (S3+S4) — Graph definition transport in features/project: draft save
 * request / response (P0008 S3-1), validation (S3-5), confirmation (S4-1) and its refusal with
 * issues (S4-4), the GET graph read, the Canvas `view` / `graph` / `graph_node` blocks, and the
 * store loaders. Every request carries this tab's `x-canvas-client-id`.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  putRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID, toFlowCanvas } from '@/features/project/flowCanvasApi'
import {
  FlowGraphApiError,
  confirmFlowGraphRevision,
  fetchFlowGraph,
  saveFlowGraphDraft,
  validateFlowGraphDraft,
} from '@/features/project/flowGraphApi'
import { useProjectStore } from '@/features/project'

const HEADERS = { headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } }
const F = 'flowgate.v02.0051'

beforeEach(() => {
  getRequest.mockReset()
  putRequest.mockReset()
  postRequest.mockReset()
})

describe('draft save (P0008 S3-1)', () => {
  it('sends the whole draft in snake_case and answers the issued keys', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, flow_id: F, draft_version: 4, key_map: { tmp_k1: 'gn_a3', tmp_e1: 'ge_a2' }, removed_node_keys: [], removed_edge_ids: ['ge_x'] } })
    const result = await saveFlowGraphDraft(F, {
      draftVersion: 3,
      nodes: [
        { nodeKey: 'gn_a2', clientKey: null, nodeType: 'document', docType: 'D', paletteItemId: 'basic_design', label: '기본설계', assignee: { kind: 'ai', providerId: 'claude-code', userId: null } },
        { nodeKey: null, clientKey: 'tmp_k1', nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '구현', assignee: null },
      ],
      edges: [{ edgeId: null, clientKey: 'tmp_e1', source: 'gn_a2', target: 'tmp_k1' }],
    })
    expect(putRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/graph/draft`, {
      draft_version: 3,
      nodes: [
        { node_key: 'gn_a2', client_key: null, node_type: 'document', doc_type: 'D', palette_item_id: 'basic_design', label: '기본설계', assignee: { kind: 'ai', user_id: null, provider_id: 'claude-code' } },
        { node_key: null, client_key: 'tmp_k1', node_type: 'document', doc_type: 'T', palette_item_id: 'task', label: '구현', assignee: null },
      ],
      edges: [{ edge_id: null, client_key: 'tmp_e1', source: 'gn_a2', target: 'tmp_k1', condition: null }],
    }, HEADERS)
    expect(result).toEqual({ draftVersion: 4, keyMap: { tmp_k1: 'gn_a3', tmp_e1: 'ge_a2' }, removedNodeKeys: [], removedEdgeIds: ['ge_x'] })
  })

  it('a refusal keeps the server code, params and message (S3-2)', async () => {
    putRequest.mockRejectedValue({
      response: { status: 409, data: { ok: false, error: { code: 'draft_version_conflict', message: '다른 사용자가 초안을 먼저 저장했습니다.', params: { current_draft_version: 5 } } } },
    })
    const error = await saveFlowGraphDraft(F, { draftVersion: 4, nodes: [], edges: [] }).catch((e) => e)
    expect(error).toBeInstanceOf(FlowGraphApiError)
    expect(error.code).toBe('draft_version_conflict')
    expect(error.status).toBe(409)
    expect(error.params).toEqual({ current_draft_version: 5 })
    expect(error.message).toBe('다른 사용자가 초안을 먼저 저장했습니다.')
    expect(error.issues).toEqual([])
  })
})

describe('validation and confirmation (S3-5, S4)', () => {
  it('validation normalizes issues and the root binding preview', async () => {
    postRequest.mockResolvedValue({ data: {
      ok: true, flow_id: F, draft_version: 6, confirmable: false,
      issues: [{ code: 'rule_undecided', severity: 'error', rule_ref: 'Q-2', structure: 'branch', node_keys: ['gn_a2'], edge_ids: ['ge_a2', 'ge_a3'], message: '분기…' }],
      root_binding_preview: { status: 'bindable', node_key: 'gn_a1', doc_id: `${F}.0001-R`, reason: null },
    } })
    const result = await validateFlowGraphDraft(F, 6)
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/graph/draft/validate`, { draft_version: 6 }, HEADERS)
    expect(result).toEqual({
      draftVersion: 6, confirmable: false,
      issues: [{ code: 'rule_undecided', ruleRef: 'Q-2', structure: 'branch', nodeKeys: ['gn_a2'], edgeIds: ['ge_a2', 'ge_a3'], message: '분기…' }],
      rootBindingPreview: { status: 'bindable', nodeKey: 'gn_a1', docId: `${F}.0001-R`, reason: null },
    })
  })

  it('confirmation answers the revision; a structure refusal carries the issues', async () => {
    postRequest.mockResolvedValueOnce({ data: {
      ok: true, flow_id: F, revision_no: 1, confirmed_by: 'u_kim',
      ownership: { state: 'graph', sequence_root_doc_id: null, graph_revision_no: 1, assigned_at: 't' },
      root_binding: { status: 'bound', node_key: 'gn_a1', doc_id: `${F}.0001-R`, bound_at_revision_no: 1 },
      retired_node_keys: [`doc:${F}.0001-R`],
    } })
    const ok = await confirmFlowGraphRevision(F, 8, null)
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/graph/revisions`, { draft_version: 8, base_revision_no: null }, HEADERS)
    expect(ok.revisionNo).toBe(1)
    expect(ok.ownership.state).toBe('graph')
    expect(ok.rootBinding).toEqual({ status: 'bound', nodeKey: 'gn_a1', docId: `${F}.0001-R`, boundAtRevisionNo: 1 })
    postRequest.mockRejectedValueOnce({ response: { status: 422, data: { ok: false, error: {
      code: 'graph_validation_failed', message: '문제 1건',
      params: { issues: [{ code: 'root_binding_ambiguous_root', rule_ref: null, structure: null, node_keys: ['doc:a', 'doc:b'], edge_ids: [], message: 'R 2건' }] },
    } } } })
    const refused = await confirmFlowGraphRevision(F, 8, null).catch((e) => e)
    expect(refused.code).toBe('graph_validation_failed')
    expect(refused.issues).toEqual([{ code: 'root_binding_ambiguous_root', ruleRef: null, structure: null, nodeKeys: ['doc:a', 'doc:b'], edgeIds: [], message: 'R 2건' }])
  })

  it('reads the definition (draft, latest revision, policies)', async () => {
    getRequest.mockResolvedValue({ data: {
      ok: true, flow_id: F, ownership: { state: 'graph', graph_revision_no: 2 },
      draft: null,
      latest_revision: { revision_no: 2, nodes: [{ node_key: 'gn_r', node_type: 'document', doc_type: 'R', palette_item_id: null, label: '요구', assignee: null }], edges: [] },
      policies: { start_node: 'undecided' },
    } })
    const def = await fetchFlowGraph(F)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/graph`, {}, HEADERS)
    expect(def.latestRevision).toEqual({ revisionNo: 2, nodes: [{ nodeKey: 'gn_r', nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구', assignee: null }], edges: [] })
    expect(def.draft).toBeNull()
    expect(def.policies).toEqual({ start_node: 'undecided' })
  })
})

describe('Canvas Graph blocks and store loaders', () => {
  it('normalizes view, graph and graph_node (P0008 S1-4)', () => {
    const canvas = toFlowCanvas({
      flow_id: F, ownership: { state: 'unassigned' }, projection: 'documents',
      view: { mode: 'draft', revision_no: null, draft_version: 3 },
      graph: { draft: { draft_version: 3, base_revision_no: null, updated_at: 't', updated_by: 'u_kim', confirmable: true, blocked_reason: null }, latest_revision_no: null, root_binding: null },
      nodes: [{
        node_key: 'gn_a1', identity_source: 'graph_node', origin: 'draft_node', kind: 'requirement', doc_type: 'R', title: '요구 시작', state: 'draft',
        position: { x: 0, y: 320, source: 'saved' }, editable: { move: true, structure: true }, document: null, slot: null,
        graph_node: { node_type: 'document', label: '요구 시작', palette_item_id: null, assignee: null, root_bound: false },
      }],
      edges: [], external_relations: [], viewport: null, identity: {}, collapsed: {},
    })
    expect(canvas.view).toEqual({ mode: 'draft', revisionNo: null, draftVersion: 3 })
    expect(canvas.graph?.draft).toEqual({ draftVersion: 3, baseRevisionNo: null, updatedAt: 't', updatedBy: 'u_kim', confirmable: true, blockedReason: null })
    expect(canvas.nodes[0].graphNode).toEqual({ nodeType: 'document', label: '요구 시작', paletteItemId: null, assignee: null, rootBound: false })
  })

  it('the store loaders reach the Graph API and pass the Canvas view', async () => {
    setActivePinia(createPinia())
    const store = useProjectStore()
    getRequest.mockResolvedValue({ data: { flow_id: F, ownership: {}, nodes: [], edges: [] } })
    await store.loadFlowCanvas(F, 'draft')
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/canvas`, { view: 'draft' }, HEADERS)
    await store.loadFlowCanvas(F)
    expect(getRequest).toHaveBeenLastCalledWith(`/api/v1/flows/${F}/canvas`, {}, HEADERS)
    postRequest.mockResolvedValue({ data: { draft_version: 1, confirmable: true, issues: [] } })
    expect((await store.validateFlowGraphDraft(F, 1)).confirmable).toBe(true)
    putRequest.mockResolvedValue({ data: { draft_version: 1, key_map: {} } })
    expect((await store.saveFlowGraphDraft(F, { draftVersion: 0, nodes: [], edges: [] })).draftVersion).toBe(1)
  })
})
