import { flushTreeReports, newTreeReportId, reportTreeFailure } from './treeReadReports'
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import i18n from './i18n'
import { resolveApiErrorWithFallbackText } from './apiErrors'
import { resolveSafeReturnPath } from './auth'
import { announceAuthSessionEnded } from './authSessionSignal'
import {
  beginVisibilityRecoveryTick,
  recordVisibilityRecovery,
} from './diagnostics/runtimeDiagnostics'

interface RefreshResponse {
  access_token: string
  refresh_token: string
  token_type?: string
}

import { notifyGetRetry } from './requestRecovery'

interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
  /** flowgate.v02gui.0017 B4: how many times this GET was already re-sent after a transient failure. */
  _getRetryCount?: number
}

export const getBaseUrl = (): string =>
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
  // flowgate.v02test.0036 T#1: the session's unsent work (Flow attachment deletes) goes with the tokens.
  announceAuthSessionEnded()
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

// --- flowgate.v02gui.0017 B6: cross-tab refresh without Web Locks ---------------------
// `navigator.locks` exists only in a secure context (HTTPS, localhost). Opened over plain HTTP
// on a LAN address, the Web Lock above is missing and two remember-me tabs whose proactive
// timers fire together (they adopted the same access token, so the same expiry) both POST the
// same refresh token; the server rotates on the first and treats the second as reuse, revoking
// every session. Without Web Locks we therefore take a small localStorage mutex: claim the key
// with this tab's id, wait a moment, and only proceed if the claim is still ours (the last
// writer wins, so of two near-simultaneous claims exactly one survives the re-read). The
// waiting tab polls until the holder releases (or its claim expires), then runs the critical
// section, which finds the sibling's published rotation and reuses it instead of POSTing.
// Only used for the remember-me (shared localStorage) case; session-only logins keep their own
// refresh token per tab and need no coordination.
export const REFRESH_MUTEX_KEY = 'fg_refresh_mutex'
// The holder's POST /auth/refresh times out at DEFAULT_TIMEOUT_MS; a claim older than that plus
// a margin belongs to a tab that died mid-refresh and may be taken over.
const REFRESH_MUTEX_TTL_MS = 35_000
const REFRESH_MUTEX_SETTLE_MS = 75
const REFRESH_MUTEX_POLL_MS = 100
const REFRESH_MUTEX_WAIT_MAX_MS = REFRESH_MUTEX_TTL_MS + 5_000

