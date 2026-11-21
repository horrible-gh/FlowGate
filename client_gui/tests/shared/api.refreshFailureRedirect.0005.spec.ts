import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// (last rejection, finding 3): a stale access token plus a missing/invalid
// refresh token used to always bounce the browser to '/', even from an /app/* deep link —
// throwing away the return path before installAuthGuard's /login?next=/app/... logic ever
// got a chance to run. buildRefreshFailureRedirect() and the 401 interceptor that calls it
// reuse resolveSafeReturnPath (the same allow-list installAuthGuard and the sign-in page
// enforce) so an /app location survives the round trip.
//
// v0.1 is gone — /main is no longer allow-listed (the server redirects
// it to /app) and every location outside the allow-list goes to plain /login.

const postMock = vi.fn()

vi.mock('axios', async (importOriginal) => {
  const actual = (await importOriginal()) as any
  // Override only .post (used by the refresh call). api.ts never calls axios() directly.
  return { ...actual, default: { ...actual.default, post: (...args: unknown[]) => postMock(...args) } }
})

function setLocation(pathname: string, search = ''): void {
  delete (window as unknown as { location?: unknown }).location
  ;(window as unknown as {
    location: { pathname: string; search: string; href: string; origin: string }
  }).location = {
    pathname,
    search,
    href: '',
    origin: 'http://localhost',
  }
}

