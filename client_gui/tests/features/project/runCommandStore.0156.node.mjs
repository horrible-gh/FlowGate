// flowgate.v02.0156 T1 — the run command store's DEFAULT identity path (no uuid injected) on an insecure context, its
// coded refusals, the R4 compare-and-set / per-identity journal, and the cross-tab lease that replaces navigator.locks.
// Dependency-free: Node type stripping loads the real TypeScript modules (no alias resolution — relative imports only).
//   node --test client_gui/tests/features/project/runCommandStore.0156.node.mjs  (not *.test.mjs: vitest would collect it)
import nodeTest from 'node:test'
const test = (name, fn) => nodeTest(name, { timeout: 30_000 }, fn)
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRunCommandStore, runCommandKey, runCommandOrphanKey, COMMAND_UUID, RUN_COMMAND_ORPHAN_LIMIT } from '../../../src/features/designer/center/run/runCommandStore.ts'
import { createRunCommandLock, storageLeaseBackend, defaultLeaseBackend, RUN_COMMAND_LOCK_PREFIX } from '../../../src/features/designer/center/run/runCommandLock.ts'

/** The exact format check the server applies (server/modules/flow_gate/graph_workflow/domain/run_request.py). */
const SERVER_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const owner = { username: 'kim', projectId: 'p', flowId: 'f' }
const X = '12345678-1234-4567-89ab-123456789abc'
const Y = '12345678-1234-4567-89ab-123456789abd'
const idOf = (n) => `12345678-1234-4567-89ab-${String(n).padStart(12, '0')}`

/** Web Storage over a Map (enumerable, like localStorage). */
function memory() {
  const values = new Map()
  const storage = {
    getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, String(v)), removeItem: k => values.delete(k),
    get length() { return values.size }, key: i => [...values.keys()][i] ?? null,
  }
  return { values, storage }
}
const journalKeys = (m) => [...m.values.keys()].filter(k => k.startsWith(runCommandOrphanKey(owner)))

/** Replaces globalThis.crypto for the body (Node exposes Web Crypto there) and restores it afterwards. */
async function withCrypto(value, body) {
  const before = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', { value, configurable: true, writable: true })
  try { return await body() } finally {
    if (before) Object.defineProperty(globalThis, 'crypto', before)
    else delete globalThis.crypto
  }
}
const insecureCrypto = (calls) => ({ getRandomValues: (bytes) => { calls.push('getRandomValues'); return globalThis.__realCrypto.getRandomValues(bytes) } })
globalThis.__realCrypto = globalThis.crypto

test('S-R1-1 no uuid option + crypto without randomUUID: empty-branch sending allocates a server-valid v4 and stores it', async () => {
  const calls = []
  await withCrypto(insecureCrypto(calls), () => {
    assert.equal(typeof globalThis.crypto.randomUUID, 'undefined')
    const m = memory(), store = createRunCommandStore({ storage: () => m.storage })
    const record = store.sending(owner, 'step')
    assert.match(record.commandRequestId, COMMAND_UUID)
    assert.match(record.commandRequestId, SERVER_V4)
    assert.equal(JSON.parse(m.values.get(runCommandKey(owner))).commandRequestId, record.commandRequestId)
    assert.deepEqual(calls, ['getRandomValues'])
    console.log('S_R1_1', JSON.stringify({ randomUUID: typeof globalThis.crypto.randomUUID, commandRequestId: record.commandRequestId }))
  })
})

test('S-R1-2 no Web Crypto at all: the last-resort source still yields the v4 shape', async () => {
  await withCrypto(undefined, () => {
    assert.equal(typeof crypto, 'undefined')
    const m = memory(), store = createRunCommandStore({ storage: () => m.storage })
    for (let i = 0; i < 50; i++) {
      const record = store.sending({ ...owner, flowId: `f${i}` }, 'full')
      assert.match(record.commandRequestId, SERVER_V4)
    }
  })
})

test('S-R1-3 pending-reuse branch without randomUUID: same identity, attempts+1, zero identity generation', async () => {
  const calls = []
  await withCrypto(insecureCrypto(calls), () => {
    const m = memory()
    m.values.set(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: X, ...owner, mode: 'step', createdAt: 5, attempts: 1, state: 'unconfirmed' }))
    const store = createRunCommandStore({ storage: () => m.storage })
    const record = store.sending(owner, 'step')
    assert.equal(record.commandRequestId, X)
    assert.equal(record.attempts, 2)
    assert.deepEqual(calls, [])
  })
})

