import { computed } from 'vue'
import { settingsNodeErrorOf } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateContext, GATE_RUN_EVENTS } from '../gateCard/useGateContext'
export const CHECK_SETTINGS_EVENTS = GATE_RUN_EVENTS

/** The same confirmed settings/run/gate context used by the document result box. */
export function useCheckSettings(target: () => InspectorTarget | null) {
  const context = useGateContext(target)
  const node = computed(() => {
    const c = context.value
    const saved = c?.settings.value
    return saved ? { ...saved, locked: saved.locked || c?.gate.value?.settings_locked === true } : null
  })
  const failure = computed(() => {
    const last = context.value?.failure.value
    return last ? { field: last.field, kind: settingsNodeErrorOf(last.error) } : null
  })
  return {
    node,
    branches: computed(() => ['origin']),
    pending: computed(() => context.value?.pending.value ?? {}),
    status: computed(() => context.value?.status.value ?? 'idle'),
    errorKind: computed(() => node.value === null && context.value?.error.value ? settingsNodeErrorOf(context.value.error.value) : null),
    failure,
    disposed: computed(() => failure.value?.kind === 'disposed'),
    placing: computed(() => target()?.nodeId.startsWith('tmp_') === true),
    change: async (field: string, value: unknown) => await context.value?.change(field, value) ?? false,
    reload: async () => { await context.value?.reload() },
  }
}
