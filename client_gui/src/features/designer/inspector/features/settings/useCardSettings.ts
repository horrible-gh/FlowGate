// a Graph card's run / test settings (card settings): read when the card is
// selected, every change sent at once as one value (no save button) and its answer shown; the AI choices are
// the project's. A card just placed (`tmp_` key) has nothing stored yet: its form is drawn, off, until the
// draft save answers its key. No server event announces a settings change; a run starting or ending a node
// (the card's 🔒) is announced, and the card is read again then and when its Canvas run state moves.
// A taken change asks the Host to read the written card's Flow again (`refresh-flow`): the instruction use / omit
// moves the card's document buttons and the AI / role values its stage assignments. The Canvas event the write
// causes names this tab (`x-canvas-client-id`) and is dropped by this tab, so this request is the only read.
import { useAiProvidersChanged } from '@shared/aiProvidersChanged'
import { inject, provide, computed, ref, shallowRef, watch, effectScope, getCurrentInstance, onScopeDispose, type ComputedRef, type Ref } from 'vue'
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
import { aiRolePayload, type AiRoleValue } from '@/features/document'
import { INSPECTOR_HOST, type InspectorHost } from '../../inspectorHost'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { captureInspectorResourceContext, useInspectorFieldWrite, useInspectorResource, type InspectorEventMatch, type InspectorEventScope, type InspectorResourceStatus } from '../../useInspectorResource'
import { cardSettingsBlockedBy, type CardSettingsBlocked, type CardSettingsFailureKind, type CardSettingsField } from './cardSettingsModel'

/** Server events after which the card's settings are read again (its run, hence its lock, moved). */
export const CARD_SETTINGS_EVENTS = ['doc_review_status_changed', 'graph_canvas_changed', 'graph_run_status_changed', 'graph_node_state_changed', 'ai_invoke_started', 'ai_invoke_finished'] as const

/** An event of the card's Flow. */
export const cardSettingsEventMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

/** The resource key of a card: Flow and node_key. */
const cardKeyOf = (flowId: string, nodeKey: string) => `${flowId}\n${nodeKey}`

/** The card of a settings key (Flow, node_key and the settings context): the key without its context. */
const cardOfSettingsKey = (key: string) => key.slice(0, key.lastIndexOf('\n'))

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
   * as it sent it (x-locale; the earlier change F2-1), or null.
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

/** The Host a card's settings ask to read the Canvas again (`refresh-flow`). */
export type CardSettingsHost = InspectorHost

/**
 * The Host injected in the calling component's setup, or null (called outside setup, or no Frame above). A session that is
 * made later outside setup (a watch callback) takes the Host from its consumer's setup through this and passes it on as `host`.
 */
export function injectCardSettingsHost(): CardSettingsHost | null {
  return getCurrentInstance() ? inject(INSPECTOR_HOST, null) : null
}

/**
 * `host`: the Host a taken change asks to read the Canvas again. Omitted = the one injected here (component setup);
 * a session created outside setup (useSharedCardSettings) passes the one its consumers injected.
 */
