/** One recovery probe loop for all panels in this application. */
export function createConnectionRecovery(probe: (signal: AbortSignal) => Promise<unknown>, recovered: () => void) {
  let active = false
  let generation = 0
  let step = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  const delays = [5000, 10000, 20000, 30000]
  const onVisible = () => { if (document.visibilityState === 'visible') stop() }
  // The event hub already handles these signals. Only stop the loop here to avoid double resync.
  const onSignal = () => stop()
  function watch(on: boolean) {
    if (typeof window === 'undefined') return
    const method = on ? 'addEventListener' : 'removeEventListener'
    window[method]('online', onSignal)
    window[method]('focus', onSignal)
    document[method]('visibilitychange', onVisible)
  }
  function stop() {
    active = false
    generation++
    clearTimeout(timer)
    timer = undefined
    controller?.abort()
    controller = undefined
    watch(false)
  }
  function success() {
    if (!active) return
    stop()
    recovered()
  }
  function schedule() {
    const epoch = generation
    timer = setTimeout(async () => {
      timer = undefined
      controller = new AbortController()
      try {
        await probe(controller.signal)
        if (active && generation === epoch) success()
      } catch {
        if (active && generation === epoch) schedule()
      }
    }, delays[Math.min(step++, delays.length - 1)])
  }
  return {
    start() {
      if (active || typeof window === 'undefined') return
      active = true
      step = 0
      watch(true)
      schedule()
    },
    success,
    stop,
  }
}
