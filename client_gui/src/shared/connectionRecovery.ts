/** One recovery probe loop for all panels in this application. */
export function createConnectionRecovery(probe: (signal: AbortSignal) => Promise<unknown>, recovered: () => void) {
  let active = false
  let generation = 0
  let step = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  const delays = [5000, 10000, 20000, 30000]
  function stop() {
    active = false
    generation++
    clearTimeout(timer)
    timer = undefined
    controller?.abort()
    controller = undefined
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
      schedule()
    },
    success,
    stop,
  }
}
