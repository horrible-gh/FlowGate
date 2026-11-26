// (The old JWT `is_admin` reading helper is gone: what a person may do comes from the server's capabilities,
// shared/permissions — flowgate.v02test.0090.)

// the only allow-listed internal prefix a post-login `next` query param may point
// at. Anything outside it (external origin, protocol-relative, or another path) is rejected
// so /login never becomes an open redirect. the earlier change: v0.1's /main and /settings
// are gone (the server redirects them into /app), so /app is the only entry left.
const SAFE_RETURN_PREFIXES = ['/app']

/**
 * Validate a post-login return target carried in `next`. Returns the original value
 * only when it is a same-origin absolute path under an allow-listed prefix; otherwise
 * null, so the caller falls back to its own default (`/app`).
 *
 * The check runs against the *normalized* destination, not the raw string: a raw
 * prefix check on `rawNext` alone would accept `/app/../settings` or
 * `/app/%2e%2e/evil` (both start with `/app/`) even though `window.location.replace`
 * resolves dot-segments — including percent-encoded ones, per the WHATWG URL spec —
 * before navigating, landing outside `/app` entirely. A leading backslash is worse:
 * `/\evil.example.com/app` parses with an `http://evil.example.com` origin (a
 * protocol-relative redirect `rawNext.startsWith('//')` alone would miss), so the
 * parsed `origin` is checked too, not just the path prefix.
 */
export function resolveSafeReturnPath(rawNext: string | null | undefined): string | null {
  if (!rawNext) return null
  if (!rawNext.startsWith('/')) return null

  const origin =
    (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://localhost'
  let resolved: URL
  try {
    resolved = new URL(rawNext, origin)
  } catch {
    return null
  }
  if (resolved.origin !== origin) return null

  const path = resolved.pathname
  const isAllowed = SAFE_RETURN_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
  return isAllowed ? rawNext : null
}

/**
 * If a login session exists, the user is considered to have document.read permission.
 * Actual permission validation is performed on the server side (D020 §2-7 Step 4).
 */
export function hasDocumentReadPermission(): boolean {
  const token =
    (window as any).__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
  return !!token
}
