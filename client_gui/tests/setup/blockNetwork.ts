// Refuse every real HTTP request made from a test. Mirrors
// client/tests/setup/blockNetwork.ts (flowgate.default.0394 T0004, NR0003 §5.4/§13-11):
// a unit test must never reach a real server, so this failure mode stays deterministic
// across machines instead of depending on whether something happens to be listening.

import { afterEach, beforeEach } from 'vitest'

export const blockedRequests: { method: string; url: string }[] = []

const RealXMLHttpRequest = globalThis.XMLHttpRequest

class BlockedXMLHttpRequest extends RealXMLHttpRequest {
  private __method = 'GET'
  private __url = ''

  open(method: string, url: string | URL, ...rest: unknown[]): void {
    this.__method = String(method ?? 'GET').toUpperCase()
    this.__url = String(url ?? '')
    return super.open(method as any, url as any, ...(rest as []))
  }

  send(): void {
    blockedRequests.push({ method: this.__method, url: this.__url })
    setTimeout(() => {
      this.dispatchEvent(new Event('error'))
      this.dispatchEvent(new Event('loadend'))
    }, 0)
  }

  abort(): void {}
}

const blockedFetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const method = String(init?.method ?? 'GET').toUpperCase()
  blockedRequests.push({ method, url })
  return Promise.reject(new TypeError(`fetch failed: network access is blocked in tests (${method} ${url})`))
}

const originalFetch = globalThis.fetch

beforeEach(() => {
  blockedRequests.length = 0
  globalThis.XMLHttpRequest = BlockedXMLHttpRequest as unknown as typeof XMLHttpRequest
  globalThis.fetch = blockedFetch as unknown as typeof fetch
})

afterEach(() => {
  globalThis.XMLHttpRequest = RealXMLHttpRequest
  globalThis.fetch = originalFetch
})