test('S-R1-5 runCommandStore.ts no longer references crypto.randomUUID directly', () => {
  const text = readFileSync(new URL('../../../src/features/designer/center/run/runCommandStore.ts', import.meta.url), 'utf8')
  assert.equal((text.match(/crypto\.randomUUID/g) ?? []).length, 0)
  assert.match(text, /from '\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/client\/shared\/utils\/uuid\.ts'/)
})

test('S-R2-1 every sending refusal carries its code, keeps the stored bytes and returns no identity', () => {
  const cases = [
    ['corrupt', m => m.values.set(runCommandKey(owner), '{bad'), 'run_command_storage_unavailable'],
    ['unavailable', m => { m.storage.getItem = () => { throw new Error('denied') } }, 'run_command_storage_unavailable'],
    ['mode', m => m.values.set(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: X, ...owner, mode: 'step', createdAt: 1, attempts: 1, state: 'sending' })), 'run_command_mode_requires_void'],
    ['identity', () => {}, 'run_command_invalid_identity'],
    ['quota', m => { m.storage.setItem = () => { throw Object.assign(new Error('quota exceeded for private key'), { name: 'QuotaExceededError' }) } }, 'run_command_storage_unavailable'],
  ]
  for (const [name, arrange, code] of cases) {
    const m = memory(); arrange(m)
    const before = [...m.values.entries()]
    const store = createRunCommandStore({ storage: () => m.storage, uuid: () => name === 'identity' ? 'not-a-uuid' : Y })
    let thrown
    assert.throws(() => store.sending(owner, 'full'), e => { thrown = e; return true })
    assert.equal(thrown.code, code, name)
    assert.equal(thrown.message, '', name)
    assert.deepEqual([...m.values.entries()], before, name)
    if (name === 'quota') assert.equal(thrown.errorName, 'QuotaExceededError')
  }
})

test('S-R4-3 compare-and-set: another tab writing a different identity between put and re-read wins; ours is not returned nor journaled', () => {
  const m = memory()
  const realSet = m.storage.setItem
  let raced = false
  m.storage.setItem = (k, v) => {
    realSet(k, v)
    // The other tab's write lands right after ours (cross-process storage interleaving).
    if (!raced && k === runCommandKey(owner)) { raced = true; realSet(k, JSON.stringify({ v: 1, commandRequestId: Y, ...owner, mode: 'step', createdAt: 9, attempts: 1, state: 'sending' })) }
  }
  const store = createRunCommandStore({ storage: () => m.storage, uuid: () => X })
  assert.throws(() => store.sending(owner, 'step'), e => e.code === 'run_command_busy')
  assert.equal(JSON.parse(m.values.get(runCommandKey(owner))).commandRequestId, Y)
  assert.deepEqual(journalKeys(m), [], 'an identity that can never be sent is not journaled')
})

test('S-R4-4 (store) every identity is journaled under its own key BEFORE it can be sent; only a resolution drops it', () => {
  const m = memory()
  const a = createRunCommandStore({ storage: () => m.storage, uuid: () => X, now: () => 7 })
  const sentA = a.sending(owner, 'step')
  assert.deepEqual(journalKeys(m), [runCommandOrphanKey(owner) + X], 'journaled by sending(), before any POST')
  assert.deepEqual(a.orphans(owner), [{ commandRequestId: X, mode: 'step', createdAt: 7 }])
  // Tab B's record replaces A's (stale read on B's side): A's identity survives in its own key.
  m.values.set(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: Y, ...owner, mode: 'step', createdAt: 9, attempts: 1, state: 'sending' }))
  a.retain(sentA)
  a.retain(sentA)
  assert.deepEqual(a.orphans(owner).map(o => o.commandRequestId), [X], 'kept once')
  assert.equal(JSON.parse(m.values.get(runCommandKey(owner))).commandRequestId, Y, 'the other tab record is untouched')
  a.clear(owner, Y)
  assert.equal(a.read(owner).kind, 'empty')
  assert.deepEqual(a.orphans(owner).map(o => o.commandRequestId), [X], 'clearing the primary record never drops a journaled identity')
  a.dropOrphan(owner, X)
  assert.deepEqual(a.orphans(owner), [])
  assert.deepEqual(journalKeys(m), [])
})

