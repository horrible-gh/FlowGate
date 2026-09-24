import { beforeEach, describe, expect, it } from 'vitest'
import { installAuthGuard } from '@/core/router/guard'

type GuardFn = () => boolean
type FakeRouter = { beforeEach: (fn: GuardFn) => void }

function captureGuard(): GuardFn {
  let captured: GuardFn | null = null
  const fakeRouter: FakeRouter = {
    beforeEach: (fn) => {
      captured = fn
    },
  }
  installAuthGuard(fakeRouter as unknown as Parameters<typeof installAuthGuard>[0])
  if (!captured) throw new Error('guard was not registered')
  return captured
}

describe('installAuthGuard', () => {
  beforeEach(() => {
    sessionStorage.clear()
    delete window.__accessToken__
    // jsdom's real Location throws on navigation; swap in a plain object so the guard's
    // `window.location.href = ...` redirect is observable instead of erroring the test.
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { pathname: string; search: string; href: string } }).location = {
      pathname: '/app/projects',
      search: '?tab=x',
      href: '',
    }
  })

  it('allows navigation when an in-memory access token is present', () => {
    window.__accessToken__ = 'token-1'
    const guard = captureGuard()
    expect(guard()).toBe(true)
    expect(window.location.href).toBe('')
  })

  it('recovers the token from sessionStorage', () => {
    sessionStorage.setItem('fg_access_token', 'token-2')
    const guard = captureGuard()
    expect(guard()).toBe(true)
  })

  it('blocks navigation and redirects to /login with the current /app path as next', () => {
    const guard = captureGuard()
    expect(guard()).toBe(false)
    expect(window.location.href).toBe(`/login?next=${encodeURIComponent('/app/projects?tab=x')}`)
  })
})
