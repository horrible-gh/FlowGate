import { onScopeDispose, watch } from 'vue'

/** A missing completion event must not leave a shown recovery waiting forever.
 * Read only while waiting; one read at a time, stopped on context change/unmount.
 */
export function useGateRecoveryRefresh(waiting: () => boolean, reload: () => Promise<unknown>) {
  const stop = watch(waiting, (active, _previous, onCleanup) => {
    if (!active) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const schedule = () => {
      if (!disposed) timer = setTimeout(async () => {
        timer = null
        try { await reload() } finally { schedule() }
      }, 5000)
    }
    onCleanup(() => { disposed = true; if (timer !== null) clearTimeout(timer) })
    schedule()
  }, { immediate: true })
  onScopeDispose(stop)
}
