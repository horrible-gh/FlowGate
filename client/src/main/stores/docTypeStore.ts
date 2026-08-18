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

/**
 * flowgate.default.0429 T0004: the work-plan-only sort/dedup registry — server's single
 * ordering contract (work_plan_service.list_countable_types), additive to the `data`
 * array above. Field names mirror DocTypeItem so both shapes read the same way.
 */
export interface WorkPlanCountableType {
  code: string
  label: string
  category?: string
  unit: 'sheet' | 'set'
  pair_code?: string
  pair_name?: string
}

export const useDocTypeStore = defineStore('docType', () => {
  const labelMap = ref<Record<string, string>>({})
  const items = ref<DocTypeItem[]>([])
  // null = the server response carried no work_plan_countable_types field (old/mocked
  // response) — countableTypes then falls back to the legacy data.filter(countable) shape.
  const workPlanCountableTypes = ref<WorkPlanCountableType[] | null>(null)
  const loaded = ref(false)

  /**
   * Countable types in server-registry order — the work-plan quantity list (P0009 §4.1).
   *
   * 0429 T0004: this is the server's dedicated work-plan registry
   * (work_plan_countable_types), not the raw document-types order — the two legitimately
   * differ (DS sorts after D/P/L/DB in `data`'s series/sort_order/type_code order, but
   * leads the work-plan list). Older/mocked responses that omit the additive field fall
   * back to the previous data.filter(countable) shape so existing tests/servers keep working.
   */
  const countableTypes = computed<WorkPlanCountableType[]>(() => {
    if (workPlanCountableTypes.value) return workPlanCountableTypes.value
    return items.value
      .filter((item) => item.countable)
      .map((item) => ({
        code: item.code,
        label: item.label,
        category: item.category,
        unit: item.unit ?? 'sheet',
        pair_code: item.pair_code,
      }))
  })

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

      const res = await getRequest<{
        data: DocTypeItem[]
        work_plan_countable_types?: WorkPlanCountableType[]
      }>(`/api/v1/projects/${encodeURIComponent(projectId)}/document-types`, params)
      const rows: DocTypeItem[] = res.data?.data ?? []
      const map: Record<string, string> = {}
      for (const item of rows) {
        if (item.code) map[item.code] = item.label
      }
      // 0429 T0004: both registries come from the same response and are replaced
      // together, so a project/locale reload never mixes one project's types with
      // another's stale work-plan order.
      labelMap.value = map
      items.value = rows
      workPlanCountableTypes.value = Array.isArray(res.data?.work_plan_countable_types)
        ? res.data.work_plan_countable_types
        : null
      loaded.value = true
    } catch {
      // silent fail — components will display typeCode as fallback
    }
  }

  return {
    labelMap,
    items,
    // Exposed mainly for tests that seed store state directly instead of mocking the
    // document-types HTTP response (0429 T0004) — production code should read
    // `countableTypes`, which already prefers this over the `items` fallback.
    workPlanCountableTypes,
    countableTypes,
    loaded,
    getLabel,
    getSetName,
    getPairLabel,
    loadLabels,
  }
})
