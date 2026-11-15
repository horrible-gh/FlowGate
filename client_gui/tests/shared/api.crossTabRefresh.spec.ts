import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  REFRESH_LOCK_NAME,
  SHARED_REFRESH_KEY,
  publishSharedRefresh,
  readSharedRefresh,
  refreshIsShared,
  shouldReuseSiblingRefresh,
  withRefreshLock,
} from '@shared/api'

// Regression (group 0016 / NR0003 Finding C): with "remember me" the refresh token lives
// in shared localStorage, so two tabs that both 401 after idle each POST /auth/refresh with
// the same token. The server rotates on the first call and treats the second (retired jti)
// as token reuse, revoking EVERY session -> both tabs are force-logged-out. The fix
// serializes refresh across tabs with a Web Lock and lets the non-holder tab reuse the
// holder's freshly rotated token instead of re-POSTing the retired one.

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Finding C — refreshIsShared / publishSharedRefresh gating', () => {
  it('treats the session as shared only when the refresh token is in localStorage', () => {
    expect(refreshIsShared()).toBe(false)
    sessionStorage.setItem('fg_refresh_token', 'sess') // session-only login
    expect(refreshIsShared()).toBe(false)
    localStorage.setItem('fg_refresh_token', 'remembered')
    expect(refreshIsShared()).toBe(true)
  })

  it('publishes the shared result only in the remember-me case', () => {
    // Session-only: never widen the access token into shared localStorage.
    publishSharedRefresh('acc1', 'ref1')
    expect(readSharedRefresh()).toBeNull()

    localStorage.setItem('fg_refresh_token', 'remembered')
    publishSharedRefresh('acc2', 'ref2')
    const shared = readSharedRefresh()
    expect(shared?.access).toBe('acc2')
    expect(shared?.refresh).toBe('ref2')
    expect(typeof shared?.ts).toBe('number')
  })

  it('readSharedRefresh tolerates malformed storage', () => {
    localStorage.setItem(SHARED_REFRESH_KEY, '{not json')
    expect(readSharedRefresh()).toBeNull()
  })
})

describe('Finding C — shouldReuseSiblingRefresh decision', () => {
  const startedAt = 1_000

  it('reuses a fresh result published by a sibling for the current refresh token', () => {
    const shared = { access: 'acc', refresh: 'rotated', ts: startedAt }
    expect(shouldReuseSiblingRefresh(shared, 'rotated', startedAt)).toBe(true)
  })

  it('does not reuse a result published before this refresh began (stale)', () => {
    const shared = { access: 'acc', refresh: 'rotated', ts: startedAt - 1 }
    expect(shouldReuseSiblingRefresh(shared, 'rotated', startedAt)).toBe(false)
  })

  it('does not reuse when the stored refresh token no longer matches', () => {
    const shared = { access: 'acc', refresh: 'someoneElse', ts: startedAt }
    expect(shouldReuseSiblingRefresh(shared, 'rotated', startedAt)).toBe(false)
  })

  it('does not reuse a null / access-less result', () => {
    expect(shouldReuseSiblingRefresh(null, 'rotated', startedAt)).toBe(false)
    expect(shouldReuseSiblingRefresh({ access: '', refresh: 'rotated', ts: startedAt }, 'rotated', startedAt)).toBe(false)
  })
})

describe('Finding C — withRefreshLock', () => {
  it('runs the work under the cross-tab Web Lock when available', async () => {
    const request = vi.fn(async (_name: string, cb: () => Promise<unknown>) => cb())
    vi.stubGlobal('navigator', { locks: { request } })

    const result = await withRefreshLock(async () => 'done')

    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][0]).toBe(REFRESH_LOCK_NAME)
    expect(result).toBe('done')
  })

  it('serializes concurrent holders so only one runs the critical section at a time', async () => {
    // A minimal exclusive lock: queue callbacks and run them one after another.
    let chain: Promise<unknown> = Promise.resolve()
    const request = vi.fn((_name: string, cb: () => Promise<unknown>) => {
      const run = chain.then(() => cb())
      chain = run.catch(() => undefined)
      return run
    })
    vi.stubGlobal('navigator', { locks: { request } })

    let active = 0
    let maxActive = 0
    const critical = async () => {
      active += 1
      maxActive = Math.max(maxActive, active)
      await Promise.resolve()
      active -= 1
    }

    await Promise.all([
      withRefreshLock(critical),
      withRefreshLock(critical),
      withRefreshLock(critical),
    ])

    expect(maxActive).toBe(1) // never two refreshes in flight together
  })

  it('falls back to running inline when the Web Locks API is unavailable', async () => {
    vi.stubGlobal('navigator', {}) // no .locks (older browsers / jsdom)
    await expect(withRefreshLock(async () => 'inline')).resolves.toBe('inline')
  })
})
