/**
 * RFC 4122 v4 identifiers for request fields the server format-checks.
 *
 * `crypto.randomUUID()` is a **secure-context-only** API. This app is deployed over plain
 * HTTP on a LAN address (`ALLOWED_ORIGIN=http://192.168.0.252:8080`), and on such an origin
 * Chrome reports `isSecureContext === false` and leaves `crypto.randomUUID` `undefined` —
 * only `crypto.getRandomValues` survives. Developers never see this because `127.0.0.1` is
 * treated as trustworthy, so the fallback branch runs only on the screens people actually use.
 *
 * The per-component fallbacks this helper replaces concatenated `Date.now()` and
 * `Math.random()` slices into a string that merely *looked* like an id (four groups of
 * arbitrary length). `POST /groups/{id}/git/merge/{id}/approve` validates `attempt_id`
 * against a UUID regex, so approving a merge answered `400 invalid_attempt_id`
 * ("attempt_id must be a UUID") every single time off localhost.
 *
 * Callers that need a prefixed key build it from this value (`sess_${randomUuid()}`) so the
 * random part keeps the same shape everywhere.
 */
export function randomUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    // Last resort (no Web Crypto at all, e.g. a bare test environment). These ids are
    // idempotency keys, not secrets, so a weaker source is acceptable — an invalid shape
    // is not.
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
