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
function projectEventSubscriber(): SubscribeProjectEvents | null {
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

  useInspectorEvents(options.target, { events: options.events, matches: options.matches, changed: () => void reload() })

  onScopeDispose(() => {
    latest++
  })

  return { data, status, error, stale, reload }
}
