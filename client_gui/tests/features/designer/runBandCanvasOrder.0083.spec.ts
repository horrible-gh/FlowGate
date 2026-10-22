/**
 * flowgate.v02.0083 T#4 rev1 — the run band never shows an older run read's facts beside a newer Canvas read's status:
 *
 * - the Canvas read's `run` block carries the band's facts of the same read (command, idle reason, AI executions, waiting
 *   cards); flowCanvasApi / serverFlowGraphSource keep each only when sent.
 * - the Center session: a Canvas read newer than the run read (event sequence) tells the run as of itself — an idle
 *   reason that changed (사람 작성·승인 대기 → 완료) and a run that completed (실행 1/4 → none) are the Canvas read's, also
 *   when a run read answered before it or an older run response comes after it; an older Canvas read never hides a newer
 *   run read; a Canvas read of an older server (no band facts) shows no older read's reason or executions either.
 * - the older run read's node runs stay only for nodes the Canvas read sends none of (a draft view).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo, FlowRunNodeInfo } from '@/features/designer/center/graph/flowGraphSource'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { REQUESTED_REREAD_MS, createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { runBandView } from '@/features/designer/top/designerTop'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'

registerAppMessages(i18n)

const P = 'p'
const F = 'p.ui.0083'

/** The Canvas read's `run` block as the server sends it (rev1: with the band's facts of the same read). */
const canvasBlock = (status: string, seq: number, band: Record<string, unknown> | null) => ({
  run_id: 'run_1',
  status,
  revision_no: 1,
  open_node_keys: [],
  last_event_seq: seq,
  ...(band ?? {}),
})

const idleBand = (reason: string) => ({
  mode: 'full',
  idle_reason: reason,
  running_execution_count: 0,
  requested_execution_count: 0,
  execution_limit: 4,
  pending_propagation_count: 0,
  pending_node_keys: [],
})

/** The run read as serverFlowGraphSource.toRunInfo gives it. */
const runRead = (status: string, seq: number, fields: Partial<FlowRunInfo> = {}): FlowRunInfo => ({
  runId: 'run_1',
  status,
  openNodeKeys: [],
  mode: 'full',
  idleReason: null,
  runningExecutionCount: 0,
  requestedExecutionCount: 0,
  executionLimit: 4,
  pendingPropagationCount: 0,
  pendingNodeKeys: [],
  revisionNo: 1,
  lastEventSeq: seq,
  ...fields,
})

describe('Canvas read — the run block carries the band facts (0083 T#4 rev1)', () => {
  it('keeps each band fact sent, and none an older server does not send', () => {
    const canvas = toFlowCanvas({ flow_id: F, run: canvasBlock('idle', 15, { ...idleBand('completed'), pending_node_keys: ['gn_b', 3] }) })
    expect(canvas.run).toEqual({
      runId: 'run_1', status: 'idle', revisionNo: 1, lastEventSeq: 15, mode: 'full', idleReason: 'completed',
      runningExecutionCount: 0, requestedExecutionCount: 0, executionLimit: 4, pendingPropagationCount: 0, pendingNodeKeys: ['gn_b'],
    })
    expect(toFlowGraph(canvas).run).toMatchObject({ runId: 'run_1', status: 'idle', lastEventSeq: 15, idleReason: 'completed', runningExecutionCount: 0 })
    const older = toFlowGraph(toFlowCanvas({ flow_id: F, run: canvasBlock('idle', 15, null) })).run!
    for (const key of ['mode', 'idleReason', 'runningExecutionCount', 'requestedExecutionCount', 'executionLimit', 'pendingPropagationCount', 'pendingNodeKeys']) {
      expect(key in older).toBe(false)
    }
  })
})

