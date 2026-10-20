import { onBeforeUnmount, readonly, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useCurrentUser } from '@/core/auth/currentUser'
import { currentSettingsAccess } from '../access'

/** Thrown by live() for an answer of an ended epoch; the caller just stops. */
export const STALE = Symbol('stale')

// The same token check the auth guard makes (core/router/guard).
const hasToken = () => !!(window.__accessToken__ || sessionStorage.getItem('fg_access_token'))

/** How often a shown project screen looks for a token that went away with no request out. */
export const SESSION_CHECK_MS = 1000

// Every mounted user of this composable — the 프로젝트 selection area's list, 저장 경로, Git and
// 시스템 › 전체 프로젝트 (flowgate.v02.0076 T#5). A lost session found by any one of them ends them
// all: the parent list, the picker and the section a late answer belonged to go together.
const mounted = new Set<() => void>()
let checker: ReturnType<typeof setInterval> | null = null

function endEverySession(): void {
  for (const end of [...mounted]) end()
}

function checkToken(): void {
  if (!hasToken()) endEverySession()
}

/**
 * The lifetime of a settings view's answers (flowgate.v02.0076 T#5, D §2.3 / §3.2 / §5.2 V23): every
 * request belongs to the epoch it was sent in. Leaving the screen, another person signing in, the
 * administrator role going away, a pending /auth/me or a lost token ends the epoch; `onExpire`
 * then drops what the view holds, and an answer arriving afterwards is dropped too — it is never
 * rendered, kept or followed by another request. The rule is the one 시스템 Agent applies
 * (SystemAgentSettingsView, T#4), with the same access decision as the router guard.
 *
 * A lost session (no token, or the settings access no longer `allow`) is not this view's alone:
 * whichever view sees it — before sending (begin), when an answer arrives (live) or on the periodic
 * token check of a screen with nothing out — ends every mounted view, and `lost` stays true until a
 * request may be sent again. begin() is asked before every request, so 다시 불러오기 / 다시 시도 /
 * choosing a project after that sends nothing.
 */
export function useLiveRequests(onExpire: () => void) {
  const route = useRoute()
  const { user } = useCurrentUser()
  const lost = ref(false)
  let epoch = 0
  let disposed = false

  /** This person is still an administrator with a token. */
  function sessionOk(): boolean {
    return hasToken() && currentSettingsAccess(route).kind === 'allow'
  }

  function expire(): void {
    epoch += 1
    onExpire()
  }

  /** Ends this view's epoch because the session went away (called for every mounted view). */
  function endSession(): void {
    if (disposed || lost.value) return
    lost.value = true
    expire()
  }

  /** Epoch `at` is still this screen, this person and an administrator with a token. */
  function sameSession(at: number): boolean {
    if (disposed || at !== epoch) return false
    if (sessionOk()) return true
    endEverySession()
    return false
  }

  /**
   * The epoch to send a request in, or null when no request may be sent: the view is gone or the
   * session is (then every mounted view is ended, and nothing is sent).
   */
  function begin(): number | null {
    if (disposed) return null
    if (!sessionOk()) {
      endEverySession()
      return null
    }
    lost.value = false
    return epoch
  }

  /** The answer of a request sent in epoch `at`, or STALE when that epoch ended meanwhile. */
  async function live<T>(at: number, request: Promise<T>): Promise<T> {
    let answer: T
    try {
      answer = await request
    } catch (err) {
      if (!sameSession(at)) throw STALE
      throw err
    }
    if (!sameSession(at)) throw STALE
    return answer
  }

  // Another person, a lost role or a pending /auth/me: what was read before is not theirs.
  watch(() => `${user.value?.username ?? ''}|${currentSettingsAccess(route).kind}`, () => expire(), { flush: 'sync' })

  mounted.add(endSession)
  if (!checker) checker = setInterval(checkToken, SESSION_CHECK_MS)

  onBeforeUnmount(() => {
    disposed = true
    mounted.delete(endSession)
    if (!mounted.size && checker) {
      clearInterval(checker)
      checker = null
    }
    expire()
  })

  return {
    /** Whether the epoch ended because the session went away (no token / no administrator access). */
    lost: readonly(lost),
    /** The current epoch, to tell whether the one a request was sent in is still on. */
    current: () => epoch,
    begin,
    live,
    expire,
  }
}
