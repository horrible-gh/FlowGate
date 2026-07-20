import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'

interface RefreshResponse {
  access_token: string
  refresh_token: string
  token_type?: string
}

interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

const getBaseUrl = (): string =>
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8088/flowgate'

const getRefreshTokenStorage = (): Storage | null => {
  if (localStorage.getItem('fg_refresh_token')) {
    return localStorage
  }
  if (sessionStorage.getItem('fg_refresh_token')) {
    return sessionStorage
  }
  return null
}

const getStoredRefreshToken = (): string | null =>
  getRefreshTokenStorage()?.getItem('fg_refresh_token') ?? null

const storeRefreshToken = (token: string) => {
  const storage = getRefreshTokenStorage() ?? sessionStorage
  localStorage.removeItem('fg_refresh_token')
  sessionStorage.removeItem('fg_refresh_token')
  storage.setItem('fg_refresh_token', token)
}

// group 0016 / NR0003 Finding C: shared with the cross-tab refresh coordination block below.
export const REFRESH_LOCK_NAME = 'fg_token_refresh'
export const SHARED_REFRESH_KEY = 'fg_refresh_result'

const clearStoredAuth = () => {
  clearProactiveRefresh()
  delete window.__accessToken__
  sessionStorage.removeItem('fg_access_token')
  localStorage.removeItem('fg_refresh_token')
  sessionStorage.removeItem('fg_refresh_token')
  localStorage.removeItem(SHARED_REFRESH_KEY)
}

// --- group 0016 / NR0003 Finding C: cross-tab refresh coordination -----------------
// Symptom: "간혹" every session is force-logged-out. Root cause: with "remember me" the
// refresh token lives in shared localStorage, so two tabs that both hit 401 after an idle
// period read the SAME refresh token and each POST /auth/refresh. The server rotates the
// token on the first call and treats the second (now-retired jti) as token reuse, which
// revokes EVERY session (auth_api.py reuse detection) -> both tabs bounce to login. The
// in-tab `isRefreshing` mutex further down only serializes within a single tab.
//
// Fix: serialize the refresh across tabs with the Web Locks API. The lock holder performs
// the single network rotation and publishes the result (shared cache + BroadcastChannel)
// so sibling tabs adopt the fresh access token instead of calling /auth/refresh again with
// a token the server has already retired. The lock and channel degrade gracefully (older
// browsers / jsdom): without them we fall back to the prior in-tab behaviour, which is no
// worse than before. The shared cache is written only for the remember-me (localStorage)
// case, so session-only logins never get their access token widened into localStorage.

interface SharedRefreshResult {
  access: string
  refresh: string
  ts: number
}

type LockGrantedCallback = () => Promise<unknown>
interface MinimalLockManager {
  request: (name: string, cb: LockGrantedCallback) => Promise<unknown>
}

// @internal — exported for the Finding C cross-tab coordination tests.
export const refreshIsShared = (): boolean =>
  localStorage.getItem('fg_refresh_token') !== null

const getLockManager = (): MinimalLockManager | null => {
  const nav = navigator as unknown as { locks?: MinimalLockManager }
  return nav?.locks && typeof nav.locks.request === 'function' ? nav.locks : null
}

// @internal — runs `fn` under a cross-tab Web Lock when available; otherwise inline.
export const withRefreshLock = async <T>(fn: () => Promise<T>): Promise<T> => {
  const locks = getLockManager()
  if (locks) {
    return locks.request(REFRESH_LOCK_NAME, fn as LockGrantedCallback) as Promise<T>
  }
  return fn()
}

// @internal — whether a sibling tab's published rotation can be reused instead of POSTing
// our (now possibly retired) refresh token again. True only when a result was published
// after we began this refresh AND it matches the refresh token currently in storage.
export const shouldReuseSiblingRefresh = (
  shared: SharedRefreshResult | null,
  currentRefreshToken: string | null,
  refreshStartedAt: number,
): boolean =>
  !!shared &&
  !!shared.access &&
  shared.ts >= refreshStartedAt &&
  shared.refresh === currentRefreshToken

// @internal
export const readSharedRefresh = (): SharedRefreshResult | null => {
  try {
    const raw = localStorage.getItem(SHARED_REFRESH_KEY)
    return raw ? (JSON.parse(raw) as SharedRefreshResult) : null
  } catch {
    return null
  }
}

