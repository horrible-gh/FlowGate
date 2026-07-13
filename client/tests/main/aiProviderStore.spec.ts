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
  })
})