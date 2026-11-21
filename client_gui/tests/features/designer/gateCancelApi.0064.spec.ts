import { describe, expect, it, vi } from 'vitest'
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest }))
import { runGateDecide } from '@/features/document/api/run'

describe('pre-decision cancel transport', () => {
  it('sends the versioned cancel endpoint without creating an approval', async () => {
    postRequest.mockResolvedValue({ data: { run: { run_id: 'r', status: 'idle', node_runs: [] } } })
    const intent = { request_id: 'request', expected_attempt_id: 'attempt', expected_settings_version: 3 }
    await runGateDecide('r', 'gate', 'cancel', undefined, intent)
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/runs/r/nodes/gate/gate-cancel', intent)
  })
})
