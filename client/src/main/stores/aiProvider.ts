import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getRequest } from '@shared/api'

export interface RuntimeAiProvider {
  id: string
  name: string
  exec_type: string
  kind: string
}

interface RuntimeAiProvidersResponse {
  ok: boolean
  project: string
  providers: RuntimeAiProvider[]
  default_provider_id: string | null
}

function currentUserId(): string {
  const token = window.__accessToken__
  if (!token) return 'guest'
  try {
    const payload = JSON.parse(atob(token.split('.')[1])) as {
      sub?: string
      user_id?: string
      username?: string
    }
    return String(payload.sub ?? payload.user_id ?? payload.username ?? 'guest')
  } catch {
    return 'guest'
  }
}

function storageKey(projectId: string): string {
  return `flowgate.user.${currentUserId()}.ai-provider.${projectId}`
}

// 0448 T0005 §2-4 (NR0003 §6-1): the OLD implicit-pin key. Nothing writes it any more.
// `selectProvider()` used to stamp it on every ordinary pick, which quietly promoted "the
// default for steps that stored nothing" into "force this provider onto every step" — and
// left it that way across reloads, with no UI to undo it. The key is now only ever REMOVED,
// idempotently, and never read back: a browser still carrying a `1` from before this change
// must not come back forced. It stays a named function so the cleanup and the regression that
// guards it name the same key.
function legacyPinStorageKey(projectId: string): string {
  return `flowgate.user.${currentUserId()}.ai-provider-pin.${projectId}`
}

function purgeLegacyPin(projectId: string | null | undefined): void {
  if (!projectId) return
  localStorage.removeItem(legacyPinStorageKey(projectId))
}

export const useAiProviderStore = defineStore('ai-provider', () => {
  const providers = ref<RuntimeAiProvider[]>([])
  const selectedProviderId = ref('')
  // 0448 T0005 §2-3: force-all is explicit RUN state, not a property of the current UI
  // selection. It lives in memory only — a refresh, a re-login or a project re-entry restores
  // the selected default and nothing else. Once a run has started, the provider it settled on
  // and its item_seq override map live on the server run record, which is what pause/resume
  // and the no-output retry replay (ai_invoke_service.start_run / _handoff_bundle).
  const pinned = ref(false)
  const loadedProjectId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let requestSerial = 0

  function clear() {
    requestSerial += 1
    purgeLegacyPin(loadedProjectId.value)
    providers.value = []
    selectedProviderId.value = ''
    pinned.value = false
    loadedProjectId.value = null
    loading.value = false
    error.value = null
  }

  /**
   * The ORDINARY selector contract — every one of the ten selection surfaces in T0005 §3
   * (header, continuous-work, invoke, decision modal, conversation, work-plan proposal,
   * git status / finalize, Q&A history) lands here.
   *
   * A pick here is the default for hops that stored no provider of their own. It writes the
   * selection key and NOTHING else: it must not set `pinned`, must not call the force API,
   * and must not leave the old implicit-pin key behind. A step with a stored provider keeps
   * running that stored provider (ai_invoke_service.start_run tier 3).
   */
  function selectProvider(providerId: string) {
    if (!loadedProjectId.value || !providers.value.some((provider) => provider.id === providerId)) return
    selectedProviderId.value = providerId
    localStorage.setItem(storageKey(loadedProjectId.value), providerId)
    purgeLegacyPin(loadedProjectId.value)
  }

  /**
   * The EXPLICIT force-all contract (T0005 §2-2). Only a caller that names this function can
   * make `pinned` true, and `clearPin()` takes it back off. It is deliberately not bound to
   * any selector's change/update event — no general UI surface may reach it. A per-item_seq
   * override still outranks it on the server (start_run tier 1).
   */
  function forceProviderForAllSteps(providerId: string) {
    if (!loadedProjectId.value || !providers.value.some((provider) => provider.id === providerId)) return
    selectProvider(providerId)
    pinned.value = true
  }

  function clearPin() {
    pinned.value = false
    purgeLegacyPin(loadedProjectId.value)
  }

  async function loadForProject(projectId: string, force = false): Promise<void> {
    if (!projectId) {
      clear()
      return
    }
    // §2-4: the one-way cleanup runs BEFORE the "already loaded" early return, so a repeat
    // load, a project switch and a failed load all leave the old key gone rather than only
    // the first successful load of a session. Switching projects also drops any force state:
    // it was scoped to the project it was turned on for.
    const previousProjectId = loadedProjectId.value
    if (previousProjectId && previousProjectId !== projectId) {
      purgeLegacyPin(previousProjectId)
      pinned.value = false
    }
    purgeLegacyPin(projectId)
    if (!force && loadedProjectId.value === projectId && providers.value.length > 0) return

    const serial = ++requestSerial
    loading.value = true
    error.value = null
    try {
      const response = await getRequest<RuntimeAiProvidersResponse>('/api/v1/ai-invoke/providers', { project: projectId })
      if (serial !== requestSerial) return
      const data = response.data
      const nextProviders = data.providers ?? []
      const saved = localStorage.getItem(storageKey(projectId)) || ''
      const fallback = data.default_provider_id || nextProviders[0]?.id || ''
      const savedIsAvailable = nextProviders.some((provider) => provider.id === saved)
      providers.value = nextProviders
      loadedProjectId.value = projectId
      // The selection key survives as long as it names a provider that still exists; an
      // unusable one falls back to the project default exactly as before. Only the force
      // state is refused a restore.
      selectedProviderId.value = savedIsAvailable ? saved : fallback
      purgeLegacyPin(projectId)
      if (selectedProviderId.value) {
        localStorage.setItem(storageKey(projectId), selectedProviderId.value)
      }
    } catch {
      if (serial !== requestSerial) return
      providers.value = []
      selectedProviderId.value = ''
      pinned.value = false
      loadedProjectId.value = projectId
      error.value = 'load_failed'
      purgeLegacyPin(projectId)
    } finally {
      if (serial === requestSerial) loading.value = false
    }
  }

  async function ensureLoaded(projectId: string): Promise<void> {
    await loadForProject(projectId, false)
  }

  return {
    providers,
    selectedProviderId,
    pinned,
    loadedProjectId,
    loading,
    error,
    clear,
    clearPin,
    selectProvider,
    forceProviderForAllSteps,
    loadForProject,
    ensureLoaded,
  }
})
