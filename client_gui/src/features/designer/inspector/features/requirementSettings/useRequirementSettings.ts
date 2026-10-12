// flowgate.v02.0053 T#7 — a Graph requirement card's settings (T#2 card settings): read when the card is
// selected, every change sent at once as one value and its answer shown; the AI Team choices are the
// server's AI Teams. Turning AI 위임 on or off adds or drops the card's AI 위임 document line on the server,
// so the Canvas is read again after that change (the documents tab shows the line). A failed read is kept
// (its kind) with a read again of the card and the teams, so the same card recovers without a reconnect or a
// new selection.
import { computed, inject, ref, watch, type ComputedRef, type Ref } from 'vue'
import { settingsNodeErrorOf, settingsNodeRead, settingsNodeWrite, settingsTeamsRead, type SettingsNode, type SettingsTeam } from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { useInspectorFieldWrite, useInspectorResource, type InspectorEventMatch, type InspectorEventScope, type InspectorResourceStatus } from '../../useInspectorResource'
import type { RequirementSettingsFailureKind } from './requirementSettingsModel'

/** Server events after which the card's settings are read again (its run, hence its lock, moved). */
export const REQUIREMENT_SETTINGS_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed', 'ai_invoke_started', 'ai_invoke_finished'] as const

const flowMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

/** The field whose change adds / drops the card's document line. */
const LINE_FIELD = 'delegation'

function splitKey(key: string): [string, string] {
  const at = key.indexOf('\n')
  return [key.slice(0, at), key.slice(at + 1)]
}

export interface RequirementSettingsState {
  readonly node: ComputedRef<SettingsNode | null>
  readonly teams: ComputedRef<readonly SettingsTeam[]>
  readonly pending: Readonly<Ref<Readonly<Record<string, unknown>>>>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** Kind of the failed read of the card's settings, or null. */
  readonly errorKind: ComputedRef<RequirementSettingsFailureKind | null>
  readonly failure: ComputedRef<{ readonly field: string; readonly kind: RequirementSettingsFailureKind } | null>
  /** A refused change found the group disposed (until another card is selected). */
  readonly disposed: Readonly<Ref<boolean>>
  /** The target is a card just placed (`tmp_`). */
  readonly placing: ComputedRef<boolean>
  change(field: string, value: unknown): Promise<boolean>
  /** Reads the card's settings and the AI Teams again (the read failure's retry). */
  reload(): Promise<void>
}

export function useRequirementSettings(target: () => InspectorTarget | null): RequirementSettingsState {
  const host = inject(INSPECTOR_HOST, null)
  const scope = (): InspectorEventScope | null => {
    const current = target()
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? { projectId: current.projectId, flowId: current.flowId, docId: `${current.flowId}\n${nodeKey}` } : null
  }
  const resource = useInspectorResource<SettingsNode>({
    target: scope,
    read: (key) => settingsNodeRead(...splitKey(key)),
    events: REQUIREMENT_SETTINGS_EVENTS,
    matches: flowMatches,
  })
  const teams = useInspectorResource<SettingsTeam[]>({
    target: () => {
      const current = scope()
      return current ? { ...current, docId: current.projectId } : null
    },
    read: () => settingsTeamsRead(),
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
    teams: computed(() => (teams.data.value ?? []).filter((team) => team.enabled)),
    pending: writes.pending,
    status: resource.status,
    errorKind: computed(() => (resource.status.value === 'error' ? settingsNodeErrorOf(resource.error.value) : null)),
    failure,
    disposed,
    placing: computed(() => {
      const current = target()
      return current !== null && current.nodeType !== null && inspectorCardNodeKey(current) === null
    }),
    change: async (field, value) => {
      const ok = await writes.write(field, value)
      if (ok && field === LINE_FIELD) host?.request({ kind: 'refresh-flow' })
      return ok
    },
    reload: async () => {
      await Promise.all([resource.reload(), teams.reload()])
    },
  }
}
