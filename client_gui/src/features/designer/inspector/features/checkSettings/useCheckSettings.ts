// flowgate.v02.0053 T#7 — a Graph gate (확인) card's settings (T#2 card settings): read when the card is
// selected, every change sent at once as one value and its answer shown. The merge target choices are the
// group's Git work base as the final approval reads it (a group without Git work has none: the target stays
// "—", asked at the final approval as today). A failed read is kept (its kind) with a read again of the
// card and its Git work, so the same card recovers without a reconnect or a new selection.
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { checkGitRead, settingsNodeErrorOf, settingsNodeRead, settingsNodeWrite, type CheckGitState, type SettingsNode } from '@/features/document'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { useInspectorFieldWrite, useInspectorResource, type InspectorEventMatch, type InspectorEventScope, type InspectorResourceStatus } from '../../useInspectorResource'
import type { CheckSettingsFailureKind } from './checkSettingsModel'

/** Server events after which the card's settings are read again (its run, hence its lock, moved). */
export const CHECK_SETTINGS_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed', 'ai_invoke_started', 'ai_invoke_finished'] as const

const flowMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

function splitKey(key: string): [string, string] {
  const at = key.indexOf('\n')
  return [key.slice(0, at), key.slice(at + 1)]
}

export interface CheckSettingsState {
  readonly node: ComputedRef<SettingsNode | null>
  /** The branches the group's Git work names (work base, base branch). */
  readonly branches: ComputedRef<readonly string[]>
  readonly pending: Readonly<Ref<Readonly<Record<string, unknown>>>>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** Kind of the failed read of the card's settings, or null. */
  readonly errorKind: ComputedRef<CheckSettingsFailureKind | null>
  readonly failure: ComputedRef<{ readonly field: string; readonly kind: CheckSettingsFailureKind } | null>
  readonly disposed: Readonly<Ref<boolean>>
  readonly placing: ComputedRef<boolean>
  change(field: string, value: unknown): Promise<boolean>
  /** Reads the card's settings and its Git work again (the read failure's retry). */
  reload(): Promise<void>
}

export function useCheckSettings(target: () => InspectorTarget | null): CheckSettingsState {
  const scope = (): InspectorEventScope | null => {
    const current = target()
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? { projectId: current.projectId, flowId: current.flowId, docId: `${current.flowId}\n${nodeKey}` } : null
  }
  const resource = useInspectorResource<SettingsNode>({
    target: scope,
    read: (key) => settingsNodeRead(...splitKey(key)),
    events: CHECK_SETTINGS_EVENTS,
    matches: flowMatches,
  })
  const git = useInspectorResource<CheckGitState>({
    target: () => {
      const current = scope()
      return current ? { ...current, docId: current.flowId } : null
    },
    read: checkGitRead,
    events: [],
  })
  const writes = useInspectorFieldWrite<SettingsNode>({
    target: scope,
    resource,
    write: (key, field, value) => settingsNodeWrite(...splitKey(key), field, value),
  })

  const disposed = ref(false)
  watch(
    () => scope()?.docId ?? null,
    () => {
      disposed.value = false
    },
  )
  const failure = computed(() => {
    const last = writes.failure.value
    return last ? { field: last.field, kind: settingsNodeErrorOf(last.error) } : null
  })
  watch(failure, (now) => {
    if (now?.kind === 'disposed') disposed.value = true
  })

  return {
    node: computed(() => resource.data.value),
    branches: computed(() => {
      const state = git.data.value
      return state ? [state.workBaseRef, state.baseBranch].filter((branch): branch is string => !!branch) : []
    }),
    pending: writes.pending,
    status: resource.status,
    errorKind: computed(() => (resource.status.value === 'error' ? settingsNodeErrorOf(resource.error.value) : null)),
    failure,
    disposed,
    placing: computed(() => {
      const current = target()
      return current !== null && current.nodeType !== null && inspectorCardNodeKey(current) === null
    }),
    change: (field, value) => writes.write(field, value),
    reload: async () => {
      await Promise.all([resource.reload(), git.reload()])
    },
  }
}
