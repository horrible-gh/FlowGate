// flowgate.v02.0117 T#2 — [다음 단계] / [전체 실행] on a Flow whose stored draft holds a self edge.
//
// Fixture: the Graph of the reported Flow flowgate.v02test.0001 as stored (draft version 11, no revision, no run):
// 버그(B) -> 작업(T) -> 확인(gate), plus the edge ge_4j1uazydwsba from 작업 to itself. The server validator (rule 4,
// flowgate.v02.0061) answers that draft with one `cycle` issue, so before the fix its confirmation was refused on
// every run command, no `POST /runs` went out, and the Canvas drew no edge to remove (vueFlowMapping skips self
// edges). The fake source below judges a saved draft the same way (a self edge -> `graph_validation_failed`).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toVueFlowEdges } from '@/features/designer/center/canvas/vueFlowMapping'
import { checkConnection } from '@/features/designer/center/edit/connectionRules'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource, FlowRunCommand, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'

const P = 'flowgate'
const F = 'flowgate.v02test.0001'
const BUG = 'gn_mmwx09yqajn9'
const TASK = 'gn_d1sns0lcpwzv'
const GATE = 'gn_17olu4rhmprs'
const SELF = 'ge_4j1uazydwsba'

const card = (id: string, kind: string, nodeType: string, docType: string | null, paletteItemId: string | null, label: string, x: number): FlowGraphNode => ({
  id, kind, title: label, x, y: 0, movable: true, structureEditable: true,
  definition: { nodeType, docType, paletteItemId, label, assignee: null },
})

/** The reported Flow as its Canvas read shows it (draft v11, stored edges in their order). */
function reportedFlow(version = 11, withSelfEdge = true): FlowGraph {
  return {
    flowId: F,
    ownership: 'unassigned',
    viewMode: 'draft',
    draft: { version, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    nodes: [
      card(BUG, 'bug', 'document', 'B', null, '버그', 0),
      card(TASK, 'task', 'document', 'T', 'task', '작업', 300),
      card(GATE, 'approval', 'gate', null, 'confirm', '확인', 600),
    ],
    edges: [
      { id: 'ge_yck0jj2fkdlq', source: BUG, target: TASK, edgeClass: 'execution', editable: true },
      { id: 'ge_hgwmok1mhsp5', source: TASK, target: GATE, edgeClass: 'execution', editable: true },
      ...(withSelfEdge ? [{ id: SELF, source: TASK, target: TASK, edgeClass: 'execution', editable: true }] : []),
    ],
  } as FlowGraph
}

/** The server as far as these commands go: the stored draft, its validation (rule 4 for a self edge) and the run. */
function server() {
  const calls: string[] = []
  const stored = { version: 11, edges: reportedFlow().edges.map((e) => ({ source: e.source, target: e.target })) }
  const saves: Array<{ version: number; content: FlowDraftContent }> = []
  let revision: number | null = null
  const issuesOf = () => stored.edges.some((e) => e.source === e.target)
    ? [{ code: 'cycle', structure: null, nodeIds: [TASK], edgeIds: [SELF], message: '' }]
    : []
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => {
      calls.push('canvas')
      return reportedFlow(stored.version, stored.edges.some((e) => e.source === e.target))
    }),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async (_p: string, _f: string, version: number, content: FlowDraftContent) => {
      calls.push(`save ${version}`)
      if (version !== stored.version) throw Object.assign(new Error('conflict'), { code: 'draft_version_conflict' })
      saves.push({ version, content })
      stored.version = version + 1
      stored.edges = content.edges.map((e) => ({ source: e.source, target: e.target }))
      return { version: stored.version, keyMap: {} }
    }),
    validateDraft: vi.fn(async () => ({ confirmable: issuesOf().length === 0, issues: issuesOf() })),
    confirmDraft: vi.fn(async (_p: string, _f: string, version: number) => {
      calls.push(`confirm ${version}`)
      const issues = issuesOf()
      if (version !== stored.version) throw Object.assign(new Error('conflict'), { code: 'draft_version_conflict' })
      if (issues.length) throw Object.assign(new Error('invalid'), { code: 'graph_validation_failed', issues })
      revision = 1
      return revision
    }),
    loadRun: vi.fn(async () => null),
    startRun: vi.fn(async (_p: string, _f: string, mode?: FlowRunCommand): Promise<FlowRunInfo> => {
      calls.push(`run ${mode}`)
      if (revision === null) throw Object.assign(new Error('no revision'), { code: 'revision_not_found', status: 404 })
      return { runId: 'run_1', status: 'running', openNodeKeys: [TASK] }
    }),
    stopRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'stopping', openNodeKeys: [] })),
    resumeRun: vi.fn(async (_p: string, runId: string): Promise<FlowRunInfo> => ({ runId, status: 'running', openNodeKeys: [] })),
  }
  return { source, calls, saves, stored }
}

