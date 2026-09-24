import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 0005 T#1 rework (last rejection, finding 3): a stale access token plus a missing/invalid
// refresh token used to always bounce the browser to '/', even from an /app/* deep link —
// throwing away the return path before installAuthGuard's /login?next=/app/... logic ever
// got a chance to run. buildRefreshFailureRedirect() and the 401 interceptor that now calls
// it instead reuse resolveSafeReturnPath (the same allow-list installAuthGuard and
// authRedirect.toDashboard already enforce) so an /app/* location survives the round trip
// while every other origin (v0.1 /main) keeps the prior unconditional '/'.

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

  it('falls back to / for locations outside the allow-list (unchanged v0.1 behavior)', async () => {
    const { buildRefreshFailureRedirect } = await import('@shared/api')
    setLocation('/main')
    expect(buildRefreshFailureRedirect()).toBe('/')
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

  it('sends an invalid-refresh-token failure on /main to / (unchanged v0.1 behavior)', async () => {
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
    expect(window.location.href).toBe('/')
  })
})