describe('Center session — a newer Canvas read tells the band as of itself (0083 T#4 rev1)', () => {
  interface OrderSource extends FlowGraphSource {
    /** The next Canvas read's run block and its card's node run (null: none, a draft view). */
    canvas: { block: ReturnType<typeof canvasBlock>; nodeRun: FlowGraphNodeRun | null }
    /** The run reads sent, answered only when the test says so (in any order). */
    runReads: Array<(run: FlowRunInfo | null) => void>
  }

  function orderSource(block: ReturnType<typeof canvasBlock>): OrderSource {
    const source: OrderSource = {
      isSample: false,
      canvas: { block, nodeRun: null },
      runReads: [],
      loadGraph: async () => {
        const graph = toFlowGraph(toFlowCanvas({ flow_id: F, run: source.canvas.block }))
        return {
          ...graph,
          ownership: 'graph',
          viewMode: 'revision',
          draft: null,
          nodes: [
            { id: 'gn_t', kind: 'task', title: 't', x: 0, y: 0, movable: true, structureEditable: false, nodeRun: source.canvas.nodeRun },
            { id: 'gn_d', kind: 'task', title: 'd', x: 0, y: 0, movable: true, structureEditable: false, nodeRun: null },
          ],
          edges: [],
        } as FlowGraph
      },
      loadRun: () => new Promise<FlowRunInfo | null>((resolve) => source.runReads.push(resolve)),
      startRun: async () => runRead('running', 1),
      stopRun: async (_p, runId) => ({ runId, status: 'stopping', openNodeKeys: [] }),
      resumeRun: async (_p, runId) => ({ runId, status: 'running', openNodeKeys: [] }),
    }
    return source
  }

  let center: DesignerCenterSession | null = null
  let toolbar: VueWrapper<any> | null = null
  afterEach(() => {
    toolbar?.unmount()
    toolbar = null
    center?.dispose()
    center = null
  })

  const settle = async () => {
    for (let i = 0; i < 4; i += 1) await flushPromises()
  }

  async function open(source: OrderSource) {
    center = createDesignerCenterSession({ source })
    center.setProject(P)
    center.openFlow({ projectId: P, flowId: F, flowName: 'F' })
    await settle()
    return center
  }

  /** The next Canvas read (a run event, a refresh): its run block, then the run read it sends stays unanswered. */
  async function canvasRead(session: DesignerCenterSession, source: OrderSource, block: ReturnType<typeof canvasBlock>, nodeRun: FlowGraphNodeRun | null = null) {
    source.canvas = { block, nodeRun }
    session.refreshFlow(F)
    await settle()
  }

  /** The latest run read answers (an earlier one is outdated by it and dropped by the run control). */
  async function answer(source: OrderSource, run: FlowRunInfo | null) {
    source.runReads[source.runReads.length - 1](run)
    await settle()
  }

  const band = (session: DesignerCenterSession) => runBandView(session.activeRunStatus.value.run)

  /** The band as the toolbar draws it (ko): status words and detail line. */
  function drawn(session: DesignerCenterSession) {
    i18n.global.locale.value = 'ko'
    toolbar?.unmount()
    const runBand = band(session)
    toolbar = mount(DesignerToolbar, { props: { runPhase: runBand?.phase ?? 'idle', density: 'normal', zoom: 100, runBand }, global: { plugins: [i18n] } })
    const text = (test: string) => (toolbar!.find(`[data-test="${test}"]`).exists() ? toolbar!.get(`[data-test="${test}"]`).text() : null)
    return { status: text('designer-run-band-status'), detail: text('designer-run-band-detail') }
  }

  it('idle → idle: a reason that changed is the newer Canvas read’s, also when an older run response answers after it', async () => {
    const source = orderSource(canvasBlock('idle', 10, idleBand('waiting_approval')))
    const session = await open(source)
    await answer(source, runRead('idle', 10, { idleReason: 'waiting_approval' }))
    expect(drawn(session)).toEqual({ status: '사람 작성·승인 대기', detail: null })

    // The person's card was approved and the run completed: the Canvas read (seq 15) comes first, its run read waits.
    await canvasRead(session, source, canvasBlock('idle', 15, idleBand('completed')))
    expect(session.state.runs[F].readState).toBe('reading')
    expect(band(session)).toMatchObject({ phase: 'idle', idleReason: 'completed', running: 0 })
    expect(drawn(session)).toEqual({ status: '완료', detail: null })

    // That run read answers with a response older than the Canvas read (seq 10): the order is reversed, the band is not.
    await answer(source, runRead('idle', 10, { idleReason: 'waiting_approval' }))
    expect(session.state.runs[F].readState).toBe('read')
    expect(drawn(session)).toEqual({ status: '완료', detail: null })

    // A run read as new as the Canvas read takes over (the same facts).
    await canvasRead(session, source, canvasBlock('idle', 15, idleBand('completed')))
    await answer(source, runRead('idle', 15, { idleReason: 'completed' }))
    expect(session.activeRunStatus.value.run).toMatchObject({ idleReason: 'completed', lastEventSeq: 15 })
    expect(drawn(session)).toEqual({ status: '완료', detail: null })
  })

  it('running → idle: a completed run shows no AI execution of the run read before it', async () => {
    const source = orderSource(canvasBlock('running', 12, { ...idleBand('completed'), idle_reason: null, running_execution_count: 1 }))
    const session = await open(source)
    await answer(source, runRead('running', 12, { runningExecutionCount: 1 }))
    expect(drawn(session)).toEqual({ status: '실행 중', detail: '전체 실행 · 실행 1/4' })

    await canvasRead(session, source, canvasBlock('idle', 15, idleBand('completed')))
    expect(band(session)).toMatchObject({ phase: 'idle', idleReason: 'completed', running: 0, requested: 0, command: null })
    expect(drawn(session)).toEqual({ status: '완료', detail: null })
    await answer(source, runRead('running', 12, { runningExecutionCount: 1 }))      // late and older
    expect(drawn(session)).toEqual({ status: '완료', detail: null })
  })

  it('an older Canvas read never hides a newer run read', async () => {
    const source = orderSource(canvasBlock('idle', 15, idleBand('completed')))
    const session = await open(source)
    await answer(source, runRead('idle', 15, { idleReason: 'completed' }))
    await canvasRead(session, source, canvasBlock('idle', 10, idleBand('waiting_approval')))
    expect(drawn(session)).toEqual({ status: '완료', detail: null })                    // run read pending: the one read
    await answer(source, runRead('idle', 15, { idleReason: 'completed' }))
    expect(drawn(session)).toEqual({ status: '완료', detail: null })
  })

  it('a newer Canvas read of an older server (no band facts) shows no older reason or execution', async () => {
    const source = orderSource(canvasBlock('running', 12, null))
    const session = await open(source)
    await answer(source, runRead('running', 12, { runningExecutionCount: 1, pendingPropagationCount: 1, pendingNodeKeys: ['gn_d'] }))
    await canvasRead(session, source, canvasBlock('idle', 15, null))
    expect(band(session)).toEqual({ phase: 'idle', command: null, idleReason: null, running: 0, requested: 0, limit: null, pendingCount: 0, pendingNodeKeys: [] })
  })

  it('the older run read’s node runs stay only for the nodes the newer Canvas read sends none of', async () => {
    const source = orderSource(canvasBlock('running', 12, { ...idleBand('completed'), idle_reason: null, running_execution_count: 1 }))
    const session = await open(source)
    const nodeRuns: FlowRunNodeInfo[] = [
      { nodeKey: 'gn_t', status: 'open', runningExecutionCount: 1, requestedExecutionCount: 0 },
      { nodeKey: 'gn_d', status: 'done', runningExecutionCount: 0, requestedExecutionCount: 0 },
    ]
    await answer(source, runRead('running', 12, { runningExecutionCount: 1, nodeRuns }))
    expect([...session.activeRunMarks.value.nodeRuns.keys()]).toEqual(['gn_t', 'gn_d'])
    const done: FlowGraphNodeRun = { status: 'done', openedAt: null, finishedAt: null, resultDocId: 'd', runningExecutionCount: 0, requestedExecutionCount: 0 }
    await canvasRead(session, source, canvasBlock('idle', 15, idleBand('completed')), done)
    const marks = session.activeRunMarks.value
    expect([...marks.nodeRuns.keys()]).toEqual(['gn_d'])                               // gn_t: the Canvas node run (done)
    expect(marks.activeNodeIds.has('gn_t')).toBe(false)
    expect(marks.protectedNodeIds.has('gn_t')).toBe(true)
  })
})