// @internal
export const publishSharedRefresh = (access: string, refresh: string) => {
  if (!refreshIsShared()) return
  try {
    localStorage.setItem(
      SHARED_REFRESH_KEY,
      JSON.stringify({ access, refresh, ts: Date.now() } satisfies SharedRefreshResult),
    )
  } catch {
    // localStorage may be unavailable/full; the lock alone still prevents the race.
  }
}

const tokenChannel: BroadcastChannel | null =
  typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('fg_auth') : null

const adoptRotatedToken = (token: string) => {
  if (token === window.__accessToken__) return
  window.__accessToken__ = token
  sessionStorage.setItem('fg_access_token', token)
  // Reuse the 0021 rotation signal so the SSE EventSource reconnects with the new token.
  window.dispatchEvent(new CustomEvent('fg:access_token_refreshed', { detail: { token } }))
}

tokenChannel?.addEventListener('message', (event: MessageEvent) => {
  // Only react while this tab shares the session (remember-me); never overwrite or drop an
  // independent session-only login that happens to live in another tab of the same origin.
  if (!refreshIsShared()) return
  const msg = event.data as { type?: string; access?: string } | null
  if (msg?.type === 'token' && typeof msg.access === 'string') {
    adoptRotatedToken(msg.access)
  } else if (msg?.type === 'logout') {
    clearStoredAuth()
  }
})

// 0279 T0005 (NR0003 원인 3): this instance had no `timeout`, so axios defaulted to
// `timeout: 0` — wait forever. When a request stalled, its promise never settled, the
// `finally { loading.value = false }` never ran, and no rejection reached the existing
// error+retry UI. The screen therefore froze on "loading" rather than showing an error,
// with no self-recovery path short of a page reload. A finite default converts that
// permanent freeze into an ordinary, retryable ECONNABORTED.
const DEFAULT_TIMEOUT_MS = 30_000

// Endpoints that are legitimately slow, so the 30s default would abort healthy work.
// Matched on the request path, which keeps the policy in one place — a per-call
// override would have to be repeated at every call site and silently forgotten at new
// ones. Ceiling is 130s: just above the server's GIT_NET_TIMEOUT_SEC = 120 (git_service),
// so the server's own timeout is what surfaces, not ours.
const LONG_TIMEOUT_MS = 130_000
const LONG_RUNNING_PATHS = [
  /\/git\//,
  /\/files\/upload/,
  /\/files\/download/,
  /\/search\//,
]

const api: AxiosInstance = axios.create({
  baseURL: getBaseUrl(),
  headers: { 'Content-Type': 'application/json' },
  timeout: DEFAULT_TIMEOUT_MS,
})

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (window.__accessToken__) {
    config.headers.Authorization = `Bearer ${window.__accessToken__}`
  }
  // Raise the ceiling for known-slow endpoints unless the caller set its own timeout.
  if (config.timeout === DEFAULT_TIMEOUT_MS) {
    const path = config.url || ''
    if (LONG_RUNNING_PATHS.some((re) => re.test(path))) {
      config.timeout = LONG_TIMEOUT_MS
    }
  }
  // Forward the UI locale so server-built artifacts (e.g. worker mentions) can
  // emit localized doc-type names. Same source the i18n bootstrap reads.
  config.headers['X-Locale'] = localStorage.getItem('preferred_locale') || 'ko'
  return config
})

// A single in-flight rotation shared by every caller (concurrent reactive 401 retries AND
// the proactive timer). Coalescing onto one promise guarantees we never POST the refresh
// token twice, which would trip the server's reuse detection and revoke all sessions.
let refreshPromise: Promise<string> | null = null

