// flowgate.v02.0117 T#4 — what the Designer's existing toast says after a run command (0012-T T#4 items 1–2).
//
// 1. A command that ended waiting for a person — the run idle `waiting_approval`; T#1 TR §5 observed it with the manual
//    write wait of a role set to 사용하지 않음 (S4 / S6) — is told once per run and reason (`tell`), in the existing words
//    (run_idle_reasons + card_states). A step boundary (step_done), the window's completed / failed and the reasons T#1
//    did not observe (hold, no_targets) are never told.
// 2. A run command whose draft confirmation the server refused for its issues ended with no word before (T#2 TR §2.2:
//    422 graph_validation_failed, no POST /runs, only the cards' issue border). It is now the command's refusal, so the
//    Shell's existing run toast (run_errors.graph_validation_failed) says it. [검증] and any other no-go stay as they were.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { toFlowCommandResult } from '@/features/project/commandResultApi'
import { TOLD_WAIT_REASONS, createRunResultWatch, isToldWait, runWaitText } from '@/features/ai/runResult/runResultWatch'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunCommand, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const t = (key: string) => String(i18n.global.t(key))
const te = (key: string) => i18n.global.te(key)

/** A command result as the server answers it; by default T#1 S4 / S6: [다음 단계] left 작업 waiting for its instruction writer. */
function result(over: Record<string, unknown> = {}) {
  return toFlowCommandResult({
    run_id: 'run_1', flow_id: 'f', command_seq: 5, latest: true, scope: 'step', started_at: '2026-11-01T00:00:00.000+00:00',
    ended_at: '2026-11-01T00:00:00.100+00:00', ended: true, continues: true, state: 'waiting',
    idle_reason: 'waiting_approval', next_state: 'waiting_approval', counts: { done: 0, failed: 0, waiting: 1 },
    nodes: [{ node_key: 'gn_t', title: '작업', doc_type: 'T', status: 'open', outcome: 'waiting', stage: 'instruction', reason: 'manual_write_wait', detail: null }],
    failure: null, failures: [], roles: { instruction: [{ kind: 'person' }], execution: [], review: [] }, message_count: 0, messages: [],
    ...over,
  })
}

describe('a run left waiting for a person is told once (item 1)', () => {
  beforeEach(() => { i18n.global.locale.value = 'ko' })

  it('tells the waiting command once, never again for the same run and reason, again for another run', async () => {
    const answers = [result(), result(), result({ run_id: 'run_2' })]
    const load = vi.fn(async () => answers.shift()!)
    const tell = vi.fn()
    const watch = createRunResultWatch({ load, tell })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 3 })     // POST /runs answered idle / waiting_approval
    await flushPromises()
    expect(tell).toHaveBeenCalledTimes(1)
    expect(tell.mock.calls[0][0]).toMatchObject({ runId: 'run_1', state: 'waiting', idleReason: 'waiting_approval' })
    expect(watch.state.resultOpen).toBe(false)                                  // no window for a wait
    expect(Object.keys(watch.pending)).toEqual(['f'])                           // still watched (a person's save goes on)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })     // the same wait read again
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(2)
    expect(tell).toHaveBeenCalledTimes(1)                                       // not told twice
    watch.arm('f', { runId: 'run_1', status: 'idle', lastEventSeq: 4 })
    watch.observe('f', { runId: 'run_2', status: 'idle', lastEventSeq: 1 })     // another run, the same reason
    await flushPromises()
    expect(tell).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['step_done (다음 단계 done)', { idle_reason: 'step_done', next_state: 'step_done', nodes: [] }, false],
    ['hold (not observed by T#1)', { idle_reason: 'hold', next_state: 'hold' }, false],
    ['no_targets (not observed by T#1)', { idle_reason: 'no_targets', next_state: 'no_targets' }, false],
    ['completed (the window opens)', { state: 'completed', idle_reason: 'completed', next_state: 'completed', continues: false }, true],
    ['failed (the window opens)', { state: 'failed', idle_reason: 'error', next_state: 'error', continues: false }, true],
    ['a wait that can no longer go on', { continues: false }, false],
  ] as const)('never tells %s', async (_name, over, window) => {
    const tell = vi.fn()
    const watch = createRunResultWatch({ load: vi.fn(async () => result(over)), tell })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 3 })
    await flushPromises()
    expect(isToldWait(result(over))).toBe(false)
    expect(tell).not.toHaveBeenCalled()
    expect(watch.state.resultOpen).toBe(window)
  })

  it('tells only the observed wait for a person; a watch without `tell` still works', async () => {
    expect(TOLD_WAIT_REASONS).toEqual(['waiting_approval'])
    expect(isToldWait(result())).toBe(true)
    const watch = createRunResultWatch({ load: vi.fn(async () => result()) })
    watch.arm('f', null)
    watch.observe('f', { runId: 'run_1', status: 'idle', lastEventSeq: 3 })
    await flushPromises()
    expect(Object.keys(watch.pending)).toEqual(['f'])
  })

  it('says it in the existing words: the run idle reason, then the waiting card and its Canvas state', () => {
    expect(runWaitText(result(), t, te)).toBe('사람 작성·승인 대기 — 작업: 수동 작성 대기')
    const gate = { node_key: 'gn_g', title: '확인', doc_type: null, status: 'open', outcome: 'waiting', stage: null, reason: null, detail: null }
    expect(runWaitText(result({ nodes: [gate] }), t, te)).toBe('사람 작성·승인 대기')
    i18n.global.locale.value = 'en'
    expect(runWaitText(result(), t, te)).toBe(`${t('gui.designer.top.run_idle_reasons.waiting_approval')} — 작업: ${t('gui.designer.center.card_states.manual_write_wait')}`)
    expect(t('gui.designer.top.run_idle_reasons.waiting_approval')).toBe('Waiting for a person (writing or approval)')
  })
})