const TAB_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`

interface RefreshMutexRecord {
  owner: string
  exp: number
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const readRefreshMutex = (): RefreshMutexRecord | null => {
  try {
    const raw = localStorage.getItem(REFRESH_MUTEX_KEY)
    const parsed = raw ? (JSON.parse(raw) as RefreshMutexRecord) : null
    return parsed && typeof parsed.owner === 'string' && typeof parsed.exp === 'number' ? parsed : null
  } catch {
    return null
  }
}

const tryClaimRefreshMutex = async (owner: string): Promise<boolean> => {
  const held = readRefreshMutex()
  if (held && held.owner !== owner && held.exp > Date.now()) return false
  try {
    localStorage.setItem(
      REFRESH_MUTEX_KEY,
      JSON.stringify({ owner, exp: Date.now() + REFRESH_MUTEX_TTL_MS } satisfies RefreshMutexRecord),
    )
  } catch {
    return true // storage unavailable: no coordination possible, behave as before
  }
  await sleep(REFRESH_MUTEX_SETTLE_MS)
  return readRefreshMutex()?.owner === owner
}

const releaseRefreshMutex = (owner: string) => {
  try {
    if (readRefreshMutex()?.owner === owner) localStorage.removeItem(REFRESH_MUTEX_KEY)
  } catch {
    // nothing to release
  }
}

// @internal — exported for the B6 tests; `owner` defaults to this tab.
export const withStorageRefreshMutex = async <T>(fn: () => Promise<T>, owner: string = TAB_ID): Promise<T> => {
  const deadline = Date.now() + REFRESH_MUTEX_WAIT_MAX_MS
  while (!(await tryClaimRefreshMutex(owner))) {
    // A claim always expires, so this ends; the deadline only guards against a clock jump.
    if (Date.now() > deadline) break
    await sleep(REFRESH_MUTEX_POLL_MS + Math.floor(Math.random() * 50))
  }
  try {
    return await fn()
  } finally {
    releaseRefreshMutex(owner)
  }
}

// @internal — runs `fn` under a cross-tab Web Lock when available; without Web Locks a shared
// (remember-me) session falls back to the localStorage mutex (B6); otherwise inline.
export const withRefreshLock = async <T>(fn: () => Promise<T>): Promise<T> => {
  const locks = getLockManager()
  if (locks) {
    return locks.request(REFRESH_LOCK_NAME, fn as LockGrantedCallback) as Promise<T>
  }
  if (refreshIsShared()) {
    return withStorageRefreshMutex(fn)
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
  // Reuse the 0021 rotation signal: the project event hub (projectEvents.ts) replaces a stream that is not open with
  // one carrying the new token.
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

// 0279 T0005 (NR0003 cause 3): this instance had no `timeout`, so axios defaulted to
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
  // 0283 T0004 (NR0003 root cause): the base file-tree and group-tree GETs do a
  // synchronous recursive directory walk of the storage root. On remote/UNC storage
  // that walk intermittently exceeds the 30s default and axios aborts it, surfacing
  // "트리를 불러오지 못했습니다." Give these reads the same 130s ceiling the other
  // legitimately-slow endpoints get. (git group-branch tree /git/.../tree already
  // matches /\/git\// above.)
  /\/files\/tree/,
  /\/groups\/tree/,
  // flowgate.v02.0047: the AI title of a new chat waits for one model answer, which the
  // server allows up to 90s (auto_title.TIMEOUT_SEC); a CLI model often needs more than
  // the 30s default, and the title was then dropped although the server still wrote it.
  /\/conversations\/[^/]+\/auto-title/,
  // flowgate.v02.0152: an AI start answers only after admission — worktree check, the group's
  // first source sync (git commands of up to 120s each), lease, token, scratch. The 30s default
  // gave up on healthy starts the server went on to run, and the chat said "AI를 호출하지
  // 못했습니다" for its first call. The chat resends the same press (client_request_id) when even
  // this ceiling passes, so a slower admission is still recovered rather than started twice.
  /\/ai-invoke\/start(?:$|\?)/,
]

// flowgate.default.0607 T0004 §3.5 (NR0003 §6): a final approval that carries a
// `git_action` runs the whole Git finalize inside the request — fetch (≤120s),
// ff-only + merge (≤30s each), push (≤120s) — before it answers, so the 30s default
// abandoned healthy merges mid-flight. Only that request gets this ceiling; a plain
// approve on the same path keeps the default. Decided on path + body here so a new
// call site cannot forget it.
export const GIT_APPROVAL_TIMEOUT_MS = 330_000
const GIT_APPROVAL_PATH = /\/documents\/review_transitions\/approve(?:$|\?)/
// flowgate.v02.0096 CH: the merge-review [승인] (`POST …/git/merge/{id}/approve`, group and
// project routes) also publishes inside the request — every changed path validated, then
// commit, push (≤120s), the AC approval and the slot cleanup — before it answers. It matched
// `/\/git\//` and got 130s, so the 0079 approval of 2026-10-26 (577 changed paths) was
// abandoned at 130s while the server went on to merge, push and approve; the screen said
// "서버에 연결할 수 없습니다". This route always publishes, so there is no body test.
const GIT_MERGE_APPROVE_PATH = /\/git\/merge\/[^/]+\/approve(?:$|\?)/

function carriesGitAction(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false
  const action = (data as Record<string, unknown>).git_action
  return typeof action === 'string' && action.length > 0
}

const api: AxiosInstance = axios.create({
  baseURL: getBaseUrl(),
  headers: { 'Content-Type': 'application/json' },
  timeout: DEFAULT_TIMEOUT_MS,
})