describe('flowRunControl — a requested AI execution is read again until it runs (0083 T#4 rev1)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  function control(answers: FlowRunInfo[]) {
    const calls: string[] = []
    const source = {
      isSample: false,
      loadGraph: async () => { throw new Error('no canvas') },
      loadRun: async () => {
        calls.push('current')
        return answers.length > 1 ? answers.shift()! : answers[0]
      },
      startRun: async () => runRead('running', 1),
      stopRun: async (_p: string, runId: string) => ({ runId, status: 'stopping', openNodeKeys: [] }),
      resumeRun: async (_p: string, runId: string) => ({ runId, status: 'running', openNodeKeys: [] }),
    } as FlowGraphSource
    const runs = reactive<Record<string, FlowRunState>>({})
    const ctl = createFlowRunControl({ source, projectId: () => P, isOpen: () => true, prepare: () => true, refresh: () => {} }, runs)
    return { ctl, runs, calls }
  }

  it('reads again while an execution is requested, then stops', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { ctl, runs, calls } = control([
      runRead('running', 4, { runningExecutionCount: 0, requestedExecutionCount: 1 }),
      runRead('running', 4, { runningExecutionCount: 1, requestedExecutionCount: 0 }),
    ])
    ctl.read(F)
    await flushPromises()
    expect(runBandView(runs[F].run)).toMatchObject({ running: 0, requested: 1 })
    expect(calls).toEqual(['current'])
    vi.advanceTimersByTime(REQUESTED_REREAD_MS)
    await flushPromises()
    expect(calls).toEqual(['current', 'current'])
    expect(runBandView(runs[F].run)).toMatchObject({ running: 1, requested: 0 })
    vi.advanceTimersByTime(REQUESTED_REREAD_MS * 3)
    await flushPromises()
    expect(calls).toHaveLength(2)                                                          // nothing requested: no more
  })

  it('an idle run or no request is not read again, and closing the Flow drops the read', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const idle = control([runRead('idle', 9, { idleReason: 'waiting_approval', requestedExecutionCount: 1 })])
    idle.ctl.read(F)
    await flushPromises()
    vi.advanceTimersByTime(REQUESTED_REREAD_MS * 2)
    await flushPromises()
    expect(idle.calls).toEqual(['current'])
    const closed = control([runRead('running', 4, { requestedExecutionCount: 1 })])
    closed.ctl.read(F)
    await flushPromises()
    closed.ctl.forget(F)
    vi.advanceTimersByTime(REQUESTED_REREAD_MS * 2)
    await flushPromises()
    expect(closed.calls).toEqual(['current'])
  })
})
