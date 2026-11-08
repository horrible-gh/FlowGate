// flowgate.v02.0156 T1 — run preparation (A0~A8) on an insecure context: the default identity path (no uuid injected),
// failures that name their stage and code (R2/R3), the persisted diagnostics (E2), and the R4 two-tab race model.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive, watch } from 'vue'
import { flushPromises } from '@vue/test-utils'
const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), serverLogout: vi.fn() }))
vi.mock('@shared/api', () => ({ ...http, extractApiErrorMessage: (_: unknown, fallback: string) => fallback }))
import { startFlowRun, fetchCurrentFlowRun, pauseFlowRun, resumeFlowRun, stopFlowRun, lookupFlowRunCommand, voidFlowRunCommand } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import { createFlowRunControl, createFlowRunState, type FlowRunState, type FlowRunControlDeps } from '@/features/designer/center/run/flowRunControl'
import { createRunCommandStore, runCommandKey, runCommandOrphanKey, COMMAND_UUID, type CommandStorage } from '@/features/designer/center/run/runCommandStore'
import { recentRunFailureDiagnostics, RUN_FAILURE_DIAGNOSTICS_KEY, type RunFailureDiagnostic } from '@/features/designer/center/run/runFailureDiagnostic'
import { runErrorText } from '@/features/designer/shell/runFailureToast'
import { AiRequestError } from '@/features/ai/api'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ko from '@/app/i18n/ko'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
registerAppMessages(i18n)
i18n.global.locale.value = 'ko'
const words = (e: any) => runErrorText(e, (k, v) => v ? i18n.global.t(k, v) : i18n.global.t(k), k => i18n.global.te(k))

/** The server's own format check (run_request.py). */
const SERVER_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const owner = { username: 'kim', projectId: 'p', flowId: 'f' }
const NEW_CODES = ['run_command_storage_unavailable', 'run_command_record_corrupt', 'run_command_mode_requires_void',
  'run_command_invalid_identity', 'run_command_busy', 'run_command_unresolved', 'internal_error', 'client_error', 'ai_setup_check_failed'] as const
const realCrypto = globalThis.crypto
const rawRun = (status = 'idle', seq = 0) => ({ run_id: 'run_1', flow_id: 'f', status, last_event_seq: seq, idle_reason: status === 'idle' ? 'step_done' : null })
const httpError = (status?: number, code = 'rejected') => status === undefined ? new Error('timeout')
  : { response: { status, data: { ok: false, error: { code, message: 'private' } } } }

/** Insecure context as Chrome has it on http://<LAN-IP>: no randomUUID, no navigator.locks; getRandomValues stays. */
function insecureContext() {
  vi.stubGlobal('crypto', { getRandomValues: (b: Uint8Array) => realCrypto.getRandomValues(b) })
  Object.defineProperty(globalThis.navigator, 'locks', { value: undefined, configurable: true })
  vi.stubGlobal('isSecureContext', false)
}

function source() {
  return createServerFlowGraphSource({ loadFlowCanvas: vi.fn(), saveFlowCanvasLayout: vi.fn(),
    loadFlowRun: fetchCurrentFlowRun, startFlowRun, pauseFlowRun, stopFlowRun, resumeFlowRun, lookupFlowRunCommand, voidFlowRunCommand })
}
function control(extra: Partial<FlowRunControlDeps> = {}, run: any = null) {
  const states = reactive<Record<string, FlowRunState>>({ f: { ...createFlowRunState(), readState: 'read', run } })
  const diagnose = vi.fn<(r: RunFailureDiagnostic) => void>()
  const deps: FlowRunControlDeps = { source: source(), projectId: () => 'p', isOpen: () => true, username: () => 'kim',
    prepare: vi.fn(async () => true), diagnose, refresh: () => {}, ...extra }
  return { states, diagnose, ctl: createFlowRunControl(deps, states) }
}
const posts = () => http.postRequest.mock.calls.filter(([url]) => url === '/api/v1/flows/f/runs')

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks()
  http.getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/current')) return { status: 200, data: { run: null } }
    return { status: 200, data: { found: false } }
  })
  http.postRequest.mockImplementation(async (url: string, body: any) => ({ status: 201, data: { run: rawRun('idle', 1),
    command: { command_request_id: body.command_request_id, run_id: 'run_1', command_seq: 1, replayed: false } } }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); delete (globalThis.navigator as any).locks })

