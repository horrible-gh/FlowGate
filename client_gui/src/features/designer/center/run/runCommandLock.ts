// flowgate.v02.0156 T1 R4 — cross-tab mutual exclusion for a Flow's run command when `navigator.locks` is missing.
//
// `navigator.locks` is a secure-context API: on the LAN plain-HTTP deployment it does not exist (and some secure browsers
// lack it too), so two tabs of the same Flow could read "no pending command" at once, each allocate an identity and
// overwrite each other's record (0156 NR §4 R4). flowRunControl injects this lock as runCommandStore's `lock` whenever
// `navigator.locks` is absent.
//
// The lease is one record {owner, until} per scope, changed only through a backend's atomic read-decide-write:
// - IndexedDB (every current browser, insecure origins included): one readwrite transaction per change. The browser
//   serializes those transactions across the tabs of an origin, so "free or expired -> mine" is a real compare-and-set:
//   no settle wait, no dependence on how fast another tab sees a write.
// - localStorage, only where IndexedDB is missing or cannot be opened: write, wait `settleMs`, read back. This is NOT a
//   compare-and-set; it excludes only while cross-tab propagation stays within `settleMs / 2` (a second writer that
//   missed the first write must still be seen by the first writer's read-back). Identity safety never rests on
//   it: runCommandStore journals every identity under its own key before sending, so a sent identity cannot be lost even
//   when two holders overlap (0156 T1 rework, R4-M lag > settle).
//
// Holding: a heartbeat renews `until` while the body runs (the AI setup modal wait included). A holder never trusts a
// lease its own clock says has run out — a frozen or backgrounded tab whose heartbeat did not run may have been taken
// over meanwhile — and a renewal that finds another owner marks the lease lost. The body receives a `fence`: called
// right before the start POST it renews atomically or fails with `run_command_busy`, so a former holder that resumes
// after a take-over can never send. A passed fence leaves `leaseMs` before anyone may take over.
// Waiting: a live lease of another owner is polled up to `maxWaitMs`; then `run_command_busy` before anything is sent.
// Release: the owner deletes the record; a closed tab's lease simply runs out (`leaseMs`).
// The lease never touches the command record itself: identity retention stays runCommandStore's (age never removes it).
import { randomUuid } from '../../../../../../client/shared/utils/uuid.ts'

export const RUN_COMMAND_LOCK_PREFIX = 'flowgate.runCommandLock.v1.'
/** IndexedDB database (one object store `lease`) holding the per-scope leases. */
export const RUN_COMMAND_LOCK_DB = 'flowgate.runCommandLock.v1'

export interface LeaseStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
export interface Lease { owner: string; until: number }
/** What one atomic step decides: the new record (`null` deletes, absent keeps) and what the step answers. */
export interface LeaseDecision<T> { next?: Lease | null; result: T }
export interface LeaseBackend {
  /** True when `update` is a real compare-and-set across tabs (no settle wait needed). */
  readonly atomic: boolean
  readonly kind: string
  update<T>(key: string, decide: (current: Lease | null) => LeaseDecision<T>): Promise<T>
}
/** Called by the body right before it sends: renews the lease or throws `run_command_busy`. */
export type RunCommandFence = () => Promise<void>

export interface RunCommandLockOptions {
  backend?: LeaseBackend
  /** localStorage for the fallback backend. */
  storage?: () => LeaseStorage
  /** IndexedDB factory (`null`: none). Defaults to `globalThis.indexedDB`. */
  indexedDb?: IDBFactory | null
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  /** Heartbeat scheduler; returns its stop. */
  every?: (ms: number, tick: () => void) => () => void
  token?: () => string
  /** Fallback backend only: wait after writing the lease before reading it back. */
  settleMs?: number
  /** How long a lease lives without renewal (a closed tab frees the Flow after this). */
  leaseMs?: number
  heartbeatMs?: number
  /** How long a click waits for another tab's lease before failing with `run_command_busy`. */
  maxWaitMs?: number
  pollMs?: number
}

export const runCommandBusy = (): Error => Object.assign(new Error(''), { code: 'run_command_busy' })
const leaseUnavailable = (): Error => Object.assign(new Error(''), { code: 'run_command_storage_unavailable' })

function leaseOf(value: unknown): Lease | null {
  const lease = value as Lease | null
  return lease && typeof lease === 'object' && typeof lease.owner === 'string' && Number.isFinite(lease.until) ? { owner: lease.owner, until: lease.until } : null
}

/** localStorage read-decide-write. Not atomic across tabs: the lock adds the settle-and-reread step for it. */
export function storageLeaseBackend(storage: () => LeaseStorage = () => globalThis.localStorage): LeaseBackend {
  return {
    atomic: false,
    kind: 'localStorage',
    async update(key, decide) {
      const text = storage().getItem(key)
      let current: Lease | null = null
      if (text !== null) { try { current = leaseOf(JSON.parse(text)) } catch { current = null } }
      const out = decide(current)
      if (out.next === null) storage().removeItem(key)
      else if (out.next) storage().setItem(key, JSON.stringify(out.next))
      return out.result
    },
  }
}

