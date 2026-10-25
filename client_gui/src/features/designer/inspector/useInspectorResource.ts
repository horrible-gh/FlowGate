// flowgate.v02.0048 T#3 — the Inspector's common read (D#1 §7.2). A feature composable passes the
// target, a read function from `@/features/document` and the server events that change what it
// reads; it gets back the data, a state (idle / loading / ready / error), the error, whether the
// shown data is stale and a manual read again. There is no central event table (D#1 §7.3): each
// caller names its own events.
// - Events come from the project event hub through the project store — this is the one Inspector
//   file that knows the store. One subscription per project; a frame counts when it is about the
//   target (default: the frame's doc_id is the target document, or a frame without doc_id is
//   about the target Flow). Events are signals only: the resource is read again from the server.
// - Matching events within INSPECTOR_REFRESH_COALESCE_MS of the first are one read again. A
//   reconnect (events may be missing) reads again as well.
// - A new target document drops the previous one's data, and a read answered after the target
//   changed (or after a newer read started) is thrown away: results are applied by request order.
// - Leaving (component unmount / scope dispose) unsubscribes and drops a pending read again.
// flowgate.v02.0053 T#7 — a resource whose server answers a change with the whole resource (the card
// settings, one value per request, no save button) takes that answer as its data (`replace`), and
// useInspectorFieldWrite sends the changes: one at a time, the value sent shown until it is answered.
import { getCurrentInstance, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { useProjectStore, type ProjectEventFrame, type ProjectEventSubscription } from '@/features/project'
import type { InspectorTarget } from './inspectorTarget'

/** Window after a first matching event within which further events join the same read again. */
export const INSPECTOR_REFRESH_COALESCE_MS = 200

/** What events are matched against: the target's project, Flow and document. */
export type InspectorEventScope = Pick<InspectorTarget, 'projectId' | 'flowId' | 'docId'>

export type InspectorEventMatch = (frame: ProjectEventFrame, scope: InspectorEventScope) => boolean

/** Default match: the target document's frame, or a Flow-level frame (no doc_id) of the target Flow. */
export function inspectorEventMatches(frame: ProjectEventFrame, scope: InspectorEventScope): boolean {
  if (scope.docId !== null && frame.docId === scope.docId) return true
  return frame.docId === null && frame.groupId === scope.flowId
}

type SubscribeProjectEvents = (projectId: string, subscription: ProjectEventSubscription) => () => void

// The store lives in the app's Pinia. A part mounted without one (a Frame unit test with no app
// plugins) has no project stream to hear and subscribes to nothing.
let inheritedSubscriber: SubscribeProjectEvents | null | undefined

/** Capture the owning app stream for retained card scopes resumed outside component setup. */
export function captureInspectorResourceContext() {
  const subscriber = projectEventSubscriber()
  return <T>(create: () => T): T => {
    const previous = inheritedSubscriber
    inheritedSubscriber = subscriber
    try { return create() } finally { inheritedSubscriber = previous }
  }
}

function projectEventSubscriber(): SubscribeProjectEvents | null {
  if (inheritedSubscriber !== undefined) return inheritedSubscriber
  const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
  if (!pinia) return null
  const store = useProjectStore(pinia)
  return (projectId, subscription) => store.subscribeProjectEvents(projectId, subscription)
}

export interface InspectorEventsOptions {
  /** Server event names (event_type) that mean "what you show may have changed". */
  events: readonly string[]
  /** Which frames are about the target; defaults to inspectorEventMatches. */
  matches?: InspectorEventMatch
  /** Treat a reconnect as a change (default true). */
  resync?: boolean
  /** A matching event (coalesced) or a reconnect arrived. */
  changed(): void
}

const scopeKey = (scope: InspectorEventScope | null) => (scope ? `${scope.projectId}\n${scope.flowId}\n${scope.docId ?? ''}` : '')

/**
 * Calls `changed` when the scope's data may have changed on the server. Must run in a component
 * setup or an effect scope; it stops with it.
 */
export function useInspectorEvents(scope: () => InspectorEventScope | null, options: InspectorEventsOptions): void {
  const subscribe = projectEventSubscriber()
  const matches = options.matches ?? inspectorEventMatches
  let pending: ReturnType<typeof setTimeout> | null = null

  const cancel = () => {
    if (pending !== null) clearTimeout(pending)
    pending = null
  }
  const signal = () => {
    if (pending !== null) return
    pending = setTimeout(() => {
      pending = null
      options.changed()
    }, INSPECTOR_REFRESH_COALESCE_MS)
  }

  // The scope is read when a frame arrives, so a Node change inside the project keeps the subscription.
  watch(
    () => scope()?.projectId ?? null,
    (projectId, _previous, onCleanup) => {
      if (subscribe === null || projectId === null) return
      onCleanup(
        subscribe(projectId, {
          events: options.events,
          event: (frame) => {
            const current = scope()
            if (current !== null && matches(frame, current)) signal()
          },
          resync: () => {
            if (options.resync !== false && scope() !== null) signal()
          },
        }),
      )
    },
    { immediate: true },
  )
  // A pending signal belongs to the target it was raised for.
  watch(() => scopeKey(scope()), cancel)
  onScopeDispose(cancel)
}

export type InspectorResourceStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface InspectorResourceOptions<T> {
  /** The target (null: nothing to read). Its docId is the document read. */
  target: () => InspectorEventScope | null
  /** Reads the resource of a document — a `@/features/document` call. */
  read: (docId: string) => Promise<T>
  /** Server events after which the resource is read again. */
  events: readonly string[]
  matches?: InspectorEventMatch
}

export interface InspectorResource<T> {
  /** The last read of the current target document; null before the first one succeeds. */
  readonly data: Readonly<Ref<T | null>>
  /** idle: no document; loading: a read is running; ready: data is the last read; error: the last read failed. */
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** What the last read threw (see documentErrorOf), or null. */
  readonly error: Readonly<Ref<unknown>>
  /** The data shown is older than a known change: a read again is running or failed. */
  readonly stale: Readonly<Ref<boolean>>
  /** Reads the current target document again (after the caller's own change, or a retry). */
  reload(): Promise<void>
  /** Takes `value` as the current target's data (a change's answer); a read running now is thrown away. */
  replace(value: T): void
}

export function useInspectorResource<T>(options: InspectorResourceOptions<T>): InspectorResource<T> {
  const data = shallowRef<T | null>(null)
  const status = ref<InspectorResourceStatus>('idle')
  const error = shallowRef<unknown>(null)
  const stale = ref(false)
  // Request order: only the latest read of the current document may set the state.
  let latest = 0

  const docId = () => options.target()?.docId ?? null

  async function load(id: string): Promise<void> {
    const request = ++latest
    status.value = 'loading'
    try {
      const value = await options.read(id)
      if (request !== latest) return
      data.value = value
      error.value = null
      stale.value = false
      status.value = 'ready'
    } catch (failure) {
      if (request !== latest) return
      error.value = failure
      status.value = 'error'
    }
  }

  function reload(): Promise<void> {
    const id = docId()
    if (id === null) return Promise.resolve()
    if (data.value !== null) stale.value = true
    return load(id)
  }

  watch(
    docId,
    (id) => {
      latest++
      data.value = null
      error.value = null
      stale.value = false
      status.value = 'idle'
      if (id !== null) void load(id)
    },
    { immediate: true },
  )

  function replace(value: T): void {
    if (docId() === null) return
    latest++
    data.value = value
    error.value = null
    stale.value = false
    status.value = 'ready'
  }

  useInspectorEvents(options.target, { events: options.events, matches: options.matches, changed: () => void reload() })

  onScopeDispose(() => {
    latest++
  })

  return { data, status, error, stale, reload, replace }
}

export interface InspectorFieldWriteOptions<T> {
  /** The target the writes belong to; its docId is the resource key the write is sent for. */
  target: () => InspectorEventScope | null
  /** The resource a write's answer replaces. */
  resource: InspectorResource<T>
  /** Changes one value of the resource — a `@/features/document` call answering the whole resource. */
  write: (docId: string, field: string, value: unknown) => Promise<T>
}

export interface InspectorFieldWrite {
  /** Values sent and not answered yet, by field: shown instead of the read until the answer comes. */
  readonly pending: Readonly<Ref<Readonly<Record<string, unknown>>>>
  /** A write is waiting for the server. */
  readonly busy: Readonly<Ref<boolean>>
  /**
   * The last refused write of the target (its field and what it threw); cleared by a new write of that field
   * and by the field's latest change being taken. A refusal of a change made earlier than the field's latest is
   * not kept (that value is no longer the one wanted).
   */
  readonly failure: Readonly<Ref<{ readonly field: string; readonly error: unknown } | null>>
  /** Sends one value; true when the server took it. Writes go one after another, in the order made. */
  write(field: string, value: unknown): Promise<boolean>
}

/**
 * The common write of one value (flowgate.v02.0053 T#7). Each change is sent at once; while it waits its
 * value is pending (shown), the next change waits for it, and the answer replaces the resource. A refusal
 * keeps the field's failure and reads the resource again. A new target drops the pending values and the
 * failure; an answer for an earlier target is not applied.
 * Each change has a number; a field remembers its latest. Only the answer to a field's latest change settles
 * its pending value or its failure — an earlier answer (the same field changed again before it came, e.g.
 * on → off → on) neither drops the newer pending value nor sets or keeps a failure over it.
 */
export function useInspectorFieldWrite<T>(options: InspectorFieldWriteOptions<T>): InspectorFieldWrite {
  const pending = shallowRef<Readonly<Record<string, unknown>>>({})
  const busy = ref(false)
  const failure = shallowRef<{ readonly field: string; readonly error: unknown } | null>(null)
  let queue: Promise<unknown> = Promise.resolve()
  let waiting = 0
  let ticket = 0
  // Change numbers: `latestChange[field]` is the field's latest change of the current target.
  let changes = 0
  let latestChange: Record<string, number> = {}

  const key = () => options.target()?.docId ?? null

  watch(key, () => {
    ticket++
    pending.value = {}
    failure.value = null
    latestChange = {}
  })

  /** Drops the field's pending value when `change` is its latest change; false for an earlier change. */
  function settle(field: string, change: number): boolean {
    if (latestChange[field] !== change) return false
    delete latestChange[field]
    const rest: Record<string, unknown> = { ...pending.value }
    delete rest[field]
    pending.value = rest
    return true
  }

  function write(field: string, value: unknown): Promise<boolean> {
    const id = key()
    if (id === null) return Promise.resolve(false)
    const own = ticket
    const change = ++changes
    latestChange[field] = change
    pending.value = { ...pending.value, [field]: value }
    if (failure.value?.field === field) failure.value = null
    waiting++
    busy.value = true
    const run = queue.then(async () => {
      try {
        const answer = await options.write(id, field, value)
        if (own !== ticket) return false
        options.resource.replace(answer)
        if (settle(field, change) && failure.value?.field === field) failure.value = null
        return true
      } catch (error) {
        if (own !== ticket) return false
        if (settle(field, change)) failure.value = { field, error }
        await options.resource.reload()
        return false
      } finally {
        waiting--
        if (waiting === 0) busy.value = false
      }
    })
    queue = run.catch(() => false)
    return run
  }

  onScopeDispose(() => {
    ticket++
  })

  return { pending, busy, failure, write }
}
