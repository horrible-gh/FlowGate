/** flowgate.v02test.0066 #36-1: every open tab's conflict mark — 병합 충돌 / AI 해결 중 · n/m — from the Git panel's own
 *  answers (git/status rows + the AI run of conflict tabs only), with the Graph deciding where Git has no conflict row. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, type EffectScope } from 'vue'
import type { FlowAiActivity, ProjectGitStatus } from '@/features/project'
import type { ProjectEventSubscription } from '@/features/project/projectEvents'
import { TAB_CONFLICT_EVENTS, TAB_CONFLICT_POLL_MS, useFlowTabConflicts } from '@/features/designer/center/flowTabConflicts'

type Row = { groupId: string; status?: string; mergeId?: number | null; reviewState?: string | null }

function gitStatus(rows: Row[], enabled = true): ProjectGitStatus {
  return {
    enabled, baseBranch: 'main', slots: [],
    pending: rows.map((row) => ({ groupId: row.groupId, status: row.status ?? 'conflict', mergeId: row.mergeId === undefined ? 7 : row.mergeId,
      acDocId: null, reviewState: row.reviewState ?? null, finalApprovalBound: false, plan: null })),
    baseDirty: { files: [], mergeInProgress: false, aiRun: false },
    baseUntracked: { files: [], truncated: false, total: null },
  }
}

const IDLE: FlowAiActivity = { active: false, runId: null, status: null, actionScope: null, conflictProgress: null }
const resolvingRun = (done: number | null, total: number | null): FlowAiActivity => ({
  active: true, runId: 'r1', status: 'running', actionScope: 'resolve_conflict', conflictProgress: done === null || total === null ? null : { done, total } })

function harness(init: { flows?: string[]; rows?: Row[]; ai?: Record<string, FlowAiActivity>; graph?: Record<string, boolean> } = {}) {
  const world = reactive({
    projectId: 'p' as string | null,
    flows: init.flows ?? ['a', 'b', 'c'],
    graph: (init.graph ?? {}) as Record<string, boolean | undefined>,
  })
  const server = { status: gitStatus(init.rows ?? []) as ProjectGitStatus | null, ai: { ...(init.ai ?? {}) } as Record<string, FlowAiActivity>, statusFails: false, aiFails: false }
  let subscription: ProjectEventSubscription | null = null
  const stop = vi.fn()
  const source = {
    loadStatus: vi.fn(async (_pid: string) => { if (server.statusFails) throw new Error('down'); return server.status }),
    loadAiActivity: vi.fn(async (gid: string) => { if (server.aiFails) throw new Error('down'); return server.ai[gid] ?? IDLE }),
    subscribe: vi.fn((_pid: string, sub: ProjectEventSubscription) => { subscription = sub; return stop }),
  }
  const scope: EffectScope = effectScope()
  const marks = scope.run(() => useFlowTabConflicts({
    projectId: () => world.projectId, flows: () => world.flows, graphConflicts: () => world.graph, source }))!
  const emit = (type: string, groupId: string) => subscription!.event({ type, groupId, docId: null, payload: { group_id: groupId } })
  return { world, server, source, marks, scope, stop, emit, resync: () => subscription!.resync?.() }
}

/** Let the reads in flight (and the reads they start) settle. */
async function settle() {
  for (let i = 0; i < 8; i += 1) { await Promise.resolve(); await nextTick() }
}

let current: ReturnType<typeof harness> | null = null
function start(init?: Parameters<typeof harness>[0]) { current = harness(init); return current }

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { current?.scope.stop(); current = null; vi.useRealTimers() })