// Perform one token rotation: reuse a sibling tab's freshly published result when available,
// otherwise POST /auth/refresh. Stores the new access+refresh tokens and notifies token-bound
// long-lived connections (the SSE EventSource, which pins the token into its URL) that the
// token rotated. (group 0016 Finding C + group 0021 NR0003 item 1)
const runRefresh = async (): Promise<string> => {
  const intendedRefreshToken = getStoredRefreshToken()
  if (!intendedRefreshToken) {
    throw new AxiosError('No refresh token available', 'ERR_NO_REFRESH_TOKEN')
  }
  const refreshStartedAt = Date.now()

  // group 0016 / NR0003 Finding C: hold a cross-tab lock so only one tab rotates the shared
  // refresh token; siblings adopt that tab's result instead of re-POSTing a token the server
  // has already retired (which would revoke every session).
  const newToken = await withRefreshLock(async () => {
    const currentRefreshToken = getStoredRefreshToken()
    const shared = readSharedRefresh()
    if (shouldReuseSiblingRefresh(shared, currentRefreshToken, refreshStartedAt)) {
      // Another tab rotated while we waited for the lock — reuse its fresh token.
      window.__accessToken__ = shared!.access
      sessionStorage.setItem('fg_access_token', shared!.access)
      return shared!.access
    }

    // Bare `axios`, not `api` — deliberately, to skip the auth interceptor. That also
    // skips the instance default, so set the timeout explicitly: a rotation that hangs
    // forever holds the cross-tab refresh lock and blocks every queued caller (0279 T0005).
    const { data } = await axios.post<RefreshResponse>(`${getBaseUrl()}/auth/refresh`, {
      refresh_token: currentRefreshToken ?? intendedRefreshToken,
    }, { timeout: DEFAULT_TIMEOUT_MS })
    window.__accessToken__ = data.access_token
    sessionStorage.setItem('fg_access_token', data.access_token)
    storeRefreshToken(data.refresh_token)
    publishSharedRefresh(data.access_token, data.refresh_token)
    if (refreshIsShared()) {
      tokenChannel?.postMessage({ type: 'token', access: data.access_token })
    }
    return data.access_token
  })

  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('fg:access_token_refreshed', { detail: { token: newToken } }),
    )
  }
  return newToken
}

// @internal — coalesce every concurrent caller onto the single in-flight rotation, and re-arm
// the proactive timer off the new token on success (whether the rotation was proactive or a
// reactive 401 retry).
const ensureFreshToken = (): Promise<string> => {
  if (!refreshPromise) {
    refreshPromise = runRefresh()
      .then((token) => {
        scheduleProactiveRefresh()
        return token
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

// --- group 0028 / T0004: proactive token refresh ----------------------------------------
// The access-token lifetime is intentionally short on this deployment (operator .env, e.g.
// 1 minute — deliberately NOT widened to 30m per T0004). A purely reactive 401→refresh loop
// then means (a) every expiry surfaces at least one 401 in the browser console, and (b) the
// SSE stream can sit pinned to a dead token until the next user action ("[워크플로 결정] not
// refreshing"). We instead rotate the token slightly BEFORE it expires, so live requests
// never carry an expired token and the SSE reconnect (via fg:access_token_refreshed) always
// has a fresh token. The reactive interceptor below stays as a safety net.
const REFRESH_SKEW_MS = 15000
const MIN_REFRESH_DELAY_MS = 2000
let proactiveTimer: ReturnType<typeof setTimeout> | null = null
let autoRefreshStarted = false

const currentAccessToken = (): string | null =>
  window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null

const decodeExpMs = (token: string): number | null => {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null
  } catch {
    return null
  }
}

function clearProactiveRefresh() {
  if (proactiveTimer !== null) {
    clearTimeout(proactiveTimer)
    proactiveTimer = null
  }
}

function scheduleProactiveRefresh() {
  if (typeof window === 'undefined') return
  clearProactiveRefresh()
  const token = currentAccessToken()
  // Without a refresh token we cannot rotate; leave recovery to the login/router guards.
  if (!token || !getStoredRefreshToken()) return
  const expMs = decodeExpMs(token)
  if (expMs === null) return
  const delay = Math.max(expMs - Date.now() - REFRESH_SKEW_MS, MIN_REFRESH_DELAY_MS)
  proactiveTimer = setTimeout(() => {
    proactiveTimer = null
    // On failure stop the chain; the reactive interceptor / a visibility-regain recovers.
    ensureFreshToken().catch(() => clearProactiveRefresh())
  }, delay)
}

const onVisibilityRefresh = () => {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (!getStoredRefreshToken()) return
  const token = currentAccessToken()
  const expMs = token ? decodeExpMs(token) : null
  // setTimeout is throttled/parked while the tab is hidden or the machine sleeps, so on regain
  // the token may already be (near) expired — rotate now to keep the next request and the SSE
  // reconnect off an expired token.
  if (expMs !== null && expMs - Date.now() <= REFRESH_SKEW_MS) {
    ensureFreshToken().catch(() => clearProactiveRefresh())
  } else {
    scheduleProactiveRefresh()
  }
}

/**
 * Start the proactive refresh loop. Called by the app shell (main.ts) once a valid session
 * exists. Idempotent. Stopped by stopTokenAutoRefresh() on logout.
 */
export const startTokenAutoRefresh = () => {
  if (typeof window === 'undefined') return
  if (!autoRefreshStarted) {
    autoRefreshStarted = true
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityRefresh)
    }
    window.addEventListener('focus', onVisibilityRefresh)
  }
  scheduleProactiveRefresh()
}

/** Stop the proactive refresh loop and detach its listeners (logout). */
export const stopTokenAutoRefresh = () => {
  autoRefreshStarted = false
  clearProactiveRefresh()
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityRefresh)
  }
  if (typeof window !== 'undefined') {
    window.removeEventListener('focus', onVisibilityRefresh)
  }
}

