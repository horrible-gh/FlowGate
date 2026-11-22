// 0060 (22:09): while the server could not be reached, every panel's polling (notifications, live runs, the open
// chat, document windows …) kept sending its own GET, each re-sent five more times by api.ts — 1150 failed requests
// in the console, none of which could bring the connection back. While shared/api.ts's recovery loop runs, its one
// probe (GET /auth/connection-check) is what checks the connection; the app's other GETs wait here instead of going
// out:
// - the connection comes back (the loop ends) → every waiting GET goes out at once;
// - a GET has waited HOLD_MAX_MS → it fails without being sent (the same ERR_NETWORK its caller already handles), so
//   a caller's own retry or poll loop still turns over, at most once per HOLD_MAX_MS, and none spins;
// - once every PASS_EVERY_MS one GET that reached HOLD_MAX_MS is sent for real instead, so the app still recovers
//   even if the probe itself kept failing while the rest of the API answered.
// Writes (POST/PATCH/PUT/DELETE) are never held: a user's action goes out and fails or succeeds on its own.

/** How long a GET waits for the connection before it fails without being sent. */
export const HOLD_MAX_MS = 10_000
/** While the connection stays lost, one held GET is sent for real this often. */
export const PASS_EVERY_MS = 30_000

export type GateVerdict = 'send' | 'fail'

interface AbortLike {
  readonly aborted?: boolean
  addEventListener?: (type: 'abort', listener: () => void, options?: { once?: boolean }) => void
  removeEventListener?: (type: 'abort', listener: () => void) => void
}

export function createConnectionGate(isLost: () => boolean, now: () => number = () => Date.now()) {
  const waiters = new Set<() => void>()
  let lastPass = 0

  /** Resolves 'send' to send the request now, or 'fail' to fail it without sending. */
  function admit(signal?: AbortLike | null): 'send' | Promise<GateVerdict> {
    if (!isLost() || signal?.aborted) return 'send'
    return new Promise<GateVerdict>((resolve) => {
      let timer: ReturnType<typeof setTimeout> | null = null
      const finish = (verdict: GateVerdict) => {
        if (timer !== null) clearTimeout(timer)
        timer = null
        waiters.delete(onOpen)
        signal?.removeEventListener?.('abort', onAbort)
        resolve(verdict)
      }
      const onOpen = () => finish('send')
      // A cancelled request goes on to axios, which rejects it as cancelled without sending it.
      const onAbort = () => finish('send')
      timer = setTimeout(() => {
        if (!isLost()) return finish('send')
        if (now() - lastPass >= PASS_EVERY_MS) {
          lastPass = now()
          return finish('send')
        }
        finish('fail')
      }, HOLD_MAX_MS)
      waiters.add(onOpen)
      signal?.addEventListener?.('abort', onAbort, { once: true })
    })
  }

  return {
    admit,
    /** The connection was just lost: the GET that failed counts as the last one sent. */
    lost() {
      lastPass = now()
    },
    /** The connection is back: send every waiting GET. */
    open() {
      const pending = [...waiters]
      waiters.clear()
      pending.forEach((resolve) => resolve())
    },
    /** @internal — tests. */
    waiting: () => waiters.size,
  }
}
