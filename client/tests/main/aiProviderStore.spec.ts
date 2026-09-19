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

// flowgate.default.0585 T0004 §3 (NR0003 §6/§13 권장 2): AiInvokeDialog's visible watcher and
// its own start() both called ensureLoaded(project) on a cold open, and every other preload
// surface (AppHeader, ContinuousWarningDialog, ...) can race the same project too. Before this,
// "already loaded" was the only de-dup — an in-flight, not-yet-resolved load was invisible to a
// second caller, so it fired its own GET.
describe('in-flight load coalescing (0585 T0004 §3)', () => {
  it('joins a second ensureLoaded for the same project onto the first in-flight GET', async () => {
    let resolveFirst!: (value: unknown) => void
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    const store = useAiProviderStore()

    const first = store.ensureLoaded('flowgate')
    const second = store.ensureLoaded('flowgate')
    expect(getRequest).toHaveBeenCalledTimes(1)

    resolveFirst({ data: payload() })
    await Promise.all([first, second])

    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(store.selectedProviderId).toBe('aip_two')
    expect(store.loadedProjectId).toBe('flowgate')
  })

  it('still starts its own request for force=true while a non-force load is in flight', async () => {
    let resolveFirst!: (value: unknown) => void
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()

    const first = store.ensureLoaded('flowgate')
    const forced = store.loadForProject('flowgate', true)
    expect(getRequest).toHaveBeenCalledTimes(2)

    resolveFirst({ data: payload() })
    await Promise.all([first, forced])
    expect(store.selectedProviderId).toBe('aip_two')
  })

  it('lets a caller retry with force after a failed load, instead of joining nothing forever', async () => {
    getRequest.mockRejectedValueOnce(new Error('boom'))
    const store = useAiProviderStore()
    await store.ensureLoaded('flowgate')
    expect(store.error).toBe('load_failed')

    getRequest.mockResolvedValueOnce({ data: payload() })
    await store.loadForProject('flowgate', true)
    expect(store.error).toBeNull()
    expect(store.selectedProviderId).toBe('aip_two')
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('does not let a late response for an abandoned project overwrite the project switched to', async () => {
    let resolveFlowgate!: (value: unknown) => void
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { resolveFlowgate = resolve }))
    getRequest.mockResolvedValueOnce({
      data: { ok: true, project: 'other', default_provider_id: 'aip_other', providers: [
        { id: 'aip_other', name: 'Other', exec_type: 'cli', kind: 'codex' },
      ] },
    })
    const store = useAiProviderStore()

    const flowgateLoad = store.loadForProject('flowgate')
    const otherLoad = store.loadForProject('other')
    await otherLoad
    expect(store.loadedProjectId).toBe('other')
    expect(store.selectedProviderId).toBe('aip_other')

    // The abandoned 'flowgate' request resolves AFTER 'other' has already landed.
    resolveFlowgate({ data: payload() })
    await flowgateLoad

    expect(store.loadedProjectId).toBe('other')
    expect(store.selectedProviderId).toBe('aip_other')
  })

  // 0585 TR0005 rework: `pendingLoads` used to key purely on projectId, so re-requesting A while
  // its first fetch was still in flight but already superseded by an intervening B load would
  // JOIN that doomed A promise instead of starting a new one. The joined promise resolves fine,
  // but its own fetchAndApply discards its response (serial guard), so the caller's "it loaded"
  // await returns while the store is still left on B's data.
  it('starts a fresh request for A instead of joining its own superseded in-flight load (A -> B -> A)', async () => {
    let resolveA1!: (value: unknown) => void
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { resolveA1 = resolve }))
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'other',
        default_provider_id: 'aip_other',
        providers: [{ id: 'aip_other', name: 'Other', exec_type: 'cli', kind: 'codex' }],
      },
    })
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()

    const a1 = store.ensureLoaded('flowgate')
    const b = store.ensureLoaded('other')
    await b
    expect(store.loadedProjectId).toBe('other')

    // A is requested again while its first fetch is still pending and already doomed by B's
    // serial bump -- this must fire a third GET, not join a1.
    const a2 = store.ensureLoaded('flowgate')
    expect(getRequest).toHaveBeenCalledTimes(3)

    await a2
    expect(store.loadedProjectId).toBe('flowgate')
    expect(store.selectedProviderId).toBe('aip_two')

    // The stale first A response lands last and must not disturb the fresh A2 result.
    resolveA1({ data: payload() })
    await a1
    expect(store.loadedProjectId).toBe('flowgate')
    expect(store.selectedProviderId).toBe('aip_two')
  })

  // Same defect, reached via clear() instead of a second project: clear() bumps requestSerial
  // exactly like a switch to another project does, so an entry left behind by a pre-clear load
  // must not be joined by a same-project reload right after.
  it('does not join a stale entry left behind by clear() when the same project is reloaded immediately', async () => {
    let resolveFirst!: (value: unknown) => void
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    getRequest.mockResolvedValueOnce({ data: payload() })
    const store = useAiProviderStore()

    const first = store.ensureLoaded('flowgate')
    store.clear()
    const second = store.ensureLoaded('flowgate')
    expect(getRequest).toHaveBeenCalledTimes(2)

    await second
    expect(store.loadedProjectId).toBe('flowgate')
    expect(store.selectedProviderId).toBe('aip_two')

    resolveFirst({ data: payload() })
    await first
    expect(store.loadedProjectId).toBe('flowgate')
    expect(store.selectedProviderId).toBe('aip_two')
  })
})