describe('0156 T1 §2.1 R1 — the default identity path on an insecure context', () => {
  it('S-R1-4 default store (nothing injected), no randomUUID / navigator.locks: next and all reach start with a v4 id', async () => {
    insecureContext()
    expect(typeof (globalThis.crypto as any).randomUUID).toBe('undefined')
    expect((globalThis.navigator as any).locks).toBeUndefined()
    expect(globalThis.isSecureContext).toBe(false)
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    const { ctl, states } = control()
    for (const mode of ['next', 'all'] as const) {
      expect(await ctl.run('f', mode)).toBe(true)
      expect(states.f.error).toBeNull()
    }
    const sent = posts().map(([, body]) => body)
    expect(sent.map(b => b.mode)).toEqual(['step', 'full'])
    for (const body of sent) { expect(body.command_request_id).toMatch(SERVER_V4); expect(body.command_request_id).toMatch(COMMAND_UUID) }
    expect(new Set(sent.map(b => b.command_request_id)).size).toBe(2)
    // Success clears the record; the cross-tab lease is released.
    expect(Object.keys(localStorage).filter(k => k.startsWith('flowgate.runCommand'))).toEqual([])
    // The default wiring guarded each command with the cross-tab lease (no navigator.locks here).
    expect(writes.mock.calls.filter(([k]) => String(k).startsWith('flowgate.runCommandLock.v1.')).length).toBeGreaterThanOrEqual(2)
    writes.mockRestore()
    console.log('S_R1_4', JSON.stringify(sent))
  })
  it('S-R2-3 (R1 original defect) a TypeError from the identity source is client_error at prepare, named, never silent', async () => {
    const store = createRunCommandStore({ storage: () => localStorage, uuid: () => { throw new TypeError('crypto.randomUUID is not a function at secret C:\\stack') } })
    const { ctl, states, diagnose } = control({ commandStore: store })
    expect(await ctl.run('f', 'next')).toBe(false)
    const e = states.f.error!
    expect(e).toMatchObject({ code: 'client_error', stage: 'prepare', outcome: 'failed', commandRequestId: null, errorName: 'TypeError', message: '' })
    expect(posts()).toHaveLength(0)
    const record = diagnose.mock.calls.at(-1)![0]
    expect(record).toMatchObject({ code: 'client_error', stage: 'prepare', errorName: 'TypeError' })
    expect(JSON.stringify({ e, record })).not.toMatch(/randomUUID is not|secret|stack/)
    const text = words(e)
    expect(text).toContain('실행 준비 요청을 처리하지 못했습니다.')
    expect(text).toContain(ko.designer.top.run_errors.client_error)
    expect(text).not.toContain('gui.designer.top.')
  })
})

