import { describe, expect, it } from 'vitest'
import { resolveSafeReturnPath } from '@shared/auth'

// T0005 §5 / T0009 §6: resolveSafeReturnPath is the single allow-list gate shared by
// the login app's post-auth redirect and (indirectly, by contract) client_gui's guard.
// It must accept only same-origin paths under an allow-listed prefix and reject
// everything that could turn /login into an open redirect. T0009 §6's minimum allowed
// scope added /main and /settings to the previously /app-only allow-list so a next=
// deep link back into v0.1 (or the GUI-switch's own /main destination) round-trips too.
describe('resolveSafeReturnPath', () => {
  it('accepts the allow-listed /app prefix', () => {
    expect(resolveSafeReturnPath('/app')).toBe('/app')
    expect(resolveSafeReturnPath('/app/')).toBe('/app/')
    expect(resolveSafeReturnPath('/app/projects?tab=x')).toBe('/app/projects?tab=x')
  })

  it('accepts the allow-listed /main and /settings prefixes (T0009 §6)', () => {
    expect(resolveSafeReturnPath('/main')).toBe('/main')
    expect(resolveSafeReturnPath('/main/')).toBe('/main/')
    expect(resolveSafeReturnPath('/settings')).toBe('/settings')
    expect(resolveSafeReturnPath('/settings/projects')).toBe('/settings/projects')
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
    expect(resolveSafeReturnPath('/apple')).toBeNull()
    expect(resolveSafeReturnPath('/settingsx')).toBeNull()
    expect(resolveSafeReturnPath('/other')).toBeNull()
  })

  it('rejects dot-segment traversal (literal, backslash, and percent-encoded) out of /app', () => {
    // A raw string-prefix check would accept all three (they start with "/app/"), but
    // window.location.replace() resolves dot-segments — including %2e%2e, per the
    // WHATWG URL spec — before navigating, landing outside /app. The escape target here
    // (/evil) is deliberately outside every allow-listed prefix (/app, /main, /settings)
    // so this stays a traversal regression test even after T0009 §6 widened the list —
    // unlike /app/../settings, which now legitimately resolves inside the allow-list.
    expect(resolveSafeReturnPath('/app/../evil')).toBeNull()
    expect(resolveSafeReturnPath('/app/%2e%2e/evil')).toBeNull()
    expect(resolveSafeReturnPath('/app\\..\\evil')).toBeNull()
  })

  it('still resolves a dot-segment traversal that lands on another allow-listed prefix (T0009 §6)', () => {
    // /app/../settings normalizes to pathname /settings, which T0009 §6 now allow-lists
    // in its own right — this is not an escape, just two allow-listed prefixes reached
    // via different literal strings.
    expect(resolveSafeReturnPath('/app/../settings')).toBe('/app/../settings')
  })

  it('rejects a backslash-based cross-origin redirect the leading "//" check alone would miss', () => {
    // "/\evil.example.com/app" does not start with "//", but the WHATWG URL parser
    // treats a leading backslash as a path separator for http(s), so it resolves to
    // origin http://evil.example.com with pathname /app — a lookalike-safe pathname on
    // the wrong origin.
    expect(resolveSafeReturnPath('/\\evil.example.com/app')).toBeNull()
  })
})
