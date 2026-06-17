import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// group 0028 / T0004: the access-token lifetime is deliberately short (operator .env, e.g.
// 1 minute). A purely reactive 401→refresh loop surfaces a 401 in the console on every expiry
// and can leave the SSE stream pinned to a dead token. The client now rotates the token a few
// seconds BEFORE it expires so requests never carry an expired token. These tests lock in the
// proactive-rotation timing and the up-front rotation used at app startup.

const postMock = vi.fn()

vi.mock('axios', async (importOriginal) => {
  const actual = (await importOriginal()) as any
  // Override only .post (used by the refresh call). Keep .create (api instance) and the
  // AxiosError named export intact. api.ts never calls axios() directly.
  return { ...actual, default: { ...actual.default, post: (...args: unknown[]) => postMock(...args) } }
})

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ sub: 'u1', exp }))
  return `${header}.${payload}.sig`
}

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  sessionStorage.clear()
  postMock.mockReset()
  delete (window as { __accessToken__?: string }).__accessToken__
})

afterEach(async () => {
  const { stopTokenAutoRefresh } = await import('@shared/api')
  stopTokenAutoRefresh()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('proactive token refresh (T0004)', () => {
  it('rotates the token shortly before it expires, without any 401', async () => {
    const { startTokenAutoRefresh } = await import('@shared/api')
    const token = makeJwt(60) // 60s lifetime
    ;(window as { __accessToken__?: string }).__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)
    sessionStorage.setItem('fg_refresh_token', 'refresh-1')
    // Generate the rotated token per-call so its exp is relative to the (advanced) clock,
    // mirroring the server. A frozen exp would make every re-arm fire immediately.
    postMock.mockImplementation(async () => ({
      data: { access_token: makeJwt(60), refresh_token: 'refresh-2' },
    }))

    startTokenAutoRefresh()

    // Nothing should fire well before the skew window (refresh is scheduled at exp - 15s = 45s).
    await vi.advanceTimersByTimeAsync(30000)
    expect(postMock).not.toHaveBeenCalled()

    // Cross the skew boundary → exactly one proactive rotation.
    await vi.advanceTimersByTimeAsync(20000)
    expect(postMock).toHaveBeenCalledTimes(1)
    expect(String(postMock.mock.calls[0][0])).toContain('/auth/refresh')
    expect(postMock.mock.calls[0][1]).toEqual({ refresh_token: 'refresh-1' })
    // New tokens were stored (refresh token rotated).
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-2')
  })

  it('ensureValidAccessToken rotates up-front when the stored token is already expired', async () => {
    const { ensureValidAccessToken } = await import('@shared/api')
    const expired = makeJwt(-10) // already expired
    ;(window as { __accessToken__?: string }).__accessToken__ = expired
    sessionStorage.setItem('fg_access_token', expired)
    sessionStorage.setItem('fg_refresh_token', 'refresh-1')
    const fresh = makeJwt(60)
    postMock.mockResolvedValue({ data: { access_token: fresh, refresh_token: 'refresh-2' } })

    const ok = await ensureValidAccessToken()

    expect(ok).toBe(true)
    expect(postMock).toHaveBeenCalledTimes(1)
    expect((window as { __accessToken__?: string }).__accessToken__).toBe(fresh)
  })

  it('ensureValidAccessToken does not rotate a token that is still comfortably valid', async () => {
    const { ensureValidAccessToken } = await import('@shared/api')
    const token = makeJwt(120)
    ;(window as { __accessToken__?: string }).__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)
    sessionStorage.setItem('fg_refresh_token', 'refresh-1')

    const ok = await ensureValidAccessToken()

    expect(ok).toBe(true)
    expect(postMock).not.toHaveBeenCalled()
  })
})