beforeEach(() => {
  vi.useRealTimers()
  localStorage.clear()
  sessionStorage.clear()
  postMock.mockReset()
  delete (window as { __accessToken__?: string }).__accessToken__
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('buildRefreshFailureRedirect (0005)', () => {
  it('preserves an /app/* deep link as next on /login', async () => {
    const { buildRefreshFailureRedirect } = await import('@shared/api')
    setLocation('/app/projects', '?tab=x')
    expect(buildRefreshFailureRedirect()).toBe(
      `/login?next=${encodeURIComponent('/app/projects?tab=x')}`,
    )
  })

  it('falls back to plain /login for locations outside the allow-list (0024)', async () => {
    const { buildRefreshFailureRedirect } = await import('@shared/api')
    setLocation('/some-other-page')
    expect(buildRefreshFailureRedirect()).toBe('/login')
  })

  it('no longer carries a removed v0.1 /main location as next (0024)', async () => {
    const { buildRefreshFailureRedirect } = await import('@shared/api')
    setLocation('/main')
    expect(buildRefreshFailureRedirect()).toBe('/login')
  })
})

describe('401 refresh-failure redirect round trip (0005)', () => {
  it('sends an /app deep link with a stale token and no refresh token to /login?next=/app/...', async () => {
    const api = (await import('@shared/api')).default
    setLocation('/app/projects', '?tab=x')
    // No fg_refresh_token in storage: runRefresh() throws ERR_NO_REFRESH_TOKEN before ever
    // reaching axios.post, exactly like client_gui's main.ts bootstrap hitting a stale
    // sessionStorage access token whose refresh token is missing/expired.
    ;(window as { __accessToken__?: string }).__accessToken__ = 'stale-access-token'
    sessionStorage.setItem('fg_access_token', 'stale-access-token')

    const rejected = (api.interceptors.response as unknown as {
      handlers: { rejected: (error: unknown) => Promise<unknown> }[]
    }).handlers[0].rejected

    const error = {
      response: { status: 401, data: {} },
      config: { headers: {}, url: '/auth/me' },
    }

    await expect(rejected(error)).rejects.toBeTruthy()

    expect(postMock).not.toHaveBeenCalled()
    expect(window.location.href).toBe(`/login?next=${encodeURIComponent('/app/projects?tab=x')}`)
    expect(sessionStorage.getItem('fg_access_token')).toBeNull()
    expect((window as { __accessToken__?: string }).__accessToken__).toBeUndefined()
  })

  it('sends an invalid-refresh-token failure on /main to plain /login (0024)', async () => {
    const api = (await import('@shared/api')).default
    setLocation('/main')
    ;(window as { __accessToken__?: string }).__accessToken__ = 'stale-access-token'
    sessionStorage.setItem('fg_access_token', 'stale-access-token')
    sessionStorage.setItem('fg_refresh_token', 'invalid-refresh-token')
    postMock.mockRejectedValue({ response: { status: 401, data: { detail: 'invalid_refresh' } } })

    const rejected = (api.interceptors.response as unknown as {
      handlers: { rejected: (error: unknown) => Promise<unknown> }[]
    }).handlers[0].rejected

    const error = {
      response: { status: 401, data: {} },
      config: { headers: {}, url: '/auth/me' },
    }

    await expect(rejected(error)).rejects.toBeTruthy()

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(window.location.href).toBe('/login')
  })

  it('sends an invalid-refresh-token failure on a location outside the allow-list to /login (0024)', async () => {
    const api = (await import('@shared/api')).default
    setLocation('/some-other-page')
    ;(window as { __accessToken__?: string }).__accessToken__ = 'stale-access-token'
    sessionStorage.setItem('fg_access_token', 'stale-access-token')
    sessionStorage.setItem('fg_refresh_token', 'invalid-refresh-token')
    postMock.mockRejectedValue({ response: { status: 401, data: { detail: 'invalid_refresh' } } })

    const rejected = (api.interceptors.response as unknown as {
      handlers: { rejected: (error: unknown) => Promise<unknown> }[]
    }).handlers[0].rejected

    const error = {
      response: { status: 401, data: {} },
      config: { headers: {}, url: '/auth/me' },
    }

    await expect(rejected(error)).rejects.toBeTruthy()

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(window.location.href).toBe('/login')
  })
})

// B1: a refresh that never reached a verdict (network error, timeout, 5xx —
// e.g. the server restarting) used to take the same path as a rejected one: every token was
// deleted and the browser sent to /login although the session was valid. Only a definitive
// answer (401/403 from /auth/refresh, or no refresh token) may end the session now.
describe('401 refresh failure without a verdict keeps the session (v02gui.0017 B1)', () => {
  const rejectedHandler = async () => {
    const api = (await import('@shared/api')).default
    return (api.interceptors.response as unknown as {
      handlers: { rejected: (error: unknown) => Promise<unknown> }[]
    }).handlers[0].rejected
  }

  const arrangeSession = () => {
    setLocation('/app/projects', '?tab=x')
    ;(window as { __accessToken__?: string }).__accessToken__ = 'stale-access-token'
    sessionStorage.setItem('fg_access_token', 'stale-access-token')
    localStorage.setItem('fg_refresh_token', 'valid-refresh-token')
  }

  const expectSessionKept = () => {
    expect(window.location.href).toBe('')
    expect(sessionStorage.getItem('fg_access_token')).toBe('stale-access-token')
    expect((window as { __accessToken__?: string }).__accessToken__).toBe('stale-access-token')
    expect(localStorage.getItem('fg_refresh_token')).toBe('valid-refresh-token')
  }

  const original401 = () => ({ response: { status: 401, data: {} }, config: { headers: {}, url: '/auth/me' } })

  it('keeps every token and stays on the page when /auth/refresh fails with a network error', async () => {
    const rejected = await rejectedHandler()
    arrangeSession()
    const networkError = { code: 'ERR_NETWORK', message: 'Network Error' }
    postMock.mockRejectedValue(networkError)

    await expect(rejected(original401())).rejects.toBe(networkError)

    expect(postMock).toHaveBeenCalledTimes(1)
    expectSessionKept()
  })

  it('keeps the session when /auth/refresh times out', async () => {
    const rejected = await rejectedHandler()
    arrangeSession()
    postMock.mockRejectedValue({ code: 'ECONNABORTED', message: 'timeout of 30000ms exceeded' })

    await expect(rejected(original401())).rejects.toBeTruthy()

    expectSessionKept()
  })

  it('keeps the session when /auth/refresh answers 503', async () => {
    const rejected = await rejectedHandler()
    arrangeSession()
    postMock.mockRejectedValue({ response: { status: 503, data: {} } })

    await expect(rejected(original401())).rejects.toBeTruthy()

    expectSessionKept()
  })

  it('still ends the session when /auth/refresh answers 401 (session revoked)', async () => {
    const rejected = await rejectedHandler()
    arrangeSession()
    postMock.mockRejectedValue({ response: { status: 401, data: { detail: 'session_revoked' } } })

    await expect(rejected(original401())).rejects.toBeTruthy()

    expect(window.location.href).toBe(`/login?next=${encodeURIComponent('/app/projects?tab=x')}`)
    expect(sessionStorage.getItem('fg_access_token')).toBeNull()
    expect(localStorage.getItem('fg_refresh_token')).toBeNull()
  })

  it('isAuthRejection tells a verdict apart from a failed connection', async () => {
    const { isAuthRejection } = await import('@shared/api')
    expect(isAuthRejection({ response: { status: 401 } })).toBe(true)
    expect(isAuthRejection({ response: { status: 403 } })).toBe(true)
    expect(isAuthRejection({ code: 'ERR_NO_REFRESH_TOKEN' })).toBe(true)
    expect(isAuthRejection({ code: 'ERR_NETWORK' })).toBe(false)
    expect(isAuthRejection({ code: 'ECONNABORTED' })).toBe(false)
    expect(isAuthRejection({ response: { status: 502 } })).toBe(false)
    expect(isAuthRejection({ response: { status: 503 } })).toBe(false)
    expect(isAuthRejection(null)).toBe(false)
  })
})
