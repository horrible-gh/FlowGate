import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive, watch } from 'vue'
import { flushPromises } from '@vue/test-utils'
const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), serverLogout: vi.fn() }))
vi.mock('@shared/api', () => ({ ...http, extractApiErrorMessage: (_: unknown, fallback: string) => fallback }))
import { startFlowRun, fetchCurrentFlowRun, pauseFlowRun, resumeFlowRun, stopFlowRun, lookupFlowRunCommand, voidFlowRunCommand, commandIdentityOf } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import { createFlowRunControl, createFlowRunState, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { createRunCommandStore, runCommandKey, STALE_AFTER_MS, type RunCommandRecord } from '@/features/designer/center/run/runCommandStore'
import { createRunResultWatch } from '@/features/ai/runResult/runResultWatch'
import { toFlowCommandResult } from '@/features/project/commandResultApi'
import { createRunFailurePublisher } from '@/features/designer/shell/runFailureToast'
import { clearStoredAuth } from '@/core/auth/logout'

const X = '12345678-1234-4567-89ab-123456789abc'
const Y = '12345678-1234-4567-89ab-123456789abd'
const Z = '12345678-1234-4567-89ab-123456789abe'
const owner = { username: 'kim', projectId: 'p', flowId: 'f' }
const rawRun = (seq = 0) => ({ run_id: 'run_1', flow_id: 'f', status: 'idle', last_event_seq: seq, idle_reason: 'completed' })
const error = (status?: number, code = 'rejected', envelope = true) => status === undefined ? new Error('timeout')
  : { response: { status, data: envelope ? { ok: false, error: { code, message: 'private' } } : { detail: 'Not Found' } } }
type Entry = { run_id: string; command_seq: number; mode: string; cause: string } | 'void'
const cleanup: (() => void)[] = []

function model() {
  const registry = new Map<string, Entry>()
  const sent: string[] = [], arrived: string[] = [], generated: string[] = []
  let nextId = 0, events = 0
  const state = { delivery: true, response: 'ok' as number | 'ok' | 'timeout' | 'unreadable', legacy: false,
    lookup: 'ok' as 'ok' | 'failed' | 'not_found', voidFailure: false, currentSeq: 0,
    startCode: 'rejected', beforeAnswer: null as null | ((cid: string) => void) }
  const store = () => createRunCommandStore({ storage: () => localStorage, now: Date.now, uuid: () => {
    let cid = [X, Y, Z][nextId++]
    while (cid && registry.has(cid)) cid = [X, Y, Z][nextId++]
    if (!cid) throw new Error('unexpected extra identity')
    generated.push(cid); return cid
  } })
  function accept(cid: string, mode = 'step') {
    if (!registry.has(cid)) { events++; registry.set(cid, { run_id: 'run_1', command_seq: events * 10, mode, cause: events === 1 ? 'user_start' : 'user_command' }) }
    return registry.get(cid)
  }
  http.getRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/current')) return { status: 200, data: { run: rawRun(state.currentSeq) } }
    const match = /\/commands\/([^/]+)$/.exec(url)
    if (!match) throw new Error(`unexpected GET ${url}`)
    if (state.legacy) throw error(404, '', false)
    if (state.lookup === 'failed') throw error(503)
    const row = state.lookup === 'not_found' ? undefined : registry.get(match[1])
    return { status: 200, data: row === 'void' ? { found: false, voided: true } : row ? { found: true, ...row } : { found: false } }
  })
  http.postRequest.mockImplementation(async (url: string, body: Record<string, any>) => {
    const voided = /\/commands\/([^/]+)\/void$/.exec(url)
    if (voided) {
      if (state.legacy) throw error(405, '', false)
      if (state.voidFailure) throw error(503)
      const row = registry.get(voided[1])
      if (row && row !== 'void') return { status: 200, data: { voided: false, found: true, ...row } }
      registry.set(voided[1], 'void'); return { status: 200, data: { voided: true } }
    }
    if (/\/(pause|resume|stop)$/.test(url)) {
      if (typeof state.response === 'number') throw error(state.response)
      if (state.response === 'timeout') throw error()
      return { status: 200, data: { run: rawRun() } }
    }
    expect(url).toBe('/api/v1/flows/f/runs')
    const cid = body.command_request_id as string
    sent.push(cid)
    // The durable pending identity MUST exist before HTTP is invoked.
    expect(JSON.parse(localStorage.getItem(runCommandKey(owner))!).commandRequestId).toBe(cid)
    if (!state.delivery) throw error()
    arrived.push(cid)
    state.beforeAnswer?.(cid)
    if (typeof state.response === 'number' && state.response >= 400 && state.response < 500 && state.response !== 408) throw error(state.response, state.startCode)
    const existing = registry.get(cid)
    if (existing === 'void') throw error(409, 'command_request_voided')
    // Old servers ignore the field and may execute each retry. They have no registry.
    const row = state.legacy ? { run_id: 'run_1', command_seq: ++events * 10, mode: body.mode, cause: 'user_command' }
      : accept(cid, body.mode) as Exclude<Entry, 'void'>
    if (state.response === 'timeout') throw error()
    if (typeof state.response === 'number') throw error(state.response)
    if (state.response === 'unreadable') return { status: 201, headers: { 'x-request-id': 'response_id' }, data: {} }
    return { status: 201, data: { run: rawRun(row.command_seq + 1), ...(!state.legacy ? { command: { command_request_id: cid, ...row, replayed: !!existing } } : {}) } }
  })
  return { registry, state, store, accept, generated, sent, arrived, get events() { return events } }
}
function tab(m: ReturnType<typeof model>, username = 'kim', baseline: 'run' | 'none' | 'unread' = 'run') {
  const states = reactive<Record<string, FlowRunState>>({ f: { ...createFlowRunState(), readState: baseline === 'unread' ? 'unread' : 'read', run: baseline === 'run' ? { runId: 'run_1', status: 'idle', lastEventSeq: 0, openNodeKeys: [] } : null } })
  const store = m.store(), prepare = vi.fn(() => true), diagnose = vi.fn(), beforePrepare = vi.fn(async () => true)
  const source = createServerFlowGraphSource({ loadFlowCanvas: vi.fn(), saveFlowCanvasLayout: vi.fn(),
    loadFlowRun: fetchCurrentFlowRun, startFlowRun, pauseFlowRun, stopFlowRun, resumeFlowRun,
    lookupFlowRunCommand, voidFlowRunCommand })
  const load = vi.fn(async (runId: string, commandSeq?: number) => toFlowCommandResult({
    run_id: runId, flow_id: 'f', command_seq: commandSeq ?? m.events * 10, latest: true, scope: 'step', started_at: 't', ended_at: 't',
    ended: true, end_seq: (commandSeq ?? 0) + 1, state: 'completed', idle_reason: 'completed', next_state: 'completed',
    counts: { done: 1, failed: 0, waiting: 0 }, nodes: [], failure: null, failures: [], roles: { instruction: [], execution: [], review: [] }, message_count: 0, messages: [],
  }))
  const results = createRunResultWatch({ load })
  const armCommand = vi.spyOn(results, 'armCommand'), arm = vi.spyOn(results, 'arm')
  const stopWatch = watch(() => states.f?.confirmedCommand, command => { if (command) results.armCommand('f', command) }, { flush: 'sync' })
  const ctl = createFlowRunControl({ source, projectId: () => 'p', isOpen: () => true, username: () => username,
    commandStore: store, prepare, beforePrepare, diagnose, refresh: () => {} }, states)
  async function click(mode: 'next' | 'all' = 'next') {
    const prior = states.f.run
    const taken = await ctl.run('f', mode)
    if (taken && !states.f.run?.command) { results.arm('f', prior); results.observe('f', states.f.run) }
    await flushPromises(); return taken
  }
  const close = () => { stopWatch(); ctl.clear(); results.dispose() }
  cleanup.push(close)
  return { states, store, ctl, click, prepare, beforePrepare, diagnose, results, load, armCommand, arm, close }
}
function seed(m: ReturnType<typeof model>, state: RunCommandRecord['state'] = 'unconfirmed') {
  const record: RunCommandRecord = { ...owner, v: 1, commandRequestId: X, createdAt: Date.now(), attempts: 1, mode: 'step', state }
  m.store().put(record); return record
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T00:00:00Z')); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks() })
afterEach(() => { cleanup.splice(0).forEach(close => close()); vi.useRealTimers() })

describe('0149 T-B transport -> controller -> durable pending -> actual result watch', () => {
  it.each(['run', 'none', 'unread'] as const)('B7a/b/c accepted response discarded; baseline %s connects only by found', async baseline => {
    const m = model(), t = tab(m, 'kim', baseline)
    m.state.response = 'timeout'
    expect(await t.click()).toBe(false)
    expect(t.states.f.error).toMatchObject({ outcome: 'unconfirmed', baseline: { state: baseline }, transport: 'no_response', responseStatus: null, commandRequestId: X })
    expect(t.armCommand).not.toHaveBeenCalled(); expect(t.arm).not.toHaveBeenCalled()
    const before = m.sent.length
    await t.click()
    expect(m.sent.length).toBe(before); expect(t.prepare).toHaveBeenCalledTimes(1)
    expect(t.armCommand).toHaveBeenCalledWith('f', { commandRequestId: X, runId: 'run_1', commandSeq: 10, replayed: true })
    expect(t.load).toHaveBeenCalledWith('run_1', 10); expect(m.events).toBe(1)
    expect(t.store.read(owner).kind).toBe('empty'); expect(t.states.f.commandRecovery?.key).toBe('confirmed')
    m.state.response = 'ok'; await t.click()
    expect(m.arrived).toEqual([X, Y]); expect(m.generated).toEqual([X, Y]); expect(m.events).toBe(2)
  })
  it.each([false, true])('B7d/B8/B9 observation changes never prove acceptance (settle=%s)', async settle => {
    const m = model(), t = tab(m); m.state.delivery = false
    await t.click(); m.state.currentSeq = settle ? 999 : 333
    t.ctl.read('f'); await flushPromises()
    expect(t.armCommand).not.toHaveBeenCalled(); expect(t.arm).not.toHaveBeenCalled()
    expect(t.results.state.resultOpen).toBe(false); expect(t.store.read(owner).kind).toBe('pending')
    expect(m.events).toBe(0)
  })
  it('B10 another logical command or actor at the same time cannot satisfy X', async () => {
    const m = model(), t = tab(m); seed(m); m.accept(Y)
    t.ctl.read('f'); await flushPromises()
    expect(t.armCommand).not.toHaveBeenCalled(); expect(t.results.state.resultOpen).toBe(false)
    expect(t.store.read(owner).kind).toBe('pending')
  })
  it.each([400, 401, 403, 404, 409, 422])('B11a first HTTP %i is rejected and resolves first transmission', async status => {
    const m = model(), t = tab(m); m.state.response = status
    await t.click()
    expect(t.states.f.error?.outcome).toBe('rejected'); expect(t.states.f.error?.outcome).not.toBe('unconfirmed')
    expect(t.store.read(owner).kind).toBe('empty'); expect(m.events).toBe(0)
  })
  it.each([408, 500, 502, 503, 504, 'timeout'] as const)('B11b %s is unconfirmed, retained, no arm', async response => {
    const m = model(), t = tab(m); m.state.response = response
    await t.click()
    expect(t.states.f.error?.outcome).toBe('unconfirmed'); expect(t.states.f.error?.outcome).not.toBe('rejected')
    expect(t.store.read(owner).kind).toBe('pending'); expect(t.armCommand).not.toHaveBeenCalled(); expect(t.arm).not.toHaveBeenCalled()
  })
  it('B11c unreadable 201 differs from no-response timeout and keeps request ID', async () => {
    const m = model(), t = tab(m); m.state.response = 'unreadable'
    await t.click()
    expect(t.states.f.error).toMatchObject({ code: 'run_response_unreadable', outcome: 'unconfirmed', transport: 'unreadable_2xx', responseStatus: 201, serverRequestId: 'response_id' })
    expect(t.diagnose).toHaveBeenCalledWith(expect.objectContaining({ transport: 'unreadable_2xx', responseStatus: 201, commandRequestId: X }))
    await t.click(); expect(t.load).toHaveBeenCalledWith('run_1', 10)
  })
  it('B11d prepare throws before any record or UUID', async () => {
    const m = model(), t = tab(m); t.prepare.mockImplementation(() => { throw new Error('prepare') })
    await t.click(); expect(m.generated).toEqual([]); expect(m.sent).toEqual([])
    expect(t.states.f.error).toMatchObject({ outcome: 'failed', stage: 'prepare' })
  })
  it.each(['pause', 'resume', 'stop'] as const)('B11e %s keeps classification and has no identity', async action => {
    const m = model(), t = tab(m); m.state.response = 408
    expect(await t.ctl[action]('f')).toBe(false); await flushPromises()
    expect(t.states.f.error?.outcome).toBe('unconfirmed'); expect(m.generated).toEqual([])
    const [, body] = http.postRequest.mock.calls.at(-1)!
    expect(body).not.toHaveProperty('command_request_id')
  })
  it('B11f failed current read keeps failed read lock', async () => {
    const m = model(), t = tab(m); m.state.response = 'timeout'
    http.getRequest.mockRejectedValue(error(503))
    await t.click(); expect(t.states.f.readState).toBe('failed'); expect(t.store.read(owner).kind).toBe('pending')
  })
  it('B13b not delivered then same-mode retry arrives with X once; B13c same-tab double click', async () => {
    const m = model(), t = tab(m); m.state.delivery = false
    await t.click(); m.state.delivery = true
    const one = t.click(), two = t.click(); await Promise.all([one, two])
    expect(m.generated).toEqual([X]); expect(m.arrived).toEqual([X]); expect(m.events).toBe(1)
    expect(t.store.read(owner).kind).toBe('empty')
  })
  it.each(['sending', 'unconfirmed'] as const)('B14/B15 F5 automatic lookup for %s never starts', async pendingState => {
    const m = model(); seed(m, pendingState); m.accept(X)
    const t = tab(m, 'kim', 'none'); t.ctl.read('f'); await flushPromises()
    expect(m.sent).toEqual([]); expect(m.generated).toEqual([]); expect(t.prepare).not.toHaveBeenCalled()
    expect(t.armCommand).toHaveBeenCalledTimes(1); expect(t.load).toHaveBeenCalledWith('run_1', 10)
    expect(t.store.read(owner).kind).toBe('empty')
  })
  it('B15a not-found sending F5 retains, stable publisher, same-ID next click', async () => {
    const m = model(); seed(m, 'sending'); const t = tab(m)
    t.ctl.read('f'); await flushPromises(); const first = t.states.f.error!
    const publisher = createRunFailurePublisher(); expect(publisher.accept(first)).toBe(true)
    t.ctl.read('f'); await flushPromises(); expect(publisher.accept(t.states.f.error!)).toBe(false)
    expect(m.sent).toEqual([]); await t.click(); expect(m.arrived).toEqual([X]); expect(m.generated).toEqual([])
  })
  it('B15b/c close/reopen and real logout cleanup preserve owner record; another user cannot see it', async () => {
    const m = model(); seed(m); const a = tab(m); a.close()
    clearStoredAuth('kim')
    expect(m.store().read(owner).kind).toBe('pending')
    const other = tab(m, 'lee'); other.ctl.read('f'); await flushPromises(); expect(other.armCommand).not.toHaveBeenCalled()
    expect(m.store().read({ ...owner, username: 'lee' }).kind).toBe('empty')
    m.accept(X); const reopened = tab(m); reopened.ctl.read('f'); await flushPromises()
    expect(reopened.armCommand).toHaveBeenCalledTimes(1); expect(m.sent).toEqual([])
  })
  it('B16 another tab rereads shared identity and rereads again after first tab resolves it', async () => {
    const m = model(), a = tab(m), b = tab(m)
    m.state.response = 'timeout'; await a.click(); await b.click()
    expect(m.sent).toEqual([X]); expect(b.armCommand).toHaveBeenCalledTimes(1); expect(m.events).toBe(1)
    m.state.response = 'ok'; await a.click()
    expect(m.arrived).toEqual([X, Y]); expect(m.events).toBe(2)
  })
  it('B17a mode change voids undelivered X before Y/full; late X is tombstoned', async () => {
    const m = model(); seed(m); const t = tab(m)
    await t.click('all'); expect(m.registry.get(X)).toBe('void'); expect(m.arrived).toEqual([Y])
    expect(m.generated).toEqual([Y]); expect((m.registry.get(Y) as any).mode).toBe('full')
    const events = m.events; expect(m.accept(X)).toBe('void'); expect(m.events).toBe(events)
  })
  it.each(['failed', 'legacy'] as const)('B17a mode change + void %s blocks every start', async failure => {
    const m = model(); seed(m); const t = tab(m)
    m.state.voidFailure = failure === 'failed'; m.state.legacy = failure === 'legacy'
    await t.click('all'); expect(m.sent).toEqual([]); expect(m.generated).toEqual([]); expect(t.store.read(owner).kind).toBe('pending')
  })
  it.each([STALE_AFTER_MS + 1, 24 * 60 * 60_000 + 1, 30 * 24 * 60 * 60_000])('B17c/B19a elapsed %i never clears or mints a new ID; found consumes click', async elapsed => {
    const m = model(), t = tab(m); m.state.response = 'timeout'; await t.click()
    // Advance both clocks without executing timers, then click before the next background check.
    vi.setSystemTime(Date.now() + elapsed)
    expect(t.store.read(owner).kind).toBe('pending'); const posts = m.sent.length, events = m.events
    await t.click()
    expect(m.sent.length).toBe(posts); expect(m.events).toBe(events); expect(m.generated).toEqual([X]); expect(t.prepare).toHaveBeenCalledTimes(1)
    m.state.response = 'ok'; await t.click(); expect(m.generated).toEqual([X, Y]); expect(m.events).toBe(events + 1)
    console.info('B19_CLIENT_FOUND', { elapsed, arrived: m.arrived, eventsBeforeIntentionalSecondClick: events, extraStep: 0, finalEvents: m.events })
  })
  it.each([STALE_AFTER_MS + 1, 24 * 60 * 60_000 + 1, 30 * 24 * 60 * 60_000])('B19b elapsed %i undelivered reuses X exactly once', async elapsed => {
    const m = model(), t = tab(m); m.state.delivery = false; await t.click()
    vi.setSystemTime(Date.now() + elapsed); m.state.delivery = true; await t.click()
    expect(m.generated).toEqual([X]); expect(m.arrived).toEqual([X]); expect(m.events).toBe(1)
    console.info('B19_CLIENT_RETRY', { elapsed, arrived: m.arrived, generated: m.generated, events: m.events })
  })
  it('B17d/B19c elapsed with lookup failure blocks start, recovers only after lookup succeeds', async () => {
    const m = model(), t = tab(m); m.state.delivery = false; await t.click()
    vi.setSystemTime(Date.now() + 30 * 24 * 60 * 60_000); m.state.lookup = 'failed'
    await t.click(); expect(m.sent).toEqual([X]); expect(m.generated).toEqual([X]); expect(t.states.f.commandRecovery?.key).toBe('stale')
    m.state.lookup = 'ok'; m.state.delivery = true; await t.click(); expect(m.arrived).toEqual([X]); expect(m.events).toBe(1)
  })
  it.each([false, true])('B19d elapsed F5 does only lookup (found=%s)', async found => {
    const m = model(); seed(m); if (found) m.accept(X)
    vi.setSystemTime(Date.now() + 30 * 24 * 60 * 60_000)
    const t = tab(m); t.ctl.read('f'); await flushPromises()
    expect(m.sent).toEqual([]); expect(m.generated).toEqual([])
    expect(t.armCommand).toHaveBeenCalledTimes(found ? 1 : 0)
    expect(t.store.read(owner).kind).toBe(found ? 'empty' : 'pending')
  })
  it.each([false, true])('B19e explicit stale void returns voided or accepted (accepted=%s)', async accepted => {
    const m = model(); seed(m); vi.setSystemTime(Date.now() + STALE_AFTER_MS + 1)
    const t = tab(m); m.state.lookup = 'failed'; t.ctl.read('f'); await flushPromises()
    if (accepted) m.accept(X)
    m.state.lookup = 'ok'; await t.states.f.commandRecovery!.voidPrevious!(); await flushPromises()
    expect(t.store.read(owner).kind).toBe('empty'); expect(m.sent).toEqual([])
    expect(t.armCommand).toHaveBeenCalledTimes(accepted ? 1 : 0)
    expect(m.registry.get(X) === 'void').toBe(!accepted)
  })
  it.each([401, 403, 404, 409, 422])('B11a/B17b/B19f retransmission HTTP %i cannot resolve prior uncertainty', async status => {
    const m = model(), t = tab(m); m.state.delivery = false; await t.click()
    vi.setSystemTime(Date.now() + 30 * 24 * 60 * 60_000); m.state.delivery = true; m.state.response = status
    await t.click(); expect(m.generated).toEqual([X]); expect(m.arrived).toEqual([X])
    expect(t.store.read(owner)).toMatchObject({ kind: 'pending', record: { commandRequestId: X, attempts: 2 } })
    expect(t.armCommand).not.toHaveBeenCalled(); expect(m.events).toBe(0)
  })
  it.each([401, 403, 422])('B19f delayed first acceptance wins over retry HTTP %i', async status => {
    const m = model(), t = tab(m); m.state.delivery = false; await t.click()
    vi.setSystemTime(Date.now() + 24 * 60 * 60_000); m.state.delivery = true; m.state.response = status
    m.state.beforeAnswer = cid => { m.accept(cid) }
    await t.click(); expect(m.generated).toEqual([X]); expect(m.events).toBe(1)
    expect(t.store.read(owner).kind).toBe('empty'); expect(t.armCommand).toHaveBeenCalledTimes(1)
  })
  it('B17f/B18 old server reuses X and never arms unconfirmed; upgrade uses found', async () => {
    const m = model(), t = tab(m); m.state.legacy = true; m.state.response = 'timeout'
    await t.click(); await t.click(); expect(m.generated).toEqual([X]); expect(m.arrived).toEqual([X, X]); expect(t.armCommand).not.toHaveBeenCalled()
    expect(t.store.read(owner)).toMatchObject({ kind: 'pending', record: { state: 'legacy_unconfirmed' } })
    m.state.legacy = false; m.accept(X) // A delayed transmission accepted by the upgraded server.
    await t.click(); expect(m.sent).toEqual([X, X]); expect(t.armCommand).toHaveBeenCalledTimes(1)
  })
  it('B11 first conflict remains pending; command_request_voided resolves', async () => {
    const m = model(), t = tab(m); m.state.response = 409; m.state.startCode = 'command_request_conflict'
    await t.click(); expect(t.states.f.error?.outcome).toBe('rejected')
    expect(t.store.read(owner).kind).toBe('pending'); expect(m.generated).toEqual([X])
    m.state.startCode = 'command_request_voided'; await t.click()
    expect(t.store.read(owner).kind).toBe('empty'); expect(m.generated).toEqual([X]); expect(t.armCommand).not.toHaveBeenCalled()
  })
  it('B17f upgrade with not_found resends the retained old-server identity', async () => {
    const m = model(), t = tab(m); m.state.legacy = true; m.state.response = 'timeout'; await t.click()
    m.state.legacy = false; m.state.response = 'ok'; await t.click()
    expect(m.generated).toEqual([X]); expect(m.arrived).toEqual([X, X]); expect(t.store.read(owner).kind).toBe('empty')
  })
  it('B18 successful old response keeps heuristic arm; successful command block uses direct arm', async () => {
    const m = model(), t = tab(m); m.state.legacy = true
    await t.click(); expect(t.arm).toHaveBeenCalledTimes(1); expect(t.armCommand).not.toHaveBeenCalled()
    expect(t.store.read(owner).kind).toBe('empty')
    m.state.legacy = false; await t.click(); expect(t.armCommand).toHaveBeenCalledTimes(1)
  })
  it('B17c backoff is lookup-only, exposes explicit void, stops on close', async () => {
    const m = model(); seed(m); const t = tab(m); t.ctl.read('f'); await flushPromises()
    await vi.advanceTimersByTimeAsync(STALE_AFTER_MS)
    expect(t.states.f.commandRecovery?.key).toBe('stale'); expect(t.states.f.commandRecovery?.voidPrevious).toBeTypeOf('function')
    const calls = http.getRequest.mock.calls.length
    await vi.advanceTimersByTimeAsync(15_000 + 30_000 + 60_000)
    expect(http.getRequest.mock.calls.length).toBeGreaterThan(calls); expect(m.sent).toEqual([])
    t.close(); const afterClose = http.getRequest.mock.calls.length
    await vi.advanceTimersByTimeAsync(300_000); expect(http.getRequest.mock.calls.length).toBe(afterClose)
  })
  it('corrupt or storage-unavailable record blocks UUID and start until explicit corrupt discard', async () => {
    const m = model(), t = tab(m); localStorage.setItem(runCommandKey(owner), '{broken')
    await t.click(); expect(m.generated).toEqual([]); expect(m.sent).toEqual([])
    expect(localStorage.getItem(runCommandKey(owner))).toBe('{broken')
    await t.states.f.commandRecovery!.discardCorrupt!(); await t.click(); expect(m.events).toBe(1)
  })
  it('quota failure before HTTP never downgrades to identity-free POST', async () => {
    const m = model(), t = tab(m), set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    try { await t.click(); expect(m.sent).toEqual([]); expect(t.states.f.error?.outcome).toBe('failed') } finally { set.mockRestore() }
  })
  it('armCommand persisted/restore retains exact run and sequence', async () => {
    const m = model(), t = tab(m)
    t.load.mockImplementation(() => new Promise(() => {}))
    t.results.armCommand('f', { runId: 'run_exact', commandSeq: 81 })
    expect(t.load).toHaveBeenCalledWith('run_exact', 81)
    const snapshot = t.results.persisted()
    const restored = createRunResultWatch({ load: t.load }); cleanup.push(() => restored.dispose())
    restored.restore(snapshot.watches, snapshot.seen)
    expect(restored.persisted().watches).toEqual([{ flowId: 'f', runId: 'run_exact', commandSeq: 81, afterSeq: 0 }])
  })
})

describe('command lookup contract and safe deployment detection', () => {
  it.each([404, 405])('non-envelope %i is legacy; enveloped status is failed', async status => {
    http.getRequest.mockRejectedValue(error(status, 'other', false)); expect((await lookupFlowRunCommand('f', X)).kind).toBe('legacy')
    http.getRequest.mockRejectedValue(error(status)); expect((await lookupFlowRunCommand('f', X)).kind).toBe('failed')
    http.postRequest.mockRejectedValue(error(status, '', false)); expect((await voidFlowRunCommand('f', X)).kind).toBe('legacy')
  })
  it.each([401, 403, 408, 422, 500, 503])('HTTP %i is failed, never legacy', async status => {
    http.getRequest.mockRejectedValue(error(status)); expect((await lookupFlowRunCommand('f', X)).kind).toBe('failed')
  })
  it('only envelope flow_not_found clears flow; malformed 200 stays failed', async () => {
    http.getRequest.mockRejectedValue(error(404, 'flow_not_found')); expect((await lookupFlowRunCommand('f', X)).kind).toBe('flow_gone')
    for (const data of [{}, { found: true, run_id: 'run_1', command_seq: 0 }, { found: true, run_id: 'bad/path', command_seq: 1 }, { found: false, voided: 'true' }]) {
      http.getRequest.mockResolvedValue({ data }); expect((await lookupFlowRunCommand('f', X)).kind).toBe('failed')
    }
  })
  it('validates command identity strictly', () => {
    const valid = { command_request_id: X, run_id: 'run_1', command_seq: 2, replayed: true }
    expect(commandIdentityOf(valid)).toMatchObject({ commandRequestId: X, commandSeq: 2 })
    for (const change of [{ command_request_id: 'bad' }, { run_id: '../bad' }, { command_seq: 0 }, { command_seq: 1.1 }, { replayed: 'true' }]) expect(commandIdentityOf({ ...valid, ...change })).toBeUndefined()
  })
})
