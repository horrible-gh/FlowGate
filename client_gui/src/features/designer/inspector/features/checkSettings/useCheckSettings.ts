import { computed } from 'vue'
import { settingsNodeErrorOf } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { checkControlFailure } from './checkSettingsModel'
import { useGateContext, GATE_RUN_EVENTS } from '../gateCard/useGateContext'
export const CHECK_SETTINGS_EVENTS = GATE_RUN_EVENTS

/** The same confirmed settings/run/gate context used by the document result box. */
export function useCheckSettings(target: () => InspectorTarget | null) {
  const context = useGateContext(target)
  const node = computed(() => {
    const c = context.value
    const saved = c?.settings.value
    return saved ? { ...saved, locked: saved.locked || c?.gate.value?.settings_locked === true || !!c?.busy.value || c?.hasRequest() === true } : null
  })
  const failure = computed(() => {
    const last = context.value?.failure.value
    const decisionError = context.value?.decisionError.value
    const specific = checkControlFailure(decisionError ?? last?.error)
    if (specific) return { ...specific, kind: settingsNodeErrorOf(decisionError ?? last?.error) }
    if (!last) return null
    const kind = settingsNodeErrorOf(last.error)
    return { field: last.field, kind, key: last.field === 'merge_target_branch' && kind === 'invalid' ? 'target.invalid' : null }
  })
  return {
    node,
    branches: computed(() => node.value?.mergeTargetOptions ?? []),
    pending: computed(() => context.value?.pending.value ?? {}),
    status: computed(() => context.value?.status.value ?? 'idle'),
    /** A candidate re-read (refresh_merge_targets) is asked for or in flight: blocks a second one, never a lock. */
    refreshing: computed(() => context.value?.refreshing.value === true),
    /** 0006-T §2: the kept confirmed candidates valid for this target now (project, flow, card, work base, 5 min), or null. */
    cachedTargets: computed(() => {
      const c = context.value
      // Reactive on the kept entry and the answer it is checked against; expiry drops the entry by its own timer.
      void c?.targetCache.value
      return c ? c.cachedTargets(target()?.projectId ?? null) : null
    }),
    errorKind: computed(() => node.value === null && context.value?.error.value ? settingsNodeErrorOf(context.value.error.value) : null),
    failure,
    disposed: computed(() => failure.value?.kind === 'disposed'),
    placing: computed(() => target()?.nodeId.startsWith('tmp_') === true),
    change: async (field: string, value: unknown) => await context.value?.change(field, value) ?? false,
    reload: async () => { await context.value?.reload(true) },
  }
}
