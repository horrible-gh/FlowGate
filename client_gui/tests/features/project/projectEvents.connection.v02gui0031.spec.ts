/**
 * ① — the project event hub follows the session's connection signals. The stream's URL pins
 * the token it was opened with, and the hub heard no `fg:access_token_refreshed`, online or visibility signal (the
 * comment in shared/api.ts said it did). Now a token rotation, online and a return to the tab replace a stream that is
 * not open (the browser retrying the old URL, a closed one waiting for its reopen) with one carrying the current token,
 * clear the pending reopen, keep every subscriber, resync each once on the new stream's open, and leave no second
 * stream, no stale callback and no window listener behind the last unsubscribe. An open stream is kept.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PROJECT_STREAM_RETRY_MS, subscribeProjectEvents, type ProjectEventFrame } from '@/features/project/projectEvents'

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static all: FakeEventSource[] = []
  readyState = FakeEventSource.CONNECTING
  closed = false
  private listeners = new Map<string, Array<(event: Event) => void>>()

  constructor(readonly url: string) {
    FakeEventSource.all.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  listened(type: string): number {
    return this.listeners.get(type)?.length ?? 0
  }

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  emit(type: string, data?: unknown) {
    if (type === 'open') this.readyState = FakeEventSource.OPEN
    const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  /** The browser lost the stream and retries it itself (same URL). */
  drop() {
    this.readyState = FakeEventSource.CONNECTING
    this.emit('error')
  }

  /** The browser gave the stream up (e.g. 401 / 5xx on connect): it is not retried. */
  fail() {
    this.readyState = FakeEventSource.CLOSED
    this.emit('error')
  }
}

const live = () => FakeEventSource.all.filter((es) => !es.closed)
const tokenOf = (es: FakeEventSource) => new URL(es.url).searchParams.get('token')
const rotate = (token: string) => {
  sessionStorage.setItem('fg_access_token', token)
  window.dispatchEvent(new CustomEvent('fg:access_token_refreshed', { detail: { token } }))
}

function recorder(events: readonly string[]) {
  const frames: ProjectEventFrame[] = []
  const resync = vi.fn()
  return { frames, resync, subscription: { events, event: (f: ProjectEventFrame) => frames.push(f), resync } }
}

const stops: Array<() => void> = []
function hub(projectId: string, subscription: Parameters<typeof subscribeProjectEvents>[1]) {
  const stop = subscribeProjectEvents(projectId, subscription)
  stops.push(stop)
  return stop
}
let visibility: DocumentVisibilityState = 'visible'