describe('0156 T1 §2.2 R2 / R3 / E2', () => {
  it('S-R2-2 no username: command_lookup / run_command_storage_unavailable, nothing sent', async () => {
    const { ctl, states } = control({ username: () => null, commandStore: createRunCommandStore({ storage: () => localStorage }) })
    expect(await ctl.run('f', 'all')).toBe(false)
    expect(states.f.error).toMatchObject({ stage: 'command_lookup', code: 'run_command_storage_unavailable', outcome: 'failed' })
    expect(words(states.f.error)).toContain(ko.designer.top.run_errors.run_command_storage_unavailable)
    expect(posts()).toHaveLength(0)
  })
  it.each([
    ['corrupt', (s: Storage) => s.setItem(runCommandKey(owner), '{bad'), 'run_command_record_corrupt'],
    ['mode', (s: Storage) => s.setItem(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: '12345678-1234-4567-89ab-123456789abc', ...owner, mode: 'full', createdAt: Date.now(), attempts: 1, state: 'unconfirmed' })), null],
  ] as const)('S-R2-1 (control) %s record: no POST, record bytes kept', async (_name, arrange, code) => {
    arrange(localStorage)
    const before = localStorage.getItem(runCommandKey(owner))
    http.getRequest.mockImplementation(async (url: string) => url.endsWith('/current') ? { status: 200, data: { run: null } } : { status: 503, data: {} })
    const { ctl, states } = control({ commandStore: createRunCommandStore({ storage: () => localStorage }) })
    expect(await ctl.run('f', 'next')).toBe(false); await flushPromises()
    expect(posts()).toHaveLength(0)
    expect(localStorage.getItem(runCommandKey(owner))).toBe(before)
    if (code) expect(states.f.error?.code).toBe(code)
  })
  it('S-R2-1 (control) quota on the record write: run_command_storage_unavailable at prepare, errorName kept, no POST', async () => {
    const storage: CommandStorage = { getItem: k => localStorage.getItem(k), removeItem: k => localStorage.removeItem(k),
      get length() { return localStorage.length }, key: i => localStorage.key(i),
      setItem: () => { throw Object.assign(new Error('quota secret'), { name: 'QuotaExceededError' }) } }
    const { ctl, states, diagnose } = control({ commandStore: createRunCommandStore({ storage: () => storage }) })
    expect(await ctl.run('f', 'next')).toBe(false)
    expect(states.f.error).toMatchObject({ stage: 'prepare', code: 'run_command_storage_unavailable', errorName: 'QuotaExceededError' })
    expect(diagnose.mock.calls.at(-1)![0]).toMatchObject({ errorName: 'QuotaExceededError' })
    expect(JSON.stringify(states.f.error)).not.toContain('secret')
    expect(posts()).toHaveLength(0)
  })
  it('S-R2-4 no answer at start stays unconfirmed (not client_error); record retained and looked up again (0149)', async () => {
    vi.useFakeTimers()
    http.postRequest.mockRejectedValue(httpError())
    const { ctl, states } = control({ commandStore: createRunCommandStore({ storage: () => localStorage }) })
    expect(await ctl.run('f', 'next')).toBe(false)
    expect(states.f.error).toMatchObject({ stage: 'start', outcome: 'unconfirmed', code: null, transport: 'no_response' })
    expect(states.f.error?.errorName).toBeUndefined() // a transport error is not a client failure
    const record = JSON.parse(localStorage.getItem(runCommandKey(owner))!)
    expect(record.state).toBe('unconfirmed')
    const lookups = () => http.getRequest.mock.calls.filter(([url]) => String(url).includes('/commands/')).length
    const before = lookups()
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 10)
    expect(lookups()).toBeGreaterThan(before)
    expect(posts()).toHaveLength(1)
    ctl.clear()
  })
  it('S-R2-5 every new code exists in ko/en/ja with one key set and a non-empty reason; existing counts hold', () => {
    const tables = [ko, en, ja].map(m => m.designer.top.run_errors as Record<string, string>)
    expect(Object.keys(tables[1]).sort()).toEqual(Object.keys(tables[0]).sort())
    expect(Object.keys(tables[2]).sort()).toEqual(Object.keys(tables[0]).sort())
    for (const code of NEW_CODES) for (const table of tables) {
      expect(typeof table[code]).toBe('string'); expect(table[code].trim().length).toBeGreaterThan(0)
      expect(code.startsWith('blocked_')).toBe(false)
    }
    expect(Object.keys(tables[0]).filter(k => k.startsWith('blocked_') || ['graph_revision_migration_blocked', 'graph_node_protected', 'graph_node_key_reused'].includes(k))).toHaveLength(12)
    for (const m of [ko, en, ja]) expect(Object.keys(m.designer.top.run_command_recovery)).toHaveLength(13)
    for (const locale of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = locale
      for (const code of NEW_CODES) {
        const text = words({ kind: 'run', code, status: null, message: '', approval: null, refusal: null, seq: 1, stage: 'prepare', action: 'start', mode: 'next', outcome: 'failed' })
        expect(text).toContain(({ ko, en, ja })[locale].designer.top.run_errors[code])
        expect(text).not.toContain('gui.designer.top.')
      }
    }
    i18n.global.locale.value = 'ko'
  })
  it.each([
    ['server code', new AiRequestError('private message', 'ai_roles_unavailable', []), 'ai_roles_unavailable', null],
    ['no code', new AiRequestError('private message', null, []), null, null],
    ['status', Object.assign(new Error('private'), { code: 'internal_error', status: 503 }), 'internal_error', 503],
    ['unsafe code', Object.assign(new Error('private'), { code: 'Bearer secret-token', status: 401 }), null, 401],
  ] as const)('S-R3-1 beforePrepare throws (%s): ai_setup_check_failed at before_prepare, safe causeCode, reason shown', async (_n, thrown, cause, status) => {
    const { ctl, states, diagnose } = control({ beforePrepare: vi.fn(async () => { throw thrown }), commandStore: createRunCommandStore({ storage: () => localStorage }) })
    expect(await ctl.run('f', 'all')).toBe(false)
    expect(states.f.error).toMatchObject({ code: 'ai_setup_check_failed', stage: 'before_prepare', status, causeCode: cause, message: '' })
    expect(diagnose.mock.calls.at(-1)![0]).toMatchObject({ code: 'ai_setup_check_failed', stage: 'before_prepare', causeCode: cause })
    expect(words(states.f.error)).toContain(ko.designer.top.run_errors.ai_setup_check_failed)
    expect(JSON.stringify(states.f.error)).not.toMatch(/private|secret/)
    expect(posts()).toHaveLength(0)
  })
  it('S-R3-1 beforePrepare false (cancelled) stays silent', async () => {
    const { ctl, states } = control({ beforePrepare: vi.fn(async () => false) })
    expect(await ctl.run('f', 'next')).toBe(false)
    expect(states.f.error).toBeNull(); expect(posts()).toHaveLength(0)
  })
  it('S-E2-1 diagnostics are mirrored to sessionStorage, restored on reload, bounded at 50; a failing write breaks nothing', async () => {
    vi.resetModules()
    const first = await import('@/features/designer/center/run/runFailureDiagnostic')
    const make = (i: number) => ({ publicationId: `p${i}`, scope: 's', seq: i, localRequestId: null, serverRequestId: null, action: 'start', stage: 'prepare', mode: 'next', projectId: 'p', flowId: 'f', targetRunId: null, baselineRunId: null, status: null, code: 'client_error', outcome: 'failed', observation: null, errorName: 'TypeError' })
    for (let i = 1; i <= 55; i++) first.recordRunFailureDiagnostic(make(i))
    expect(JSON.parse(sessionStorage.getItem(RUN_FAILURE_DIAGNOSTICS_KEY)!)).toHaveLength(50)
    vi.resetModules()
    const reloaded = await import('@/features/designer/center/run/runFailureDiagnostic')
    const restored = reloaded.recentRunFailureDiagnostics()
    expect(restored).toHaveLength(50)
    expect(restored[0].seq).toBe(6); expect(restored.at(-1)).toMatchObject({ seq: 55, stage: 'prepare', code: 'client_error', errorName: 'TypeError' })
    expect(restored.every(r => !('message' in r))).toBe(true)
    // Writes failing (quota / disabled storage): recording, and the command and its failure, still happen normally.
    vi.stubGlobal('sessionStorage', { getItem: () => null, setItem: () => { throw new Error('quota') }, removeItem: () => {} })
    expect(() => reloaded.recordRunFailureDiagnostic(make(99))).not.toThrow()
    const failing = createRunCommandStore({ storage: () => localStorage, uuid: () => { throw new TypeError('x') } })
    const { ctl, states } = control({ commandStore: failing, diagnose: undefined })
    expect(await ctl.run('f', 'next')).toBe(false)
    expect(states.f.error).toMatchObject({ code: 'client_error', stage: 'prepare' })
    vi.unstubAllGlobals()
    expect(reloaded.recentRunFailureDiagnostics().at(-1)?.seq).toBe(99)
  })
  it('S-E2-2 the console accessor returns copies only', async () => {
    const { ctl } = control({ commandStore: createRunCommandStore({ storage: () => localStorage, uuid: () => { throw new TypeError('x') } }) })
    await ctl.run('f', 'next')
    const accessor = (globalThis as any).__flowgateRunDiagnostics as () => RunFailureDiagnostic[]
    expect(typeof accessor).toBe('function')
    const before = JSON.stringify(recentRunFailureDiagnostics())
    const copy = accessor() as any[]
    copy.length = 0
    const again = accessor() as any[]
    expect(again.length).toBeGreaterThan(0)
    again[again.length - 1].code = 'tampered'
    expect(JSON.stringify(recentRunFailureDiagnostics())).toBe(before)
    expect(Object.getOwnPropertyDescriptor(globalThis, '__flowgateRunDiagnostics')?.writable).toBe(false)
  })
})