type TreeTiming = { start: number; id: string; owner: string; project: string; screen: 'full_tree' | 'designer_tree' }
const treeTimings = new WeakMap<object, TreeTiming>()
function reportOwner(): string {
  try {
    const token = window.__accessToken__ || sessionStorage.getItem('fg_access_token') || ''
    const payload = token.split('.')[1] ?? ''
    return String(JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/'))).sub || '')
  } catch { return '' }
}
// Bare transport: a diagnostic failure must not refresh auth, redirect, retry or emit diagnostics.
const sendTreeReportFor = (owner: string) => async (report: import('./treeReadReports').TreeReport) => {
  if (!owner || owner !== reportOwner()) throw new Error('report owner changed')
  return axios.post(`${getBaseUrl()}/api/v1/client-reports/tree-read-failed`, report, {
    headers: { Authorization: `Bearer ${window.__accessToken__ || sessionStorage.getItem('fg_access_token') || ''}` },
    timeout: 10_000,
  })
}
export const flushPendingTreeReports = () => {
  const owner = reportOwner()
  return flushTreeReports(owner, sendTreeReportFor(owner))
}
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const match = /^\/api\/v1\/projects\/([^/?]+)\/(groups\/tree|git\/groups\/[^/?]+\/tree)$/.exec((config.url || '').split('?')[0]!)
  try {
  if (config.method?.toLowerCase() === 'get' && match) {
    const id = newTreeReportId()
    config.headers['X-Request-ID'] = id
    treeTimings.set(config, { start: Date.now(), id, owner: reportOwner(), project: decodeURIComponent(match[1]!),
      screen: match[2] === 'groups/tree' ? 'full_tree' : 'designer_tree' })
  }
  } catch { /* diagnostics must never reject the tree request */ }
  if (window.__accessToken__) {
    config.headers.Authorization = `Bearer ${window.__accessToken__}`
  }
  // Raise the ceiling for known-slow endpoints unless the caller set its own timeout.
  if (config.timeout === DEFAULT_TIMEOUT_MS) {
    const path = config.url || ''
    if (
      (GIT_APPROVAL_PATH.test(path) && carriesGitAction(config.data))
      || GIT_MERGE_APPROVE_PATH.test(path)
    ) {
      config.timeout = GIT_APPROVAL_TIMEOUT_MS
    } else if ((config.method?.toUpperCase() === 'POST' && /^\/api\/v1\/flows\/[^/?]+\/runs(?:$|\?)/.test(path))
      || LONG_RUNNING_PATHS.some((re) => re.test(path))) {
      config.timeout = LONG_TIMEOUT_MS
    }
  }
  // Forward the locale currently rendered by the UI. The i18n instance already
  // normalizes stored/browser values and also changes immediately on a runtime switch.
  config.headers['X-Locale'] = String(i18n.global.locale.value)
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
        proactiveFailures = 0
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
// SSE stream can sit pinned to a dead token until the next user action ("[Workflow Decision] not
// refreshing"). We instead rotate the token slightly BEFORE it expires, so live requests
// never carry an expired token. The reactive interceptor below stays as a safety net.
// flowgate.v02gui.0031 T0005 ①: the token reaches the SSE stream through fg:access_token_refreshed, which the project
// event hub (features/project/projectEvents.ts) listens to — it replaces a stream that is not open (one the browser
// retries on its old URL, or a closed one waiting to reopen) with one carrying the new token. An open stream is kept:
// the server checks the token only when the stream connects.
const REFRESH_SKEW_MS = 15000
const MIN_REFRESH_DELAY_MS = 2000
let proactiveTimer: ReturnType<typeof setTimeout> | null = null
let autoRefreshStarted = false

// flowgate.v02gui.0031 T0005 ④: a proactive rotation that failed without a verdict (network error, timeout, 5xx — the
// server restarting) used to stop the timer, and only a focus / visibility return, or the next request's 401, tried
// again. A screen watched without leaving it went on to an expired token. It is now tried again on its own, the wait
// doubling from PROACTIVE_RETRY_BASE_MS up to PROACTIVE_RETRY_MAX_DELAY_MS, at most PROACTIVE_RETRY_MAX times; the
// browser going online again tries at once (and starts a new series). A refresh the server rejected (401/403, no
// refresh token) is not retried: the existing login path stays. Every try goes through ensureFreshToken — the one
// in-flight rotation and the cross-tab rotation lock.
export const PROACTIVE_RETRY_BASE_MS = 5_000
export const PROACTIVE_RETRY_MAX_DELAY_MS = 60_000
export const PROACTIVE_RETRY_MAX = 6
let proactiveFailures = 0

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

/** The wait before proactive retry number `failures` (1-based): doubling, capped. */
// @internal — exported for the retry tests.
export const proactiveRetryDelayMs = (failures: number): number =>
  Math.min(PROACTIVE_RETRY_BASE_MS * 2 ** Math.max(failures - 1, 0), PROACTIVE_RETRY_MAX_DELAY_MS)

/** T0005 ④: a rotation started by the timer, a visibility return or `online`; a failure without a verdict retries. */
function refreshInBackground(): void {
  ensureFreshToken().catch((error) => retryProactiveRefresh(error))
}

function retryProactiveRefresh(error: unknown) {
  clearProactiveRefresh()
  // A rejected refresh is a verdict: leave it to the login path (the next request's 401). Logged out: nothing.
  if (!autoRefreshStarted || isAuthRejection(error) || !getStoredRefreshToken()) {
    proactiveFailures = 0
    return
  }
  // Exhausted: focus / visibility, `online` and the reactive 401 refresh are left.
  if (proactiveFailures >= PROACTIVE_RETRY_MAX) return
  proactiveFailures += 1
  proactiveTimer = setTimeout(() => {
    proactiveTimer = null
    refreshInBackground()
  }, proactiveRetryDelayMs(proactiveFailures))
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
    // T0005 ④: a failure without a verdict is tried again (bounded backoff); a rejection stops the chain.
    refreshInBackground()
  }, delay)
}

