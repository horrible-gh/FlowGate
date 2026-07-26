// Per-document-type AI provider assignment for the continuous chain (0317 D0004 §6).
//
// Loads and saves a project's "문서 종류 -> 프로바이더" 배정 규칙 the backend hop provider
// decider reads at each step boundary. The continuous dialog reads `assignments` to pre-fill
// its mapping table and calls `save` before starting the run so the persisted map is in place
// when the first hop resolves its provider.
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getRequest, putRequest } from '@shared/api'

export interface DoctypeAssignment {
  doc_type: string
  provider_id: string
}

interface DoctypeProvidersResponse {
  ok: boolean
  project: string
  assignments: DoctypeAssignment[]
  providers: { id: string; name: string; exec_type: string; kind: string }[]
  default_provider_id: string | null
}

export const useAiDoctypeProvidersStore = defineStore('ai-doctype-providers', () => {
  // Current assignments as a plain map (doc_type -> provider_id) for easy binding.
  const byDocType = ref<Record<string, string>>({})
  const loadedProjectId = ref<string | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  let requestSerial = 0

  function clear() {
    requestSerial += 1
    byDocType.value = {}
    loadedProjectId.value = null
    loading.value = false
    error.value = null
  }

  async function load(projectId: string, force = false): Promise<void> {
    if (!projectId) {
      clear()
      return
    }
    if (!force && loadedProjectId.value === projectId) return
    const serial = ++requestSerial
    loading.value = true
    error.value = null
    try {
      const res = await getRequest<DoctypeProvidersResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/ai-doctype-providers`,
      )
      if (serial !== requestSerial) return
      const map: Record<string, string> = {}
      for (const a of res.data.assignments ?? []) map[a.doc_type] = a.provider_id
      byDocType.value = map
      loadedProjectId.value = projectId
    } catch {
      if (serial !== requestSerial) return
      byDocType.value = {}
      loadedProjectId.value = projectId
      error.value = 'load_failed'
    } finally {
      if (serial === requestSerial) loading.value = false
    }
  }

  // Persist the given map, dropping blank selections (a blank = "use the default provider").
  async function save(projectId: string, map: Record<string, string>): Promise<boolean> {
    if (!projectId) return false
    saving.value = true
    error.value = null
    try {
      const assignments: DoctypeAssignment[] = Object.entries(map)
        .filter(([docType, providerId]) => docType && providerId)
        .map(([doc_type, provider_id]) => ({ doc_type, provider_id }))
      await putRequest(
        `/api/v1/projects/${encodeURIComponent(projectId)}/ai-doctype-providers`,
        { assignments },
      )
      byDocType.value = { ...map }
      loadedProjectId.value = projectId
      return true
    } catch {
      error.value = 'save_failed'
      return false
    } finally {
      saving.value = false
    }
  }

  return { byDocType, loadedProjectId, loading, saving, error, clear, load, save }
})