test('S-R4-4 journal write refused (quota): sending throws storage_unavailable, so the identity is never sent unjournaled', () => {
  const m = memory()
  const realSet = m.storage.setItem
  m.storage.setItem = (k, v) => { if (k.startsWith('flowgate.runCommandOrphan.')) throw Object.assign(new Error('q'), { name: 'QuotaExceededError' }); realSet(k, v) }
  const store = createRunCommandStore({ storage: () => m.storage, uuid: () => X })
  assert.throws(() => store.sending(owner, 'step'), e => e.code === 'run_command_storage_unavailable' && e.errorName === 'QuotaExceededError')
  // The unsent identity stays the pending record: the next click reuses it (0149), never a second identity.
  m.storage.setItem = realSet
  const again = store.sending(owner, 'step')
  assert.equal(again.commandRequestId, X); assert.equal(again.attempts, 2)
  assert.deepEqual(journalKeys(m), [runCommandOrphanKey(owner) + X])
})

test('S-R4-8 no eviction: 25 distinct unresolved identities retained all stay traceable after F5; a new identity is refused at the limit', () => {
  const m = memory()
  const records = Array.from({ length: 25 }, (_, i) => ({ v: 1, commandRequestId: idOf(i + 1), ...owner, mode: i % 2 ? 'full' : 'step', createdAt: 100 + i, attempts: 1, state: 'unconfirmed' }))
  const tab = createRunCommandStore({ storage: () => m.storage })
  // Each was sent and its primary record replaced by another tab (retain finds a different identity).
  m.values.set(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: Y, ...owner, mode: 'step', createdAt: 1, attempts: 1, state: 'sending' }))
  for (const r of records) tab.retain(r)
  const f5 = createRunCommandStore({ storage: () => m.storage })
  const traced = new Set(f5.orphans(owner).map(o => o.commandRequestId))
  for (const r of records) assert.ok(traced.has(r.commandRequestId), `${r.commandRequestId} lost`)
  assert.equal(traced.size, 25)
  assert.equal(f5.orphans(owner)[0].commandRequestId, idOf(1), 'oldest first, and the oldest is still there')
  // Primary record resolved elsewhere; with >= RUN_COMMAND_ORPHAN_LIMIT unresolved, no new identity is allocated or written.
  f5.clear(owner, Y)
  const before = [...m.values.entries()]
  const fresh = createRunCommandStore({ storage: () => m.storage, uuid: () => X })
  assert.throws(() => fresh.sending(owner, 'step'), e => e.code === 'run_command_unresolved' && e.message === '')
  assert.deepEqual([...m.values.entries()], before)
  assert.ok(RUN_COMMAND_ORPHAN_LIMIT <= 25)
})

test('S-R4-8 concurrent journaling: two tabs that never see each other\'s writes both keep their identities', () => {
  // One shared backing map; each tab sees only its own writes for the primary record (worst-case propagation).
  const shared = new Map()
  const view = (tab) => ({
    getItem: k => (k === runCommandKey(owner) ? shared.get(`${tab}|${k}`) : shared.get(k)) ?? null,
    setItem: (k, v) => shared.set(k === runCommandKey(owner) ? `${tab}|${k}` : k, String(v)),
    removeItem: k => shared.delete(k === runCommandKey(owner) ? `${tab}|${k}` : k),
    get length() { return shared.size }, key: i => [...shared.keys()][i] ?? null,
  })
  const a = createRunCommandStore({ storage: () => view('A'), uuid: () => X })
  const b = createRunCommandStore({ storage: () => view('B'), uuid: () => Y })
  a.sending(owner, 'step'); b.sending(owner, 'step')
  assert.deepEqual(a.orphans(owner).map(o => o.commandRequestId).sort(), [X, Y])
})

test('journal keys lie outside the logout prefix; unreadable entry bytes still keep the identity named by the key', () => {
  const m = memory(), store = createRunCommandStore({ storage: () => m.storage, uuid: () => X })
  assert.ok(!runCommandOrphanKey(owner).startsWith('flowgate.user.'))
  m.values.set(runCommandOrphanKey(owner) + Y, '{bad')
  m.values.set(runCommandOrphanKey(owner) + 'not-an-id', '{}')
  assert.deepEqual(store.orphans(owner), [{ commandRequestId: Y, mode: 'step', createdAt: 0 }])
  // Another scope's journal is not this scope's.
  m.values.set(runCommandOrphanKey({ ...owner, flowId: 'g' }) + X, JSON.stringify({ commandRequestId: X, mode: 'full', createdAt: 1 }))
  assert.deepEqual(store.orphans(owner).map(o => o.commandRequestId), [Y])
})

