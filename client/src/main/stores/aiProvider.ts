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

function pinStorageKey(projectId: string): string {
  return `flowgate.user.${currentUserId()}.ai-provider-pin.${projectId}`
}

export const useAiProviderStore = defineStore('ai-provider', () => {
  const providers = ref<RuntimeAiProvider[]>([])
  const selectedProviderId = ref('')
  const pinned = ref(false)
  const loadedProjectId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let requestSerial = 0

  function clear() {
    requestSerial += 1
    if (loadedProjectId.value) localStorage.removeItem(pinStorageKey(loadedProjectId.value))
    providers.value = []
    selectedProviderId.value = ''
    pinned.value = false
    loadedProjectId.value = null
    loading.value = false
    error.value = null
  }

  function selectProvider(providerId: string) {
    if (!loadedProjectId.value || !providers.value.some((provider) => provider.id === providerId)) return
    selectedProviderId.value = providerId
    pinned.value = true
    localStorage.setItem(storageKey(loadedProjectId.value), providerId)
    localStorage.setItem(pinStorageKey(loadedProjectId.value), '1')
  }

  function clearPin() {
    pinned.value = false
    if (loadedProjectId.value) localStorage.removeItem(pinStorageKey(loadedProjectId.value))
  }

  async function loadForProject(projectId: string, force = false): Promise<void> {
    if (!projectId) {
      clear()
      return
    }
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
      selectedProviderId.value = savedIsAvailable ? saved : fallback
      pinned.value = savedIsAvailable && localStorage.getItem(pinStorageKey(projectId)) === '1'
      if (!pinned.value) localStorage.removeItem(pinStorageKey(projectId))
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
    loadForProject,
    ensureLoaded,
  }
})