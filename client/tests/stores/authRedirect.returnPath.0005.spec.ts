import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authRedirect } from '@login/stores/auth'

// T0005 §5 / T0009 §5: authRedirect.toDashboard is the single place every login-success
// path (password, TOTP, backup code, password-change) calls through. Unlike
// client/tests/stores/auth.spec.ts (which mocks toDashboard away entirely to test the
// state machine), this spec exercises the real redirect-target computation so the
// next > fg_ui > default priority chain is covered end to end.
describe('authRedirect.toDashboard', () => {
  beforeEach(() => {
    localStorage.clear()
    // jsdom's real Location throws on navigation; swap in a plain object so `.replace`
    // is observable instead of erroring the test.
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { origin: string; search: string; replace: (url: string) => void } }).location = {
      origin: 'http://localhost',
      search: '',
      replace: vi.fn(),
    }
  })

  it('defaults to /main when there is no next param and no stored fg_ui (unchanged v0.1 behavior)', () => {
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('returns to the requested /app path when next is allow-listed', () => {
    window.location.search = `?next=${encodeURIComponent('/app/projects?tab=x')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/app/projects?tab=x')
  })

  it('falls back to /main when next is a protocol-relative external URL', () => {
    window.location.search = `?next=${encodeURIComponent('//evil.example.com/app')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('falls back to /main when next is a backslash-based cross-origin redirect', () => {
    window.location.search = `?next=${encodeURIComponent('/\\evil.example.com/app')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('uses the stored fg_ui preference when there is no next param (T0009 §4/§5)', () => {
    localStorage.setItem('fg_ui', 'app')
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/app')
  })

  it('an explicit next still wins over a stored fg_ui preference (T0009 §5 priority)', () => {
    localStorage.setItem('fg_ui', 'app')
    window.location.search = `?next=${encodeURIComponent('/main')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('ignores an invalid fg_ui value and falls back to /main (T0009 §10 regression: "잘못된 fg_ui 값")', () => {
    localStorage.setItem('fg_ui', 'not-a-real-value')
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })
})