// ---------------------------------------------------------------------------------------------------------------------
// The lease. A virtual clock drives every wait; `every` (the heartbeat) can be frozen to model a backgrounded tab.
// ---------------------------------------------------------------------------------------------------------------------
function clock() {
  let t = 0
  const timers = []
  return {
    now: () => t,
    sleep: ms => new Promise(done => timers.push({ at: t + ms, done })),
    jump(ms) { t += ms },
    async advance(ms) {
      const end = t + ms
      const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)) }
      for (;;) {
        // Let pending continuations register their timers before choosing the next one.
        await flush()
        timers.sort((a, b) => a.at - b.at)
        const next = timers[0]
        if (!next || next.at > end) break
        timers.shift(); t = Math.max(t, next.at); next.done()
      }
      t = Math.max(t, end)
      await flush()
    },
  }
}
/** Seeded PRNG so a schedule is reproducible. */
function prng(seed) { return () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff } }

/**
 * IndexedDB-like backend: every step is an atomic read-decide-write that commits after `delay()` virtual ms (other
 * steps may be queued meanwhile, as transactions of other tabs are). Atomic = decided and written in one turn.
 */
function atomicBackend(c, delay = () => 0) {
  const values = new Map()
  return {
    values, atomic: true, kind: 'model-idb',
    async update(key, decide) {
      const d = delay(key)
      if (d > 0) await c.sleep(d)
      const out = decide(values.get(key) ?? null)
      if (out.next === null) values.delete(key)
      else if (out.next) values.set(key, { ...out.next })
      return out.result
    },
  }
}
/** Critical-section probe: fence passed -> "send" -> done; counts how many holders are past their fence at once. */
function probe() {
  let inside = 0
  const p = { max: 0, sent: [], async section(name, fence, c, work) {
    await fence()
    inside++; p.max = Math.max(p.max, inside); p.sent.push(name)
    try { await c.sleep(work) } finally { inside-- }
  } }
  return p
}

test('S-R4-1 atomic backend, random delays up to 800 ms (well past any settle) on every step: no two holders ever overlap', async () => {
  const rnd = prng(156)
  let worst = 0, total = 0
  for (let round = 0; round < 60; round++) {
    const c = clock()
    const backend = atomicBackend(c, () => Math.floor(rnd() * 800))
    let id = 0
    const lock = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every: () => () => {}, token: () => `t${++id}`, pollMs: 50, leaseMs: 60_000, maxWaitMs: 120_000 })
    let bodies = 0, maxBodies = 0
    const p = probe()
    const holders = Array.from({ length: 3 }, (_, n) => (async () => {
      await c.sleep(Math.floor(rnd() * 300))
      return lock(runCommandKey(owner), async (fence) => {
        bodies++; maxBodies = Math.max(maxBodies, bodies)
        try { await c.sleep(Math.floor(rnd() * 400)); await p.section(`h${n}`, fence, c, 50) } finally { bodies-- }
      })
    })())
    await c.advance(200_000)
    await Promise.all(holders)
    worst = Math.max(worst, maxBodies, p.max)
    total += p.sent.length
  }
  console.log('S_R4_1_ATOMIC', JSON.stringify({ rounds: 60, holdersPerRound: 3, sections: total, maxConcurrentBodies: worst }))
  assert.equal(worst, 1)
  assert.equal(total, 180)
})

test('S-R4-1 delayed writer during acquisition: the tab whose claim commits later waits, enters only after release', async () => {
  const c = clock()
  // A's claim step is held 500 ms in flight; B's claim (started 10 ms later) commits first.
  let first = true
  const backend = atomicBackend(c, () => { if (first) { first = false; return 500 } return 0 })
  let id = 0
  const lock = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every: () => () => {}, token: () => ['A', 'B'][id++], pollMs: 50, leaseMs: 60_000 })
  const log = []
  const a = lock(runCommandKey(owner), async () => { log.push('A:in'); await c.sleep(300); log.push('A:out') })
  await c.advance(10)
  const b = lock(runCommandKey(owner), async () => { log.push('B:in'); await c.sleep(300); log.push('B:out') })
  await c.advance(5000)
  await Promise.all([a, b])
  assert.deepEqual(log, ['B:in', 'B:out', 'A:in', 'A:out'])
})

