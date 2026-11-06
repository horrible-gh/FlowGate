/**
 * JWT-based permission utility (for UI guard purposes).
 * Independent of server-side validation; do not rely on this for security decisions.
 */

interface JwtPayload {
  sub?: string
  username?: string
  roles?: string[]
  is_admin?: boolean
  jti?: string
  type?: string
  iat?: number
  exp?: number
}

function decodeJwtPayload(): JwtPayload | null {
  const token =
    (window as any).__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
  if (!token) return null
  const segs = token.split('.')
  if (segs.length < 2) return null
  try {
    const b64 = segs[1].replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4))
    return JSON.parse(json) as JwtPayload
  } catch {
    return null
  }
}

export function isAdminFromToken(): boolean {
  const p = decodeJwtPayload()
  return !!p?.is_admin
}

// Only v0.1 entry points may receive a post-login return redirect.
const SAFE_RETURN_PREFIXES = ['/main', '/settings']

/**
 * Validate a post-login `next` path. Only same-origin paths under /main or
 * /settings may be used; unsafe or removed /app destinations fall back to /main.
 * Compare normalized URLs to reject protocol-relative and cross-origin redirects.
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
