/**
 * flowgate.v02.0132 T#4 (review rework) — a confirm gate's Git work (`gate.git_active`) locks the Flow's structure on
 * screen, through the real Center session: the Canvas read and the run read both carry it, a gate moving between parked
 * and active raises no run event (only its job event), and the run stays idle all along.
 *  - the job event reads the Flow's Canvas (and run) again;
 *  - a Canvas read's gate decides the lock while the run read is pending / failed / older;
 *  - parked -> active -> parked on an idle run locks and unlocks the session, not just a pure function.
 */
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { flowRunMarks, gateGitActiveBy } from '@/features/designer/center/run/flowRunLock'
import {
  CANVAS_CARD_STEP_COALESCE_MS,
  CANVAS_GATE_JOB_EVENTS,
  CANVAS_REFRESH_EVENTS,
  subscribeFlowCanvasChanges,
} from '@/features/project/flowCanvasEvents'

const P = 'p'
const F = 'p.ui.0132'
const SEQ = 5

interface Gate { git_active?: boolean; parked?: boolean }
const gateBlock = (gate: Gate | null) => gate === null ? undefined : {
  decision: null, decision_in_progress: true, can_decide: false, settings_locked: true, archived: false,
  recovery_reset_required: false, recovery: null, job: null, ...gate,
}

interface GateSource extends FlowGraphSource {
  /** The gate as the next Canvas read tells it (null: the node run carries none). */
  canvasGate: Gate | null
  /** The gate as the next run read tells it. */
  runGate: Gate | null
  runStatus: string
  autoRun: boolean
  failGraph: boolean
  runReads: Array<{ resolve: (run: FlowRunInfo | null) => void; reject: (error: Error) => void }>
  changed: ((flowId: string) => void) | null
  graphReads: number
}

function gateSource(canvasGate: Gate | null, runGate: Gate | null = canvasGate): GateSource {
  const runOf = (): FlowRunInfo => ({
    runId: 'run_1', status: source.runStatus, lastEventSeq: SEQ, openNodeKeys: [],
    nodeRuns: [{ nodeKey: 'g1', status: 'open', ...(source.runGate ? { gate: gateBlock(source.runGate) } : {}) }],
  } as unknown as FlowRunInfo)
  const source: GateSource = {
    isSample: false,
    canvasGate,
    runGate,
    runStatus: 'idle',
    autoRun: true,
    failGraph: false,
    runReads: [],
    changed: null,
    graphReads: 0,
    loadGraph: async () => {
      source.graphReads += 1
      if (source.failGraph) throw new Error('canvas failed')
      return {
        flowId: F,
        ownership: 'graph',
        viewMode: 'draft',
        draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
        nodes: [{
          id: 'g1', kind: 'gate', title: 'confirm', x: 0, y: 0, movable: true, structureEditable: true,
          nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, ...(source.canvasGate ? { gate: gateBlock(source.canvasGate) } : {}) },
        }],
        edges: [],
        run: { runId: 'run_1', status: source.runStatus, lastEventSeq: SEQ },
      } as unknown as FlowGraph
    },
    loadRun: () => {
      if (source.autoRun) return Promise.resolve(runOf())
      return new Promise<FlowRunInfo | null>((resolve, reject) => { source.runReads.push({ resolve, reject }) })
    },
    watchChanges: (_projectId, handlers) => {
      source.changed = handlers.changed
      return () => { source.changed = null }
    },
    startRun: async () => runOf(),
    stopRun: async () => runOf(),
    resumeRun: async () => runOf(),
    pauseRun: async () => runOf(),
  } as GateSource
  return source
}

let center: DesignerCenterSession | null = null
afterEach(() => {
  center?.dispose()
  center = null
})

async function open(source: GateSource) {
  center = createDesignerCenterSession({ source })
  center.setProject(P)
  center.openFlow({ projectId: P, flowId: F, flowName: 'F' })
  for (let i = 0; i < 5; i += 1) await flushPromises()
  return center
}
const settle = async () => { for (let i = 0; i < 5; i += 1) await flushPromises() }
const lockOf = (session: DesignerCenterSession) => session.runMarks(F).lock
/** The job event of the gate: the Canvas change the project event hub would deliver. */
async function jobEvent(source: GateSource) {
  expect(source.changed).not.toBeNull()
  source.changed!(F)
  await settle()
}

