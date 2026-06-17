import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

interface DocTypeItem {
  id: number
  code: string
  label: string
  category: string
  color?: string
  is_active?: number
  sort_order?: number
}

export const useDocTypeStore = defineStore('docType', () => {
  const labelMap = ref<Record<string, string>>({})
  const loaded = ref(false)

  /**
   * Return the localized display name for typeCode.
   * Handles compound codes like 'N/NR', 'T/TR', 'TS/TSR' by splitting and joining.
   * Fallback: returns typeCode itself (PM decision).
   */
  function getLabel(typeCode: string): string {
    if (!typeCode) return typeCode
    if (typeCode.includes('/')) {
      return typeCode
        .split('/')
        .map((c) => labelMap.value[c] ?? c)
        .join('/')
    }
    return labelMap.value[typeCode] ?? typeCode
  }

  /**
   * Fetch doc-type labels from the server and cache them.
   * locale is forwarded as a query param for future server-side locale support.
   * Silent fail — components fall back to getLabel()'s typeCode passthrough.
   */
  async function loadLabels(locale?: string) {
    const projectStore = useProjectStore()
    const projectId = projectStore.currentProjectId
    if (!projectId) return

    try {
      const params: Record<string, unknown> = {}
      if (locale) params.locale = locale

      const res = await getRequest<{ data: DocTypeItem[] }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/document-types`,
        params,
      )
      const items: DocTypeItem[] = res.data?.data ?? []
      const map: Record<string, string> = {}
      for (const item of items) {
        if (item.code) map[item.code] = item.label
      }
      labelMap.value = map
      loaded.value = true
    } catch {
      // silent fail — components will display typeCode as fallback
    }
  }

  return { labelMap, loaded, getLabel, loadLabels }
})
