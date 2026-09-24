import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authRedirect } from '@login/stores/auth'

// T0005 §5: authRedirect.toDashboard is the single place every login-success path
// (password, TOTP, backup code, password-change) calls through. Unlike
// client/tests/stores/auth.spec.ts (which mocks toDashboard away entirely to test the
// state machine), this spec exercises the real redirect-target computation so the
// /app return-path contract and the unchanged /main default are both covered.
describe('authRedirect.toDashboard', () => {
  beforeEach(() => {
    // jsdom's real Location throws on navigation; swap in a plain object so `.replace`
    // is observable instead of erroring the test.
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { origin: string; search: string; replace: (url: string) => void } }).location = {
      origin: 'http://localhost',
      search: '',
      replace: vi.fn(),
    }
  })

  it('defaults to /main when there is no next param (unchanged v0.1 behavior)', () => {
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('returns to the requested /app path when next is allow-listed', () => {
    window.location.search = `?next=${encodeURIComponent('/app/projects?tab=x')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/app/projects?tab=x')
  })

  it('falls back to /main when next is outside the allow-list', () => {
    window.location.search = `?next=${encodeURIComponent('/main')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('falls back to /main when next is a protocol-relative external URL', () => {
    window.location.search = `?next=${encodeURIComponent('//evil.example.com/app')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('falls back to /main when next dot-segments its way out of /app', () => {
    window.location.search = `?next=${encodeURIComponent('/app/../settings')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('falls back to /main when next is a backslash-based cross-origin redirect', () => {
    window.location.search = `?next=${encodeURIComponent('/\\evil.example.com/app')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })
})
