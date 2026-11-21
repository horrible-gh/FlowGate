// S2 — per-Flow layout save batching (the plan S2-1 "Send one debounced batch
// after dragging ends"). Moved Node positions (last one per Node wins) and the last committed Viewport of
// one Flow are collected and handed to `send` once the Flow has been quiet for `delayMs`.
// No Vue, no transport: the Center session owns the send and what happens after it.
//
// One save per Flow is on the wire at a time. The server keeps the last write (P0008 §4), so a
// slow earlier PUT landing after a later one would bring back the older positions / Viewport.
// While a save of the Flow is running, newer changes keep collecting and go out (newest values)
// right after it settles, success or failure.
import { readPending, writePending } from './edit/pendingDraft'
import type { FlowGraphViewport } from './graph/flowGraph'
import type { FlowLayoutChange } from './graph/flowGraphSource'

export const LAYOUT_SAVE_DELAY_MS = 0

export interface LayoutSaveQueue {
  queuePosition(flowId: string, nodeId: string, x: number, y: number): void
  queueViewport(flowId: string, viewport: FlowGraphViewport): void
  /** Sends what is pending for the Flow now (tab close, project change), or right after its running save. */
  flush(flowId: string): void
  flushAll(): void
  /** True while the Flow has a change waiting or a save running. */
  busy(flowId: string): boolean
  positions(flowId: string): ReadonlyMap<string, { x: number; y: number }>
  dispose(): void
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
  storageKey?: string,
): LayoutSaveQueue {
  const pending = new Map<string, Pending>()
  const sending = new Set<string>()
  const inFlight = new Map<string, Pending>()
  let disposed = false
  const failures = new Map<string, number>()
  function positions(flowId: string) {
    return new Map([...(inFlight.get(flowId)?.positions ?? []), ...(pending.get(flowId)?.positions ?? [])])
  }
  function persist() {
    const ids = new Set([...inFlight.keys(), ...pending.keys()])
    writePending(storageKey, ids.size ? [...ids].map(flowId => ({ flowId,
      positions: [...positions(flowId)], viewport: pending.get(flowId)?.viewport ?? inFlight.get(flowId)?.viewport ?? null,
    })) : null)
  }
  const restored = readPending<Array<{ flowId: string; positions: Array<[string, { x: number; y: number }]>; viewport: FlowGraphViewport | null }>>(storageKey)
  if (Array.isArray(restored)) for (const item of restored) {
    if (typeof item.flowId !== 'string' || !Array.isArray(item.positions)) continue
    pending.set(item.flowId, { positions: new Map(item.positions), viewport: item.viewport, timer: null, due: false })
  }

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
    if (!disposed) item.timer = setTimeout(() => flush(flowId), failures.has(flowId) ? Math.min(30000, 1000 * 2 ** Math.min(failures.get(flowId)! - 1, 5)) : delayMs)
  }

  function settled(flowId: string) {
    sending.delete(flowId)
    inFlight.delete(flowId)
    persist()
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
    inFlight.set(flowId, item)
    persist()
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
      () => { failures.delete(flowId); settled(flowId) },
      () => {
        sending.delete(flowId)
        inFlight.delete(flowId)
        const newer = pending.get(flowId)
        const retry = entry(flowId)
        retry.positions = new Map([...item.positions, ...(newer?.positions ?? [])])
        retry.viewport = newer?.viewport ?? item.viewport
        retry.due = false
        failures.set(flowId, (failures.get(flowId) ?? 0) + 1)
        persist()
        schedule(flowId, retry)
      },
    )
  }

  for (const [flowId, item] of pending) schedule(flowId, item)

  return {
    positions,
    dispose() { disposed = true; for (const item of pending.values()) if (item.timer !== null) clearTimeout(item.timer) },
    queuePosition(flowId, nodeId, x, y) {
      const item = entry(flowId)
      item.positions.set(nodeId, { x, y })
      persist()
      schedule(flowId, item)
    },
    queueViewport(flowId, viewport) {
      const item = entry(flowId)
      item.viewport = { x: viewport.x, y: viewport.y, zoom: viewport.zoom }
      persist()
      schedule(flowId, item)
    },
    flush,
    flushAll() {
      for (const flowId of [...pending.keys()]) flush(flowId)
    },
    busy: (flowId) => pending.has(flowId) || sending.has(flowId),
  }
}
