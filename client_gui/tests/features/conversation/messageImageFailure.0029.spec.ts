import { describe, expect, it, vi } from 'vitest'
const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({ getRequest, postRequest: vi.fn(), patchRequest: vi.fn(), extractApiErrorMessage: vi.fn() }))
import { fetchRun } from '@/features/conversation/api'

const diagnostic = { reason: 'message_images', images_total: 1, images_delivered: 1 }
const first = { reason: 'spawn_failed', detail: 'first connection failed' }
const last = { reason: 'spawn_failed', detail: 'last connection failed', remote_final: { cause: 'spawn_error', message: 'cannot launch', workdir: '/work' } }

describe('image chat with all providers failed', () => {
  it.each([
    [diagnostic, first, last],
    [first, last, diagnostic],
    [first, diagnostic, last, diagnostic],
    [{ reason: 'truncated', dropped: 10 }, last],
    [first, last],
  ])('retains the last actual connection failure for history %#', async (...history) => {
    // The request client answers `{ data }` (conversation api `call` unwraps it), like the other api specs.
    getRequest.mockResolvedValue({ data: { run_id: 'r', status: 'finished', action_scope: 'chat', end_reason: 'all_providers_failed', fallback_history: history } })
    expect((await fetchRun('r')).failure).toEqual({ cause: 'spawn_error', message: 'cannot launch', workdir: '/work', detail: 'last connection failed' })
  })
  it('does not invent a failure from diagnostics alone', async () => {
    getRequest.mockResolvedValue({ data: { end_reason: 'all_providers_failed', fallback_history: [diagnostic] } })
    expect((await fetchRun('r')).failure).toBeUndefined()
  })
})
