import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02gui.0031 T0005 ④ — a proactive token rotation that failed without a verdict (network error, timeout, 5xx)
// stopped the timer: only a focus / visibility return or the next request's 401 tried again, so a screen watched without
// leaving it ran into an expired token. It is now retried with a doubling, capped wait and a bounded count, and the
// browser going online again rotates at once. A rejected refresh (401/403) is not retried (the login path stays), and
// every try goes through the one in-flight rotation.

const postMock = vi.fn()

vi.mock('axios', async (importOriginal) => {
  const actual = (await importOriginal()) as any
  return { ...actual, default: { ...actual.default, post: (...args: unknown[]) => postMock(...args) } }
})

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ sub: 'u1', exp }))
  return `${header}.${payload}.sig`
}

const networkError = () => Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
const rejected = (status: number) => Object.assign(new Error('Request failed'), { response: { status } })
const fresh = async () => ({ data: { access_token: makeJwt(60), refresh_token: 'refresh-next' } })

function signIn(lifetimeSeconds: number) {
  const token = makeJwt(lifetimeSeconds)
  ;(window as { __accessToken__?: string }).__accessToken__ = token
  sessionStorage.setItem('fg_access_token', token)
  sessionStorage.setItem('fg_refresh_token', 'refresh-1')
}

beforeEach(() => {
  vi.useFakeTimers()
  // A whole second: the token's exp (seconds) then lands exactly where the timings below expect it.
  vi.setSystemTime(new Date('2026-11-16T00:00:00Z'))
  localStorage.clear()
  sessionStorage.clear()
  postMock.mockReset()
  delete (window as { __accessToken__?: string }).__accessToken__
})

afterEach(async () => {
  const { stopTokenAutoRefresh } = await import('@shared/api')
  stopTokenAutoRefresh()
  await vi.runOnlyPendingTimersAsync()
  vi.useRealTimers()
})

describe('v02gui.0031 T0005 ④ proactive refresh retries after a failure', () => {
  it('the retry wait doubles from the base and is capped', async () => {
    const { PROACTIVE_RETRY_BASE_MS, PROACTIVE_RETRY_MAX_DELAY_MS, proactiveRetryDelayMs } = await import('@shared/api')
    expect(proactiveRetryDelayMs(1)).toBe(PROACTIVE_RETRY_BASE_MS)
    expect(proactiveRetryDelayMs(2)).toBe(PROACTIVE_RETRY_BASE_MS * 2)
    expect(proactiveRetryDelayMs(3)).toBe(PROACTIVE_RETRY_BASE_MS * 4)
    expect(proactiveRetryDelayMs(20)).toBe(PROACTIVE_RETRY_MAX_DELAY_MS)
  })

  it('a failed rotation is tried again without focus or a request, and the normal schedule resumes after success', async () => {
    const { startTokenAutoRefresh, PROACTIVE_RETRY_BASE_MS } = await import('@shared/api')
    signIn(20) // rotation due at exp - 15s = 5s
    postMock.mockRejectedValueOnce(networkError()).mockRejectedValueOnce(networkError()).mockImplementation(fresh)
    const rotated = vi.fn()
    window.addEventListener('fg:access_token_refreshed', rotated)

    startTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(postMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(PROACTIVE_RETRY_BASE_MS - 1)
    expect(postMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(postMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(PROACTIVE_RETRY_BASE_MS * 2)
    expect(postMock).toHaveBeenCalledTimes(3)
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-next')
    // The SSE hub hears the rotation (T0005 ①).
    expect(rotated).toHaveBeenCalledTimes(1)
    window.removeEventListener('fg:access_token_refreshed', rotated)

    // Back on the normal schedule: the next rotation at exp(60s) - 15s, not at a retry wait.
    await vi.advanceTimersByTimeAsync(44_000)
    expect(postMock).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(postMock).toHaveBeenCalledTimes(4)
  })

  it('stops after PROACTIVE_RETRY_MAX retries', async () => {
    const { startTokenAutoRefresh, PROACTIVE_RETRY_MAX } = await import('@shared/api')
    signIn(20)
    postMock.mockRejectedValue(networkError())

    startTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(60 * 60_000)

    expect(postMock).toHaveBeenCalledTimes(1 + PROACTIVE_RETRY_MAX)
    // Tokens are kept: a failure without a verdict never ends the session.
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-1')
  })

  it.each([401, 403])('a refresh rejected with %i is not retried and leaves the session to the login path', async (status) => {
    const { startTokenAutoRefresh } = await import('@shared/api')
    signIn(20)
    postMock.mockRejectedValue(rejected(status))
    const href = window.location.href

    startTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(10 * 60_000)

    expect(postMock).toHaveBeenCalledTimes(1)
    expect(window.location.href).toBe(href)
  })

  it('online after a failure rotates at once and starts a new bounded series', async () => {
    const { startTokenAutoRefresh, PROACTIVE_RETRY_MAX } = await import('@shared/api')
    signIn(20)
    postMock.mockRejectedValue(networkError())
    startTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(60 * 60_000)
    expect(postMock).toHaveBeenCalledTimes(1 + PROACTIVE_RETRY_MAX) // exhausted

    postMock.mockImplementation(fresh)
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(postMock).toHaveBeenCalledTimes(2 + PROACTIVE_RETRY_MAX)
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-next')
  })

  it('online with a healthy token rotates nothing; online during a rotation joins it', async () => {
    const { startTokenAutoRefresh } = await import('@shared/api')
    signIn(120)
    startTokenAutoRefresh()
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(postMock).not.toHaveBeenCalled()

    // A rotation in flight (the proactive timer at exp - 15s) and an online signal meanwhile: one POST.
    let answer!: (value: unknown) => void
    postMock.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    await vi.advanceTimersByTimeAsync(105_000)
    expect(postMock).toHaveBeenCalledTimes(1)
    // The token is now within the skew: online asks for a rotation, which joins the one in flight.
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(postMock).toHaveBeenCalledTimes(1)
    answer(await fresh())
    await vi.advanceTimersByTimeAsync(0)
    expect(postMock).toHaveBeenCalledTimes(1)
  })

  it('logout stops the retries and the online listener', async () => {
    const { startTokenAutoRefresh, stopTokenAutoRefresh } = await import('@shared/api')
    signIn(20)
    postMock.mockRejectedValue(networkError())
    startTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(postMock).toHaveBeenCalledTimes(1)

    stopTokenAutoRefresh()
    await vi.advanceTimersByTimeAsync(60 * 60_000)
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(postMock).toHaveBeenCalledTimes(1)
  })
})