let center: DesignerCenterSession | null = null

async function open(source: FlowGraphSource) {
  center = createDesignerCenterSession({ source, draftSaveDelayMs: 50, layoutSaveDelayMs: 50 })
  center.setProject(P)
  center.openFlow({ projectId: P, flowId: F, flowName: '구구절절 플로우 삭제' })
  await vi.runAllTimersAsync()
  return center
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  center?.dispose()
  center = null
  vi.useRealTimers()
})

describe('self edge of the reported Flow (flowgate.v02test.0001)', () => {
  it('is not drawn on the Canvas, so it cannot be seen or removed there', () => {
    expect(toVueFlowEdges(reportedFlow()).map((e) => e.id)).toEqual(['ge_yck0jj2fkdlq', 'ge_hgwmok1mhsp5'])
  })

  it('cannot be drawn any more: the connection pre-check refuses a card connected to itself', () => {
    const model = { nodes: reportedFlow().nodes.map((n) => ({ key: n.id })), edges: [] } as never
    expect(checkConnection(model, TASK, TASK)).toEqual({ ok: false, reason: 'self_edge' })
    expect(checkConnection(model, BUG, TASK)).toEqual({ ok: true, warnings: [] })
  })

  it.each([['next', 'step'], ['all', 'full']] as const)(
    '[%s] saves the draft without the self edge, confirms it and starts the run (one command)',
    async (mode, command) => {
      const { source, calls, saves } = server()
      const session = await open(source)
      expect(session.state.runs[F]?.readState).toBe('read')
      calls.length = 0

      expect(await session.runFlow(F, mode)).toBe(true)

      expect(calls.filter((c) => c !== 'canvas')).toEqual(['save 11', 'confirm 12', `run ${command}`])
      expect(saves).toHaveLength(1)
      expect(saves[0].content.edges.map((e) => e.key)).toEqual(['ge_yck0jj2fkdlq', 'ge_hgwmok1mhsp5'])
      expect(saves[0].content.nodes.map((n) => n.key)).toEqual([BUG, TASK, GATE])
      expect(session.state.runs[F]).toMatchObject({ run: { runId: 'run_1', status: 'running' }, error: null })
      expect(session.state.edits[F]?.check ?? null).toBeNull()
      // Not offered to undo: the user never saw that edge.
      expect(session.state.edits[F]?.canUndo ?? false).toBe(false)
    },
  )

  it('a draft without a self edge is confirmed as stored: no extra save', async () => {
    const { source, calls, stored } = server()
    stored.edges = stored.edges.filter((e) => e.source !== e.target)
    const session = await open(source)
    calls.length = 0
    expect(await session.runFlow(F, 'all')).toBe(true)
    expect(calls.filter((c) => c !== 'canvas')).toEqual(['confirm 11', 'run full'])
  })

  it('[검증] of the reported draft reports no issue once the self edge is taken out', async () => {
    const { source, calls } = server()
    const session = await open(source)
    calls.length = 0
    await session.validateDraft(F)
    expect(calls).toContain('save 11')
    expect(session.state.edits[F]?.check).toEqual({ confirmable: true, issues: [] })
  })
})
