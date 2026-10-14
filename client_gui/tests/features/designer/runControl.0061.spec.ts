/**
 * flowgate.v02.0061 T#14 — the run commands, the run band and the run lock on screen:
 * - flowRunControl: 다음 단계 / 전체 실행 are one step / full command (no stop after it, the draft confirmed on an idle
 *   Flow only); 일시정지 / 재개 / 중지 are one call each; a refusal keeps the server's code and message.
 * - designerTop: the run buttons and the Run menu follow the run phase (idle / running / paused / stopping); 재실행
 *   never; every run command waits while one is answered; the band names status, command, idle reason, executions.
 * - flowRunLock: running / paused / stopping lock the structure; done / open / review nodes and their incoming lines
 *   are protected; a line into a node the work is going through is running.
 * - TR0031 rev1: a draft view keeps the cards' result integration / Hold from the run read (a card added to the draft
 *   gets none); a run status not confirmed yet (being read, read failed) is not idle — the structure and the run commands
 *   wait (the Canvas read's run status confirms it too), and a Graph's load never lifts a lock.
 */
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunCommand, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { flowRunMarks, isRunLocked } from '@/features/designer/center/run/flowRunLock'
import { toVueFlowEdges, toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import {
  ALL_MENU_TARGETS,
  isMenuCommandEnabled,
  nextRunPhase,
  runBandView,
  runPhaseOf,
} from '@/features/designer/top/designerTop'

const P = 'p'
const F = 'p.ui.0001'

interface FakeRunSource extends FlowGraphSource {
  calls: string[]
  run: FlowRunInfo | null
  refuse: { code: string; message: string } | null
}

function fakeSource(initial: FlowRunInfo | null = null): FakeRunSource {
  const source: FakeRunSource = {
    ...fixtureFlowGraphSource,
    calls: [],
    run: initial,
    refuse: null,
    loadRun: async (_p, flowId) => {
      source.calls.push(`current ${flowId}`)
      return source.run
    },
    startRun: async (_p, flowId, mode?: FlowRunCommand) => {
      source.calls.push(`command ${flowId} ${mode}`)
      if (source.refuse) throw Object.assign(new Error(source.refuse.message), { code: source.refuse.code })
      source.run = { runId: 'run_1', status: 'running', openNodeKeys: ['gn_a'], mode: mode ?? 'full' }
      return source.run
    },
    pauseRun: async (_p, runId) => {
      source.calls.push(`pause ${runId}`)
      source.run = { ...source.run!, status: 'paused' }
      return source.run
    },
    resumeRun: async (_p, runId) => {
      source.calls.push(`resume ${runId}`)
      source.run = { ...source.run!, status: 'running' }
      return source.run
    },
    stopRun: async (_p, runId) => {
      source.calls.push(`stop ${runId}`)
      source.run = { ...source.run!, status: 'stopping' }
      return source.run
    },
  }
  return source
}

function control(source: FakeRunSource, prepare: () => boolean = () => true) {
  const runs = reactive<Record<string, FlowRunState>>({})
  const prepared: string[] = []
  const refreshed: string[] = []
  const ctl = createFlowRunControl({
    source,
    projectId: () => P,
    isOpen: () => true,
    prepare: (flowId) => {
      prepared.push(flowId)
      return prepare()
    },
    refresh: (flowId) => refreshed.push(flowId),
  }, runs)
  return { ctl, runs, prepared, refreshed }
}

describe('flowRunControl — one server call per button (T#14)', () => {
  it('다음 단계 is one step command and nothing is stopped after it', async () => {
    const source = fakeSource()
    const { ctl, runs, prepared, refreshed } = control(source)
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(source.calls).toEqual([`command ${F} step`])
    expect(prepared).toEqual([F])
    expect(runs[F].run?.status).toBe('running')
    expect(refreshed).toEqual([F])
  })

  it('전체 실행 is one full command', async () => {
    const source = fakeSource({ runId: 'run_1', status: 'idle', openNodeKeys: [], idleReason: 'completed' })
    const { ctl } = control(source)
    expect(await ctl.run(F, 'all')).toBe(true)
    expect(source.calls).toEqual([`command ${F} full`])
  })

  it('a locked run takes no command and its draft is not confirmed', async () => {
    const source = fakeSource({ runId: 'run_1', status: 'paused', openNodeKeys: [] })
    const { ctl, runs, prepared } = control(source)
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run?.status).toBe('paused')
    source.calls.length = 0
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(prepared).toEqual([])
    expect(source.calls.some((call) => call.startsWith('command'))).toBe(false)
  })

  it('a refused draft confirmation sends no command', async () => {
    const source = fakeSource()
    const { ctl } = control(source, () => false)
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(source.calls.some((call) => call.startsWith('command'))).toBe(false)
  })

  it('일시정지 · 재개 · 중지 are one call each on the known run', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source)
    await ctl.run(F, 'all')
    source.calls.length = 0
    expect(await ctl.pause(F)).toBe(true)
    expect(runs[F].run?.status).toBe('paused')
    expect(await ctl.resume(F)).toBe(true)
    expect(runs[F].run?.status).toBe('running')
    expect(await ctl.stop(F)).toBe(true)
    expect(runs[F].run?.status).toBe('stopping')
    expect(source.calls.filter((call) => !call.startsWith('current'))).toEqual(['pause run_1', 'resume run_1', 'stop run_1'])
  })

  it('a refusal keeps the server code and message', async () => {
    const source = fakeSource()
    source.refuse = { code: 'graph_run_locked', message: 'A running, paused or stopping flow cannot be edited.' }
    const { ctl, runs } = control(source)
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(runs[F].error?.code).toBe('graph_run_locked')
    expect(runs[F].error?.message).toBe('A running, paused or stopping flow cannot be edited.')
  })
})

