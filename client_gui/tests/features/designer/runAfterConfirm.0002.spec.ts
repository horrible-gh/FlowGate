// flowgate.v02gui.0002 A — one 전체 실행 / 다음 단계 click confirms the drawn draft AND starts the run.
//
// Reported on flowgate.v02test.0026 (stg): the draft was confirmed at 16:28:17 but the run started only at 16:29:13, on a
// second click. The confirmation re-reads the Canvas (draftActions `host.reread`), and every Canvas read re-reads the
// archive state (designerCenterSession `refreshFlowArchive`: unknown = read only until it answers). The run control then
// asked `canProceed` once, got false for "not answered yet" and dropped the click with no word. "Not answered yet" is not
// "may not run": the click waits for the answer and goes on. It stops only on a real no (archived, a run started
// elsewhere, the tab closed).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunCommand, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'

const P = 'flowgate'
const F = 'flowgate.v02test.0026'
const BUG = 'gn_bug'
const GATE = 'gn_gate'
const ARCHIVE_MS = 100

const card = (id: string, kind: string, nodeType: string, docType: string | null, paletteItemId: string | null, label: string, x: number): FlowGraphNode => ({
  id, kind, title: label, x, y: 0, movable: true, structureEditable: true,
  definition: { nodeType, docType, paletteItemId, label, assignee: null },
})

/** The reported shape: 버그 -> 확인, a drawn draft (version 3), no revision, no run yet. */
const reportedFlow = (): FlowGraph => ({
  flowId: F,
  ownership: 'unassigned',
  viewMode: 'draft',
  draft: { version: 3, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  nodes: [card(BUG, 'bug', 'document', 'B', null, '버그', 0), card(GATE, 'approval', 'gate', null, 'confirm', '확인', 300)],
  edges: [{ id: 'ge_1', source: BUG, target: GATE, edgeClass: 'execution', editable: true }],
  run: null,
} as FlowGraph)

function server() {
  const calls: string[] = []
  let revision: number | null = null
  let current: FlowRunInfo | null = null
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => { calls.push('canvas'); return reportedFlow() }),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async (_p: string, _f: string, version: number) => ({ version: version + 1, keyMap: {} })),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async (_p: string, _f: string, version: number) => { calls.push(`confirm ${version}`); revision = 1; return revision }),
    loadRun: vi.fn(async () => current),
    startRun: vi.fn(async (_p: string, _f: string, mode?: FlowRunCommand): Promise<FlowRunInfo> => {
      calls.push(`run ${mode}`)
      if (revision === null) throw Object.assign(new Error('no revision'), { code: 'revision_not_found', status: 404 })
      current = { runId: 'run_1', status: 'running', openNodeKeys: [GATE] }
      return current
    }),
    stopRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'stopping', openNodeKeys: [] })),
    resumeRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'running', openNodeKeys: [] })),
  }
  return { source, calls }
}

/** The production archive read: answers over the network, so never within the click's own microtasks. */
function archiveRead(server: { archived: boolean; silent?: boolean } = { archived: false }) {
  return vi.fn((_flowId: string) => new Promise<{ archived: boolean }>((resolve) => {
    const archived = server.archived
    if (!server.silent) setTimeout(() => resolve({ archived }), ARCHIVE_MS)
  }))
}

let center: DesignerCenterSession | null = null

async function open(source: FlowGraphSource, readArchive: ReturnType<typeof archiveRead>) {
  center = createDesignerCenterSession({ source, readArchive, draftSaveDelayMs: 50, layoutSaveDelayMs: 50 })
  center.setProject(P)
  center.openFlow({ projectId: P, flowId: F, flowName: '버그 확인' })
  await vi.runAllTimersAsync()
  return center
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  center?.dispose()
  center = null
  vi.useRealTimers()
})

