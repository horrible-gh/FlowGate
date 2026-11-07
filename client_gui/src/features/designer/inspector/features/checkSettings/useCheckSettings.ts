import { computed } from 'vue'
import { settingsNodeErrorOf } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { checkControlFailure, type CheckSettingsFailureKind } from './checkSettingsModel'
import { useGateContext, GATE_RUN_EVENTS } from '../gateCard/useGateContext'
export const CHECK_SETTINGS_EVENTS = GATE_RUN_EVENTS

/** The same confirmed settings/run/gate context used by the document result box. */
export function useCheckSettings(target: () => InspectorTarget | null) {
  const context = useGateContext(target)
  const node = computed(() => {
    const c = context.value
    const saved = c?.settings.value
    if (!saved) return null
    const gate = c?.gate.value
    // flowgate.v02.0132 T#4: the lock keeps the server's reason (the gate's, else the settings read's) and the gate's Git state
    return { ...saved, locked: saved.locked || gate?.settings_locked === true || !!c?.busy.value || c?.hasRequest() === true,
      lockReason: gate?.settings_lock_reason ?? saved.lockReason ?? null, gitActive: gate?.git_active === true, parked: gate?.parked === true }
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
  // flowgate.v02.0148 T#2: each field keeps the refusal of its own last write (the single `failure` is the latest one).
  const fieldFailures = computed(() => {
    const out: Record<string, { kind: CheckSettingsFailureKind; key: string | null }> = {}
    for (const [field, error] of Object.entries(context.value?.failures.value ?? {})) {
      const kind = settingsNodeErrorOf(error)
      const specific = checkControlFailure(error)
      if (specific) out[specific.field] = { kind, key: specific.key }
      else out[field] = { kind, key: field === 'merge_target_branch' && kind === 'invalid' ? 'target.invalid' : null }
    }
    return out
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
    fieldFailures,
    /** Per field: `saving` / `saved` / `failed` (flowgate.v02.0148 T#2). */
    fieldStates: computed(() => context.value?.fieldStates.value ?? {}),
    disposed: computed(() => failure.value?.kind === 'disposed'),
    placing: computed(() => target()?.nodeId.startsWith('tmp_') === true),
    change: async (field: string, value: unknown) => await context.value?.change(field, value) ?? false,
    reload: async () => { await context.value?.reload(true) },
    retry: async (field: string) => await context.value?.retry(field) ?? false,
  }
}
