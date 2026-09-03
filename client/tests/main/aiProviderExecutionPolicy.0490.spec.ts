import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_EXECUTION_POLICY,
  repeatCountChoices,
  useAiProviderStore,
} from '@main/stores/aiProvider'

// 0490 T0007 §6: repeatCountChoices() boundaries + the store's execution_policy
// adopt/fallback contract (§3.1/§3.2). Distinct from the protected aiProviderStore.spec.ts,
// which must stay untouched and keeps asserting the pre-0490 provider-selection behavior.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const PROVIDERS = [{ id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' }]

function payload(executionPolicy?: unknown) {
  const base: Record<string, unknown> = {
    ok: true,
    project: 'flowgate',
    default_provider_id: 'aip_one',
    providers: PROVIDERS,
  }
  if (executionPolicy !== undefined) base.execution_policy = executionPolicy
  return base
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
  window.__accessToken__ = undefined
})

describe('repeatCountChoices (pure function)', () => {
  it('builds the finite tail with the unlimited/zero sentinels prepended', () => {
    const policy = { repeat_count_max: 3, repeat_count_min: 1, repeat_count_hard_max: 30 }
    expect(repeatCountChoices(policy, { allowZero: false })).toEqual([-1, 1, 2, 3])
    expect(repeatCountChoices(policy, { allowZero: true })).toEqual([-1, 0, 1, 2, 3])
  })

  it('clamps to the structural minimum at the max=1 boundary', () => {
    const policy = { repeat_count_max: 1, repeat_count_min: 1, repeat_count_hard_max: 30 }
    expect(repeatCountChoices(policy, { allowZero: false })).toEqual([-1, 1])
    expect(repeatCountChoices(policy, { allowZero: true })).toEqual([-1, 0, 1])
  })

  it('reaches the structural hard max (max=30) with no truncation', () => {
    const policy = { repeat_count_max: 30, repeat_count_min: 1, repeat_count_hard_max: 30 }
    const choices = repeatCountChoices(policy, { allowZero: true })
    // -1, 0, then 1..30
    expect(choices).toHaveLength(32)
    expect(choices[0]).toBe(-1)
    expect(choices[1]).toBe(0)
    expect(choices[choices.length - 1]).toBe(30)
  })

  it('omits the unlimited sentinel when allowUnlimited is false', () => {
    expect(repeatCountChoices(DEFAULT_EXECUTION_POLICY, { allowZero: false, allowUnlimited: false })).toEqual([1, 2, 3])
  })
})

describe('aiProvider store execution_policy adoption (§3.2 fallback contract)', () => {
  it('adopts a complete, finite execution_policy from the response', async () => {
    getRequest.mockResolvedValueOnce({
      data: payload({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy).toEqual({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 })
  })

  it('falls back to the default ceiling when execution_policy is absent (pre-0490 mocked response)', async () => {
    getRequest.mockResolvedValueOnce({ data: payload(undefined) })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy).toEqual(DEFAULT_EXECUTION_POLICY)
  })

  it('falls back when execution_policy is missing a field', async () => {
    getRequest.mockResolvedValueOnce({ data: payload({ repeat_count_max: 5, repeat_count_min: 1 }) })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy).toEqual(DEFAULT_EXECUTION_POLICY)
  })

  it('falls back when a field is non-numeric', async () => {
    getRequest.mockResolvedValueOnce({
      data: payload({ repeat_count_max: '5', repeat_count_min: 1, repeat_count_hard_max: 30 }),
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy).toEqual(DEFAULT_EXECUTION_POLICY)
  })

  it('resets to the default on clear()', async () => {
    getRequest.mockResolvedValueOnce({
      data: payload({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy.repeat_count_max).toBe(5)

    store.clear()
    expect(store.executionPolicy).toEqual(DEFAULT_EXECUTION_POLICY)
  })

  it('resets to the default when a subsequent load fails', async () => {
    getRequest.mockResolvedValueOnce({
      data: payload({ repeat_count_max: 5, repeat_count_min: 1, repeat_count_hard_max: 30 }),
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.executionPolicy.repeat_count_max).toBe(5)

    getRequest.mockRejectedValueOnce(new Error('boom'))
    await store.loadForProject('other')
    expect(store.executionPolicy).toEqual(DEFAULT_EXECUTION_POLICY)
  })
})
