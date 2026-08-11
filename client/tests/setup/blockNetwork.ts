// Refuse every real HTTP request made from a test (flowgate.default.0394 T0004,
// NR0003 §5.4 / §13-11).
//
// Four spec files mount components that fetch on mount without mocking `@shared/api`,
// so the suite really did open XHRs to the dev server's address. They pass today only
// because nothing is listening on 127.0.0.1:8088 and the resulting error is swallowed —
// on a machine where the dev server IS up, those requests are answered, and the specs
// see data no one wrote for them. That is precisely the environment-dependent test
// R0001 §5.1 wants gone, and unlike an ordinary flake it fails toward "looks fine".
//
// Blocking here rather than in each spec is deliberate: the rule is global (a unit test
// never talks to a real server), so — NR0003 §5.3 — the check has to be global too, or
// the next spec that forgets a mock reintroduces it silently.
//
// This makes the refusal deterministic instead of ambient: every request fails the same
// way on every machine, which is the behaviour those four specs were already written
// against. A spec that wants a response still mocks `@shared/api` (or stubs fetch) as
// usual; a spec that genuinely wants the failure path gets it reliably.

import { afterEach, beforeEach } from 'vitest'

/** Requests attempted since the current test started — url plus method. */
export const blockedRequests: { method: string; url: string }[] = []

const RealXMLHttpRequest = globalThis.XMLHttpRequest

class BlockedXMLHttpRequest extends RealXMLHttpRequest {
  private __method = 'GET'
  private __url = ''

  open(method: string, url: string | URL, ...rest: unknown[]): void {
    this.__method = String(method ?? 'GET').toUpperCase()
    this.__url = String(url ?? '')
    // Still open for real: readyState/headers/withCredentials all keep working, so the
    // caller (axios) sees an ordinary request object right up to the point of sending.
    return super.open(method as any, url as any, ...(rest as []))
  }

  send(): void {
    blockedRequests.push({ method: this.__method, url: this.__url })
    // Report it the way an unreachable host does — asynchronously, as a transport
    // error — instead of letting jsdom open a socket.
    setTimeout(() => {
      this.dispatchEvent(new Event('error'))
      this.dispatchEvent(new Event('loadend'))
    }, 0)
  }

  // Nothing was sent, so there is nothing to abort; keep it a no-op rather than letting
  // the base class fire an abort event for a request that never left.
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