describe('v02gui.0031 T0005 ① project stream follows token rotation, online and visibility', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
    visibility = 'visible'
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  })
  afterEach(() => {
    for (const stop of stops.splice(0)) stop()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.restoreAllMocks()
    sessionStorage.clear()
    delete (window as { __accessToken__?: string }).__accessToken__
    delete (document as unknown as Record<string, unknown>).visibilityState
  })

  it('a rotation while the browser retries the old URL replaces the stream with the new token and resyncs once', () => {
    const a = recorder(['doc_review_status_changed'])
    const b = recorder(['graph_canvas_changed'])
    hub('flowgate', a.subscription)
    hub('flowgate', b.subscription)
    const first = FakeEventSource.all[0]
    first.emit('open')
    first.drop()
    expect(FakeEventSource.all).toHaveLength(1) // the same token: left to the browser

    rotate('jwt-2')

    expect(first.closed).toBe(true)
    expect(live()).toHaveLength(1)
    const second = live()[0]
    expect(tokenOf(second)).toBe('jwt-2')
    // Every subscriber is kept: each name is listened once on the new stream.
    expect([second.listened('doc_review_status_changed'), second.listened('graph_canvas_changed')]).toEqual([1, 1])
    expect([a.resync.mock.calls.length, b.resync.mock.calls.length]).toEqual([0, 0])

    second.emit('open')
    expect([a.resync.mock.calls.length, b.resync.mock.calls.length]).toEqual([1, 1])
    // The old stream's late callbacks reach nobody.
    first.emit('open')
    first.emit('doc_review_status_changed', { payload: {}, doc_id: 'old' })
    expect(a.resync).toHaveBeenCalledTimes(1)
    expect(a.frames).toEqual([])
    second.emit('doc_review_status_changed', { payload: {}, doc_id: 'new' })
    expect(a.frames.map((f) => f.docId)).toEqual(['new'])
  })

  it('a rotation while a closed stream waits for its reopen opens the new one at once and clears the wait', () => {
    const a = recorder(['x'])
    hub('flowgate', a.subscription)
    const first = FakeEventSource.all[0]
    first.emit('open')
    first.fail()
    expect(live()).toHaveLength(0)

    rotate('jwt-2')
    expect(FakeEventSource.all).toHaveLength(2)
    expect(tokenOf(FakeEventSource.all[1])).toBe('jwt-2')

    // The cleared reopen does not open a second stream.
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS * 3)
    expect(FakeEventSource.all).toHaveLength(2)
    FakeEventSource.all[1].emit('open')
    expect(a.resync).toHaveBeenCalledTimes(1)
  })

  it('an open stream is kept on a rotation (the server checks the token on connect only)', () => {
    const a = recorder(['x'])
    hub('flowgate', a.subscription)
    FakeEventSource.all[0].emit('open')

    rotate('jwt-2')
    rotate('jwt-3')

    expect(FakeEventSource.all).toHaveLength(1)
    expect(a.resync).not.toHaveBeenCalled()
    // Its next drop reconnects with the current token right away, not on the old URL.
    FakeEventSource.all[0].drop()
    expect(FakeEventSource.all).toHaveLength(2)
    expect(tokenOf(FakeEventSource.all[1])).toBe('jwt-3')
    FakeEventSource.all[1].emit('open')
    expect(a.resync).toHaveBeenCalledTimes(1)
  })

  it('a rotation signal heard twice (own + sibling tab) replaces the stream once', () => {
    hub('flowgate', recorder(['x']).subscription)
    FakeEventSource.all[0].emit('open')
    FakeEventSource.all[0].drop()

    rotate('jwt-2')
    rotate('jwt-2')
    window.dispatchEvent(new CustomEvent('fg:access_token_refreshed', { detail: { token: 'jwt-2' } }))

    expect(FakeEventSource.all).toHaveLength(2)
    expect(live()).toHaveLength(1)
  })

  it('online and a return to the tab reopen a stream that is not open at once; hidden and open streams are left', () => {
    const a = recorder(['x'])
    hub('flowgate', a.subscription)
    FakeEventSource.all[0].emit('open')

    window.dispatchEvent(new Event('online'))
    document.dispatchEvent(new Event('visibilitychange'))
    expect(FakeEventSource.all).toHaveLength(1) // open: nothing to do

    FakeEventSource.all[0].fail()
    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
    expect(FakeEventSource.all).toHaveLength(1)

    window.dispatchEvent(new Event('online'))
    expect(FakeEventSource.all).toHaveLength(2)
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS * 2)
    expect(FakeEventSource.all).toHaveLength(2) // the cleared reopen stays cleared

    FakeEventSource.all[1].fail()
    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    expect(FakeEventSource.all).toHaveLength(3)
    expect(live()).toHaveLength(1)
    FakeEventSource.all[2].emit('open')
    expect(a.resync).toHaveBeenCalledTimes(3) // online + visible refresh even an open connection
  })

  it('a stream whose first attempt failed resyncs on its first open (what was read meanwhile may be stale)', () => {
    const a = recorder(['x'])
    hub('flowgate', a.subscription)
    FakeEventSource.all[0].fail()
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS)
    FakeEventSource.all[1].emit('open')
    expect(a.resync).toHaveBeenCalledTimes(1)
  })

  it('one signal replaces each project hub once', () => {
    hub('flowgate', recorder(['x']).subscription)
    hub('other', recorder(['x']).subscription)
    for (const es of FakeEventSource.all) es.fail()

    rotate('jwt-2')

    expect(live().map((es) => [new URL(es.url).searchParams.get('project'), tokenOf(es)])).toEqual([
      ['flowgate', 'jwt-2'],
      ['other', 'jwt-2'],
    ])
  })

  it('a hub left behind once EventSource is gone ignores the signals (no error)', () => {
    const errors: unknown[] = []
    const onError = (event: ErrorEvent) => {
      errors.push(event.error ?? event.message)
      event.preventDefault()
    }
    window.addEventListener('error', onError)
    hub('flowgate', recorder(['x']).subscription)
    FakeEventSource.all[0].fail()
    vi.stubGlobal('EventSource', undefined)

    rotate('jwt-2')
    window.dispatchEvent(new Event('online'))
    document.dispatchEvent(new Event('visibilitychange'))

    window.removeEventListener('error', onError)
    vi.stubGlobal('EventSource', FakeEventSource)
    expect(errors).toEqual([])
    expect(FakeEventSource.all).toHaveLength(1)
  })

  it('the last unsubscribe removes the window listeners: later signals open nothing', () => {
    const removed = vi.spyOn(window, 'removeEventListener')
    const stop = hub('flowgate', recorder(['x']).subscription)
    FakeEventSource.all[0].fail()
    stop()
    expect(live()).toHaveLength(0)
    const names = removed.mock.calls.map((call) => call[0])
    expect(names).toEqual(expect.arrayContaining(['fg:access_token_refreshed', 'online']))

    rotate('jwt-2')
    window.dispatchEvent(new Event('online'))
    document.dispatchEvent(new Event('visibilitychange'))
    vi.advanceTimersByTime(PROJECT_STREAM_RETRY_MS * 3)
    expect(FakeEventSource.all).toHaveLength(1)

    // A new subscription listens again.
    hub('flowgate', recorder(['x']).subscription)
    FakeEventSource.all[1].fail()
    rotate('jwt-3')
    expect(tokenOf(FakeEventSource.all[2])).toBe('jwt-3')
  })
})
