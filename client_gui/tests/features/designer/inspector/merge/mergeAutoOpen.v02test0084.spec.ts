import { effectScope, ref, type EffectScope } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { toCardMergeState, type CardMergeState } from '@/features/document/api/cardMerge'
import { useMergeAutoOpen, type MergeAutoCandidate } from '@/features/designer/inspector/features/merge/useMergeAutoOpen'

vi.mock('@/features/document', () => ({ cardMergeRead: vi.fn() }))
const scopes: EffectScope[] = []
afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()) })
const candidate = (signature = '1', attemptId = 'a'): MergeAutoCandidate => ({ flowId: 'f', nodeKey: 'n', attemptId, signature })
const state = (status = 'running', attemptId = 'a') => toCardMergeState({
  flow_id: 'f', node_key: 'n', attempt_id: attemptId,
  state: status === 'running' ? 'catching_up' : 'needs_choice', integration_status: 'conflict',
  auto_resolve: { state: status, attempt: 3, max_attempts: 3,
    attempts: [{ no: 3, result: status === 'running' ? 'running' : 'conflicts_left', ended_at: '2020-01-01T00:00:00Z' }] },
})
function setup(read = vi.fn<() => Promise<CardMergeState>>()) {
  const candidates = ref<MergeAutoCandidate[]>([candidate()])
  const open = vi.fn()
  const scope = effectScope(); scopes.push(scope)
  scope.run(() => useMergeAutoOpen({ candidates: () => candidates.value, open, read, now: () => Date.parse('2026-01-01') }))
  return { candidates, open, read, scope }
}
function deferred() {
  let resolve!: (state: CardMergeState) => void
  const promise = new Promise<CardMergeState>(done => { resolve = done })
  return { promise, resolve }
}

describe('automatic exhausted conflict window', () => {
  it('opens once after three failures and does not reopen after dismissal', async () => {
    const read = vi.fn().mockResolvedValueOnce(state()).mockResolvedValue(state('exhausted'))
    const h = setup(read); await flushPromises()
    h.candidates.value = [candidate('2')]; await flushPromises()
    expect(h.open).toHaveBeenCalledTimes(1)
    expect(h.open).toHaveBeenCalledWith('f', 'n')
    h.candidates.value = [candidate('3')]; await flushPromises()
    expect(h.open).toHaveBeenCalledTimes(1)
  })
  it('does not pop up a conflict exhausted before the page opened', async () => {
    const h = setup(vi.fn().mockResolvedValue(state('exhausted'))); await flushPromises()
    expect(h.open).not.toHaveBeenCalled()
  })
  it('handles exhaustion arriving before the earlier running response', async () => {
    const first = deferred(), second = deferred()
    const h = setup(vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise))
    h.candidates.value = [candidate('2')]; await flushPromises()
    second.resolve(state('exhausted')); await flushPromises()
    expect(h.open).not.toHaveBeenCalled()
    first.resolve(state()); await flushPromises()
    expect(h.open).toHaveBeenCalledTimes(1)
    expect(h.open).toHaveBeenCalledWith('f', 'n')
  })
  it.each(['removed', 'reopened', 'replaced', 'disposed'])('ignores a late response after the card is %s', async mode => {
    const late = deferred()
    const h = setup(vi.fn().mockResolvedValueOnce(state()).mockReturnValueOnce(late.promise).mockResolvedValue(state('exhausted')))
    await flushPromises()
    h.candidates.value = [candidate('2')]; await flushPromises()
    if (mode === 'disposed') h.scope.stop()
    else if (mode === 'replaced') { h.candidates.value = [candidate('3', 'b')]; await flushPromises() }
    else {
      h.candidates.value = []; await flushPromises()
      if (mode === 'reopened') { h.candidates.value = [candidate('3')]; await flushPromises() }
    }
    late.resolve(state('exhausted')); await flushPromises()
    expect(h.open).not.toHaveBeenCalled()
  })
})