describe('Canvas gate activity locks the structure while the run read is not the later one (review P2)', () => {
  it('a Canvas read telling git_active locks an idle Flow while its run read is pending', async () => {
    const source = gateSource({ git_active: true, parked: false }, { git_active: false, parked: true })
    source.autoRun = false
    const session = await open(source)
    expect(session.state.runs[F].readState).toBe('reading')
    expect(session.runStatus(F)).toMatchObject({ known: true, run: { status: 'idle' }, gateSource: 'canvas' })
    expect(lockOf(session)).toBe('run')
    expect(session.removeNode(F, 'g1')).toBe(false)
  })

  it('and when the run read failed', async () => {
    const source = gateSource({ git_active: true, parked: false })
    source.autoRun = false
    const session = await open(source)
    source.runReads[0].reject(new Error('offline'))
    await settle()
    expect(session.state.runs[F].readState).toBe('failed')
    expect(lockOf(session)).toBe('run')
  })

  it('a newer Canvas read telling the gate parked lifts the lock a stale run read still holds', async () => {
    const source = gateSource({ git_active: true, parked: false })
    const session = await open(source)
    expect(session.state.runs[F].readState).toBe('read')
    expect(lockOf(session)).toBe('run')
    // the gate parked again: only its job event comes; the Canvas read tells it, the run read is still on its way
    source.canvasGate = { git_active: false, parked: true }
    source.autoRun = false
    await jobEvent(source)
    expect(session.state.runs[F].readState).toBe('reading')
    expect(lockOf(session)).toBeNull()
    // the run read answers later with the same parked gate
    source.runGate = { git_active: false, parked: true }
    source.runReads[source.runReads.length - 1].resolve({
      runId: 'run_1', status: 'idle', lastEventSeq: SEQ, openNodeKeys: [],
      nodeRuns: [{ nodeKey: 'g1', status: 'open', gate: gateBlock(source.runGate) }],
    } as unknown as FlowRunInfo)
    await settle()
    expect(lockOf(session)).toBeNull()
  })

  it('a gate the Canvas tells nothing of (a draft view) stays with the run read', async () => {
    const source = gateSource(null, { git_active: true, parked: false })
    source.autoRun = false
    const session = await open(source)
    expect(lockOf(session)).toBeNull()
    source.runReads[0].resolve({
      runId: 'run_1', status: 'idle', lastEventSeq: SEQ, openNodeKeys: [],
      nodeRuns: [{ nodeKey: 'g1', status: 'open', gate: gateBlock({ git_active: true, parked: false }) }],
    } as unknown as FlowRunInfo)
    await settle()
    expect(lockOf(session)).toBe('run')
  })
})

describe('a gate moving parked -> active -> parked on an idle run locks and unlocks the session (review P2)', () => {
  it('locks when the retry is accepted (the Stop-left idle run is not woken) and unlocks when it is parked again', async () => {
    const source = gateSource({ git_active: false, parked: true })
    const session = await open(source)
    const before = source.graphReads
    expect(lockOf(session)).toBeNull()

    // retry accepted: git_active true; the run is not woken (idle, same sequence); only the job event comes
    source.canvasGate = source.runGate = { git_active: true, parked: false }
    await jobEvent(source)
    expect(source.graphReads).toBeGreaterThan(before)
    expect(session.runStatus(F).run).toMatchObject({ status: 'idle', lastEventSeq: SEQ })
    expect(lockOf(session)).toBe('run')
    expect(session.activeRunMarks.value.lock).toBe('run')
    expect(session.removeNode(F, 'g1')).toBe(false)
    expect(session.checkConnection(F, 'g1', 'g1')).toEqual({ ok: false, reason: 'locked' })

    // parked again: the same, the other way
    source.canvasGate = source.runGate = { git_active: false, parked: true }
    await jobEvent(source)
    expect(session.runStatus(F).run).toMatchObject({ status: 'idle', lastEventSeq: SEQ })
    expect(lockOf(session)).toBeNull()
    expect(session.activeRunMarks.value.lock).toBeNull()
    expect(session.checkConnection(F, 'g1', 'g1')).not.toEqual({ ok: false, reason: 'locked' })
  })

  it('without the job event the old state stays — the event is what the screen follows', async () => {
    const source = gateSource({ git_active: false, parked: true })
    const session = await open(source)
    source.canvasGate = source.runGate = { git_active: true, parked: false }
    await settle()
    expect(lockOf(session)).toBeNull()
    await jobEvent(source)
    expect(lockOf(session)).toBe('run')
  })
})

