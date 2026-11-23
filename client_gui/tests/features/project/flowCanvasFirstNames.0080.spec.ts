/**
 * flowgate.v02test.0080: the AI naming a Flow (`group_view_refresh`, reason `first_names`) reads that Flow's Canvas again
 * — the card titles and the Inspector header come from that read — once per burst; other refresh reasons do not.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CARD_STEP_COALESCE_MS, subscribeFlowCanvasChanges } from '@/features/project/flowCanvasEvents'

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static all: FakeEventSource[] = []
  readyState = FakeEventSource.CONNECTING
  closed = false
  private listeners = new Map<string, Array<(event: Event) => void>>()
  constructor(readonly url: string) { FakeEventSource.all.push(this) }
  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }
  close() { this.closed = true; this.readyState = FakeEventSource.CLOSED }
  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

const refresh = (groupId: string, payload: Record<string, unknown>) => ({
  event_type: 'group_view_refresh', payload: { group_id: groupId, ...payload }, project: 'flowgate', group_id: groupId, doc_id: null,
})

describe('Canvas read on AI first names', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    sessionStorage.clear()
  })

  it('reads the named Flow again once per burst, with or without targets', () => {
    const changed = vi.fn()
    const stop = subscribeFlowCanvasChanges('flowgate', { changed, resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('group_view_refresh', refresh('f.0080', { reason: 'first_names', targets: ['flow', 'card'] }))
    es.emit('group_view_refresh', refresh('f.0080', { reason: 'first_names', targets: ['chat'], doc_id: 'f.0080.0002-CH' }))
    es.emit('group_view_refresh', refresh('f.0081', { reason: 'first_names' }))
    expect(changed).not.toHaveBeenCalled()
    vi.advanceTimersByTime(CANVAS_CARD_STEP_COALESCE_MS)
    expect(changed.mock.calls.map((call) => call[0]).sort()).toEqual(['f.0080', 'f.0081'])
    stop()
  })

  it('ignores group_view_refresh of other reasons', () => {
    const changed = vi.fn()
    const stop = subscribeFlowCanvasChanges('flowgate', { changed, resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('group_view_refresh', refresh('f.0080', { reason: 'module_deleted' }))
    es.emit('group_view_refresh', refresh('f.0080', {}))
    vi.advanceTimersByTime(CANVAS_CARD_STEP_COALESCE_MS * 2)
    expect(changed).not.toHaveBeenCalled()
    stop()
  })
})