describe('designerTop — run commands by phase (T#14)', () => {
  const enabled = (phase: Parameters<typeof isMenuCommandEnabled>[1], busy = false) =>
    (['run.next', 'run.all', 'run.pause', 'run.resume', 'run.stop', 'run.rerun', 'run.validate'] as const).filter((cmd) =>
      isMenuCommandEnabled(cmd, phase, { ...ALL_MENU_TARGETS, runBusy: busy }),
    )

  it('each phase has its own commands; 재실행 never', () => {
    expect(enabled('idle')).toEqual(['run.next', 'run.all', 'run.validate'])
    expect(enabled('running')).toEqual(['run.pause', 'run.stop'])
    expect(enabled('paused')).toEqual(['run.resume', 'run.stop'])
    expect(enabled('stopping')).toEqual([])
  })

  it('every run command waits while one is answered', () => {
    expect(enabled('idle', true)).toEqual([])
    expect(enabled('running', true)).toEqual([])
  })

  it('the phase is the server status; anything else is idle', () => {
    expect(runPhaseOf('running')).toBe('running')
    expect(runPhaseOf('stopping')).toBe('stopping')
    expect(runPhaseOf('idle')).toBe('idle')
    expect(runPhaseOf('completed')).toBe('idle')
    expect(runPhaseOf(null)).toBe('idle')
  })

  it('a source with no server run moves through the phases by the buttons alone', () => {
    expect(nextRunPhase('idle', 'run.next')).toBe('running')
    expect(nextRunPhase('running', 'run.pause')).toBe('paused')
    expect(nextRunPhase('paused', 'run.resume')).toBe('running')
    expect(nextRunPhase('running', 'run.stop')).toBe('idle')
    expect(nextRunPhase('running', 'run.next')).toBe('running')
  })

  it('the band names the command on a going run and the reason on an idle one', () => {
    expect(runBandView({ status: 'running', mode: 'step', runningExecutionCount: 2, requestedExecutionCount: 1, executionLimit: 4 }))
      .toMatchObject({ phase: 'running', command: 'step', idleReason: null, running: 2, requested: 1, limit: 4 })
    expect(runBandView({ status: 'idle', mode: 'full', idleReason: 'waiting_approval', pendingPropagationCount: 2, pendingNodeKeys: ['a', 'b'] }))
      .toMatchObject({ phase: 'idle', command: null, idleReason: 'waiting_approval', pendingCount: 2, pendingNodeKeys: ['a', 'b'] })
    expect(runBandView(null)).toBeNull()
  })
})

