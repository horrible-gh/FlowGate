// Dependency-free tests of the actual TypeScript pending store (Node type stripping).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRunCommandStore, runCommandKey, STALE_AFTER_MS } from '../../../src/features/designer/center/run/runCommandStore.ts'
const owner = { username: 'kim', projectId: 'p', flowId: 'f' }
const X = '12345678-1234-4567-89ab-123456789abc'
const Y = '12345678-1234-4567-89ab-123456789abd'
function harness() {
  const values = new Map(), ids = []
  let now = 1_000_000, counter = 0
  const storage = { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) }
  const options = { storage: () => storage, now: () => now, uuid: () => { const id = [X,Y][counter++]; ids.push(id); return id } }
  return { values, storage, ids, make: () => createRunCommandStore(options), advance: ms => { now += ms } }
}
for (const elapsed of [STALE_AFTER_MS + 1, 24 * 60 * 60_000, 30 * 24 * 60 * 60_000, 365 * 24 * 60 * 60_000]) {
  test(`B17c age ${elapsed}: reads, F5 and retry retain X; no new UUID`, () => {
    const h = harness(), a = h.make()
    const initial = a.sending(owner, 'step'); a.retain(initial)
    const bytes = h.values.get(runCommandKey(owner))
    h.advance(elapsed)
    const b = h.make()
    assert.equal(b.read(owner).kind, 'pending')
    assert.equal(h.values.get(runCommandKey(owner)), bytes)
    const retry = b.sending(owner, 'step')
    assert.equal(retry.commandRequestId, X); assert.equal(retry.attempts, 2)
    b.retain(retry)
    assert.deepEqual(h.ids, [X]); assert.equal(b.read(owner).record.createdAt, initial.createdAt)
    console.log('B17_NATIVE', JSON.stringify({ elapsed, generatedIds: h.ids, pendingId: retry.commandRequestId, attempts: retry.attempts, ageCleared: false }))
  })
}
test('B16 storage is reread across instances and conditional clear cannot delete a newer command', () => {
  const h = harness(), a = h.make(), b = h.make()
  a.sending(owner, 'step')
  assert.equal(b.read(owner).record.commandRequestId, X)
  b.clear(owner, X); const next = b.sending(owner, 'full')
  assert.equal(next.commandRequestId, Y)
  a.clear(owner, X); assert.equal(a.read(owner).record.commandRequestId, Y)
})
test('B15c key lies outside logout prefix and partitions username/project/flow', () => {
  const h = harness(), store = h.make(); store.sending(owner, 'step')
  assert.ok(!runCommandKey(owner).startsWith('flowgate.user.kim.'))
  for (const altered of [{ ...owner, username: 'lee' }, { ...owner, projectId: 'q' }, { ...owner, flowId: 'g' }]) assert.equal(store.read(altered).kind, 'empty')
  assert.notEqual(runCommandKey({ ...owner, username: 'a.b', projectId: 'c' }), runCommandKey({ ...owner, username: 'a', projectId: 'b.c' }))
})
test('B17a a changed mode needs confirmed void, age never permits replacement', () => {
  const h = harness(), store = h.make(); store.sending(owner, 'step'); h.advance(365 * 24 * 60 * 60_000)
  // 0156 T1 R2: the refusal is told by its code (the toast names it); same condition, no identity, record kept.
  assert.throws(() => store.sending(owner, 'full'), e => e.code === 'run_command_mode_requires_void' && e.message === '')
  assert.deepEqual(h.ids, [X]); assert.equal(store.read(owner).record.mode, 'step')
  store.clear(owner, X) // Caller has a server-confirmed resolution.
  assert.equal(store.sending(owner, 'full').commandRequestId, Y)
})
for (const raw of ['{bad', 'null', '[]', JSON.stringify({ v:1, ...owner, commandRequestId:'bad', mode:'step', createdAt:1, attempts:1, state:'sending' })]) {
  test(`corruption blocks identity allocation without deleting bytes: ${raw}`, () => {
    const h = harness(), store = h.make(); h.values.set(runCommandKey(owner), raw)
    assert.equal(store.read(owner).kind, 'corrupt'); assert.throws(() => store.sending(owner, 'step'))
    assert.equal(h.values.get(runCommandKey(owner)), raw); assert.deepEqual(h.ids, [])
    store.discardCorrupt(owner); assert.equal(store.read(owner).kind, 'empty')
  })
}
test('record schema rejects unknown fields rather than persisting private data on retry', () => {
  const h = harness(), store = h.make(), record = store.sending(owner, 'step')
  h.values.set(runCommandKey(owner), JSON.stringify({ ...record, token: 'private' }))
  assert.equal(store.read(owner).kind, 'corrupt')
  assert.throws(() => store.sending(owner, 'step'))
})
test('storage quota/read failure cannot return an identity for transmission', () => {
  const h = harness(), store = h.make()
  h.storage.setItem = () => { throw new Error('quota') }
  assert.throws(() => store.sending(owner, 'step'), e => e.code === 'run_command_storage_unavailable' && e.message === '')
  h.storage.getItem = () => { throw new Error('unavailable') }
  assert.equal(store.read(owner).kind, 'unavailable'); assert.throws(() => store.sending(owner, 'step'))
})
test('legacy uncertainty survives restart and age with exactly the same UUID', () => {
  const h = harness(), store = h.make(), record = store.sending(owner, 'step')
  store.retain(record, true); h.advance(30 * 24 * 60 * 60_000)
  assert.equal(h.make().read(owner).record.state, 'legacy_unconfirmed')
  assert.equal(h.make().sending(owner, 'step').commandRequestId, X)
  assert.deepEqual(h.ids, [X])
})
test('provided cross-tab lock encloses async work', async () => {
  let held = false
  const h = harness()
  const store = createRunCommandStore({ storage: () => h.storage, lock: async (key, body) => {
    assert.equal(key, runCommandKey(owner)); held = true
    try { return await body() } finally { held = false }
  } })
  assert.equal(await store.withLock(owner, async () => { assert.equal(held, true); return 7 }), 7)
  assert.equal(held, false)
})
test('B17c source check: only explicit identity clear/corrupt discard remove records; neither uses age', () => {
  const text = readFileSync(new URL('../../../src/features/designer/center/run/runCommandStore.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(text, /expiresAt|expires_at|ttl|setTimeout/i)
  const clear = text.slice(text.indexOf('function clear('), text.indexOf('function sending('))
  assert.doesNotMatch(clear, /createdAt|Date|now\(/)
  assert.equal((text.match(/removeItem\(runCommandKey/g) ?? []).length, 2)
})
