// 0060 (13:22 again): a panel whose read failed waited for one signal — `fg:connection_recovered`
// from shared/api.ts, or a resync of the project event stream — before it read again. Any gap in that
// chain (an expired token, a stream that does not come back, a recovery loop that had already ended)
// left the panel on its failure message until F5. Each failed panel now heals itself: while it shows a
// failure it reads again after 5 s, 10 s, 20 s and then every 30 s, without waiting for any signal.
// The signals stay, as a faster path: the connection coming back (fg:connection_recovered) or the page being shown
// again reads at once ('online' is left to the panels' own handlers, so one signal is one read). A hidden page waits and reads as soon as it is shown. Nothing is scheduled while a read is in
// flight, and nothing at all while the panel is healthy.
import { getCurrentScope, onScopeDispose, watch } from 'vue'

/** Waits before each automatic re-read of a failed panel; the last one repeats. */
export const SELF_HEAL_DELAYS_MS: readonly number[] = Object.freeze([5000, 10000, 20000, 30000])

export interface SelfHealOptions {
  /** The panel shows a failed read (nothing loaded, or the last refresh did not land). */
  failed: () => boolean
  /** A read is in flight: wait for it to settle. */
  busy: () => boolean
  /** Read again. Expected to make `busy()` true synchronously or fail at once. */
  reload: () => void
}

export interface SelfHeal {
  /** Read again now when the panel is failed and idle (a connection signal). */
  now(): void
  /** Stop for good (also on scope dispose). */
  stop(): void
}

function pageHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

/**
 * Re-reads a failed panel on its own. Must run in a component setup or an effect scope to stop with it;
 * outside one the caller stops it.
 */
export function useSelfHeal(options: SelfHealOptions): SelfHeal {
  let step = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  const clear = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  const reconcile = () => {
    if (stopped) return
    if (!options.failed()) {
      step = 0
      clear()
      return
    }
    if (options.busy()) {
      clear()
      return
    }
    if (timer !== null) return
    timer = setTimeout(fire, SELF_HEAL_DELAYS_MS[Math.min(step, SELF_HEAL_DELAYS_MS.length - 1)])
  }

  const read = () => {
    try {
      options.reload()
    } finally {
      // A reload that failed without ever being busy changes nothing the watcher sees: plan the next one here.
      queueMicrotask(reconcile)
    }
  }

  function fire() {
    timer = null
    if (stopped || !options.failed() || options.busy()) return
    // A hidden page reads when it is shown again (onVisible).
    if (pageHidden()) return
    step += 1
    read()
  }

  const now = () => {
    if (stopped || !options.failed() || options.busy() || pageHidden()) return
    clear()
    read()
  }

  const onVisible = () => {
    if (!pageHidden()) now()
  }

  const stopWatch = watch([options.failed, options.busy], reconcile, { immediate: true, flush: 'sync' })
  const hasWindow = typeof window !== 'undefined'
  if (hasWindow) window.addEventListener('fg:connection_recovered', now)
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible)

  const stop = () => {
    if (stopped) return
    stopped = true
    clear()
    stopWatch()
    if (hasWindow) window.removeEventListener('fg:connection_recovered', now)
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible)
  }
  if (getCurrentScope()) onScopeDispose(stop)
  return { now, stop }
}