describe('flowRunLock — lock, protected and running lines (T#14)', () => {
  const node = (id: string, status: string | null, extra: Record<string, unknown> = {}) => ({
    id, kind: 'task', title: id, state: null, x: 0, y: 0, movable: true, structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: id, assignee: null },
    nodeRun: status === null ? null : { status, openedAt: null, finishedAt: null, resultDocId: null, ...extra },
  })
  const graph = {
    flowId: F,
    nodes: [node('a', 'done'), node('b', 'open'), node('c', 'pending'), node('d', 'pending', { integrationStatus: 'pending' }), node('e', null)],
    edges: [
      { id: 'ab', source: 'a', target: 'b', edgeClass: 'execution', editable: true },
      { id: 'bc', source: 'b', target: 'c', edgeClass: 'execution', editable: true },
      { id: 'cd', source: 'c', target: 'd', edgeClass: 'execution', editable: true },
      { id: 'ce', source: 'c', target: 'e', edgeClass: 'execution', editable: true },
    ],
  } as unknown as FlowGraph

  it('running / paused / stopping lock; idle does not', () => {
    for (const status of ['running', 'paused', 'stopping']) expect(isRunLocked({ status })).toBe(true)
    expect(isRunLocked({ status: 'idle' })).toBe(false)
    expect(isRunLocked(null)).toBe(false)
  })

  it('done / open / review nodes are protected; the run read adds what the Canvas read lacks', () => {
    const marks = flowRunMarks(graph, { runId: 'r', status: 'idle', openNodeKeys: [], nodeRuns: [{ nodeKey: 'e', status: 'review' }] })
    expect([...marks.protectedNodeIds].sort()).toEqual(['a', 'b', 'e'])
    expect(marks.lock).toBeNull()
  })

  it('lines into protected nodes are never removable; lines into working nodes run', () => {
    const marks = flowRunMarks(graph, null)
    const edges = Object.fromEntries(toVueFlowEdges(graph, new Map(), null, marks).map((edge) => [edge.id, edge.data]))
    expect(edges.ab).toMatchObject({ protected: true, deletable: false, running: true })
    expect(edges.bc).toMatchObject({ deletable: true })
    expect(edges.bc.protected).toBeUndefined()
    expect(edges.cd).toMatchObject({ running: true, deletable: true })
    expect(edges.ce.running).toBeUndefined()
  })

  it('a locked run makes no line removable', () => {
    const marks = flowRunMarks(graph, { runId: 'r', status: 'running', openNodeKeys: [] })
    const edges = toVueFlowEdges(graph, new Map(), null, marks)
    expect(edges.every((edge) => edge.data.deletable === false)).toBe(true)
  })
})

describe('flowRunControl — whether the run status is confirmed (TR0031 rev1)', () => {
  it('a Flow is unread, then reading, then read', async () => {
    const source = fakeSource({ runId: 'run_1', status: 'running', openNodeKeys: [] })
    const { ctl, runs } = control(source)
    ctl.read(F)
    expect(runs[F].readState).toBe('reading')
    await flushPromises()
    expect(runs[F]).toMatchObject({ readState: 'read', run: { status: 'running' } })
  })

  it('a failed read keeps the run last read but no longer confirms it', async () => {
    const source = fakeSource({ runId: 'run_1', status: 'running', openNodeKeys: [] })
    const { ctl, runs } = control(source)
    ctl.read(F)
    await flushPromises()
    source.loadRun = async () => {
      throw new Error('offline')
    }
    ctl.read(F)
    await flushPromises()
    expect(runs[F]).toMatchObject({ readState: 'failed', run: { status: 'running' } })
  })

  it('a command answer confirms the run', async () => {
    const { ctl, runs } = control(fakeSource())
    await ctl.run(F, 'all')
    expect(runs[F]).toMatchObject({ readState: 'read', run: { status: 'running' } })
  })

  it('every run command waits while the run status is not confirmed', () => {
    for (const phase of ['idle', 'running', 'paused'] as const) {
      const shown = (['run.next', 'run.all', 'run.pause', 'run.resume', 'run.stop', 'run.validate'] as const).filter((cmd) =>
        isMenuCommandEnabled(cmd, phase, { ...ALL_MENU_TARGETS, runUnknown: true }),
      )
      expect(shown).toEqual([])
    }
  })
})

