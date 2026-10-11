import { beforeEach, describe, expect, it, vi } from 'vitest'
import { startAiInvoke } from '@main/composables/useAiInvokeStarter'

const { postRequest, ensureLoaded, trackStarted } = vi.hoisted(() => ({
  postRequest: vi.fn(),
  ensureLoaded: vi.fn(),
  trackStarted: vi.fn(),
}))
vi.mock('@shared/api', () => ({ postRequest, getRequest: vi.fn() }))
vi.mock('@main/stores/aiProvider', () => ({
  useAiProviderStore: () => ({ ensureLoaded, selectedProviderId: 'provider-1', pinned: false }),
}))
vi.mock('@main/stores/aiInvokeRuns', () => ({
  aiInvokeGroupId: () => 'flowgate.default.0491',
  useAiInvokeRunsStore: () => ({ trackStarted }),
}))

beforeEach(() => {
  postRequest.mockReset()
  ensureLoaded.mockReset()
  trackStarted.mockReset()
})

describe('unsafe token scratch start response', () => {
  it('returns a dedicated storage error without tracking a run', async () => {
    postRequest.mockRejectedValue({
      response: {
        status: 409,
        data: { code: 'token_scratch_storage_unsafe', message: 'internal path must be ignored' },
      },
    })
    const result = await startAiInvoke({
      project: 'flowgate',
      module: 'default',
      group: '0491',
      docRef: 'flowgate.default.0491.0004-T',
      actionScope: 'edit',
      mode: 'single',
    })
    expect(result).toEqual({ ok: false, kind: 'token_scratch_storage_unsafe' })
    expect(trackStarted).not.toHaveBeenCalled()
  })
})
