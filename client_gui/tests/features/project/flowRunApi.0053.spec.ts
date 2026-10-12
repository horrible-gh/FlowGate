/**
 * flowgate.v02.0053 T#9 — Graph run transport in features/project (P0008 §7 S5-1 ~ S5-5): the current run
 * read, start, stop and resume requests, the run object they answer, a refusal's server code, the store
 * loaders, and the run events as Canvas refresh signals. Every request carries this tab's
 * `x-canvas-client-id`.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID } from '@/features/project/flowCanvasApi'
import { CANVAS_REFRESH_EVENTS } from '@/features/project/flowCanvasEvents'
import { FlowGraphApiError } from '@/features/project/flowGraphApi'
import { fetchCurrentFlowRun, resumeFlowRun, startFlowRun, stopFlowRun, toFlowRun } from '@/features/project/flowRunApi'
import { useProjectStore } from '@/features/project'

const HEADERS = { headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } }
const F = 'flowgate.v02.0053'

const RUN = {
  run_id: 'run_a1',
  flow_id: F,
  revision_no: 2,
  status: 'running',
  started_at: '2026-10-11T13:00:00Z',
  started_by: 'u_lee',
  stopped_at: null,
  finished_at: null,
  open_node_keys: ['gn_b'],
  last_event_seq: 4,
  node_runs: [
    { node_key: 'gn_a', status: 'done', opened_at: null, finished_at: null, result_doc_id: `${F}.0002-T`, root_bound: true, dispatch: null },
    { node_key: 'gn_b', status: 'open', opened_at: null, finished_at: null, result_doc_id: null, root_bound: false, dispatch: { token_issued: true } },
  ],
}

function refusal(code: string, status = 409) {
  return Object.assign(new Error('Request failed'), { response: { status, data: { ok: false, error: { code, message: '실행 규칙이 아직 정해지지 않았습니다.', params: { flow_id: F } } } } })
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('run reads (P0008 S5-4)', () => {
  it('reads the current run with its nodes, camelCase', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, run: RUN } })
    const run = await fetchCurrentFlowRun(F)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/flows/${F}/runs/current`, {}, HEADERS)
    expect(run).toEqual({
      runId: 'run_a1',
      flowId: F,
      revisionNo: 2,
      status: 'running',
      openNodeKeys: ['gn_b'],
      nodeRuns: [
        { nodeKey: 'gn_a', status: 'done', resultDocId: `${F}.0002-T` },
        { nodeKey: 'gn_b', status: 'open', resultDocId: null },
      ],
      lastEventSeq: 4,
    })
  })

  it('answers null when the Flow has no running or stopped run', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, run: null } })
    expect(await fetchCurrentFlowRun(F)).toBeNull()
    expect(toFlowRun({ status: 'running' })).toBeNull()
    expect(toFlowRun(null)).toBeNull()
  })
})

describe('run control (P0008 S5-1 / S5-5)', () => {
  it('starts the latest revision, stops and resumes a run', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, run: RUN } })
    postRequest.mockResolvedValueOnce({ data: { ok: true, run: { ...RUN, status: 'stopped', node_runs: undefined } } })
    postRequest.mockResolvedValueOnce({ data: { ok: true, run: { ...RUN, node_runs: undefined } } })

    expect((await startFlowRun(F)).status).toBe('running')
    expect(postRequest).toHaveBeenNthCalledWith(1, `/api/v1/flows/${F}/runs`, { revision_no: null }, HEADERS)

    const stopped = await stopFlowRun('run_a1')
    expect(stopped).toMatchObject({ runId: 'run_a1', status: 'stopped', nodeRuns: null })
    expect(postRequest).toHaveBeenNthCalledWith(2, '/api/v1/runs/run_a1/stop', {}, HEADERS)

    expect((await resumeFlowRun('run_a1')).status).toBe('running')
    expect(postRequest).toHaveBeenNthCalledWith(3, '/api/v1/runs/run_a1/resume', {}, HEADERS)
  })

  it('a refusal rejects with the server code and its params (run_policy_undecided, run_already_active …)', async () => {
    postRequest.mockRejectedValue(refusal('run_policy_undecided'))
    const error = await startFlowRun(F).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(FlowGraphApiError)
    expect(error).toMatchObject({ code: 'run_policy_undecided', status: 409, params: { flow_id: F } })

    postRequest.mockRejectedValue(refusal('run_not_running'))
    expect(await stopFlowRun('run_a1').catch((e: unknown) => (e as FlowGraphApiError).code)).toBe('run_not_running')
  })

  it('an answer without a run is a failure, never a run', async () => {
    postRequest.mockResolvedValue({ data: { ok: true } })
    await expect(startFlowRun(F)).rejects.toBeInstanceOf(Error)
  })
})

describe('store loaders and events', () => {
  it('the project store runs the Flow through the same transport', async () => {
    setActivePinia(createPinia())
    const store = useProjectStore()
    getRequest.mockResolvedValue({ data: { ok: true, run: null } })
    postRequest.mockResolvedValue({ data: { ok: true, run: RUN } })
    expect(await store.loadFlowRun(F)).toBeNull()
    expect((await store.startFlowRun(F)).runId).toBe('run_a1')
    expect((await store.stopFlowRun('run_a1')).runId).toBe('run_a1')
    expect((await store.resumeFlowRun('run_a1')).runId).toBe('run_a1')
    expect(postRequest.mock.calls.map((call) => call[0])).toEqual([`/api/v1/flows/${F}/runs`, '/api/v1/runs/run_a1/stop', '/api/v1/runs/run_a1/resume'])
  })

  it('a run status or node state change reads the Canvas again (the cards’ colour dots)', () => {
    expect(CANVAS_REFRESH_EVENTS).toEqual(['graph_canvas_changed', 'flow_ownership_changed', 'graph_run_status_changed', 'graph_node_state_changed'])
  })
})