// `recordRecovery` distinguishes an actual hidden->visible transition (the visibilitychange
// listener below, which only reaches this point once `document.visibilityState` has just
// become 'visible') from a plain window focus that never left the tab hidden (rev3 finding
// 3) — e.g. clicking the address bar while already visible. Both still rotate/reschedule the
// token identically; only the diagnostics attribution differs, so this is diagnostics-only
// and changes no token-refresh behavior.
const onVisibilityRefresh = (recordRecovery: boolean) => {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (!getStoredRefreshToken()) return
  const token = currentAccessToken()
  const expMs = token ? decodeExpMs(token) : null
  // setTimeout is throttled/parked while the tab is hidden or the machine sleeps, so on regain
  // the token may already be (near) expired — rotate now to keep the next request and the SSE
  // reconnect off an expired token.
  const nearExpiry = expMs !== null && expMs - Date.now() <= REFRESH_SKEW_MS
  if (recordRecovery) {
    // T0004 §8: this module's own independent visibility-recovery listener — diagnostics
    // only, coalesced under the same generation as the SSE/AI-run recovery entries via the
    // shared tick. Recorded only for a genuine hidden->visible recovery (see above), not
    // every focus, so a same-state focus cannot manufacture a duplicate token recovery entry
    // or generation for a recovery that never actually happened.
    const generation = beginVisibilityRecoveryTick()
    recordVisibilityRecovery('token', generation, { action: nearExpiry ? 'refreshed' : 'rescheduled' })
  }
  if (nearExpiry) {
    refreshInBackground()
  } else if (proactiveFailures === 0 || proactiveTimer === null) {
    scheduleProactiveRefresh()
  }
  // else: a retry of a failed rotation is waiting; rescheduling off the old token would drop it.
}
const onVisibilityChangeRefresh = () => onVisibilityRefresh(true)
const onFocusRefresh = () => onVisibilityRefresh(false)

// T0005 ④: back online — a rotation that failed while offline (or a token that expired meanwhile) is done now, as a
// new bounded series; a healthy schedule is kept.
const onOnlineRefresh = () => {
  if (!getStoredRefreshToken()) return
  const token = currentAccessToken()
  const expMs = token ? decodeExpMs(token) : null
  const failedBefore = proactiveFailures > 0
  proactiveFailures = 0
  if (failedBefore || expMs === null || expMs - Date.now() <= REFRESH_SKEW_MS) {
    clearProactiveRefresh()
    refreshInBackground()
  } else if (proactiveTimer === null) {
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
      document.addEventListener('visibilitychange', onVisibilityChangeRefresh)
    }
    window.addEventListener('focus', onFocusRefresh)
    window.addEventListener('online', onOnlineRefresh)
  }
  scheduleProactiveRefresh()
}

