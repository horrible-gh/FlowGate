import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiProviderStore } from '@main/stores/aiProvider'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest }))

const SELECTION_KEY = 'flowgate.user.guest.ai-provider.flowgate'
const LEGACY_PIN_KEY = 'flowgate.user.guest.ai-provider-pin.flowgate'
const OTHER_LEGACY_PIN_KEY = 'flowgate.user.guest.ai-provider-pin.other'

const PROVIDERS = [
  { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
  { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
]

function payload(project = 'flowgate') {
  return { ok: true, project, default_provider_id: 'aip_two', providers: PROVIDERS }
}

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

  // 0448 T0005 §6 (was: "persists only an explicit selection as a provider pin"). The old
  // title WAS the defect B0001 reported: `selectProvider` — the function every one of the ten
  // ordinary selectors calls — wrote both the selection and a permanent force-all, and a
  // reload brought the force back. The two halves are separate contracts now, so this asserts
  // the ordinary half in full: the pick is stored, no force is created, and the pre-0448 key
  // is gone rather than migrated.
  it('keeps an ordinary selection as a default and never turns it into a provider pin', async () => {
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    localStorage.setItem(LEGACY_PIN_KEY, '1')

    store.selectProvider('aip_one')

    expect(store.selectedProviderId).toBe('aip_one')
    expect(localStorage.getItem(SELECTION_KEY)).toBe('aip_one')
    expect(store.pinned).toBe(false)
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()

    // A fresh store (the reload a person actually does) restores the SELECTION and nothing
    // else — this is the assertion the old test had inverted.
    setActivePinia(createPinia())
    getRequest.mockResolvedValueOnce({ data: payload() })
    const restored = useAiProviderStore()
    await restored.loadForProject('flowgate')
    expect(restored.selectedProviderId).toBe('aip_one')
    expect(restored.pinned).toBe(false)
  })

  // 0448 T0005 §7-3.
  it('turns force-all on only through the explicit API, and clearPin takes it back off', async () => {
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')

    // Positive control for every `pinned === false` assertion elsewhere: the flag CAN be set,
    // so those are not passing because nothing can ever set it.
    store.forceProviderForAllSteps('aip_one')
    expect(store.pinned).toBe(true)
    expect(store.selectedProviderId).toBe('aip_one')
    // Force-all is run state, not a stored preference — it leaves no key behind.
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()

    // An ordinary pick afterwards changes the default without cancelling the force the person
    // explicitly turned on; only clearPin() does that.
    store.selectProvider('aip_two')
    expect(store.pinned).toBe(true)
    store.clearPin()
    expect(store.pinned).toBe(false)
    expect(store.selectedProviderId).toBe('aip_two')
  })

  it('refuses to force a provider that is not in the loaded list', async () => {
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')

    store.forceProviderForAllSteps('aip_missing')
    expect(store.pinned).toBe(false)
    expect(store.selectedProviderId).toBe('aip_two')
  })

  it('clears the pin without discarding the selected provider', async () => {
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    store.forceProviderForAllSteps('aip_one')
    store.clearPin()

    expect(store.selectedProviderId).toBe('aip_one')
    expect(store.pinned).toBe(false)
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()
  })

  // 0448 T0005 §7-4.
  it('restores only the selected default on re-entry, never the force state', async () => {
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')
    store.forceProviderForAllSteps('aip_one')
    expect(store.pinned).toBe(true)

    // Re-entering the project (a fresh store, as a page reload or a re-login builds).
    setActivePinia(createPinia())
    getRequest.mockResolvedValue({ data: payload() })
    const reentered = useAiProviderStore()
    await reentered.loadForProject('flowgate')
    expect(reentered.selectedProviderId).toBe('aip_one')
    expect(reentered.pinned).toBe(false)

    // And a repeat load does not resurrect it either.
    await reentered.loadForProject('flowgate')
    await reentered.loadForProject('flowgate', true)
    expect(reentered.pinned).toBe(false)
    expect(reentered.selectedProviderId).toBe('aip_one')
  })

  // 0448 T0005 §2-4 / §7-4: the pre-0448 key is removed everywhere, and never read back.
  it('purges the legacy ai-provider-pin key on load, repeat load, project switch and clear', async () => {
    localStorage.setItem(SELECTION_KEY, 'aip_one')
    localStorage.setItem(LEGACY_PIN_KEY, '1')
    getRequest.mockResolvedValue({ data: payload() })
    const store = useAiProviderStore()

    await store.loadForProject('flowgate')
    // The stale `1` is not migrated into the new force state and not kept.
    expect(store.pinned).toBe(false)
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()
    expect(store.selectedProviderId).toBe('aip_one')

    localStorage.setItem(LEGACY_PIN_KEY, '1')
    await store.loadForProject('flowgate')
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()

    // Project switch: the key of the project being left goes too.
    localStorage.setItem(LEGACY_PIN_KEY, '1')
    localStorage.setItem(OTHER_LEGACY_PIN_KEY, '1')
    getRequest.mockResolvedValue({ data: payload('other') })
    await store.loadForProject('other')
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()
    expect(localStorage.getItem(OTHER_LEGACY_PIN_KEY)).toBeNull()
    expect(store.pinned).toBe(false)

    localStorage.setItem(OTHER_LEGACY_PIN_KEY, '1')
    store.clear()
    expect(localStorage.getItem(OTHER_LEGACY_PIN_KEY)).toBeNull()
  })

  it('leaves no legacy pin key behind when the provider list fails to load', async () => {
    localStorage.setItem(LEGACY_PIN_KEY, '1')
    getRequest.mockRejectedValueOnce(new Error('boom'))
    const store = useAiProviderStore()
    await store.loadForProject('flowgate')

    expect(store.error).toBe('load_failed')
    expect(store.pinned).toBe(false)
    expect(localStorage.getItem(LEGACY_PIN_KEY)).toBeNull()
  })
})
