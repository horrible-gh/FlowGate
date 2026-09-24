import { describe, expect, it } from 'vitest'
import { resolveSafeReturnPath } from '@shared/auth'

// T0005 §5: resolveSafeReturnPath is the single allow-list gate shared by the login
// app's post-auth redirect and (indirectly, by contract) client_gui's guard. It must
// accept only same-origin paths under an allow-listed prefix and reject everything
// that could turn /login into an open redirect.
describe('resolveSafeReturnPath', () => {
  it('accepts the allow-listed /app prefix', () => {
    expect(resolveSafeReturnPath('/app')).toBe('/app')
    expect(resolveSafeReturnPath('/app/')).toBe('/app/')
    expect(resolveSafeReturnPath('/app/projects?tab=x')).toBe('/app/projects?tab=x')
  })

  it('rejects a missing or empty next', () => {
    expect(resolveSafeReturnPath(null)).toBeNull()
    expect(resolveSafeReturnPath(undefined)).toBeNull()
    expect(resolveSafeReturnPath('')).toBeNull()
  })

  it('rejects protocol-relative and absolute external URLs', () => {
    expect(resolveSafeReturnPath('//evil.example.com/app')).toBeNull()
    expect(resolveSafeReturnPath('https://evil.example.com/app')).toBeNull()
    expect(resolveSafeReturnPath('javascript:alert(1)')).toBeNull()
  })

  it('rejects paths outside the allow-list, including a lookalike prefix', () => {
    expect(resolveSafeReturnPath('/main')).toBeNull()
    expect(resolveSafeReturnPath('/settings')).toBeNull()
    expect(resolveSafeReturnPath('/apple')).toBeNull()
  })

  it('rejects dot-segment traversal (literal, backslash, and percent-encoded) out of /app', () => {
    // A raw string-prefix check would accept all three (they start with "/app/"), but
    // window.location.replace() resolves dot-segments — including %2e%2e, per the
    // WHATWG URL spec — before navigating, landing outside /app.
    expect(resolveSafeReturnPath('/app/../settings')).toBeNull()
    expect(resolveSafeReturnPath('/app/%2e%2e/settings')).toBeNull()
    expect(resolveSafeReturnPath('/app\\..\\settings')).toBeNull()
  })

  it('rejects a backslash-based cross-origin redirect the leading "//" check alone would miss', () => {
    // "/\evil.example.com/app" does not start with "//", but the WHATWG URL parser
    // treats a leading backslash as a path separator for http(s), so it resolves to
    // origin http://evil.example.com with pathname /app — a lookalike-safe pathname on
    // the wrong origin.
    expect(resolveSafeReturnPath('/\\evil.example.com/app')).toBeNull()
  })
})