/** Stop the proactive refresh loop and detach its listeners (logout). */
export const stopTokenAutoRefresh = () => {
  autoRefreshStarted = false
  proactiveFailures = 0
  clearProactiveRefresh()
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChangeRefresh)
  }
  if (typeof window !== 'undefined') {
    window.removeEventListener('focus', onFocusRefresh)
    window.removeEventListener('online', onOnlineRefresh)
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

// flowgate.default.0578 T0010 §2.3: delegates to the common resolver so a registered code
// renders its i18n message; otherwise the caller's fallback is returned verbatim. Raw
// response.data.detail/error.message text is never returned here anymore.
export const extractApiErrorMessage = (error: unknown, fallback: string): string =>
  resolveApiErrorWithFallbackText(error, i18n.global.t, fallback)

export const localizeApiError = (error: AxiosError): AxiosError => {
  const data = error.response?.data
  const record = data && typeof data === 'object' ? data as Record<string, any> : null
  const code = record?.error?.code ?? record?.code ?? record?.detail?.error?.code
  if (error.response?.status !== 423 && code !== 'GROUP_AI_RUN_LOCKED') return error

  // 0401 NR0003 SS3 cause 3 / T0004 task 4: "진행 중" is only true when the lease's OWN run
  // is still live. A lease left behind by a dead process gets a sentence that tells the
  // user what to do about it instead of asking them to wait for something that will never
  // finish. run_live is absent on any 423 this build doesn't recognize -- default to the
  // old wait message, same fail-toward-safe choice mutation_policy makes server-side.
  const runLive = record?.error?.run_live
  const detail = String(i18n.global.t(
    runLive === false
      ? 'main.review_action_bar.ai_lease_orphaned_hint'
      : 'main.review_action_bar.ai_running_hint',
  ))
  if (record) {
    // Preserve the structured server contract for programmatic callers while keeping the
    // legacy detail field consumed by existing UI error surfaces during the transition.
    record.detail = detail
    if (record.error && typeof record.error === 'object') record.error.message = detail
  } else if (error.response) {
    error.response.data = { detail, error: { code: 'GROUP_AI_RUN_LOCKED', message: detail } }
  }
  return error
}

// 0005 T#1 (NR0003 rework, last rejection): on refresh failure the browser goes back to the
// sign-in page. client_gui's bootstrap (main.ts) hits this exact path — a stale access token
// plus a missing/invalid refresh token — when /auth/me 401s and the retried refresh also
// fails, before installAuthGuard's /login?next=/app/... logic gets a chance to run, so the
// requested /app deep link is carried here as `next` (same allow-list as the guard and the
// sign-in page, resolveSafeReturnPath). flowgate.v02gui.0024: every other location goes to
// plain /login (v0.1's '/' is now the same sign-in page, but /login says what it is).
// @internal — exported for the refresh-failure redirect regression tests.
export const buildRefreshFailureRedirect = (): string => {
  if (typeof window === 'undefined' || !window.location) return '/login'
  const current = `${window.location.pathname}${window.location.search}`
  const safeNext = resolveSafeReturnPath(current)
  return safeNext ? `/login?next=${encodeURIComponent(safeNext)}` : '/login'
}

// flowgate.v02gui.0017 B1/B2: only a definitive answer from the server ends the session — the
// refresh/identity request was answered 401/403, or there is no refresh token at all. A network
// error, a timeout or a 5xx (server restarting, proxy hiccup) says nothing about the session,
// so the tokens are kept and the caller just sees the failed request.
// @internal — exported for main.ts (bootstrap) and the refresh-failure tests.
export const isAuthRejection = (error: unknown): boolean => {
  const e = error as { code?: unknown; response?: { status?: unknown } } | null | undefined
  if (!e || typeof e !== 'object') return false
  if (e.code === 'ERR_NO_REFRESH_TOKEN') return true
  const status = e.response?.status
  return status === 401 || status === 403
}

// --- flowgate.v02gui.0017 B4: bounded retry of idempotent GETs ------------------------------
// A dropped connection, a server restart or a gateway error used to surface at once as a failed
// list ("채팅 목록을 불러오지 못했습니다", "새로고침에 실패했습니다") that stayed until the user
// pressed [다시 시도]. GETs are re-sent a few times with a short exponential backoff. Only GET:
// writes are never re-sent here (a POST may have been applied; POST /auth/refresh in particular
// would be reuse) — callers that need it resend with their own idempotency key. A timeout is
// re-sent once only, since each attempt already waited the full path timeout. The re-sent request
// goes through the request interceptor again with its already-raised timeout, so the per-path
// ceiling (30s / 130s / 330s) is kept.
const GET_RETRY_MAX = 5
const GET_RETRY_MAX_AFTER_TIMEOUT = 1
const GET_RETRY_BASE_MS = 500
const GET_RETRY_MAX_DELAY_MS = 3_000
const GET_RETRY_STATUSES = new Set([502, 503, 504])

const isTimeoutError = (error: AxiosError): boolean =>
  error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT'

// @internal — exported for the retry tests.
export const getRetryDelayMs = (attempt: number): number =>
  Math.min(GET_RETRY_BASE_MS * 2 ** attempt, GET_RETRY_MAX_DELAY_MS) + Math.floor(Math.random() * 100)

// @internal — whether this failed request may be re-sent once more.
export const shouldRetryGet = (error: AxiosError): boolean => {
  const config = error.config as RetryableRequestConfig | undefined
  if (!config || config.method?.toLowerCase() !== 'get') return false
  if (error.code === 'ERR_CANCELED' || config.signal?.aborted) return false
  const count = config._getRetryCount ?? 0
  if (error.response) {
    return GET_RETRY_STATUSES.has(error.response.status) && count < GET_RETRY_MAX
  }
  if (isTimeoutError(error)) return count < GET_RETRY_MAX_AFTER_TIMEOUT
  return error.code === 'ERR_NETWORK' && count < GET_RETRY_MAX
}

api.interceptors.response.use(
  (response: AxiosResponse) => {
    if (treeTimings.has(response.config)) void flushPendingTreeReports()
    return response
  },
  async (error: AxiosError) => {
    const timing = error.config && treeTimings.get(error.config)
    if (timing) {
      void reportTreeFailure(timing.owner, { id: newTreeReportId(), project_id: timing.project, screen: timing.screen,
        status: error.response?.status ?? 'no_response', elapsed_ms: Math.min(86400000, Math.max(0, Date.now() - timing.start)),
        occurred_at: new Date().toISOString(), request_id: String(error.response?.headers?.['x-request-id'] || timing.id).slice(0, 64) }, sendTreeReportFor(timing.owner))
    }
    localizeApiError(error)
    const originalRequest = error.config as RetryableRequestConfig | undefined

    if (originalRequest && shouldRetryGet(error)) {
      const attempt = originalRequest._getRetryCount ?? 0
      originalRequest._getRetryCount = attempt + 1
      notifyGetRetry(originalRequest.url ?? '', originalRequest.params)
      await sleep(getRetryDelayMs(attempt))
      return api(originalRequest)
    }

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
      // flowgate.v02gui.0017 B1: the refresh could not reach a verdict (network error, timeout,
      // 5xx — e.g. the server is restarting). The session may well be valid: keep every token,
      // stay on this screen and fail only this request. The next request, the proactive timer or
      // a visibility return refreshes again once the server answers.
      if (!isAuthRejection(refreshError)) {
        return Promise.reject(refreshError)
      }
      // Refresh rejected (token expired/revoked or reuse detected): drop the session and, if it
      // was shared, tell sibling tabs to do the same, then bounce to login. (Finding C)
      if (refreshIsShared()) {
        tokenChannel?.postMessage({ type: 'logout' })
      }
      clearStoredAuth()
      window.location.href = buildRefreshFailureRedirect()
      return Promise.reject(refreshError)
    }
  },
)

