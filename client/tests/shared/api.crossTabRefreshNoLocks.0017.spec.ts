import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'

// flowgate.v02gui.0017 B6: `navigator.locks` exists only in a secure context (HTTPS, localhost).
// Over plain HTTP on a LAN address two remember-me tabs refreshed at the same moment (their
// proactive timers share the same expiry) and both POSTed the same refresh token; the server
// rotated on the first and revoked every session on the second (reuse). Without Web Locks the
// refresh now takes a localStorage mutex, and the waiting tab reuses the published rotation.
//
// Two tabs are two independent instances of the module sharing one localStorage.

const postMock = vi.fn()

vi.mock('axios', async (importOriginal) => {
  const actual = (await importOriginal()) as any
  return { ...actual, default: { ...actual.default, post: (...args: unknown[]) => postMock(...args) } }
})

type ApiModule = typeof import('@shared/api')

async function openTab(): Promise<ApiModule> {
  vi.resetModules()
  const mod = await import('@shared/api')
  // The retried original request stops at a stub adapter: nothing leaves the process.
  mod.default.defaults.adapter = async (config: InternalAxiosRequestConfig) =>
    ({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config })
  return mod
}

function reactiveRefresh(mod: ApiModule): Promise<unknown> {
  const rejected = (mod.default.interceptors.response as unknown as {
    handlers: { rejected: (error: unknown) => Promise<unknown> }[]
  }).handlers[0].rejected
  return rejected({ response: { status: 401, data: {} }, config: { headers: {}, url: '/auth/me', method: 'get' } })
}

beforeEach(() => {
  vi.useRealTimers()
  localStorage.clear()
  sessionStorage.clear()
  postMock.mockReset()
  vi.stubGlobal('navigator', {}) // plain-HTTP context: no Web Locks
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('B6 — refresh across tabs without Web Locks', () => {
  it('sends /auth/refresh once when two remember-me tabs refresh together', async () => {
    localStorage.setItem('fg_refresh_token', 'R0')
    postMock.mockImplementation(async (_url: string, body: { refresh_token: string }) => {
      await new Promise((resolve) => setTimeout(resolve, 150))
      return { data: { access_token: `A-after-${body.refresh_token}`, refresh_token: 'R1' } }
    })

    const tabA = await openTab()
    const tabB = await openTab()
    expect(tabA).not.toBe(tabB)

    const results = await Promise.all([reactiveRefresh(tabA), reactiveRefresh(tabB)])

    expect(results).toHaveLength(2)
    expect(postMock).toHaveBeenCalledTimes(1)
    expect(postMock.mock.calls[0][1]).toEqual({ refresh_token: 'R0' })
    expect(localStorage.getItem('fg_refresh_token')).toBe('R1')
    expect(localStorage.getItem(tabA.REFRESH_MUTEX_KEY)).toBeNull() // released
  })

  it('serializes the critical section of two owners on the storage mutex', async () => {
    const { withStorageRefreshMutex } = await openTab()
    let active = 0
    let maxActive = 0
    const critical = async () => {
      active += 1
      maxActive = Math.max(maxActive, active)
      await new Promise((resolve) => setTimeout(resolve, 30))
      active -= 1
    }

    await Promise.all([
      withStorageRefreshMutex(critical, 'tab-a'),
      withStorageRefreshMutex(critical, 'tab-b'),
    ])

    expect(maxActive).toBe(1)
  })

  it('takes over a claim left by a tab that died mid-refresh once it expires', async () => {
    const { withStorageRefreshMutex, REFRESH_MUTEX_KEY } = await openTab()
    localStorage.setItem(REFRESH_MUTEX_KEY, JSON.stringify({ owner: 'dead-tab', exp: Date.now() - 1 }))

    await expect(withStorageRefreshMutex(async () => 'ran', 'tab-a')).resolves.toBe('ran')
  })

  it('needs no mutex for a session-only login (per-tab refresh token)', async () => {
    const { withRefreshLock, REFRESH_MUTEX_KEY } = await openTab()
    sessionStorage.setItem('fg_refresh_token', 'per-tab')
    const seen: (string | null)[] = []
    await withRefreshLock(async () => {
      seen.push(localStorage.getItem(REFRESH_MUTEX_KEY))
    })
    expect(seen).toEqual([null])
  })
})