// ---------------------------------------------------------------------------------------------------------------------
// §2.3 R4-M — deterministic model of two tabs of one Flow on an insecure origin (no navigator.locks, no randomUUID).
// Both tabs share one localStorage, but each tab sees the OTHER tab's writes only after `lag` ms (cross-process storage
// propagation). The server follows 0149: same id replays, a different id is a new command on an idle run, refused with
// run_already_active while the run is running. One start answer is lost; then both tabs reload (F5: new control/store).
// ---------------------------------------------------------------------------------------------------------------------
type Accepted = { run_id: string; command_seq: number; mode: string; cause: string }
function sharedStorage(lag: number) {
  const log: { at: number; tab: string; key: string; value: string | null }[] = []
  const view = (tab: string): CommandStorage => ({
    getItem(key) {
      for (let i = log.length - 1; i >= 0; i--) {
        const e = log[i]
        if (e.key === key && (e.tab === tab || e.at <= Date.now() - lag)) return e.value
      }
      return null
    },
    setItem(key, value) { log.push({ at: Date.now(), tab, key, value: String(value) }) },
    removeItem(key) { log.push({ at: Date.now(), tab, key, value: null }) },
    get length() { return visible(tab).length },
    key(index) { return visible(tab)[index] ?? null },
  })
  const keys = () => [...new Set(log.map(e => e.key))]
  const visible = (tab: string) => keys().filter(k => view(tab).getItem(k) !== null)
  const truth = (key: string) => { for (let i = log.length - 1; i >= 0; i--) if (log[i].key === key) return log[i].value; return null }
  const truthKeys = () => keys().filter(k => truth(k) !== null)
  return { view, truth, truthKeys }
}
// A glob, not an import: the same model also measures the pre-fix code, where this module does not exist yet.
const lockModules = import.meta.glob<typeof import('@/features/designer/center/run/runCommandLock')>('/src/features/designer/center/run/runCommandLock.ts')
async function lockModule() {
  const load = Object.values(lockModules)[0]
  return load ? await load() : null
}
/** IndexedDB-like lease backend for the model: atomic steps that commit after a random 0..300 ms (fake timers). */
function atomicLeaseBackend(random: () => number) {
  const values = new Map<string, { owner: string; until: number }>()
  return {
    atomic: true, kind: 'model-idb',
    async update<T>(key: string, decide: (c: { owner: string; until: number } | null) => { next?: { owner: string; until: number } | null; result: T }) {
      const d = Math.floor(random() * 300)
      if (d) await new Promise(r => setTimeout(r, d))
      const out = decide(values.get(key) ?? null)
      if (out.next === null) values.delete(key)
      else if (out.next) values.set(key, { ...out.next })
      return out.result
    },
  }
}
function seeded(seed: number) { return () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff } }

