/**
 * flowgate.v02.0056 F2-1 — the card approval and the run's new fields in features/project (O1 0005-TR §4-1 /
 * §4-2, O2 0007-TR §4-2): the run's pending propagation ("멈춤 · 다음 카드 N개 대기") and each node's
 * awaiting_approval / stage, the Canvas node run's awaiting_approval / stage / instruction_doc_id, the
 * POST …/approval request (path, body with the shown result, the tab's x-canvas-client-id), its answer and a
 * refusal's code / status / params / server message, the store loader; and the card step frames (a document
 * review change, an AI run starting or ending) reading the Flow's Canvas again once per burst.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  putRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID, toFlowCanvas } from '@/features/project/flowCanvasApi'
import {
  CANVAS_CARD_STEP_COALESCE_MS,
  CANVAS_CARD_STEP_EVENTS,
  CANVAS_REFRESH_EVENTS,
  subscribeFlowCanvasChanges,
} from '@/features/project/flowCanvasEvents'
import { FlowGraphApiError } from '@/features/project/flowGraphApi'
import { approveFlowRunNode, toFlowRun } from '@/features/project/flowRunApi'
import { useProjectStore } from '@/features/project'

const HEADERS = { headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } }
const F = 'flowgate.v02.0056'
const T = `${F}.0004-T`
const TR = `${F}.0005-TR`

const RUN = {
  run_id: 'run_s1',
  flow_id: F,
  revision_no: 3,
  status: 'stopped',
  open_node_keys: [],
  last_event_seq: 9,
  pending_propagation_count: 2,
  pending_node_keys: ['gn_a', 'gn_b'],
  node_runs: [
    { node_key: 'gn_a', status: 'done', result_doc_id: `${F}.0002-NR`, awaiting_approval: false, stage: 'result' },
    { node_key: 'gn_t', status: 'review', result_doc_id: TR, awaiting_approval: true, stage: 'result', instruction_doc_id: T },
    { node_key: 'gn_w', status: 'open', result_doc_id: null, awaiting_approval: false, stage: 'instruction' },
  ],
}

function refusal(code: string, status: number, params: Record<string, unknown>, message = '서버 문구') {
  return Object.assign(new Error('Request failed'), { response: { status, data: { ok: false, error: { code, message, params } } } })
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('the run’s new fields (O1 / O2 §4-2)', () => {
  it('the pending propagation and each node’s approval wait and step, camelCase', () => {
    const run = toFlowRun(RUN)!
    expect(run.pendingPropagationCount).toBe(2)
    expect(run.pendingNodeKeys).toEqual(['gn_a', 'gn_b'])
    expect(run.nodeRuns).toEqual([
      { nodeKey: 'gn_a', status: 'done', resultDocId: `${F}.0002-NR`, awaitingApproval: false, stage: 'result' },
      { nodeKey: 'gn_t', status: 'review', resultDocId: TR, awaitingApproval: true, stage: 'result' },
      { nodeKey: 'gn_w', status: 'open', resultDocId: null, awaitingApproval: false, stage: 'instruction' },
    ])
  })

  it('a run without them (before O1 / O2): none pending, the node fields left out', () => {
    const run = toFlowRun({ run_id: 'r', status: 'running', node_runs: [{ node_key: 'gn_a', status: 'open' }] })!
    expect(run.pendingPropagationCount).toBe(0)
    expect(run.pendingNodeKeys).toEqual([])
    expect(run.nodeRuns).toEqual([{ nodeKey: 'gn_a', status: 'open', resultDocId: null }])
    expect(toFlowRun({ ...RUN, pending_propagation_count: -3 })!.pendingPropagationCount).toBe(0)
  })

  it('the Canvas node run carries awaiting_approval, stage and instruction_doc_id when sent', () => {
    const canvas = toFlowCanvas({
      flow_id: F,
      ownership: { state: 'graph' },
      nodes: [
        { node_key: 'gn_t', kind: 'task', node_run: { status: 'review', opened_at: null, finished_at: null, result_doc_id: TR, awaiting_approval: true, stage: 'result', instruction_doc_id: T } },
        { node_key: 'gn_old', kind: 'task', node_run: { status: 'open', opened_at: null, finished_at: null, result_doc_id: null } },
      ],
      edges: [],
    })
    expect(canvas.nodes[0].nodeRun).toEqual({ status: 'review', openedAt: null, finishedAt: null, resultDocId: TR, awaitingApproval: true, stage: 'result', instructionDocId: T })
    expect(canvas.nodes[1].nodeRun).toEqual({ status: 'open', openedAt: null, finishedAt: null, resultDocId: null })
  })
})

describe('the card approval request (O1 §4-1)', () => {
  it('POST /runs/{run_id}/nodes/{node_key}/approval with the shown result; answers the run and what it approved', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        run: { ...RUN, node_runs: [{ node_key: 'gn_t', status: 'done', result_doc_id: TR, awaiting_approval: false, stage: 'result' }] },
        approval: { node_key: 'gn_t', target_doc_ids: [T, TR], approved_doc_ids: [T, TR] },
      },
    })
    const answer = await approveFlowRunNode('run_s1', 'gn_t', TR)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/runs/run_s1/nodes/gn_t/approval', { result_doc_id: TR }, HEADERS)
    expect(answer.run).toMatchObject({ runId: 'run_s1', status: 'stopped', pendingPropagationCount: 2, nodeRuns: [{ nodeKey: 'gn_t', status: 'done' }] })
    expect(answer.approval).toEqual({ nodeKey: 'gn_t', targetDocIds: [T, TR], approvedDocIds: [T, TR] })
  })

  it('no result shown sends null (the server’s current one)', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run: RUN, approval: {} } })
    const answer = await approveFlowRunNode('run_s1', 'gn t', null)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/runs/run_s1/nodes/gn%20t/approval', { result_doc_id: null }, HEADERS)
    expect(answer.approval).toEqual({ nodeKey: 'gn t', targetDocIds: [], approvedDocIds: [] })
  })

  it.each([
    ['result_changed', 409, { run_id: 'run_s1', node_key: 'gn_t', result_doc_id: `${F}.0006-TR` }],
    ['document_approval_refused', 409, { doc_id: TR, reason: 'tr2_validation_failed', approved_doc_ids: [T] }],
    ['approval_forbidden', 403, { required: 'document.approve' }],
  ])('a refusal (%s) rejects with its code, status, params and the server’s message', async (code, status, params) => {
    postRequest.mockRejectedValue(refusal(code, status, params, '승인할 수 없습니다'))
    const error = await approveFlowRunNode('run_s1', 'gn_t', TR).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(FlowGraphApiError)
    expect(error).toMatchObject({ code, status, params, message: '승인할 수 없습니다' })
  })

  it('an answer without a run is a failure', async () => {
    postRequest.mockResolvedValue({ data: { ok: true } })
    await expect(approveFlowRunNode('run_s1', 'gn_t', TR)).rejects.toBeInstanceOf(Error)
  })

  it('the project store approves through the same transport', async () => {
    setActivePinia(createPinia())
    postRequest.mockResolvedValue({ data: { ok: true, run: RUN, approval: { node_key: 'gn_t', target_doc_ids: [TR], approved_doc_ids: [TR] } } })
    const answer = await useProjectStore().approveFlowRunNode('run_s1', 'gn_t', TR)
    expect(answer.run.runId).toBe('run_s1')
    expect(postRequest.mock.calls[0][0]).toBe('/api/v1/runs/run_s1/nodes/gn_t/approval')
  })
})

// --- Card step frames -----------------------------------------------------------------------------

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static all: FakeEventSource[] = []
  readyState = FakeEventSource.CONNECTING
  closed = false
  private listeners = new Map<string, Array<(event: Event) => void>>()

  constructor(readonly url: string) {
    FakeEventSource.all.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const docFrame = (type: string, groupId: string, docId: string | null = null) => ({ event_type: type, payload: {}, project: 'flowgate', group_id: groupId, doc_id: docId })

describe('card step frames read the Flow’s Canvas again once per burst (O2 §4-2: a step moves without a node event)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    sessionStorage.clear()
  })

  it('the step events are their own list; the Canvas refresh list is unchanged', () => {
    expect(CANVAS_CARD_STEP_EVENTS).toEqual(['doc_review_status_changed', 'ai_invoke_started', 'ai_invoke_finished'])
    expect(CANVAS_REFRESH_EVENTS).toEqual(['graph_canvas_changed', 'flow_ownership_changed', 'graph_run_status_changed', 'graph_node_state_changed'])
  })

  it('a burst of a Flow’s step frames is one change of that Flow after the window; another Flow’s is its own', () => {
    const changed: string[] = []
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: (flowId) => changed.push(flowId), resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('doc_review_status_changed', docFrame('doc_review_status_changed', F, T))
    es.emit('ai_invoke_started', docFrame('ai_invoke_started', F, TR))
    es.emit('ai_invoke_finished', docFrame('ai_invoke_finished', 'flowgate.v02.0057'))
    expect(changed).toEqual([])
    vi.advanceTimersByTime(CANVAS_CARD_STEP_COALESCE_MS)
    expect(changed.sort()).toEqual([F, 'flowgate.v02.0057'])
    // A run event is told at once, as before.
    es.emit('graph_run_status_changed', docFrame('graph_run_status_changed', F))
    expect(changed.filter((flowId) => flowId === F)).toHaveLength(2)
    stop()
  })

  it('unsubscribing drops a waiting step change', () => {
    const changed: string[] = []
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: (flowId) => changed.push(flowId), resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('ai_invoke_finished', docFrame('ai_invoke_finished', F))
    stop()
    vi.advanceTimersByTime(CANVAS_CARD_STEP_COALESCE_MS * 2)
    expect(changed).toEqual([])
  })
})