describe('useFlowTabConflicts (v02test.0066 #36-1)', () => {
  it('reads git/status once for the whole Project and the AI run only for tabs with a conflict', async () => {
    const h = start({ rows: [{ groupId: 'b' }, { groupId: 'zz' }, { groupId: 'c', status: 'waiting' }] })
    await settle()
    expect(h.source.loadStatus).toHaveBeenCalledTimes(1)
    expect(h.source.loadStatus).toHaveBeenCalledWith('p')
    expect(h.source.loadAiActivity.mock.calls.map((call) => call[0])).toEqual(['b'])
    expect(h.marks.value).toEqual({ b: { state: 'conflict' } })
    expect(h.source.subscribe).toHaveBeenCalledWith('p', expect.objectContaining({ events: TAB_CONFLICT_EVENTS }))
    // Nothing is polled while no tab is being resolved.
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS * 4)
    expect(h.source.loadStatus).toHaveBeenCalledTimes(1)
    expect(h.source.loadAiActivity).toHaveBeenCalledTimes(1)
  })

  it('a tab not being looked at turns to AI 해결 중 · n/m when the resolver starts, follows its count, and back when it is stopped', async () => {
    const h = start({ rows: [{ groupId: 'b' }] })
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'conflict' })
    h.server.ai.b = resolvingRun(1, 46)
    h.emit('ai_invoke_started', 'b')
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: 1, total: 46 })
    // Only the resolving tab is polled: one AI read per interval, no git/status read.
    const statusReads = h.source.loadStatus.mock.calls.length
    h.server.ai.b = resolvingRun(12, 46)
    h.source.loadAiActivity.mockClear()
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS)
    await settle()
    expect(h.source.loadAiActivity.mock.calls.map((call) => call[0])).toEqual(['b'])
    expect(h.source.loadStatus).toHaveBeenCalledTimes(statusReads)
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: 12, total: 46 })
    // Stopped with conflicts left: the row stays a conflict.
    delete h.server.ai.b
    h.emit('ai_invoke_finished', 'b')
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'conflict' })
    expect(h.source.loadStatus.mock.calls.length).toBe(statusReads + 1)
    h.source.loadAiActivity.mockClear()
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS * 3)
    expect(h.source.loadAiActivity).not.toHaveBeenCalled()
  })

  it('drops the mark when the resolver finishes into a review state (해결 끝 · 승인 대기) — even while the Graph still says conflict', async () => {
    const h = start({ rows: [{ groupId: 'b' }], ai: { b: resolvingRun(45, 46) }, graph: { b: true } })
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: 45, total: 46 })
    delete h.server.ai.b
    h.server.status = gitStatus([{ groupId: 'b', reviewState: 'resolved_pending_review' }])
    h.emit('ai_invoke_finished', 'b')
    await settle()
    expect(h.marks.value).toEqual({})
    // A resolver running again on a review row wins, like the panel's card (gitSession groupCard).
    h.server.ai.b = resolvingRun(0, 3)
    h.emit('ai_invoke_started', 'b')
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: 0, total: 3 })
  })

  it('judges like the Git panel: no AI 해결 중 without a merge id, other AI runs are not resolving, unknown review states stay a conflict', async () => {
    const h = start({
      rows: [{ groupId: 'a', mergeId: null }, { groupId: 'b' }, { groupId: 'c', reviewState: 'open' }],
      ai: { a: resolvingRun(1, 2), b: { active: true, runId: 'r2', status: 'running', actionScope: 'chat' } },
    })
    await settle()
    expect(h.marks.value).toEqual({ a: { state: 'conflict' }, b: { state: 'conflict' }, c: { state: 'conflict' } })
    expect(h.source.loadAiActivity.mock.calls.map((call) => call[0]).sort()).toEqual(['b', 'c'])
  })

  it('shows AI 해결 중 without numbers when the server gave no count', async () => {
    const h = start({ rows: [{ groupId: 'b' }], ai: { b: resolvingRun(null, null) } })
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: null, total: null })
  })

  it('shows a conflict Git reports although the Graph has none (not read yet / no card for it)', async () => {
    const h = start({ rows: [{ groupId: 'c' }], graph: { a: false } })
    await settle()
    expect(h.marks.value).toEqual({ c: { state: 'conflict' } })
  })

  it('lets the Graph decide where Git has no conflict row, and asks Git again when the Graph changes', async () => {
    const h = start({ rows: [], graph: { a: false } })
    await settle()
    expect(h.marks.value).toEqual({})
    const reads = h.source.loadStatus.mock.calls.length
    h.world.graph = { a: true }
    await nextTick()
    // Shown at once from the Graph (as before this change) …
    expect(h.marks.value).toEqual({ a: { state: 'conflict' } })
    await settle()
    // … and Git is asked what it is.
    expect(h.source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(h.marks.value).toEqual({ a: { state: 'conflict' } })
    // Git off: its rows are not used, the Graph still decides.
    h.server.status = gitStatus([{ groupId: 'b' }], false)
    h.emit('git_status_changed', 'b')
    await settle()
    expect(h.marks.value).toEqual({ a: { state: 'conflict' } })
  })

  it('reads on the Git ledger events of open tabs only, and never for an AI run of a tab without a conflict', async () => {
    const h = start({ rows: [] })
    await settle()
    h.source.loadStatus.mockClear()
    h.emit('git_merge_conflict', 'not-open')
    h.emit('ai_invoke_started', 'a')
    h.emit('ai_invoke_finished', 'a')
    await settle()
    expect(h.source.loadStatus).not.toHaveBeenCalled()
    expect(h.source.loadAiActivity).not.toHaveBeenCalled()
    h.server.status = gitStatus([{ groupId: 'c' }])
    h.emit('git_merge_conflict', 'c')
    await settle()
    expect(h.source.loadStatus).toHaveBeenCalledTimes(1)
    expect(h.marks.value).toEqual({ c: { state: 'conflict' } })
    // The conflict is gone (merged / aborted).
    h.server.status = gitStatus([])
    h.emit('git_finalize_done', 'c')
    await settle()
    expect(h.marks.value).toEqual({})
  })

  it('coalesces a burst of events into at most one more read', async () => {
    const h = start({ rows: [{ groupId: 'b' }] })
    await settle()
    h.source.loadStatus.mockClear()
    for (let i = 0; i < 5; i += 1) h.emit('git_pending_changed', 'b')
    await settle()
    expect(h.source.loadStatus.mock.calls.length).toBeLessThanOrEqual(2)
  })

  it('keeps the last answer when a read fails, and reads again on resync', async () => {
    const h = start({ rows: [{ groupId: 'b' }], ai: { b: resolvingRun(1, 46) } })
    await settle()
    h.server.statusFails = true
    h.server.aiFails = true
    h.emit('git_status_changed', 'b')
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS)
    await settle()
    expect(h.marks.value.b).toEqual({ state: 'resolving', done: 1, total: 46 })
    h.server.statusFails = false
    h.server.aiFails = false
    h.server.status = gitStatus([])
    h.resync()
    await settle()
    expect(h.marks.value).toEqual({})
  })

  it('reads for a newly opened tab, forgets a closed one, and starts over on another Project', async () => {
    const h = start({ flows: ['a'], rows: [{ groupId: 'b' }], ai: { b: resolvingRun(2, 4) } })
    await settle()
    expect(h.marks.value).toEqual({})
    h.source.loadStatus.mockClear()
    h.world.flows = ['a', 'b']
    await settle()
    expect(h.source.loadStatus).toHaveBeenCalledTimes(1)
    expect(h.marks.value).toEqual({ b: { state: 'resolving', done: 2, total: 4 } })
    h.source.loadStatus.mockClear()
    h.source.loadAiActivity.mockClear()
    h.world.flows = ['a']
    await settle()
    expect(h.marks.value).toEqual({})
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS * 2)
    expect(h.source.loadStatus).not.toHaveBeenCalled()
    expect(h.source.loadAiActivity).not.toHaveBeenCalled()
    h.world.projectId = 'q'
    await settle()
    expect(h.stop).toHaveBeenCalledTimes(1)
    expect(h.source.subscribe).toHaveBeenLastCalledWith('q', expect.anything())
    expect(h.source.loadStatus).toHaveBeenLastCalledWith('q')
  })

  it('stops reading when disposed', async () => {
    const h = start({ rows: [{ groupId: 'b' }], ai: { b: resolvingRun(1, 46) } })
    await settle()
    h.scope.stop()
    h.source.loadAiActivity.mockClear()
    h.source.loadStatus.mockClear()
    await vi.advanceTimersByTimeAsync(TAB_CONFLICT_POLL_MS * 3)
    expect(h.source.loadAiActivity).not.toHaveBeenCalled()
    expect(h.source.loadStatus).not.toHaveBeenCalled()
    expect(h.stop).toHaveBeenCalledTimes(1)
  })

  it('without a source (no Project store) only the Graph decides', async () => {
    const world = reactive({ graph: { a: true } as Record<string, boolean | undefined> })
    const scope = effectScope()
    const marks = scope.run(() => useFlowTabConflicts({ projectId: () => 'p', flows: () => ['a', 'b'], graphConflicts: () => world.graph, source: null }))!
    expect(marks.value).toEqual({ a: { state: 'conflict' } })
    world.graph = {}
    await nextTick()
    expect(marks.value).toEqual({})
    scope.stop()
  })
})