// --- item 2: the run's draft confirmation refused for its issues ------------------------------------------------------

const P = 'test2'
const F = 'test2.default.0117'
const A = 'gn_a'
const B = 'gn_b'
const ISSUES = [{ code: 'cycle', structure: null, nodeIds: [A, B], edgeIds: ['ge_ab', 'ge_ba'], message: '' }]

const card = (id: string, label: string, x: number): FlowGraphNode => ({
  id, kind: 'task', title: label, x, y: 0, movable: true, structureEditable: true,
  definition: { nodeType: 'document', docType: 'T', paletteItemId: 'task', label, assignee: null },
} as FlowGraphNode)

/** A Flow whose saved draft (v3) holds a visible two-card cycle: the server refuses its confirmation (rule 4). */
function cycleServer(confirmError: Record<string, unknown> = { code: 'graph_validation_failed', issues: ISSUES }) {
  const calls: string[] = []
  const graph = (): FlowGraph => ({
    flowId: F, ownership: 'unassigned', viewMode: 'draft',
    draft: { version: 3, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    nodes: [card(A, '작업 A', 0), card(B, '작업 B', 300)],
    edges: [
      { id: 'ge_ab', source: A, target: B, edgeClass: 'execution', editable: true },
      { id: 'ge_ba', source: B, target: A, edgeClass: 'execution', editable: true },
    ],
  } as FlowGraph)
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => graph()),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async () => { calls.push('save'); return { version: 4, keyMap: {} } }),
    validateDraft: vi.fn(async () => ({ confirmable: false, issues: ISSUES })),
    confirmDraft: vi.fn(async (_p: string, _f: string, version: number) => {
      calls.push(`confirm ${version}`)
      throw Object.assign(new Error('refused'), confirmError)
    }),
    loadRun: vi.fn(async () => null),
    startRun: vi.fn(async (_p: string, _f: string, mode?: FlowRunCommand): Promise<FlowRunInfo> => {
      calls.push(`run ${mode}`)
      return { runId: 'run_1', status: 'running', openNodeKeys: [A] }
    }),
    stopRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'stopping', openNodeKeys: [] })),
    resumeRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'running', openNodeKeys: [] })),
  }
  return { source, calls }
}

describe('a run whose draft confirmation is refused for its issues says why (item 2)', () => {
  let center: DesignerCenterSession | null = null
  async function open(source: FlowGraphSource) {
    center = createDesignerCenterSession({ source, draftSaveDelayMs: 50, layoutSaveDelayMs: 50 })
    center.setProject(P)
    center.openFlow({ projectId: P, flowId: F, flowName: 'cycle' })
    await vi.runAllTimersAsync()
    return center
  }
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    center?.dispose()
    center = null
    vi.useRealTimers()
  })

  it.each([['next', 'step'], ['all', 'full']] as const)('[%s] is refused as graph_validation_failed: no run command, issues kept', async (mode, command) => {
    const { source, calls } = cycleServer()
    const session = await open(source)
    calls.length = 0
    expect(await session.runFlow(F, mode)).toBe(false)
    expect(calls).toEqual(['confirm 3'])
    expect(calls).not.toContain(`run ${command}`)
    expect(session.state.runs[F]?.error).toMatchObject({ kind: 'run', code: 'graph_validation_failed' })
    expect(session.state.edits[F]?.check).toEqual({ confirmable: false, issues: ISSUES })   // the cards still show them
    expect(te('gui.designer.top.run_errors.graph_validation_failed')).toBe(true)               // the existing sentence
  })

  it('[검증] tells no run refusal (its issues show on the cards as before)', async () => {
    const { source } = cycleServer()
    const session = await open(source)
    await session.validateDraft(F)
    expect(session.state.edits[F]?.check).toEqual({ confirmable: false, issues: ISSUES })
    expect(session.state.runs[F]?.error ?? null).toBeNull()
  })

  it('another refused confirmation (no issues) stays as it was: no run command and no new refusal', async () => {
    const { source, calls } = cycleServer({ code: 'server_error' })
    const session = await open(source)
    calls.length = 0
    expect(await session.runFlow(F, 'all')).toBe(false)
    expect(calls).toEqual(['confirm 3'])
    expect(session.state.runs[F]?.error ?? null).toBeNull()
    expect(session.state.edits[F]?.notice).toEqual({ kind: 'error', code: 'server_error' })
  })
})
