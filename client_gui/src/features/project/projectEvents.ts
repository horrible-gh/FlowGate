// flowgate.v02.0048 T#2 — the project event hub (D#1 §7.1). One EventSource per project on the
// existing screen push stream (`GET /api/v1/events/stream?project=…`, P0008 §8.1) shared by every
// subscriber — the Canvas (flowCanvasEvents.ts, an adapter over this hub) and the Inspector. The
// stream opens with the first subscription and closes with the last unsubscribe. Reconnects work as
// the Canvas stream did before the hub: the browser retries an interrupted stream itself, a closed
// one is opened again after PROJECT_STREAM_RETRY_MS with the current token, and every open after the
// first tells each subscriber to resync (events may have been missed).
// Events are signals, never state: the hub keeps no frame. It parses each frame into its event name,
// payload, group_id and doc_id and hands it to the subscribers of that name; reading the resource
// again is the subscriber's job. The Designer reaches this only through the project store
// (subscribeProjectEvents).

/** Wait before a closed stream (e.g. a 401 on a rotated token) is opened again with a fresh token. */
export const PROJECT_STREAM_RETRY_MS = 3000

/** One server event as the hub passes it on (the SSE event name and the frame's routing fields). */
export interface ProjectEventFrame {
  /** SSE event name = the server's event_type (e.g. `doc_review_status_changed`). */
  type: string
  payload: Record<string, unknown>
  groupId: string | null
  docId: string | null
}

export interface ProjectEventSubscription {
  /** Event names to hear. EventSource listens per name, so the hub adds the missing ones. */
  events: readonly string[]
  /** One event of a listed name arrived. */
  event(frame: ProjectEventFrame): void
  /** The stream (re)connected after a drop: events may be missing, read again what is shown. */
  resync?(): void
}

interface Hub {
  subscribers: Set<ProjectEventSubscription>
  /** Names the current connection listens to (reset with each new EventSource). */
  listening: Set<string>
  stream: EventSource | null
  retry: ReturnType<typeof setTimeout> | null
  opened: boolean
}

type Raw = Record<string, any>

const hubs = new Map<string, Hub>()

function streamUrl(projectId: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8088/flowgate'
  const token =
    (window as unknown as { __accessToken__?: string }).__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
  const params = new URLSearchParams()
  if (token) params.set('token', token)
  params.set('project', projectId)
  return `${base}/api/v1/events/stream?${params.toString()}`
}

/** The frame of one SSE message, or null when its data is not JSON. Keeps nothing. */
export function parseProjectEvent(type: string, data: string): ProjectEventFrame | null {
  let frame: Raw
  try {
    frame = JSON.parse(data)
  } catch {
    return null
  }
  const raw: Raw = frame && typeof frame === 'object' ? frame : {}
  const payload: Raw = raw.payload && typeof raw.payload === 'object' ? raw.payload : {}
  const text = (value: unknown) => (typeof value === 'string' && value ? value : null)
  return { type, payload, groupId: text(raw.group_id), docId: text(raw.doc_id) }
}

function deliver(hub: Hub, type: string, event: Event) {
  const frame = parseProjectEvent(type, String((event as MessageEvent).data ?? ''))
  if (frame === null) return
  for (const sub of [...hub.subscribers]) {
    if (!hub.subscribers.has(sub) || !sub.events.includes(type)) continue
    notify(() => sub.event(frame))
  }
}

function resyncAll(hub: Hub) {
  for (const sub of [...hub.subscribers]) {
    if (hub.subscribers.has(sub) && sub.resync) notify(() => sub.resync!())
  }
}

// One subscriber's failure must not keep the event from the others (the Canvas stays independent
// of the Inspector).
function notify(call: () => void) {
  try {
    call()
  } catch (error) {
    console.error('[projectEvents] subscriber failed', error)
  }
}

function listen(hub: Hub, es: EventSource, type: string) {
  if (hub.listening.has(type)) return
  hub.listening.add(type)
  es.addEventListener(type, (event) => {
    if (hub.stream === es) deliver(hub, type, event)
  })
}

function connect(projectId: string, hub: Hub) {
  hub.retry = null
  const es = new EventSource(streamUrl(projectId))
  hub.stream = es
  hub.listening = new Set()
  es.addEventListener('open', () => {
    if (hub.stream !== es) return
    // The first open is the subscribers' initial read moment; any later one follows a drop.
    if (hub.opened) resyncAll(hub)
    hub.opened = true
  })
  for (const sub of hub.subscribers) for (const type of sub.events) listen(hub, es, type)
  es.addEventListener('error', () => {
    // The browser retries an interrupted stream itself (same URL); a closed one is not retried,
    // so open a new one carrying the current token.
    if (hub.stream !== es || es.readyState !== EventSource.CLOSED || hub.retry !== null) return
    es.close()
    hub.retry = setTimeout(() => connect(projectId, hub), PROJECT_STREAM_RETRY_MS)
  })
}

function close(projectId: string, hub: Hub) {
  if (hubs.get(projectId) === hub) hubs.delete(projectId)
  if (hub.retry !== null) clearTimeout(hub.retry)
  hub.retry = null
  hub.stream?.close()
  hub.stream = null
}

/**
 * Subscribes to the project's events over the shared stream; returns the unsubscribe (idempotent).
 * The first subscription of a project opens the stream, the last unsubscribe closes it.
 * No-op without EventSource.
 */
export function subscribeProjectEvents(projectId: string, subscription: ProjectEventSubscription): () => void {
  if (typeof EventSource === 'undefined') return () => {}
  // A copy per call: the same handlers object subscribed twice counts twice.
  const sub: ProjectEventSubscription = {
    events: [...subscription.events],
    event: (frame) => subscription.event(frame),
    resync: subscription.resync ? () => subscription.resync!() : undefined,
  }
  let hub = hubs.get(projectId)
  if (!hub) {
    hub = { subscribers: new Set(), listening: new Set(), stream: null, retry: null, opened: false }
    hubs.set(projectId, hub)
    hub.subscribers.add(sub)
    connect(projectId, hub)
  } else {
    hub.subscribers.add(sub)
    // A stream waiting to reopen picks the new names up when it connects.
    if (hub.stream && hub.retry === null) for (const type of sub.events) listen(hub, hub.stream, type)
  }
  const owner = hub
  let active = true
  return () => {
    if (!active) return
    active = false
    owner.subscribers.delete(sub)
    if (owner.subscribers.size === 0) close(projectId, owner)
  }
}
