import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiProviderStore } from '@main/stores/aiProvider'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  localStorage.clear()
  window.__accessToken__ = undefined
})

describe('runtime AI provider store', () => {
  it('loads effective providers and selects the configured default', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'flowgate',
        default_provider_id: 'aip_two',
        providers: [
          { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
          { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
        ],
      },
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.selectedProviderId).toBe('aip_two')
    expect(store.pinned).toBe(false)
    expect(store.providers).toHaveLength(2)
  })

  it('uses the access-token subject in the per-user storage key', async () => {
    window.__accessToken__ = `x.${btoa(JSON.stringify({ sub: 'usr_42' }))}.x`
    localStorage.setItem('flowgate.user.usr_42.ai-provider.flowgate', 'aip_one')
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'flowgate',
        default_provider_id: 'aip_two',
        providers: [
          { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
          { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
        ],
      },
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.selectedProviderId).toBe('aip_one')
  })
  it('restores a valid per-user project selection', async () => {
    localStorage.setItem('flowgate.user.guest.ai-provider.flowgate', 'aip_one')
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'flowgate',
        default_provider_id: 'aip_two',
        providers: [
          { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
          { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
        ],
      },
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    expect(store.selectedProviderId).toBe('aip_one')
    expect(store.pinned).toBe(false)
  })

  it('persists only an explicit selection as a provider pin', async () => {
    const data = {
      ok: true,
      project: 'flowgate',
      default_provider_id: 'aip_two',
      providers: [
        { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
        { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
      ],
    }
    getRequest.mockResolvedValueOnce({ data })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    store.selectProvider('aip_one')

    expect(store.pinned).toBe(true)
    expect(localStorage.getItem('flowgate.user.guest.ai-provider-pin.flowgate')).toBe('1')

    setActivePinia(createPinia())
    getRequest.mockResolvedValueOnce({ data })
    const restored = useAiProviderStore()
    await restored.loadForProject('flowgate')
    expect(restored.selectedProviderId).toBe('aip_one')
    expect(restored.pinned).toBe(true)
  })

  it('clears the pin without discarding the selected provider', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'flowgate',
        default_provider_id: 'aip_two',
        providers: [
          { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
          { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
        ],
      },
    })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    store.selectProvider('aip_one')
    store.clearPin()

    expect(store.selectedProviderId).toBe('aip_one')
    expect(store.pinned).toBe(false)
    expect(localStorage.getItem('flowgate.user.guest.ai-provider-pin.flowgate')).toBeNull()
  })
})