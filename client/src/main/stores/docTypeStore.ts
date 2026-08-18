import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

export interface DocTypeItem {
  id: number
  code: string
  label: string
  category: string
  color?: string
  is_active?: number
  sort_order?: number
  // flowgate.default.0395 P0009 §4.1: additive fields the work-plan create dialog
  // and editor need — whether this type can be counted, its unit, and its report pair.
  countable?: boolean
  unit?: 'sheet' | 'set' | null
  pair_code?: string
}

export const useDocTypeStore = defineStore('docType', () => {
  const labelMap = ref<Record<string, string>>({})
  const items = ref<DocTypeItem[]>([])
  const loaded = ref(false)

  /** Countable types in server-registry order — the work-plan quantity list (P0009 §4.1). */
  const countableTypes = computed(() => items.value.filter((item) => item.countable))

  /**
   * flowgate.default.0395 mockup xc32frrg: a work row is named after the pair, not after
   * its instruction document — 조사 / 작업 / 테스트 (investigation / work / test), not 조사지시 / 작업지시 / 테스트지시 (their instruction-doc counterparts).
   *
   * The registry label is server-side and NOT localized, so the UI locale says nothing
   * about which suffix it carries; strip whichever of the known suffixes it ends with.
   */
  const INSTRUCTION_SUFFIXES = ['지시', '指示', ' Instruction']

  function getSetName(typeCode: string): string {
    const label = getLabel(typeCode)
    for (const suffix of INSTRUCTION_SUFFIXES) {
      if (label.length > suffix.length && label.endsWith(suffix)) {
        return label.slice(0, label.length - suffix.length).trim()
      }
    }
    return label
  }

  /** The label of a type's report-pair, if it has one and that pair is itself registered. */
  function getPairLabel(typeCode: string): string | undefined {
    const item = items.value.find((entry) => entry.code === typeCode)
    if (!item?.pair_code) return undefined
    return items.value.find((entry) => entry.code === item.pair_code)?.label
  }

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
      const rows: DocTypeItem[] = res.data?.data ?? []
      const map: Record<string, string> = {}
      for (const item of rows) {
        if (item.code) map[item.code] = item.label
      }
      labelMap.value = map
      items.value = rows
      loaded.value = true
    } catch {
      // silent fail — components will display typeCode as fallback
    }
  }

  return { labelMap, items, countableTypes, loaded, getLabel, getSetName, getPairLabel, loadLabels }
})
