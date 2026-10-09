// flowgate.v02.0045 S2 — per-Flow layout save batching (P0008 S2-1 "드래그 종료 후 디바운스 묶음
// 1회 전송"). Moved Node positions (last one per Node wins) and the last committed Viewport of
// one Flow are collected and handed to `send` once the Flow has been quiet for `delayMs`.
// No Vue, no transport: the Center session owns the send and what happens after it.
//
// One save per Flow is on the wire at a time. The server keeps the last write (P0008 §4), so a
// slow earlier PUT landing after a later one would bring back the older positions / Viewport.
// While a save of the Flow is running, newer changes keep collecting and go out (newest values)
// right after it settles, success or failure.
import type { FlowGraphViewport } from './graph/flowGraph'
import type { FlowLayoutChange } from './graph/flowGraphSource'

export const LAYOUT_SAVE_DELAY_MS = 400

export interface LayoutSaveQueue {
  queuePosition(flowId: string, nodeId: string, x: number, y: number): void
  queueViewport(flowId: string, viewport: FlowGraphViewport): void
  /** Sends what is pending for the Flow now (tab close, project change), or right after its running save. */
  flush(flowId: string): void
  flushAll(): void
  /** True while the Flow has a change waiting or a save running. */
  busy(flowId: string): boolean
}

/** Sends one batch; a returned promise holds the Flow's next batch until it settles. */
export type LayoutSend = (flowId: string, change: FlowLayoutChange) => unknown

interface Pending {
  positions: Map<string, { x: number; y: number }>
  viewport: FlowGraphViewport | null
  timer: ReturnType<typeof setTimeout> | null
  /** Its quiet time is over (or a flush asked for it) but a save of the Flow is still running. */
  due: boolean
}

export function createLayoutSaveQueue(
  send: LayoutSend,
  delayMs: number = LAYOUT_SAVE_DELAY_MS,
  /** Called when the Flow has nothing waiting and nothing running any more after a save. */
  onIdle?: (flowId: string) => void,
): LayoutSaveQueue {
  const pending = new Map<string, Pending>()
  const sending = new Set<string>()

  function entry(flowId: string): Pending {
    let item = pending.get(flowId)
    if (!item) {
      item = { positions: new Map(), viewport: null, timer: null, due: false }
      pending.set(flowId, item)
    }
    return item
  }

  function schedule(flowId: string, item: Pending) {
    if (item.timer !== null) clearTimeout(item.timer)
    item.timer = setTimeout(() => flush(flowId), delayMs)
  }

  function settled(flowId: string) {
    sending.delete(flowId)
    const next = pending.get(flowId)
    if (next?.due) flush(flowId)
    else if (!next) onIdle?.(flowId)
  }

  function flush(flowId: string) {
    const item = pending.get(flowId)
    if (!item) return
    if (item.timer !== null) clearTimeout(item.timer)
    item.timer = null
    if (sending.has(flowId)) {
      item.due = true
      return
    }
    pending.delete(flowId)
    if (item.positions.size === 0 && item.viewport === null) return
    sending.add(flowId)
    let request: Promise<unknown>
    try {
      request = Promise.resolve(
        send(flowId, {
          positions: [...item.positions].map(([nodeId, p]) => ({ nodeId, x: p.x, y: p.y })),
          viewport: item.viewport,
        }),
      )
    } catch (error) {
      request = Promise.reject(error)
    }
    request.then(
      () => settled(flowId),
      () => settled(flowId),
    )
  }

  return {
    queuePosition(flowId, nodeId, x, y) {
      const item = entry(flowId)
      item.positions.set(nodeId, { x, y })
      schedule(flowId, item)
    },
    queueViewport(flowId, viewport) {
      const item = entry(flowId)
      item.viewport = { x: viewport.x, y: viewport.y, zoom: viewport.zoom }
      schedule(flowId, item)
    },
    flush,
    flushAll() {
      for (const flowId of [...pending.keys()]) flush(flowId)
    },
    busy: (flowId) => pending.has(flowId) || sending.has(flowId),
  }
}