// `config` (optional, flowgate.v02.0045 S3): per-request extras such as the Canvas tab id header.
// Without it the call is exactly the previous one.
export const postRequest = async <T>(path: string, data: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>> =>
  config ? api.post<T>(path, data, config) : api.post<T>(path, data)

// 0282 NR0003 finding 3: the screen-load SQL log showed the client running the
// same GET series twice (racing mounts / duplicated event listeners), roughly
// doubling the server's per-load query work. Coalesce identical in-flight GETs
// onto one network call: a caller issuing a GET whose (path, params) twin is
// still pending gets that pending promise. The entry is dropped as soon as the
// request settles, so sequential refetches (e.g. re-reading status right after
// a finalize POST) always hit the server.
const inflightGets = new Map<string, Promise<AxiosResponse<unknown>>>()

const getRequestKey = (path: string, params: Record<string, unknown>): string => {
  const keys = Object.keys(params).sort()
  // Query params are flat key/value pairs; a sorted-key stringify makes the
  // dedupe key insensitive to caller-side property order.
  return keys.length ? `${path}?${JSON.stringify(params, keys)}` : path
}

// `config` (optional) carries per-request extras such as headers (flowgate.v02.0045: the Canvas
// tab id header). It is not part of the dedupe key: a caller sends the same extras for the same GET.
export const getRequest = async <T>(
  path: string,
  params: Record<string, unknown> = {},
  config: Omit<AxiosRequestConfig, 'params'> = {},
): Promise<AxiosResponse<T>> => {
  const key = getRequestKey(path, params)
  const inflight = inflightGets.get(key)
  if (inflight) return inflight as Promise<AxiosResponse<T>>
  const request = api.get<T>(path, { ...config, params }).finally(() => {
    inflightGets.delete(key)
  })
  inflightGets.set(key, request as Promise<AxiosResponse<unknown>>)
  return request
}

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

export const patchRequest = async <T>(path: string, data: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>> =>
  config ? api.patch<T>(path, data, config) : api.patch<T>(path, data)

export const putRequest = async <T>(path: string, data: unknown, config?: AxiosRequestConfig): Promise<AxiosResponse<T>> =>
  config ? api.put<T>(path, data, config) : api.put<T>(path, data)

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
