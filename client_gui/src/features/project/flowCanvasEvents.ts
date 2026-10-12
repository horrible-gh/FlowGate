// flowgate.v02.0045 S2 — Canvas change signals over the existing SSE stream (P0008 §8.1: the
// screen push stream `GET /api/v1/events/stream?project=…`, no new stream). Events are refresh
// signals, never state: on `graph_canvas_changed` from another tab, device or user the Canvas of
// that Flow is read again, and after a reconnect every open Canvas is (events may have been
// missed). An event whose `origin_client_id` is this tab's `x-canvas-client-id` is dropped — this
// tab already applied its own response (P0008 §8.1 / §8.3: judged per tab, not per user).
// The Designer reaches this only through the project store (subscribeFlowCanvasChanges).
// flowgate.v02.0048 T#2 (D#1 §7.1): an adapter over the project event hub (projectEvents.ts), so
// the Canvas and the Inspector share one stream per project. The connection, its reconnects and
// the resync live in the hub; the Canvas filter (own tab, Flow id) stays here.
import { CANVAS_CLIENT_ID } from './flowCanvasApi'
import { PROJECT_STREAM_RETRY_MS, subscribeProjectEvents, type ProjectEventFrame } from './projectEvents'

/**
 * Event types that mean "read this Flow's Canvas again" (P0008 §8.2; ownership arrives with S4).
 * flowgate.v02.0053 T#9: a run's status and node state changes too — the Canvas read carries each card's
 * run state (its colour dot) and the top band its run. They carry no `origin_client_id`, so this tab's own
 * 실행 / 중지 is read again as well.
 */
export const CANVAS_REFRESH_EVENTS = ['graph_canvas_changed', 'flow_ownership_changed', 'graph_run_status_changed', 'graph_node_state_changed'] as const
/** Wait before a closed stream (e.g. a 401 on a rotated token) is opened again with a fresh token. */
export const CANVAS_STREAM_RETRY_MS = PROJECT_STREAM_RETRY_MS

export interface FlowCanvasChangeHandlers {
  /** Another tab / device / user changed this Flow's Canvas. */
  changed(flowId: string): void
  /** The stream (re)connected after a drop: events may be missing, read every open Canvas. */
  resync(): void
}

type Raw = Record<string, any>

/** The Flow a refresh frame is about, or null when it came from this tab. */
function changedFlowOfFrame(payload: Raw, groupId: unknown): string | null {
  if (payload.origin_client_id === CANVAS_CLIENT_ID) return null
  const flowId = payload.flow_id ?? groupId
  return typeof flowId === 'string' && flowId ? flowId : null
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
  return changedFlowOfFrame(payload, frame?.group_id)
}

/** Subscribes to the project's Canvas changes; returns the unsubscribe. No-op without EventSource. */
export function subscribeFlowCanvasChanges(projectId: string, handlers: FlowCanvasChangeHandlers): () => void {
  return subscribeProjectEvents(projectId, {
    events: CANVAS_REFRESH_EVENTS,
    event: (frame: ProjectEventFrame) => {
      const flowId = changedFlowOfFrame(frame.payload, frame.groupId)
      if (flowId !== null) handlers.changed(flowId)
    },
    resync: () => handlers.resync(),
  })
}
