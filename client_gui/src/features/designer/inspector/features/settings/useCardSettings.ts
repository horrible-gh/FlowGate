// flowgate.v02.0053 T#7 — a Graph card's run / test settings (T#2 card settings): read when the card is
// selected, every change sent at once as one value (no save button) and its answer shown; the AI choices are
// the project's. A card just placed (`tmp_` key) has nothing stored yet: its form is drawn, off, until the
// draft save answers its key. No server event announces a settings change; a run starting or ending a node
// (the card's 🔒) is announced, and the card is read again then and when its Canvas run state moves.
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  settingsChoicesRead,
  settingsNodeErrorMessage,
  settingsNodeErrorOf,
  settingsNodeErrorReason,
  settingsNodeRead,
  settingsNodeWrite,
  type SettingsChoices,
  type SettingsNode,
} from '@/features/document'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { useInspectorFieldWrite, useInspectorResource, type InspectorEventMatch, type InspectorEventScope, type InspectorResourceStatus } from '../../useInspectorResource'
import { cardSettingsBlockedBy, type CardSettingsBlocked, type CardSettingsFailureKind, type CardSettingsField } from './cardSettingsModel'

/** Server events after which the card's settings are read again (its run, hence its lock, moved). */
export const CARD_SETTINGS_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed', 'ai_invoke_started', 'ai_invoke_finished'] as const

/** An event of the card's Flow. */
export const cardSettingsEventMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

/** The resource key of a card: Flow and node_key. */
const cardKeyOf = (flowId: string, nodeKey: string) => `${flowId}\n${nodeKey}`

function splitKey(key: string): [string, string] {
  const at = key.indexOf('\n')
  return [key.slice(0, at), key.slice(at + 1)]
}

export interface CardSettingsState {
  /** The card's settings as last read or answered, or null. */
  readonly node: ComputedRef<SettingsNode | null>
  readonly choices: ComputedRef<SettingsChoices | null>
  /** Values sent and not answered yet, by field. */
  readonly pending: Readonly<Ref<Readonly<Record<string, unknown>>>>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** Kind of the failed read, or null. */
  readonly errorKind: ComputedRef<CardSettingsFailureKind | null>
  /**
   * The last refused change: its field, why, (a refused value) the server's reason, and the server's own message
   * as it sent it (x-locale; flowgate.v02.0056 F2-1), or null.
   */
  readonly failure: ComputedRef<{
    readonly field: string
    readonly kind: CardSettingsFailureKind
    readonly reason: string | null
    readonly message: string | null
  } | null>
  readonly blocked: Readonly<Ref<CardSettingsBlocked>>
  /** The target is a card just placed (`tmp_`): nothing to read yet. */
  readonly placing: ComputedRef<boolean>
  change(field: CardSettingsField, value: unknown): Promise<boolean>
  reload(): Promise<void>
}

export function useCardSettings(target: () => InspectorTarget | null): CardSettingsState {
  const scope = (): InspectorEventScope | null => {
    const current = target()
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? { projectId: current.projectId, flowId: current.flowId, docId: cardKeyOf(current.flowId, nodeKey) } : null
  }
  const resource = useInspectorResource<SettingsNode>({
    target: scope,
    read: (key) => settingsNodeRead(...splitKey(key)),
    events: CARD_SETTINGS_EVENTS,
    matches: cardSettingsEventMatches,
  })
  const choices = useInspectorResource<SettingsChoices>({
    target: () => {
      const current = target()
      return current && current.nodeType !== null ? { projectId: current.projectId, flowId: current.flowId, docId: current.projectId } : null
    },
    read: settingsChoicesRead,
    events: [],
  })
  const writes = useInspectorFieldWrite<SettingsNode>({
    target: scope,
    resource,
    write: (key, field, value) => settingsNodeWrite(...splitKey(key), field, value),
  })

  const blocked = ref<CardSettingsBlocked>(null)
  watch(
    () => scope()?.docId ?? null,
    () => {
      blocked.value = null
    },
  )
  // The Canvas shows the card's run moving (its 🔒): read the settings again.
  watch(
    () => target()?.nodeRun?.status ?? null,
    (now, before) => {
      if (now !== before && scope() !== null) void resource.reload()
    },
  )

  const failure = computed(() => {
    const last = writes.failure.value
    return last
      ? { field: last.field, kind: settingsNodeErrorOf(last.error), reason: settingsNodeErrorReason(last.error), message: settingsNodeErrorMessage(last.error) }
      : null
  })
  watch(failure, (now) => {
    if (now) blocked.value = cardSettingsBlockedBy(now.kind) ?? blocked.value
  })

  return {
    node: computed(() => resource.data.value),
    choices: computed(() => choices.data.value),
    pending: writes.pending,
    status: resource.status,
    errorKind: computed(() => (resource.status.value === 'error' ? settingsNodeErrorOf(resource.error.value) : null)),
    failure,
    blocked,
    placing: computed(() => {
      const current = target()
      return current !== null && current.nodeType !== null && inspectorCardNodeKey(current) === null
    }),
    change: (field, value) => writes.write(field, value),
    reload: async () => {
      blocked.value = null
      await Promise.all([resource.reload(), choices.reload()])
    },
  }
}