test('S-R4-1 live expired holder resumes (heartbeat frozen past the lease, another tab took over): it can never send', async () => {
  const c = clock()
  const backend = atomicBackend(c)
  const frozen = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every: () => () => {}, token: () => 'A', leaseMs: 6000, pollMs: 100, maxWaitMs: 20_000 })
  const live = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, token: () => 'B', leaseMs: 6000, heartbeatMs: 1500, pollMs: 100, maxWaitMs: 20_000,
    every: (ms, tick) => { let on = true; (async () => { while (on) { await c.sleep(ms); if (on) tick() } })(); return () => { on = false } } })
  const p = probe()
  let resumeA
  const modal = new Promise(r => { resumeA = r })
  const a = frozen(runCommandKey(owner), async (fence) => { await modal; await p.section('A', fence, c, 10) }).then(() => 'A sent', e => e.code)
  await c.advance(100)
  // A's tab is frozen: no heartbeat. 6.5 s later B clicks; A's lease ran out, B takes over and sends.
  const b = live(runCommandKey(owner), async (fence) => { await c.sleep(2000); await p.section('B', fence, c, 3000) }).then(() => 'B sent', e => e.code)
  await c.advance(6400)
  // A resumes (its AI setup modal answers) while B still holds the lease.
  resumeA()
  await c.advance(20_000)
  assert.equal(await a, 'run_command_busy')
  assert.equal(await b, 'B sent')
  assert.deepEqual(p.sent, ['B'])
  assert.equal(p.max, 1)
})

test('S-R4-1 a holder whose own lease ran out by its clock refuses at the fence even when nobody took over', async () => {
  const c = clock()
  const lock = createRunCommandLock({ backend: atomicBackend(c), now: c.now, sleep: c.sleep, every: () => () => {}, token: () => 'A', leaseMs: 6000 })
  const run = lock(runCommandKey(owner), async (fence) => { await c.sleep(7000); await fence(); return 'sent' }).then(v => v, e => e.code)
  await c.advance(8000)
  assert.equal(await run, 'run_command_busy')
})

test('S-R4-1 the heartbeat keeps a long body (AI setup modal) its lease; the fence then passes', async () => {
  const c = clock()
  const backend = atomicBackend(c)
  const every = (ms, tick) => { let on = true; (async () => { while (on) { await c.sleep(ms); if (on) tick() } })(); return () => { on = false } }
  const lock = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every, token: () => 'A', leaseMs: 6000, heartbeatMs: 1500 })
  const other = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every, token: () => 'B', leaseMs: 6000, maxWaitMs: 5000, pollMs: 100 })
  const a = lock(runCommandKey(owner), async (fence) => { await c.sleep(30_000); await fence(); return 'sent' }).then(v => v, e => e.code)
  await c.advance(1000)
  const b = other(runCommandKey(owner), async () => 'B entered').then(v => v, e => e.code)
  await c.advance(40_000)
  assert.equal(await a, 'sent')
  assert.equal(await b, 'run_command_busy', 'B waited its bound and sent nothing')
})

test('S-R4-1 lease wait beyond the bound fails with run_command_busy and never runs the body', async () => {
  const c = clock()
  const backend = atomicBackend(c)
  backend.values.set(RUN_COMMAND_LOCK_PREFIX + runCommandKey(owner), { owner: 'other-tab', until: 1e12 })
  const lock = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every: () => () => {}, token: () => 'me', pollMs: 100, maxWaitMs: 1000 })
  let ran = false
  const outcome = lock(runCommandKey(owner), async () => { ran = true }).then(() => 'ok', e => e.code)
  await c.advance(1500)
  assert.equal(await outcome, 'run_command_busy')
  assert.equal(ran, false)
})

test('S-R4-1 an expired lease (closed tab) is taken over; the holder releases its record afterwards', async () => {
  const c = clock()
  const backend = atomicBackend(c)
  backend.values.set(RUN_COMMAND_LOCK_PREFIX + runCommandKey(owner), { owner: 'closed-tab', until: -1 })
  const lock = createRunCommandLock({ backend, now: c.now, sleep: c.sleep, every: () => () => {}, token: () => 'me' })
  const run = lock(runCommandKey(owner), async () => 'entered')
  await c.advance(200)
  assert.equal(await run, 'entered')
  assert.equal(backend.values.has(RUN_COMMAND_LOCK_PREFIX + runCommandKey(owner)), false)
})