describe('one run click confirms the draft and starts the run (flowgate.v02gui.0002 A)', () => {
  it.each([['all', 'full'], ['next', 'step']] as const)(
    '[%s] the Canvas / archive re-read of the confirmation does not drop the click: startRun goes once',
    async (mode, command) => {
      const { source, calls } = server()
      const readArchive = archiveRead()
      const session = await open(source, readArchive)
      expect(session.isFlowReadOnly(F)).toBe(false)
      expect(session.state.runs[F]?.readState).toBe('read')
      calls.length = 0
      const archiveReadsBefore = readArchive.mock.calls.length

      const going = session.runFlow(F, mode)
      await vi.runAllTimersAsync()

      expect(await going).toBe(true)
      expect(calls.filter((c) => c !== 'canvas')).toEqual(['confirm 3', `run ${command}`])
      expect(source.startRun).toHaveBeenCalledTimes(1)
      // The confirmation's re-read did ask the archive again (that is what used to drop the click).
      expect(readArchive.mock.calls.length).toBeGreaterThan(archiveReadsBefore)
      expect(session.state.runs[F]).toMatchObject({ run: { runId: 'run_1', status: 'running' }, error: null, action: null })
    },
  )

  it('a Flow the re-read finds archived is a real no: the draft is confirmed, no run goes', async () => {
    const { source, calls } = server()
    const archive = { archived: false }
    const session = await open(source, archiveRead(archive))
    calls.length = 0
    // Archived elsewhere meanwhile: the confirmation's re-read is the first to tell it.
    archive.archived = true

    const going = session.runFlow(F, 'all')
    await vi.runAllTimersAsync()

    expect(await going).toBe(false)
    expect(calls.filter((c) => c !== 'canvas')).toEqual(['confirm 3'])
    expect(source.startRun).not.toHaveBeenCalled()
    expect(session.isFlowReadOnly(F)).toBe(true)
  })

  it('an archive re-read that never answers ends the click with a told refusal, not silently and not forever', async () => {
    const { source, calls } = server()
    const archive: { archived: boolean; silent?: boolean } = { archived: false }
    const session = await open(source, archiveRead(archive))
    calls.length = 0
    archive.silent = true

    const going = session.runFlow(F, 'all')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(await going).toBe(false)
    expect(calls.filter((c) => c !== 'canvas')).toEqual(['confirm 3'])
    expect(source.startRun).not.toHaveBeenCalled()
    expect(session.state.runs[F]).toMatchObject({ action: null, error: { kind: 'run', code: 'flow_archive_unconfirmed' } })
  })
})

describe('run control: an unconfirmed run status after the confirmation is read, not taken as a no', () => {
  function control(run: FlowRunInfo | null) {
    const runs = reactive<Record<string, FlowRunState>>({})
    const loadRun = vi.fn(async () => run)
    const startRun = vi.fn(async (): Promise<FlowRunInfo> => ({ runId: 'run_9', status: 'running', openNodeKeys: [] }))
    const source = { ...fixtureFlowGraphSource, loadRun, startRun, stopRun: startRun, resumeRun: startRun } as FlowGraphSource
    const ctl = createFlowRunControl({
      source, projectId: () => P, isOpen: () => true, prepare: () => true, refresh: vi.fn(),
      // The session's gate: a run command goes only on a confirmed run status of an unlocked run.
      canProceed: (flowId) => runs[flowId]?.readState === 'read' && !(runs[flowId]?.run && runs[flowId]!.run!.status !== 'idle'),
    }, runs)
    // A run read was going when the button was clicked (every Canvas read starts one); the action drops its answer.
    runs[F] = { run: null, readState: 'reading', action: null, approving: null, holding: null, error: null }
    return { ctl, runs, loadRun, startRun }
  }

  it('reads the run itself and starts it in the same click', async () => {
    const { ctl, runs, loadRun, startRun } = control(null)
    expect(await ctl.run(F, 'all')).toBe(true)
    expect(loadRun).toHaveBeenCalled()
    expect(startRun).toHaveBeenCalledTimes(1)
    expect(runs[F]).toMatchObject({ run: { runId: 'run_9' }, readState: 'read', error: null })
  })

  it('a run that read shows started elsewhere is a real no: nothing is sent', async () => {
    const { ctl, startRun } = control({ runId: 'run_other', status: 'running', openNodeKeys: [] })
    expect(await ctl.run(F, 'all')).toBe(false)
    expect(startRun).not.toHaveBeenCalled()
  })
})