/**
 * Ensure the in-memory access token is valid before the first authenticated request of a page
 * load. If it is missing/expired/near-expiry and a refresh token exists, rotate up-front so the
 * startup /auth/me never has to eat a 401. Returns false only when no usable session remains.
 */
export const ensureValidAccessToken = async (): Promise<boolean> => {
  const token = currentAccessToken()
  if (!getStoredRefreshToken()) return !!token
  const expMs = token ? decodeExpMs(token) : null
  if (!token || expMs === null || expMs - Date.now() <= REFRESH_SKEW_MS) {
    try {
      await ensureFreshToken()
      return true
    } catch {
      return false
    }
  }
  return true
}

api.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined

    if (error.response?.status !== 401 || !originalRequest || originalRequest._retry) {
      return Promise.reject(error)
    }

    // T376-3: skip refresh during active logout to prevent token re-issue
    if (sessionStorage.getItem('fg_logout_in_progress')) {
      return Promise.reject(error)
    }

    // Mark BEFORE retrying so that a retry which STILL 401s rejects instead of looping into
    // another refresh — which, against an already-rotated token, would trip server reuse
    // detection and revoke every session. This is the latent cause of the intermittent
    // "approve fails with Token has expired" in group 0028. (T0004 req 4)
    originalRequest._retry = true

    try {
      const newToken = await ensureFreshToken()
      originalRequest.headers.Authorization = `Bearer ${newToken}`
      return api(originalRequest)
    } catch (refreshError) {
      // Refresh failed (token expired/revoked or reuse detected): drop the session and, if it
      // was shared, tell sibling tabs to do the same, then bounce to login. (Finding C)
      if (refreshIsShared()) {
        tokenChannel?.postMessage({ type: 'logout' })
      }
      clearStoredAuth()
      window.location.href = '/'
      return Promise.reject(refreshError)
    }
  },
)

export const postRequest = async <T>(path: string, data: unknown): Promise<AxiosResponse<T>> =>
  api.post<T>(path, data)

export const getRequest = async <T>(
  path: string,
  params: Record<string, unknown> = {},
): Promise<AxiosResponse<T>> => api.get<T>(path, { params })

/**
 * T376: Server logout API call.
 * Calls POST /auth/logout with refresh_token in the request body.
 * Returns regardless of server response success/failure; on failure, handled with console.warn.
 * T376-3: Sets the fg_logout_in_progress flag to suppress automatic 401 refresh.
 */
export const serverLogout = async (): Promise<void> => {
  sessionStorage.setItem('fg_logout_in_progress', '1')
  stopTokenAutoRefresh()
  try {
    const refreshToken = getStoredRefreshToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (window.__accessToken__) {
      headers['Authorization'] = `Bearer ${window.__accessToken__}`
    }
    await axios.post(
      `${getBaseUrl()}/auth/logout`,
      { refresh_token: refreshToken },
      { headers, timeout: DEFAULT_TIMEOUT_MS },
    )
  } catch (e) {
    console.warn('[serverLogout] Server logout call failed:', e)
  } finally {
    sessionStorage.removeItem('fg_logout_in_progress')
  }
}

export const patchRequest = async <T>(path: string, data: unknown): Promise<AxiosResponse<T>> =>
  api.patch<T>(path, data)

export const putRequest = async <T>(path: string, data: unknown): Promise<AxiosResponse<T>> =>
  api.put<T>(path, data)

export const deleteRequest = async <T>(path: string): Promise<AxiosResponse<T>> =>
  api.delete<T>(path)

export const postFormRequest = async <T>(path: string, formData: FormData): Promise<AxiosResponse<T>> =>
  api.post<T>(path, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

export const downloadBlobRequest = async (
  path: string,
  params: Record<string, unknown> = {},
): Promise<AxiosResponse<Blob>> =>
  api.get<Blob>(path, { params, responseType: 'blob' })

export const postUrlEncoded = async <T>(path: string, params: Record<string, string>): Promise<AxiosResponse<T>> =>
  api.post<T>(path, new URLSearchParams(params), {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

export default api
