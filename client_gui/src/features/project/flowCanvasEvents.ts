// flowgate.v02.0045 S2 — Canvas change signals over the existing SSE stream (P0008 §8.1: the
// screen push stream `GET /api/v1/events/stream?project=…`, no new stream). Events are refresh
// signals, never state: on `graph_canvas_changed` from another tab, device or user the Canvas of
// that Flow is read again, and after a reconnect every open Canvas is (events may have been
// missed). An event whose `origin_client_id` is this tab's `x-canvas-client-id` is dropped — this
// tab already applied its own response (P0008 §8.1 / §8.3: judged per tab, not per user).
// The Designer reaches this only through the project store (subscribeFlowCanvasChanges).
import { CANVAS_CLIENT_ID } from './flowCanvasApi'

/** Event types that mean "read this Flow's Canvas again" (P0008 §8.2; ownership arrives with S4). */
export const CANVAS_REFRESH_EVENTS = ['graph_canvas_changed', 'flow_ownership_changed'] as const
/** Wait before a closed stream (e.g. a 401 on a rotated token) is opened again with a fresh token. */
export const CANVAS_STREAM_RETRY_MS = 3000

export interface FlowCanvasChangeHandlers {
  /** Another tab / device / user changed this Flow's Canvas. */
  changed(flowId: string): void
  /** The stream (re)connected after a drop: events may be missing, read every open Canvas. */
  resync(): void
}

type Raw = Record<string, any>

function streamUrl(projectId: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8088/flowgate'
  const token =
    (window as unknown as { __accessToken__?: string }).__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
  const params = new URLSearchParams()
  if (token) params.set('token', token)
  params.set('project', projectId)
  return `${base}/api/v1/events/stream?${params.toString()}`
}

/** The Flow a refresh event is about, or null when it came from this tab or is unreadable. */
export function changedFlowOf(data: string): string | null {
  let frame: Raw
  try {
    frame = JSON.parse(data)
  } catch {
    return null
  }
  const payload: Raw = frame && typeof frame.payload === 'object' && frame.payload ? frame.payload : {}
  if (payload.origin_client_id === CANVAS_CLIENT_ID) return null
  const flowId = payload.flow_id ?? frame.group_id
  return typeof flowId === 'string' && flowId ? flowId : null
}

/** Subscribes to the project's Canvas changes; returns the unsubscribe. No-op without EventSource. */
export function subscribeFlowCanvasChanges(projectId: string, handlers: FlowCanvasChangeHandlers): () => void {
  if (typeof EventSource === 'undefined') return () => {}
  let stream: EventSource | null = null
  let retry: ReturnType<typeof setTimeout> | null = null
  let opened = false
  let stopped = false

  const onChange = (event: Event) => {
    const flowId = changedFlowOf(String((event as MessageEvent).data ?? ''))
    if (flowId !== null) handlers.changed(flowId)
  }

  function connect() {
    retry = null
    const es = new EventSource(streamUrl(projectId))
    stream = es
    es.addEventListener('open', () => {
      // The first open is the initial read's moment; any later one follows a drop.
      if (opened) handlers.resync()
      opened = true
    })
    for (const type of CANVAS_REFRESH_EVENTS) es.addEventListener(type, onChange)
    es.addEventListener('error', () => {
      // The browser retries an interrupted stream itself (same URL); a closed one is not retried,
      // so open a new one carrying the current token.
      if (stopped || es.readyState !== EventSource.CLOSED || retry !== null) return
      es.close()
      retry = setTimeout(connect, CANVAS_STREAM_RETRY_MS)
    })
  }

  connect()
  return () => {
    stopped = true
    if (retry !== null) clearTimeout(retry)
    stream?.close()
    stream = null
  }
}
