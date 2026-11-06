import { describe, expect, it } from 'vitest'
import { resolveSafeReturnPath } from '@shared/auth'

describe('resolveSafeReturnPath', () => {
  it('accepts /main and /settings with subpaths and queries', () => {
    expect(resolveSafeReturnPath('/main')).toBe('/main')
    expect(resolveSafeReturnPath('/main/?doc=example')).toBe('/main/?doc=example')
    expect(resolveSafeReturnPath('/settings')).toBe('/settings')
    expect(resolveSafeReturnPath('/settings/projects')).toBe('/settings/projects')
  })

  it('rejects the removed v0.2 GUI path', () => {
    expect(resolveSafeReturnPath('/app')).toBeNull()
    expect(resolveSafeReturnPath('/app/')).toBeNull()
    expect(resolveSafeReturnPath('/app/projects?tab=x')).toBeNull()
  })

  it('rejects missing, external, and unsupported paths', () => {
    expect(resolveSafeReturnPath(null)).toBeNull()
    expect(resolveSafeReturnPath(undefined)).toBeNull()
    expect(resolveSafeReturnPath('')).toBeNull()
    expect(resolveSafeReturnPath('//evil.example.com/app')).toBeNull()
    expect(resolveSafeReturnPath('https://evil.example.com/main')).toBeNull()
    expect(resolveSafeReturnPath('javascript:alert(1)')).toBeNull()
    expect(resolveSafeReturnPath('/other')).toBeNull()
    expect(resolveSafeReturnPath('/mainx')).toBeNull()
  })

  it('rejects normalized traversal outside allowed prefixes', () => {
    expect(resolveSafeReturnPath('/main/../evil')).toBeNull()
    expect(resolveSafeReturnPath('/main/%2e%2e/evil')).toBeNull()
  })

  it('accepts a normalized path that remains inside another allowed prefix', () => {
    expect(resolveSafeReturnPath('/main/../settings')).toBe('/main/../settings')
  })

  it('rejects backslash-based cross-origin redirects', () => {
    expect(resolveSafeReturnPath('/\\\\evil.example.com/main')).toBeNull()
  })
})