async function raceOnce(o: { scenario: 1 | 2; lag: number; offset: number; lose: 'first' | 'second'; backend: 'storage' | 'atomic' | 'none' }) {
  const mod = await lockModule()
  const shared = sharedStorage(o.lag)
  const registry = new Map<string, Accepted | 'void'>()
  let run: { status: string; seq: number } | null = o.scenario === 2 ? { status: 'idle', seq: 5 } : null
  let events = 0, posted = 0, ids = 0, phase: 'click' | 'f5' = 'click'
  let inside = 0, overlap = 0
  const resent: string[] = []
  http.getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/current')) return { status: 200, data: { run: run ? rawRun(run.status, run.seq) : null } }
    const cid = /\/commands\/([^/]+)$/.exec(url)![1]
    const row = registry.get(cid)
    return { status: 200, data: row === 'void' ? { found: false, voided: true } : row ? { found: true, ...row } : { found: false } }
  })
  http.postRequest.mockImplementation(async (url: string, body: any) => {
    if (/\/void$/.test(url)) throw new Error('void not expected in this model')
    const cid = body.command_request_id as string
    if (phase === 'f5') resent.push(cid)
    const index = ++posted
    await new Promise(r => setTimeout(r, 5))
    const existing = registry.get(cid)
    let answer: any
    if (existing && existing !== 'void') answer = { status: 201, data: { run: rawRun(run!.status, run!.seq), command: { command_request_id: cid, ...existing, replayed: true } } }
    else if (run && run.status !== 'idle') throw httpError(409, 'run_already_active')
    else {
      events++
      const row = { run_id: 'run_1', command_seq: (run?.seq ?? 0) + 1, mode: body.mode, cause: run ? 'user_command' : 'user_start' }
      registry.set(cid, row)
      run = { status: 'running', seq: row.command_seq }
      answer = { status: 201, data: { run: rawRun('running', run.seq), command: { command_request_id: cid, ...row, replayed: false } } }
    }
    if ((o.lose === 'first' && index === 1) || (o.lose === 'second' && index === 2)) throw httpError()
    return answer
  })
  const confirmed = new Set<string>()
  const random = seeded(o.lag * 1000 + o.offset * 10 + (o.lose === 'first' ? 1 : 2) + o.scenario * 7)
  const atomic = atomicLeaseBackend(random)
  const tabs = (['A', 'B'] as const).map(name => {
    const mk = () => {
      const states = reactive<Record<string, FlowRunState>>({ f: { ...createFlowRunState(), readState: 'read', run: run ? { runId: 'run_1', status: run.status, lastEventSeq: run.seq, openNodeKeys: [] } as any : null } })
      const base = mod && o.backend !== 'none'
        ? mod.createRunCommandLock(o.backend === 'atomic' ? { backend: atomic } : { storage: () => shared.view(name), indexedDb: null })
        : null
      // Probe: how many tabs are inside the lock body at once (what the lease is meant to keep at one).
      const lock = base ? <T,>(key: string, body: (fence?: () => Promise<void>) => Promise<T>) => base(key, async (fence) => {
        inside++; if (inside > 1) overlap++
        try { return await body(fence) } finally { inside-- }
      }) : undefined
      const store = createRunCommandStore({ storage: () => shared.view(name), now: Date.now,
        uuid: () => `12345678-1234-4567-89ab-${String(++ids).padStart(12, '0')}`, ...(lock ? { lock } : {}) })
      const ctl = createFlowRunControl({ source: source(), projectId: () => 'p', isOpen: () => true, username: () => 'kim',
        commandStore: store, prepare: vi.fn(async () => true), refresh: () => {}, diagnose: () => {} }, states)
      const stop = watch(() => states.f?.confirmedCommand, c => { if (c) confirmed.add(c.commandRequestId) }, { flush: 'sync' })
      return { states, store, ctl, stop }
    }
    return { name, current: mk(), mk }
  })
  const clicks = [tabs[0].current.ctl.run('f', 'next')]
  if (o.offset) await vi.advanceTimersByTimeAsync(o.offset)
  clicks.push(tabs[1].current.ctl.run('f', 'next'))
  await vi.advanceTimersByTimeAsync(20_000)
  await Promise.all(clicks)
  // F5 on both tabs: new instances read the run and only look up.
  phase = 'f5'
  for (const t of tabs) { t.current.stop(); t.current.ctl.clear(); t.current = t.mk(); t.current.ctl.read('f') }
  await vi.advanceTimersByTimeAsync(30_000)
  const accepted = [...registry.entries()].filter(([, v]) => v !== 'void').map(([k]) => k)
  const stored = new Set<string>()
  const primary = shared.truth(runCommandKey(owner)); if (primary) stored.add(JSON.parse(primary).commandRequestId)
  // The journal: one key per identity (runCommandOrphanKey is the prefix). Absent before the 0156 fix.
  if (typeof runCommandOrphanKey === 'function') {
    for (const key of shared.truthKeys()) if (key.startsWith(runCommandOrphanKey(owner))) stored.add(key.slice(runCommandOrphanKey(owner).length))
  }
  const lost = accepted.filter(cid => !confirmed.has(cid) && !stored.has(cid))
  const violations = {
    a: lost.length > 0,                                                  // an accepted id traced nowhere
    b: accepted.some(cid => !confirmed.has(cid)) || resent.length > 0,   // F5 did not settle it by lookup (or resent)
    c: !(registry.size === events && events === 1),                     // registry rows = user_start/user_command = 1 click effect
    d: events > 1,                                                       // an extra step / attempt
    e: events > 1,                                                       // model: accepted server commands stand for Git effects
  }
  for (const t of tabs) { t.current.stop(); t.current.ctl.clear() }
  return { ...o, lock: !!mod && o.backend !== 'none', overlap, accepted, confirmed: [...confirmed], stored: [...stored], lost, resent, events, violations }
}