/** One IndexedDB readwrite transaction per step: serialized by the browser across the tabs of the origin. */
export function indexedDbLeaseBackend(factory: IDBFactory): LeaseBackend {
  let opened: Promise<IDBDatabase> | null = null
  const open = () => opened ??= new Promise<IDBDatabase>((done, fail) => {
    const request = factory.open(RUN_COMMAND_LOCK_DB, 1)
    request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains('lease')) request.result.createObjectStore('lease') }
    request.onsuccess = () => {
      const db = request.result
      db.onversionchange = () => { db.close(); opened = null }
      done(db)
    }
    request.onerror = () => { opened = null; fail(request.error ?? new Error('indexedDB open failed')) }
    request.onblocked = () => { opened = null; fail(new Error('indexedDB open blocked')) }
  })
  return {
    atomic: true,
    kind: 'indexedDB',
    async update<T>(key: string, decide: (current: Lease | null) => LeaseDecision<T>): Promise<T> {
      const db = await open()
      return new Promise<T>((done, fail) => {
        const tx = db.transaction('lease', 'readwrite')
        const store = tx.objectStore('lease')
        let result: T
        let failed: unknown = null
        const get = store.get(key)
        get.onsuccess = () => {
          try {
            // Decided inside the transaction: no other tab's step can run between this read and the write below.
            const out = decide(leaseOf(get.result))
            result = out.result
            if (out.next === null) store.delete(key)
            else if (out.next) store.put(out.next, key)
          } catch (error) { failed = error; tx.abort() }
        }
        tx.oncomplete = () => done(result)
        tx.onerror = () => fail(failed ?? tx.error ?? new Error('indexedDB transaction failed'))
        tx.onabort = () => fail(failed ?? tx.error ?? new Error('indexedDB transaction aborted'))
      })
    },
  }
}

/** IndexedDB when the browser has it (falling back to localStorage for good if it cannot be opened), else localStorage. */
export function defaultLeaseBackend(storage: () => LeaseStorage, factory: IDBFactory | null | undefined): LeaseBackend {
  const fallback = storageLeaseBackend(storage)
  if (!factory) return fallback
  const primary = indexedDbLeaseBackend(factory)
  let active: LeaseBackend | null = null
  return {
    get atomic() { return (active ?? primary).atomic },
    get kind() { return (active ?? primary).kind },
    async update(key, decide) {
      if (active) return active.update(key, decide)
      try {
        const result = await primary.update(key, decide)
        active = primary
        return result
      } catch {
        // IndexedDB refused to open (storage disabled / broken profile): the record store still works on localStorage.
        active = fallback
        return fallback.update(key, decide)
      }
    },
  }
}

export function createRunCommandLock(options: RunCommandLockOptions = {}) {
  const storage = options.storage ?? (() => globalThis.localStorage)
  const backend = options.backend ?? defaultLeaseBackend(storage, options.indexedDb === undefined ? globalThis.indexedDB : options.indexedDb)
  const now = options.now ?? Date.now
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((done) => setTimeout(done, ms)))
  const every = options.every ?? ((ms: number, tick: () => void) => { const timer = setInterval(tick, ms); return () => clearInterval(timer) })
  const token = options.token ?? randomUuid
  const settleMs = options.settleMs ?? 120
  const leaseMs = options.leaseMs ?? 6000
  const heartbeatMs = options.heartbeatMs ?? 1500
  const maxWaitMs = options.maxWaitMs ?? 10_000
  const pollMs = options.pollMs ?? 100

  return async function lock<T>(scopeKey: string, body: (fence: RunCommandFence) => Promise<T>): Promise<T> {
    const key = RUN_COMMAND_LOCK_PREFIX + scopeKey
    const me = token()
    const deadline = now() + maxWaitMs
    /** Until when this holder may act, as its own clock and its last successful claim / renewal say. */
    let until = 0
    let lost = false
    const step = async <R>(decide: (current: Lease | null) => LeaseDecision<R>): Promise<R> => {
      // Storage that cannot coordinate refuses the command (nothing is sent) instead of running it unguarded.
      try { return await backend.update(key, decide) } catch { throw leaseUnavailable() }
    }
    for (;;) {
      const claimed = await step<number | null>((current) => {
        const t = now()
        if (current && current.until > t && current.owner !== me) return { result: null }
        return { next: { owner: me, until: t + leaseMs }, result: t + leaseMs }
      })
      let held = claimed !== null
      if (held && !backend.atomic) {
        await sleep(settleMs)
        held = await step((current) => ({ result: current?.owner === me }))
      }
      if (held) { until = claimed!; break }
      if (now() >= deadline) throw runCommandBusy()
      await sleep(pollMs)
    }
    /** Renews the lease; false once it is lost (another owner, or this holder's own lease ran out by its clock). */
    const renew = async (): Promise<boolean> => {
      if (lost) return false
      if (now() >= until) { lost = true; return false }
      let renewed: number | null
      try {
        renewed = await backend.update(key, (current) => {
          const t = now()
          if (current?.owner !== me || current.until <= t || t >= until) return { result: null }
          return { next: { owner: me, until: t + leaseMs }, result: t + leaseMs }
        })
      } catch {
        // A failed renewal cannot prove the lease; it keeps what was held only until that runs out.
        return now() < until
      }
      if (renewed === null) { lost = true; return false }
      until = renewed
      return true
    }
    const fence: RunCommandFence = async () => { if (!(await renew())) throw runCommandBusy() }
    const stop = every(heartbeatMs, () => { void renew() })
    try {
      return await body(fence)
    } finally {
      stop()
      try { await backend.update(key, (current) => (current?.owner === me ? { next: null, result: true } : { result: false })) } catch { /* runs out by itself */ }
    }
  }
}
export type RunCommandLock = ReturnType<typeof createRunCommandLock>
