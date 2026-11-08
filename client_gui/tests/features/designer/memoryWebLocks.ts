/**
 * flowgate.v02.0156 T1 R4 — an in-memory Web Locks for jsdom Shell suites.
 *
 * Without `navigator.locks` the Designer's run commands take the cross-tab lease (runCommandLock), whose localStorage
 * fallback (jsdom has no IndexedDB) waits on real timers. Suites that drive a click to its answer with flushPromises only
 * install this instead: requests of one name run one after another, like the browser's Web Locks.
 */
export function memoryWebLocks() {
  const tails = new Map<string, Promise<unknown>>()
  return {
    request(name: string, callback: (lock: { name: string }) => Promise<unknown>) {
      const run = (tails.get(name) ?? Promise.resolve()).then(() => callback({ name }))
      tails.set(name, run.catch(() => undefined))
      return run
    },
  }
}

export function installMemoryWebLocks() {
  Object.defineProperty(globalThis.navigator, 'locks', { value: memoryWebLocks(), configurable: true })
}

export function removeMemoryWebLocks() {
  delete (globalThis.navigator as { locks?: unknown }).locks
}