async function raceMatrix(backend: 'storage' | 'atomic', lags: number[]) {
  const results = []
  for (const scenario of [1, 2] as const) for (const lag of lags) for (const offset of [0, 1, 10, 30]) for (const lose of ['first', 'second'] as const) {
    localStorage.clear()
    results.push(await raceOnce({ scenario, lag, offset, lose, backend }))
  }
  const count = (k: keyof (typeof results)[number]['violations']) => results.filter(r => r.violations[k]).length
  const overlapByLag = Object.fromEntries(lags.map(lag => [lag, results.filter(r => r.lag === lag && r.overlap > 0).length]))
  return { backend, runs: results.length, lock: results[0].lock, a: count('a'), b: count('b'), c: count('c'), d: count('d'), e: count('e'),
    violating: results.filter(r => Object.values(r.violations).some(Boolean)).length, overlapByLag,
    examples: results.filter(r => Object.values(r.violations).some(Boolean)).slice(0, 3) }
}

describe('0156 T1 §2.3 R4-M two tabs, one lost start answer, both reload', () => {
  it('S-R4-2 localStorage-fallback lease, lags up to 1000 ms (beyond its 120 ms settle): a~e hold; body overlap measured', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    insecureContext()
    const summary = await raceMatrix('storage', [0, 5, 20, 50, 200, 500, 1000])
    console.log('R4M_SUMMARY_STORAGE', JSON.stringify(summary))
    expect(summary.lock).toBe(true)
    expect(summary.violating).toBe(0)
  }, 240_000)

  it('S-R4-2 atomic (IndexedDB-like) lease with 0..300 ms step delays, lags up to 1000 ms: a~e hold and bodies never overlap', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    insecureContext()
    const summary = await raceMatrix('atomic', [0, 50, 200, 1000])
    console.log('R4M_SUMMARY_ATOMIC', JSON.stringify(summary))
    expect(summary.violating).toBe(0)
    expect(Object.values(summary.overlapByLag).every(n => n === 0)).toBe(true)
  }, 240_000)

  it('S-R4-4 retain finds another tab\'s record: own id stays journaled -> F5 lookup confirms it (no resend) -> dropped', async () => {
    vi.useFakeTimers()
    const X = '12345678-1234-4567-89ab-123456789abc', Y = '12345678-1234-4567-89ab-123456789abd'
    const registry = new Map<string, Accepted>()
    let phase = 'click'; const resent: string[] = []
    http.getRequest.mockImplementation(async (url: string) => {
      if (url.endsWith('/current')) return { status: 200, data: { run: rawRun('running', 1) } }
      const row = registry.get(/\/commands\/([^/]+)$/.exec(url)![1])
      return { status: 200, data: row ? { found: true, ...row } : { found: false } }
    })
    http.postRequest.mockImplementation(async (_url: string, body: any) => {
      if (phase === 'f5') resent.push(body.command_request_id)
      registry.set(body.command_request_id, { run_id: 'run_1', command_seq: 1, mode: 'step', cause: 'user_start' })
      // Another tab's record replaces ours while the answer is on the way; then the answer is lost.
      localStorage.setItem(runCommandKey(owner), JSON.stringify({ v: 1, commandRequestId: Y, ...owner, mode: 'step', createdAt: Date.now(), attempts: 1, state: 'sending' }))
      throw httpError()
    })
    const store = () => createRunCommandStore({ storage: () => localStorage, uuid: () => X, lock: async (_k, body) => body() })
    const a = control({ commandStore: store() })
    expect(await a.ctl.run('f', 'next')).toBe(false)
    expect(a.states.f.error).toMatchObject({ outcome: 'unconfirmed', commandRequestId: X })
    expect(JSON.parse(localStorage.getItem(runCommandOrphanKey(owner) + X)!)).toMatchObject({ commandRequestId: X, mode: 'step' })
    expect(JSON.parse(localStorage.getItem(runCommandKey(owner))!).commandRequestId).toBe(Y)
    a.ctl.clear()
    phase = 'f5'
    const f5 = control({ commandStore: store() })
    const confirmed: string[] = []
    watch(() => f5.states.f?.confirmedCommand, c => { if (c) confirmed.push(c.commandRequestId) }, { flush: 'sync' })
    f5.ctl.read('f'); await vi.advanceTimersByTimeAsync(100)
    expect(confirmed).toContain(X)
    expect(resent).toEqual([])
    expect(localStorage.getItem(runCommandOrphanKey(owner) + X)).toBeNull()
    expect(JSON.parse(localStorage.getItem(runCommandKey(owner))!).commandRequestId).toBe(Y)
    f5.ctl.clear()
  })
})

