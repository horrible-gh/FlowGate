// flowgate.v02.0048 T#2 — the project event hub (D#1 §7.1). One EventSource per project on the
// existing screen push stream (`GET /api/v1/events/stream?project=…`, P0008 §8.1) shared by every
// subscriber — the Canvas (flowCanvasEvents.ts, an adapter over this hub) and the Inspector. The
// stream opens with the first subscription and closes with the last unsubscribe. Reconnects work as
// the Canvas stream did before the hub: the browser retries an interrupted stream itself, a closed
// one is opened again after PROJECT_STREAM_RETRY_MS with the current token, and every open after the
// first tells each subscriber to resync (events may have been missed).
// flowgate.v02gui.0031 T0005 ①: the stream also follows the session's connection signals. A token rotation
// (`fg:access_token_refreshed`, sent by shared/api.ts), the browser going online again and the tab being shown
// again replace a stream that is not open — one the browser is retrying with the token pinned in its URL, a closed
// one waiting for its reopen — with a new one carrying the current token at once, instead of leaving it to the
// browser's retry on the old URL or to the retry wait. An open stream is kept: the server checks the token only when
// the stream connects (sse_routes.py), so replacing a healthy stream on every rotation would only make every
// subscriber read everything again each time. A replaced stream resyncs its subscribers once, on its open; a stream
// whose first attempt failed resyncs on its first open too (what was read meanwhile may be stale). The window
// listeners exist while a hub does and go with the last unsubscribe.
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
  /** A connection attempt failed before any stream of the hub opened: the first open resyncs too. */
  missed: boolean
  /** The access token the current stream carries in its URL. */
  token: string
}

type Raw = Record<string, any>

const hubs = new Map<string, Hub>()

/** The access token of the session now (the one shared/api.ts keeps current). */
function currentToken(): string {
  return (window as unknown as { __accessToken__?: string }).__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
}

function streamUrl(projectId: string, token: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8088/flowgate'
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
  if (hub.retry !== null) clearTimeout(hub.retry)
  hub.retry = null
  const token = currentToken()
  const es = new EventSource(streamUrl(projectId, token))
  hub.stream = es
  hub.token = token
  hub.listening = new Set()
  es.addEventListener('open', () => {
    if (hub.stream !== es) return
    // The first open is the subscribers' initial read moment; any later one follows a drop (or a replacement).
    if (hub.opened || hub.missed) resyncAll(hub)
    hub.opened = true
    hub.missed = false
  })
  for (const sub of hub.subscribers) for (const type of sub.events) listen(hub, es, type)
  es.addEventListener('error', () => {
    if (hub.stream !== es) return
    if (!hub.opened) hub.missed = true
    if (es.readyState === EventSource.CLOSED) {
      // A closed stream is not retried by the browser: open a new one carrying the current token.
      if (hub.retry !== null) return
      es.close()
      hub.retry = setTimeout(() => connect(projectId, hub), PROJECT_STREAM_RETRY_MS)
      return
    }
    // The browser retries an interrupted stream itself, on the same URL. When the token rotated since this stream
    // opened, that retry would present the old token: open a new stream with the current one now.
    if (hub.token !== currentToken()) replace(projectId, hub)
  })
}

/** Drops the hub's current stream (and a pending reopen) for a new one with the current token. */
function replace(projectId: string, hub: Hub) {
  if (hubs.get(projectId) !== hub) return
  const old = hub.stream
  hub.stream = null
  old?.close()
  connect(projectId, hub)
}

/** Whether the hub's stream is open and nothing waits to replace it. */
function healthy(hub: Hub): boolean {
  return hub.retry === null && hub.stream !== null && hub.stream.readyState === EventSource.OPEN
}

type Revive = 'token' | 'online' | 'visible' | 'focus' | 'recovered'

/**
 * T0005 ①: a connection signal. Every hub whose stream is not open gets a new one with the current token now; an
 * open stream is kept (see the header). A token signal that brings no new token leaves a connecting stream to the
 * browser (shared/api.ts may send one rotation twice: its own and a sibling tab's).
 */
export function revive(reason: Revive) {
  // EventSource can be gone while a hub lingers (a test environment that removed it): nothing to reconnect with.
  if (typeof EventSource === 'undefined') return
  for (const [projectId, hub] of [...hubs]) {
    if (hubs.get(projectId) !== hub) continue
    if (healthy(hub)) {
      if (reason !== 'token') resyncAll(hub)
      continue
    }
    if (reason === 'token' && hub.retry === null && hub.stream !== null && hub.token === currentToken()) continue
    if (reason === 'recovered') hub.missed = true
    replace(projectId, hub)
  }
}

const onRecovered = () => revive('recovered')
const onTokenRefreshed = () => revive('token')
const onOnline = () => revive('online')
const onFocus = () => revive('focus')
const onVisibility = () => {
  if (document.visibilityState === 'visible') revive('visible')
}
let windowListening = false

function watchConnection(on: boolean) {
  if (on === windowListening || typeof window === 'undefined') return
  windowListening = on
  if (on) {
    window.addEventListener('fg:connection_recovered', onRecovered)
    window.addEventListener('fg:access_token_refreshed', onTokenRefreshed)
    window.addEventListener('online', onOnline)
    window.addEventListener('focus', onFocus)
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility)
  } else {
    window.removeEventListener('fg:connection_recovered', onRecovered)
    window.removeEventListener('fg:access_token_refreshed', onTokenRefreshed)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('focus', onFocus)
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility)
  }
}

function close(projectId: string, hub: Hub) {
  if (hubs.get(projectId) === hub) hubs.delete(projectId)
  if (hub.retry !== null) clearTimeout(hub.retry)
  hub.retry = null
  hub.stream?.close()
  hub.stream = null
  if (hubs.size === 0) watchConnection(false)
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
    hub = { subscribers: new Set(), listening: new Set(), stream: null, retry: null, opened: false, missed: false, token: '' }
    hubs.set(projectId, hub)
    hub.subscribers.add(sub)
    watchConnection(true)
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
