// flowgate.default.0481 T0010 rev4 — "attempt_id must be a UUID" (2026-09-08 12:29).
//
// `crypto.randomUUID` is a secure-context-only API. The reviewer's browser opens this app at
// `http://192.168.0.252:8080` (the deploy's own ALLOWED_ORIGIN), where Chrome reports
// `isSecureContext === false` and does NOT expose `crypto.randomUUID` — measured, not assumed.
// The old per-component fallbacks emitted four groups of arbitrary length, which the approve
// route rejects with `400 invalid_attempt_id`. These pin the shape in the branch that only
// ever runs off localhost.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { randomUuid } from '@shared/utils/uuid'

/** The exact regex `server/modules/flow_gate/api/v1/git_routes.py` validates against. */
const SERVER_UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Reproduce an insecure origin: Web Crypto is present, `randomUUID` is not. */
function insecureOriginCrypto() {
  vi.stubGlobal('crypto', {
    getRandomValues: (buf: Uint8Array) => {
      for (let i = 0; i < buf.length; i++) buf[i] = (i * 37 + 11) % 256
      return buf
    },
  })
}

describe('randomUuid (0481 T0010 rev4)', () => {
  it('uses the platform generator when the origin is a secure context', () => {
    const randomUUID = vi.fn(() => '11111111-2222-4333-8444-555555555555')
    vi.stubGlobal('crypto', { randomUUID, getRandomValues: () => undefined })
    expect(randomUuid()).toBe('11111111-2222-4333-8444-555555555555')
    expect(randomUUID).toHaveBeenCalled()
  })

  it('still produces a server-valid UUID when crypto.randomUUID is missing', () => {
    insecureOriginCrypto()
    const id = randomUuid()
    expect(id).toMatch(SERVER_UUID_RE)
    // RFC 4122 v4: version nibble and variant bits, so it is a UUID and not just UUID-shaped.
    expect(id[14]).toBe('4')
    expect(['8', '9', 'a', 'b']).toContain(id[19])
  })

  it('falls back to Math.random when there is no Web Crypto at all', () => {
    vi.stubGlobal('crypto', undefined)
    for (let i = 0; i < 50; i++) expect(randomUuid()).toMatch(SERVER_UUID_RE)
  })

  it('does not repeat itself', () => {
    insecureOriginCrypto()
    vi.stubGlobal('crypto', {
      getRandomValues: (buf: Uint8Array) => {
        for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256)
        return buf
      },
    })
    const seen = new Set(Array.from({ length: 200 }, () => randomUuid()))
    expect(seen.size).toBe(200)
  })
})