describe('flowRunLock — a draft view keeps the cards\' integration and Hold (TR0031 rev1)', () => {
  const draftNode = (id: string, nodeRun: Record<string, unknown> | null = null) => ({
    id, kind: 'task', title: id, state: null, x: 0, y: 0, movable: true, structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: id, assignee: null },
    nodeRun: nodeRun === null ? null : { status: 'pending', openedAt: null, finishedAt: null, resultDocId: null, ...nodeRun },
  })
  // A draft view: no node run on its nodes. `gn_a` is a node of the run, `gn_new` was added to the draft, `tmp_x` is not
  // saved yet.
  const draft = {
    flowId: F,
    viewMode: 'draft',
    nodes: [draftNode('gn_a'), draftNode('gn_new'), draftNode('tmp_x')],
    edges: [],
  } as unknown as FlowGraph
  const idle: FlowRunInfo = {
    runId: 'r',
    status: 'idle',
    idleReason: 'merge_conflict',
    openNodeKeys: [],
    nodeRuns: [
      { nodeKey: 'gn_a', status: 'done', integrationStatus: 'conflict', hold: true },
      { nodeKey: 'gn_gone', status: 'done', integrationStatus: 'failed', hold: true },
    ],
  }
  const dataOf = (graph: FlowGraph, run: FlowRunInfo | null) =>
    Object.fromEntries(toVueFlowNodes(graph, null, new Map(), { marks: flowRunMarks(graph, run) }).map((node) => [node.id, node.data]))

  it('a node of the run shows the run read\'s integration (the 병합 충돌 button) and Hold', () => {
    expect(dataOf(draft, idle).gn_a).toMatchObject({ integration: 'conflict', hold: true })
  })

  it('a node added to the draft is no node of the run and shows neither', () => {
    const data = dataOf(draft, idle)
    expect(data.gn_new.integration).toBeUndefined()
    expect(data.gn_new.hold).toBeUndefined()
    expect(data.tmp_x.integration).toBeUndefined()
    expect([...flowRunMarks(draft, idle).nodeRuns.keys()]).toEqual(['gn_a'])
  })

  it('the run read wins over the Canvas node run; without it the Canvas node run shows', () => {
    const revision = { ...draft, viewMode: 'revision', nodes: [draftNode('gn_a', { integrationStatus: 'conflict', hold: true })] } as unknown as FlowGraph
    expect(dataOf(revision, null).gn_a).toMatchObject({ integration: 'conflict', hold: true })
    const settled: FlowRunInfo = { ...idle, nodeRuns: [{ nodeKey: 'gn_a', status: 'done', integrationStatus: 'integrated', hold: false }] }
    expect(dataOf(revision, settled).gn_a.integration).toBeUndefined()
    expect(dataOf(revision, settled).gn_a.hold).toBeUndefined()
  })

  it('a locked or unconfirmed run locks with no Graph; an unconfirmed one never reads as idle', () => {
    expect(flowRunMarks(null, { runId: 'r', status: 'running', openNodeKeys: [] }).lock).toBe('run')
    expect(flowRunMarks(draft, null, false).lock).toBe('unknown')
    expect(flowRunMarks(draft, { runId: 'r', status: 'paused', openNodeKeys: [] }, false).lock).toBe('run')
    expect(flowRunMarks(draft, { runId: 'r', status: 'idle', openNodeKeys: [] }, true).lock).toBeNull()
  })
})