// ---------------------------------------------------------------------------------------------------------------------
// 0156 T1 rework — unresolved journaled identities hold every command; the fence; Web Locks missing in a secure context.
// ---------------------------------------------------------------------------------------------------------------------
describe('0156 T1 R4 rework — no new identity around an unresolved one; the execution boundary', () => {
  const X = '12345678-1234-4567-89ab-123456789abc'
  const journal = (cid: string, createdAt = Date.now()) => localStorage.setItem(runCommandOrphanKey(owner) + cid, JSON.stringify({ commandRequestId: cid, mode: 'step', createdAt }))
  const lookups = (cid: string) => http.getRequest.mock.calls.filter(([url]) => String(url).endsWith(`/commands/${cid}`)).length

  it.each([
    ['not_found', { status: 200, data: { found: false } }, 'unconfirmed'],
    ['failed (5xx)', { status: 503, data: {} }, 'unconfirmed'],
    ['failed (no answer)', 'throw', 'unconfirmed'],
    ['legacy (404 without envelope)', 'legacy', 'legacy'],
  ] as const)('S-R4-7 orphan X %s with an empty primary record: no POST (no new id), recovery shown, looked up again; F5 and re-clicks too', async (_name, answer, key) => {
    vi.useFakeTimers()
    journal(X)
    let accepted = false
    http.getRequest.mockImplementation(async (url: string) => {
      if (url.endsWith('/current')) return { status: 200, data: { run: rawRun('idle', 3) } }
      if (url.endsWith(`/commands/${X}`)) {
        if (accepted) return { status: 200, data: { found: true, run_id: 'run_1', command_seq: 4, mode: 'step', cause: 'user_command' } }
        if (answer === 'throw') throw httpError()
        if (answer === 'legacy') throw { response: { status: 404, data: 'Not Found' } }
        return answer
      }
      return { status: 200, data: { found: false } }
    })
    const mk = () => control({ commandStore: createRunCommandStore({ storage: () => localStorage, lock: async (_k, body) => body() }) }, { runId: 'run_1', status: 'idle', lastEventSeq: 3, openNodeKeys: [] })
    const tab = mk()
    for (const mode of ['next', 'all', 'next'] as const) {
      expect(await tab.ctl.run('f', mode)).toBe(false)
      expect(posts()).toHaveLength(0)
      expect(tab.states.f.commandRecovery?.key).toBe(key)
      expect(tab.states.f.error).toMatchObject({ stage: 'command_lookup', outcome: 'unconfirmed', commandRequestId: X })
    }
    expect(localStorage.getItem(runCommandOrphanKey(owner) + X)).not.toBeNull()
    const before = lookups(X)
    await vi.advanceTimersByTimeAsync(15_000 + 10)
    expect(lookups(X)).toBeGreaterThan(before) // periodic re-lookup
    expect(posts()).toHaveLength(0)
    // F5 while still unresolved: lookup only, the identity stays, still no POST.
    tab.ctl.clear()
    const f5 = mk()
    f5.ctl.read('f'); await vi.advanceTimersByTimeAsync(50)
    expect(f5.states.f.commandRecovery?.key).toBe(key)
    expect(await f5.ctl.run('f', 'next')).toBe(false)
    expect(posts()).toHaveLength(0)
    expect(localStorage.getItem(runCommandOrphanKey(owner) + X)).not.toBeNull()
    // Late acceptance: the next periodic lookup finds X -> confirmed, journal entry dropped; only now may a new id go.
    accepted = true
    const confirmed: string[] = []
    watch(() => f5.states.f?.confirmedCommand, c => { if (c) confirmed.push(c.commandRequestId) }, { flush: 'sync' })
    await vi.advanceTimersByTimeAsync(300_000)
    expect(confirmed).toContain(X)
    expect(localStorage.getItem(runCommandOrphanKey(owner) + X)).toBeNull()
    expect(await f5.ctl.run('f', 'next')).toBe(true)
    expect(posts()).toHaveLength(1)
    expect(posts()[0][1].command_request_id).not.toBe(X)
    f5.ctl.clear()
  })

  it('S-R4-7 a stale unresolved orphan: void is offered; the explicit void resolves it and only then a new id is sent', async () => {
    vi.useFakeTimers()
    journal(X, Date.now() - 11 * 60 * 1000)
    let voided = false
    http.getRequest.mockImplementation(async (url: string) => {
      if (url.endsWith('/current')) return { status: 200, data: { run: rawRun('idle', 3) } }
      return { status: 200, data: voided && url.endsWith(`/commands/${X}`) ? { found: false, voided: true } : { found: false } }
    })
    http.postRequest.mockImplementation(async (url: string, body: any) => {
      if (url.endsWith(`/commands/${X}/void`)) { voided = true; return { status: 200, data: { found: false, voided: true } } }
      return { status: 201, data: { run: rawRun('idle', 4), command: { command_request_id: body.command_request_id, run_id: 'run_1', command_seq: 4, replayed: false } } }
    })
    const tab = control({ commandStore: createRunCommandStore({ storage: () => localStorage, lock: async (_k, body) => body() }) }, { runId: 'run_1', status: 'idle', lastEventSeq: 3, openNodeKeys: [] })
    expect(await tab.ctl.run('f', 'next')).toBe(false)
    expect(posts()).toHaveLength(0)
    expect(tab.states.f.commandRecovery?.key).toBe('stale')
    expect(tab.states.f.commandRecovery?.voidPrevious).toBeTypeOf('function')
    await tab.states.f.commandRecovery!.voidPrevious!()
    expect(http.postRequest.mock.calls.some(([url]) => String(url).endsWith(`/commands/${X}/void`))).toBe(true)
    expect(localStorage.getItem(runCommandOrphanKey(owner) + X)).toBeNull()
    expect(await tab.ctl.run('f', 'next')).toBe(true)
    expect(posts()).toHaveLength(1)
    tab.ctl.clear()
  })

  it('S-R4-9 secure context WITHOUT Web Locks: the default wiring still injects the lease; two controls serialize and keep both ids', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('isSecureContext', true)
    Object.defineProperty(globalThis.navigator, 'locks', { value: undefined, configurable: true })
    expect(globalThis.isSecureContext).toBe(true)
    expect((globalThis.navigator as any).locks).toBeUndefined()
    let inFlight = 0, maxInFlight = 0
    http.postRequest.mockImplementation(async (_url: string, body: any) => {
      inFlight++; maxInFlight = Math.max(maxInFlight, inFlight)
      await new Promise(r => setTimeout(r, 400))
      inFlight--
      return { status: 201, data: { run: rawRun('idle', 1), command: { command_request_id: body.command_request_id, run_id: 'run_1', command_seq: 1, replayed: false } } }
    })
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    // Default wiring: no commandStore injected.
    const a = control({}, null), b = control({}, null)
    const clicks = [a.ctl.run('f', 'next'), b.ctl.run('f', 'next')]
    await vi.advanceTimersByTimeAsync(10_000)
    expect(await Promise.all(clicks)).toEqual([true, true])
    expect(maxInFlight).toBe(1) // the second command waited for the first tab's lease
    const sent = posts().map(([, body]) => body.command_request_id)
    expect(new Set(sent).size).toBe(2)
    expect(writes.mock.calls.some(([k]) => String(k).startsWith('flowgate.runCommandLock.v1.'))).toBe(true)
    expect(writes.mock.calls.filter(([k]) => String(k).startsWith(runCommandOrphanKey(owner))).map(([k]) => String(k).slice(runCommandOrphanKey(owner).length)).sort()).toEqual([...sent].sort())
    expect(Object.keys(localStorage).filter(k => k.startsWith('flowgate.runCommand'))).toEqual([])
    writes.mockRestore()
    a.ctl.clear(); b.ctl.clear()
  })

  it('S-R4-10 a tab frozen in the AI setup modal past its lease: the other tab takes over and sends; the resumed tab never sends', async () => {
    vi.useFakeTimers()
    const mod = (await lockModule())!
    const values = new Map<string, { owner: string; until: number }>()
    const backend = { atomic: true, kind: 'model-idb', async update(key: string, decide: (c: any) => any) {
      const out = decide(values.get(key) ?? null)
      if (out.next === null) values.delete(key); else if (out.next) values.set(key, { ...out.next })
      return out.result
    } }
    let resumeModal: (v: boolean) => void = () => {}
    const frozen = mod.createRunCommandLock({ backend, every: () => () => {}, token: () => 'A' }) // heartbeat never runs
    const live = mod.createRunCommandLock({ backend, token: () => 'B' })
    const storeA = createRunCommandStore({ storage: () => localStorage, lock: frozen })
    const storeB = createRunCommandStore({ storage: () => localStorage, lock: live })
    const a = control({ commandStore: storeA, beforePrepare: () => new Promise<boolean>(r => { resumeModal = r }) }, null)
    const b = control({ commandStore: storeB }, null)
    const clickA = a.ctl.run('f', 'next')
    await vi.advanceTimersByTimeAsync(100)
    const clickB = b.ctl.run('f', 'next')
    await vi.advanceTimersByTimeAsync(6_500) // A's lease (6 s) ran out with no heartbeat; B took over and sent
    expect(posts()).toHaveLength(1)
    resumeModal(true) // A's modal answers now
    await vi.advanceTimersByTimeAsync(5_000)
    expect(await clickB).toBe(true)
    expect(await clickA).toBe(false)
    expect(a.states.f.error).toMatchObject({ code: 'run_command_busy', stage: 'before_prepare', outcome: 'failed' })
    expect(words(a.states.f.error)).toContain(ko.designer.top.run_errors.run_command_busy)
    expect(posts()).toHaveLength(1)
    a.ctl.clear(); b.ctl.clear()
  })
})