test('S-R4-1 storage that cannot coordinate refuses (run_command_storage_unavailable) instead of running unguarded', async () => {
  const c = clock()
  const lock = createRunCommandLock({ backend: { atomic: true, kind: 'broken', update: async () => { throw new Error('denied') } }, now: c.now, sleep: c.sleep, every: () => () => {} })
  let ran = false
  assert.equal(await lock('k', async () => { ran = true }).then(() => 'ok', e => e.code), 'run_command_storage_unavailable')
  assert.equal(ran, false)
})

test('S-R4-1 default backend: IndexedDB when present (falls back to localStorage if it cannot open), localStorage when absent', async () => {
  const m = memory()
  assert.equal(defaultLeaseBackend(() => m.storage, undefined).kind, 'localStorage')
  const broken = { open: () => { const r = {}; setTimeout(() => { r.error = new Error('disabled'); r.onerror?.() }, 0); return r } }
  const backend = defaultLeaseBackend(() => m.storage, broken)
  assert.equal(backend.kind, 'indexedDB')
  assert.equal(await backend.update('k', () => ({ next: { owner: 'me', until: 5 }, result: 'done' })), 'done')
  assert.equal(backend.kind, 'localStorage')
  assert.equal(JSON.parse(m.values.get('k')).owner, 'me')
})

/** localStorage fallback over a lagged view: each tab sees the other tab's writes only `lag` virtual ms later. */
function laggedStorage(c, lag) {
  const log = []
  const view = (tab) => ({
    getItem(key) { for (let i = log.length - 1; i >= 0; i--) { const e = log[i]; if (e.key === key && (e.tab === tab || e.at <= c.now() - lag)) return e.value } return null },
    setItem(key, value) { log.push({ at: c.now(), tab, key, value: String(value) }) },
    removeItem(key) { log.push({ at: c.now(), tab, key, value: null }) },
  })
  return { view }
}

test('S-R4-1 localStorage fallback: excludes while propagation stays within settle / 2; measured (not claimed) beyond it', async () => {
  const measured = {}
  for (const lag of [0, 50, 100, 200, 500]) {
    let overlaps = 0
    for (let offset = 0; offset < 40; offset += 5) {
      const c = clock(), shared = laggedStorage(c, lag)
      const mk = (tab) => createRunCommandLock({ backend: storageLeaseBackend(() => shared.view(tab)), now: c.now, sleep: c.sleep, every: () => () => {}, token: () => tab, settleMs: 120, pollMs: 20, leaseMs: 60_000 })
      let inside = 0, max = 0
      const body = async () => { inside++; max = Math.max(max, inside); await c.sleep(300); inside-- }
      const a = mk('A')(runCommandKey(owner), body)
      await c.advance(offset)
      const b = mk('B')(runCommandKey(owner), body)
      await c.advance(10_000)
      await Promise.all([a, b])
      if (max > 1) overlaps++
    }
    measured[lag] = overlaps
  }
  console.log('S_R4_1_STORAGE_FALLBACK_OVERLAPS', JSON.stringify({ settleMs: 120, offsetsPerLag: 8, overlapsByLag: measured }))
  // Write-settle-reread excludes only while propagation stays within half the settle: a second writer that missed the
  // first write (lag) must still be seen by the first writer's reread (lag again) -> 2 x lag <= settleMs.
  for (const lag of [0, 50]) assert.equal(measured[lag], 0, `lag ${lag} <= settle / 2`)
  // Beyond settle the fallback cannot exclude (no compare-and-set in localStorage); identity safety there is the journal's
  // (vitest R4-M runs lag 200/500/1000 on this fallback and asserts a~e). Production browsers use the IndexedDB backend.
})

test('S-R4-6 lease/TTL code stays out of runCommandStore.ts (0149 source rule) and lives in runCommandLock.ts', () => {
  const store = readFileSync(new URL('../../../src/features/designer/center/run/runCommandStore.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(store, /expiresAt|expires_at|ttl|setTimeout|setInterval|lease/i)
  assert.equal((store.match(/removeItem\(runCommandKey/g) ?? []).length, 2)
  assert.doesNotMatch(store, /slice\(-/, 'the journal never trims identities')
  const lock = readFileSync(new URL('../../../src/features/designer/center/run/runCommandLock.ts', import.meta.url), 'utf8')
  assert.match(lock, /setInterval/)
  assert.match(lock, /indexedDB|IDBFactory/)
  assert.doesNotMatch(lock, /runCommand\.v1|runCommandOrphan|removeItem\(runCommandKey/, 'the lease never touches the command record or the journal')
})
