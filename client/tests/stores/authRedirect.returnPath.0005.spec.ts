import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authRedirect } from '@login/stores/auth'

// With the v0.2 GUI removed, login returns to a safe v0.1 deep link or /main.
describe('authRedirect.toDashboard', () => {
  beforeEach(() => {
    localStorage.clear()
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { origin: string; search: string; replace: (url: string) => void } }).location = {
      origin: 'http://localhost',
      search: '',
      replace: vi.fn(),
    }
  })

  it('defaults to /main without a next parameter', () => {
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('returns to an allowed v0.1 deep link', () => {
    window.location.search = `?next=${encodeURIComponent('/settings/projects?tab=x')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/settings/projects?tab=x')
  })

  it('rejects a next path for the removed /app GUI', () => {
    window.location.search = `?next=${encodeURIComponent('/app/projects')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('ignores the obsolete fg_ui preference', () => {
    localStorage.setItem('fg_ui', 'app')
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })

  it('preserves an explicit v0.1 next path despite a stale preference', () => {
    localStorage.setItem('fg_ui', 'app')
    window.location.search = `?next=${encodeURIComponent('/main/?doc=example')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main/?doc=example')
  })

  it('rejects an external redirect target', () => {
    window.location.search = `?next=${encodeURIComponent('//evil.example.com/app')}`
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/main')
  })
})
