import { afterEach, expect, it, vi } from 'vitest'
import { effectScope, nextTick } from 'vue'
import { beginChatActivity, useFlowChatActivity } from '@/features/conversation/flowChatActivity'
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: get }))
vi.mock('@/features/project', () => ({ useProjectStore: vi.fn() }))
afterEach(() => vi.clearAllMocks())
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }
it('shows local preparation and server chat activity, but ignores a live card run', async () => {
  get.mockResolvedValue({ data: { active: true, doc_ref: 'flow.0001-T', chat_runs: [] } })
  const scope = effectScope()
  const states = scope.run(() => useFlowChatActivity(() => 'p', () => ['f']))!
  await settle()
  expect(states.value.f).toBe(false)
  const finish = beginChatActivity('f')
  expect(states.value.f).toBe(true)
  await settle()
  get.mockResolvedValue({ data: { chat_runs: [{ doc_ref: 'flow.other-CH' }] } })
  finish()
  await settle()
  expect(states.value.f).toBe(true)
  get.mockResolvedValue({ data: { chat_runs: [] } })
  window.dispatchEvent(new Event('online'))
  await settle()
  expect(states.value.f).toBe(false)
  scope.stop()
})
it('refreshes again when completion arrives during a pending read and retains state on network error', async () => {
  let resolve!: (data: unknown) => void
  get.mockImplementationOnce(() => new Promise(done => { resolve = done }))
  get.mockResolvedValue({ data: { chat_runs: [{ doc_ref: 'flow-CH' }] } })
  const scope = effectScope()
  const states = scope.run(() => useFlowChatActivity(() => 'p', () => ['f']))!
  window.dispatchEvent(new Event('online'))
  resolve({ data: { chat_runs: [] } })
  await settle()
  expect(get).toHaveBeenCalledTimes(2)
  expect(states.value.f).toBe(true)
  get.mockRejectedValue(new Error('offline'))
  window.dispatchEvent(new Event('online'))
  await settle()
  expect(states.value.f).toBe(true)
  scope.stop()
})
