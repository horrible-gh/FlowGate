import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios, { AxiosError } from 'axios'
import api from '@shared/api'
import { createConnectionRecovery } from '@shared/connectionRecovery'
import { subscribeProjectEvents } from '@/features/project/projectEvents'

class Stream extends EventTarget {
  static OPEN = 1
  static CLOSED = 2
  static all: Stream[] = []
  readyState = 0
  constructor(_url: string) { super(); Stream.all.push(this) }
  close() { this.readyState = 2 }
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
}
const original = api.defaults.adapter
let stops: Array<() => void> = []
beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  vi.stubGlobal('EventSource', Stream)
  Stream.all = []
})
afterEach(async () => {
  // A normal success ends the module singleton even after a failed assertion.
  api.defaults.adapter = async config => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config })
  await api.get('/cleanup')
  stops.splice(0).forEach(stop => stop())
  api.defaults.adapter = original
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})
function fail(status?: number, code = 'ERR_NETWORK') {
  api.defaults.adapter = async config => {
    throw new AxiosError('failed', code, config, undefined,
      status ? { data: {}, status, statusText: '', headers: {}, config } : undefined)
  }
}
function subscriber(project = 'p') {
  const resync = vi.fn()
  stops.push(subscribeProjectEvents(project, { events: [], event() {}, resync }))
  return resync
}
async function exhaust(status = 503) {
  fail(status)
  const result = api.get('/list').catch(error => error)
  await vi.advanceTimersByTimeAsync(9500)
  return result
}
describe('0060 connection recovery', () => {
  it('exhausts retries, probes at 5s then 10s, resyncs every open hub once and stops', async () => {
    const a = subscriber('a'), b = subscriber('b')
    Stream.all.forEach(s => s.open())
    const probe = vi.spyOn(axios, 'get').mockRejectedValueOnce(new Error('db down')).mockResolvedValue({ data: { ok: true } })
    await exhaust()
    await vi.advanceTimersByTimeAsync(4999)
    expect(probe).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(probe).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(9999)
    expect(a).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
    expect(Stream.all).toHaveLength(2)
    expect(probe.mock.calls[0][0]).toMatch(/auth\/connection-check$/)
    await vi.advanceTimersByTimeAsync(100000)
    expect(probe).toHaveBeenCalledTimes(2)
  })
  it.each([401, 403, 404, 422, 500])('does not probe for status %s', async status => {
    const probe = vi.spyOn(axios, 'get')
    // Mark refresh as already attempted to isolate the terminal response contract.
    fail(status)
    await api.get('/list', { _retry: true } as any).catch(() => {})
    await vi.advanceTimersByTimeAsync(100000)
    expect(probe).not.toHaveBeenCalled()
  })
  it('merges concurrent failures into one loop and recovers on an ordinary GET', async () => {
    const resync = subscriber(); Stream.all[0].open()
    const probe = vi.spyOn(axios, 'get').mockRejectedValue(new Error('down'))
    fail(503)
    const requests = Promise.all([api.get('/a').catch(() => {}), api.get('/b').catch(() => {})])
    await vi.advanceTimersByTimeAsync(15500); await requests
    expect(probe).toHaveBeenCalledTimes(1)
    api.defaults.adapter = async config => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config })
    await api.get('/ok')
    expect(resync).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100000)
    expect(probe).toHaveBeenCalledTimes(1)
  })
  it.each(['online', 'focus', 'visibilitychange'])('keeps probing after %s until an actual response succeeds', async name => {
    const resync = subscriber(); Stream.all[0].open()
    const probe = vi.spyOn(axios, 'get').mockRejectedValue(new Error('still down'))
    await exhaust()
    if (name === 'visibilitychange') {
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
      document.dispatchEvent(new Event(name))
    } else window.dispatchEvent(new Event(name))
    expect(resync).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(probe).toHaveBeenCalledTimes(1)
    probe.mockResolvedValue({ data: {} })
    await vi.advanceTimersByTimeAsync(10000)
    expect(probe).toHaveBeenCalledTimes(2)
    expect(resync).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(100000)
    expect(probe).toHaveBeenCalledTimes(2)
  })
  it.each([false, true])('resyncs before SSE opens, then catches up on open (previously opened: %s)', async opened => {
    const resync = subscriber(); if (opened) Stream.all[0].open(); Stream.all[0].readyState = 0
    vi.spyOn(axios, 'get').mockResolvedValue({ data: {} })
    await exhaust(); await vi.advanceTimersByTimeAsync(5000)
    expect(Stream.all).toHaveLength(2)
    expect(Stream.all[0].readyState).toBe(Stream.CLOSED)
    expect(resync).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(30000)
    expect(resync).toHaveBeenCalledTimes(1)
    Stream.all[1].open()
    expect(resync).toHaveBeenCalledTimes(2)
  })
  it.each([false, true])('refreshes an expired probe token (transient refresh failure: %s)', async transient => {
    sessionStorage.setItem('fg_refresh_token', 'refresh-old')
    sessionStorage.setItem('fg_access_token', 'access-old')
    window.__accessToken__ = 'access-old'
    const resync = subscriber(); Stream.all[0].open()
    const unauthorized = new AxiosError('expired', undefined, undefined, undefined,
      { status: 401, data: {}, statusText: '', headers: {}, config: {} as any })
    const probe = vi.spyOn(axios, 'get').mockRejectedValueOnce(unauthorized)
    const refresh = vi.spyOn(axios, 'post')
    if (transient) {
      refresh.mockRejectedValueOnce(new Error('server restarting'))
      probe.mockRejectedValueOnce(unauthorized)
    }
    refresh.mockResolvedValue({ data: { access_token: 'access-new', refresh_token: 'refresh-new' } })
    probe.mockResolvedValue({ data: {} })
    try {
      await exhaust(); await vi.advanceTimersByTimeAsync(5000)
      if (transient) {
        expect(resync).not.toHaveBeenCalled()
        expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-old')
        await vi.advanceTimersByTimeAsync(10000)
      }
      expect(refresh).toHaveBeenCalledTimes(transient ? 2 : 1)
      expect(probe.mock.calls.at(-1)?.[1]?.headers?.Authorization).toBe('Bearer access-new')
      expect(resync).toHaveBeenCalledTimes(1)
      const count = probe.mock.calls.length
      await vi.advanceTimersByTimeAsync(100000)
      expect(probe).toHaveBeenCalledTimes(count)
    } finally {
      sessionStorage.removeItem('fg_refresh_token')
      sessionStorage.removeItem('fg_access_token')
      delete window.__accessToken__
    }
  })
  it.each(['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT'])('starts after exhausted %s', async code => {
    const probe = vi.spyOn(axios, 'get').mockResolvedValue({ data: {} })
    fail(undefined, code)
    const request = api.get('/list').catch(() => {})
    await vi.advanceTimersByTimeAsync(code === 'ERR_NETWORK' ? 15500 : 5500)
    await request
    expect(probe).toHaveBeenCalledTimes(1)
  })
  it.each(['post', 'patch', 'delete'] as const)('probes after %s fails without replaying the write', async method => {
    const probe = vi.spyOn(axios, 'get').mockResolvedValue({ data: {} })
    fail(503)
    const adapter = vi.fn(api.defaults.adapter as any)
    api.defaults.adapter = adapter
    await api.request({ method, url: '/write' }).catch(() => {})
    await vi.advanceTimersByTimeAsync(5000)
    expect(probe).toHaveBeenCalledTimes(1)
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(probe.mock.calls[0][0]).toMatch(/auth\/connection-check$/)
  })
  it('uses 5,10,20,30,30 second delays and ignores a late completion after stop', async () => {
    const probe = vi.fn().mockRejectedValue(new Error('down'))
    const recovered = vi.fn()
    const loop = createConnectionRecovery(probe, recovered)
    stops.push(loop.stop)
    loop.start(); loop.start()
    for (const [i, delay] of [5000,10000,20000,30000,30000].entries()) {
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(probe).toHaveBeenCalledTimes(i)
      await vi.advanceTimersByTimeAsync(1)
      expect(probe).toHaveBeenCalledTimes(i + 1)
    }
    let resolve!: () => void
    probe.mockImplementationOnce(() => new Promise<void>(r => { resolve = r }))
    await vi.advanceTimersByTimeAsync(30000)
    loop.success(); resolve(); await Promise.resolve()
    expect(recovered).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100000)
    expect(probe).toHaveBeenCalledTimes(6)
  })
})