export function useCardSettings(target: () => InspectorTarget | null, host?: () => InspectorHost | null): CardSettingsState {
  const injectedHost = host ? null : injectCardSettingsHost()
  const hostOf = host ?? (() => injectedHost)
  const scope = (): InspectorEventScope | null => {
    const current = target()
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? { projectId: current.projectId, flowId: current.flowId, docId: cardKeyOf(current.flowId, nodeKey) + '\n' + JSON.stringify(current.settingsContext ?? null) } : null
  }
  // the card alone, without its context — writes belong to the card, so a context moved by
  // another tab (a card placed, the draft version up) neither drops a value sent nor refuses the next one.
  const cardScope = (): InspectorEventScope | null => {
    const current = scope()
    return current ? { ...current, docId: cardOfSettingsKey(current.docId!) } : null
  }
  // The latest read; a write waits for it so it sends the context and version that read answers.
  let reading: Promise<unknown> = Promise.resolve()
  const resource = useInspectorResource<SettingsNode>({
    target: scope,
    read: (key) => {
      const [flowId, tail] = splitKey(key)
      const [nodeKey, context] = tail.split('\n')
      const answer = settingsNodeRead(flowId, nodeKey, false, JSON.parse(context) ?? undefined)
      reading = answer.catch(() => undefined)
      return answer
    },
    events: CARD_SETTINGS_EVENTS,
    matches: cardSettingsEventMatches,
    // the same card under a moved context is read again with its AI rows kept (a card placed
    // in another tab moved the draft version; dropping the answer emptied the AI tab until the read came back).
    identity: cardOfSettingsKey,
  })
  const choices = useInspectorResource<SettingsChoices>({
    target: () => {
      const current = target()
      return current && current.nodeType !== null ? { projectId: current.projectId, flowId: current.flowId, docId: current.projectId } : null
    },
    read: settingsChoicesRead,
    events: [],
  })
  useAiProvidersChanged(() => { void choices.reload() })
  const writes = useInspectorFieldWrite<SettingsNode>({
    target: cardScope,
    resource,
    write: async (card, field, value) => {
      // A read running now (the context moved, or an event) answers the context and version to send back.
      for (let last = reading; ; last = reading) {
        await last
        if (last === reading) break
      }
      const key = scope()?.docId ?? null
      if (key === null || cardOfSettingsKey(key) !== card) throw new Error('gate_context_changed')
      const [flowId, tail] = splitKey(key)
      const [nodeKey, context] = tail.split('\n')
      // the context sent back is the one the server answered for this card (its own run / revision /
      // draft choice), never the one the screen built from its view — those differ and 0126 refused every write then.
      const answered = resource.data.value?.context
      const answer = await settingsNodeWrite(flowId, nodeKey, field, field.endsWith('_role') ? aiRolePayload(value as AiRoleValue) : value,
        resource.data.value?.settingsVersion, answered ?? JSON.parse(context) ?? undefined)
      // Taken: the Canvas of the written card's Flow is read again (its own Canvas event is dropped by this tab), even
      // when another card was selected meanwhile — the server already holds the value.
      hostOf()?.request({ kind: 'refresh-flow', flowId })
      return answer
    },
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
    change: (field, value) => {
      const current = target()
      const reason = current?.readOnly === 'conflict' ? 'conflict' : current?.settingsReadOnly === undefined ? current?.readOnly : current.settingsReadOnly
      // 0130: once the server answered, its lock decides (an unconfirmed Canvas run status no longer holds a read card).
      if (!current || !resource.data.value || reason || current.nodeRun?.nodeRunning || resource.data.value?.locked || resource.data.value?.settingsEditable === false) return Promise.resolve(false)
      return writes.write(field, value)
    },
    reload: async () => {
      blocked.value = null
      await Promise.all([resource.reload(), choices.reload()])
    },
  }
}

const SHARED_CARD_SETTINGS = Symbol('inspector-card-settings')
export function provideCardSettings(target: () => InspectorTarget | null) {
  const state = useCardSettings(target)
  provide(SHARED_CARD_SETTINGS, state)
}
interface CardSettingsSession {
  target: ReturnType<typeof shallowRef<InspectorTarget | null>>
  state: CardSettingsState
  users: number
  /** The Host the latest consumer injected (the session outlives a remounted Frame). */
  host: InspectorHost | null
}
const cardSettingsApps = new WeakMap<object, Map<string, CardSettingsSession>>()
const fallbackCardSettingsApp = {}

/** 0113: settings and AI panels share the app/card session, without a feature import in the Frame.
 * Like testCardSession (0071), resources become inactive when the last consumer leaves. */
export function useSharedCardSettings(target: () => InspectorTarget | null): CardSettingsState {
  const provided = inject<CardSettingsState | null>(SHARED_CARD_SETTINGS, null)
  if (provided) return provided
  const app = getCurrentInstance()?.appContext ?? fallbackCardSettingsApp
  const inResourceContext = captureInspectorResourceContext()
  const host = injectCardSettingsHost()
  let sessions = cardSettingsApps.get(app)
  if (!sessions) { sessions = new Map(); cardSettingsApps.set(app, sessions) }
  const current = shallowRef<CardSettingsSession | null>(null)
  const stop = watch(() => {
    const value = target()
    return value ? [value.projectId, value.flowId, value.nodeId].join('\n') : null
  }, (key, _before, cleanup) => {
    if (!key) { current.value = null; return }
    let entry = sessions!.get(key)
    if (!entry) {
      const selected = shallowRef(target())
      const scope = effectScope(true)
      const created: CardSettingsSession = { target: selected, state: null as unknown as CardSettingsState, users: 0, host }
      created.state = inResourceContext(() => scope.run(() => useCardSettings(() => selected.value, () => created.host)))!
      entry = created; sessions!.set(key, entry)
    }
    entry.users++; if (host) entry.host = host; entry.target.value = target(); current.value = entry
    cleanup(() => { if (--entry!.users === 0) entry!.target.value = null })
  }, { immediate: true, flush: 'sync' })
  const stopTarget = watch(target, value => { if (value && current.value) current.value.target.value = value }, { flush: 'sync' })
  onScopeDispose(() => { stopTarget(); stop() })
  return {
    node: computed(() => current.value?.state.node.value ?? null),
    choices: computed(() => current.value?.state.choices.value ?? null),
    pending: computed(() => current.value?.state.pending.value ?? {}),
    status: computed(() => current.value?.state.status.value ?? 'idle'),
    errorKind: computed(() => current.value?.state.errorKind.value ?? null),
    failure: computed(() => current.value?.state.failure.value ?? null),
    blocked: computed(() => current.value?.state.blocked.value ?? null),
    placing: computed(() => current.value?.state.placing.value ?? false),
    change: (field, value) => current.value?.state.change(field, value) ?? Promise.resolve(false),
    reload: () => current.value?.state.reload() ?? Promise.resolve(),
  }
}