describe('gateGitActiveBy — which read tells (review P2)', () => {
  const graph = (gate: Gate | null) => ({ nodes: [{ id: 'g1', nodeRun: gate ? { gate: gateBlock(gate) } : null }, { id: 'a' }] }) as unknown as FlowGraph
  const run = (gate: Gate | null) => ({ runId: 'r', status: 'idle', openNodeKeys: [], nodeRuns: [{ nodeKey: 'g1', status: 'open', ...(gate ? { gate: gateBlock(gate) } : {}) }] }) as unknown as FlowRunInfo
  it('the run read tells by default; the Canvas read only when asked', () => {
    expect(gateGitActiveBy(graph({ git_active: true }), run({ git_active: false }))).toBe(false)
    expect(gateGitActiveBy(graph({ git_active: true }), run({ git_active: false }), 'canvas')).toBe(true)
    expect(gateGitActiveBy(graph({ git_active: false }), run({ git_active: true }), 'canvas')).toBe(false)
    expect(gateGitActiveBy(graph({ git_active: false }), run({ git_active: true }), 'run')).toBe(true)
  })
  it('a gate the Canvas says nothing of (or without the field) falls back to the run read', () => {
    expect(gateGitActiveBy(graph(null), run({ git_active: true }), 'canvas')).toBe(true)
    expect(gateGitActiveBy(graph({}), run({ git_active: true }), 'canvas')).toBe(true)
    expect(gateGitActiveBy(null, run({ git_active: true }), 'canvas')).toBe(true)
    expect(gateGitActiveBy(graph({ parked: true, git_active: false }), null, 'canvas')).toBe(false)
  })
  it('flowRunMarks takes the source into the lock', () => {
    expect(flowRunMarks(graph({ git_active: true }), run(null), true, 'canvas').lock).toBe('run')
    expect(flowRunMarks(graph({ git_active: true }), run(null), true).lock).toBeNull()
    expect(flowRunMarks(graph({ git_active: false }), run({ git_active: true }), true, 'canvas').lock).toBeNull()
    // a running run locks whatever the gates say
    expect(flowRunMarks(graph({ git_active: false }), { ...run(null), status: 'running' }, true, 'canvas').lock).toBe('run')
  })
})

describe('gate job frames read the Flow’s Canvas again (review P2)', () => {
  class FakeEventSource {
    static all: FakeEventSource[] = []
    static readonly CLOSED = 2
    readyState = 0
    private listeners = new Map<string, Array<(event: Event) => void>>()
    constructor(readonly url: string) { FakeEventSource.all.push(this) }
    addEventListener(type: string, listener: (event: Event) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]) }
    close() { this.readyState = FakeEventSource.CLOSED }
    emit(type: string, data?: unknown) {
      const event = Object.assign(new Event(type), { data: data === undefined ? undefined : JSON.stringify(data) })
      for (const listener of this.listeners.get(type) ?? []) listener(event)
    }
  }
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('fg_access_token', 'jwt-1')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    sessionStorage.clear()
  })
  const jobFrame = (groupId: string) => ({ event_type: 'gate_publish_job_changed', payload: { group_id: groupId, job_id: 'j1', status: 'pending' }, project: 'flowgate', group_id: groupId, doc_id: null })

  it('the job event is its own list; the Canvas refresh list is unchanged', () => {
    expect(CANVAS_GATE_JOB_EVENTS).toEqual(['gate_publish_job_changed'])
    expect(CANVAS_REFRESH_EVENTS).toEqual(['graph_canvas_changed', 'flow_ownership_changed', 'graph_run_status_changed', 'graph_node_state_changed'])
  })

  it('a burst of a Flow’s job frames is one change of that Flow after the window', () => {
    const changed: string[] = []
    const stop = subscribeFlowCanvasChanges('flowgate', { changed: (flowId) => changed.push(flowId), resync: () => {} })
    const es = FakeEventSource.all[0]
    es.emit('open')
    es.emit('gate_publish_job_changed', jobFrame(F))
    es.emit('gate_publish_job_changed', jobFrame(F))
    es.emit('gate_publish_job_changed', jobFrame('flowgate.v02.0057'))
    expect(changed).toEqual([])
    vi.advanceTimersByTime(CANVAS_CARD_STEP_COALESCE_MS)
    expect([...changed].sort()).toEqual([F, 'flowgate.v02.0057'].sort())
    stop()
  })
})