describe('Center session — the run lock before the run status is confirmed (TR0031 rev1)', () => {
  interface PendingSource extends FlowGraphSource {
    calls: string[]
    canvasRun: { runId: string; status: string } | null | undefined
    failGraph: boolean
    answerRun: ((run: FlowRunInfo | null) => void) | null
    failRun: ((error: Error) => void) | null
  }

  function pendingSource(canvasRun: PendingSource['canvasRun']): PendingSource {
    const source: PendingSource = {
      isSample: false,
      calls: [],
      canvasRun,
      failGraph: false,
      answerRun: null,
      failRun: null,
      loadGraph: async () => {
        source.calls.push('canvas')
        if (source.failGraph) throw new Error('canvas failed')
        return {
          flowId: F,
          ownership: 'graph',
          viewMode: 'draft',
          draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
          nodes: [{ id: 'gn_a', kind: 'task', title: 'a', x: 0, y: 0, movable: true, structureEditable: true }],
          edges: [],
          ...(source.canvasRun !== undefined ? { run: source.canvasRun } : {}),
        } as FlowGraph
      },
      // The run read answers only when the test says so.
      loadRun: () => {
        source.calls.push('current')
        return new Promise<FlowRunInfo | null>((resolve, reject) => {
          source.answerRun = resolve
          source.failRun = reject
        })
      },
      startRun: async (_p, flowId, mode) => {
        source.calls.push(`command ${flowId} ${mode}`)
        return { runId: 'run_1', status: 'running', openNodeKeys: [] }
      },
      stopRun: async (_p, runId) => {
        source.calls.push(`stop ${runId}`)
        return { runId, status: 'stopping', openNodeKeys: [] }
      },
      resumeRun: async (_p, runId) => {
        source.calls.push(`resume ${runId}`)
        return { runId, status: 'running', openNodeKeys: [] }
      },
      pauseRun: async (_p, runId) => {
        source.calls.push(`pause ${runId}`)
        return { runId, status: 'paused', openNodeKeys: [] }
      },
    }
    return source
  }

  let center: DesignerCenterSession | null = null
  afterEach(() => {
    center?.dispose()
    center = null
  })

  async function open(source: PendingSource) {
    center = createDesignerCenterSession({ source })
    center.setProject(P)
    center.openFlow({ projectId: P, flowId: F, flowName: 'F' })
    for (let i = 0; i < 4; i += 1) await flushPromises()
    return center
  }

  it('with neither read telling the status, nothing is edited or run until the run read answers', async () => {
    const source = pendingSource(undefined)
    const session = await open(source)
    expect(session.activeRunStatus.value.known).toBe(false)
    expect(session.activeRunMarks.value.lock).toBe('unknown')
    expect(session.activeCanValidate.value).toBe(false)
    expect(session.removeNode(F, 'gn_a')).toBe(false)
    expect(await session.runFlow(F, 'next')).toBe(false)
    expect(await session.stopRun(F)).toBe(false)
    expect(source.calls.filter((call) => call !== 'canvas' && call !== 'current')).toEqual([])
    source.answerRun!(null)
    await flushPromises()
    expect(session.activeRunStatus.value).toMatchObject({ known: true, run: null })
    expect(session.activeRunMarks.value.lock).toBeNull()
  })

  it('a running Flow is locked by its Canvas read while its run read is pending', async () => {
    const source = pendingSource({ runId: 'run_1', status: 'running' })
    const session = await open(source)
    expect(session.activeRunStatus.value).toMatchObject({ known: true, run: { runId: 'run_1', status: 'running' } })
    expect(session.activeRunMarks.value.lock).toBe('run')
    expect(await session.runFlow(F, 'all')).toBe(false)
    expect(source.calls.some((call) => call.startsWith('command'))).toBe(false)
  })

  it('a failed run read is not idle', async () => {
    const source = pendingSource(undefined)
    const session = await open(source)
    source.failRun!(new Error('offline'))
    await flushPromises()
    expect(session.state.runs[F].readState).toBe('failed')
    expect(session.activeRunMarks.value.lock).toBe('unknown')
    expect(await session.runFlow(F, 'next')).toBe(false)
  })

  it('a Graph that fails to load again keeps the known run lock', async () => {
    const source = pendingSource(undefined)
    const session = await open(source)
    source.answerRun!({ runId: 'run_1', status: 'paused', openNodeKeys: [] })
    await flushPromises()
    expect(session.activeRunMarks.value.lock).toBe('run')
    source.failGraph = true
    session.retryGraph(F)
    expect(session.activeRunMarks.value.lock).toBe('run')
    for (let i = 0; i < 4; i += 1) await flushPromises()
    expect(session.activeGraphLoad.value?.status).toBe('error')
    expect(session.activeRunMarks.value.lock).toBe('run')
  })
})

describe('Canvas read — the run block reaches the Graph (TR0031 rev1)', () => {
  it('keeps the run block's status; null is no run; absent says nothing', () => {
    const running = toFlowCanvas({ flow_id: F, run: { run_id: 'run_1', status: 'running', revision_no: 2, open_node_keys: [] } })
    expect(running.run).toEqual({ runId: 'run_1', status: 'running', revisionNo: 2 })
    expect(toFlowGraph(running).run).toEqual({ runId: 'run_1', status: 'running' })
    expect(toFlowGraph(toFlowCanvas({ flow_id: F, run: null })).run).toBeNull()
    expect('run' in toFlowGraph(toFlowCanvas({ flow_id: F }))).toBe(false)
  })
})
