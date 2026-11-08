import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { cardSettingsDisabledReason } from '@/features/designer/inspector/features/settings/cardSettingsModel'
import { cardMemoLockedReason } from '@/features/designer/inspector/features/cardMemo/cardMemoModel'
import { gateDecisionReason } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import { qnaDisabledReason } from '@/features/designer/inspector/features/qna/qnaModel'

const F = 'p.ui.0152'
const settle = async () => { for (let i = 0; i < 6; i++) await flushPromises() }
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function fixture() {
  const graph = {
    flowId: F, ownership: 'graph', viewMode: 'draft', latestRevisionNo: 1,
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
    nodes: ['a', 'b'].map((id, i) => ({
      id, kind: 'task', title: id, x: i * 300, y: 0, movable: true, structureEditable: true,
      definition: { nodeType: 'document', docType: 'T', rootBound: false, assignee: null, paletteItemId: null },
    })),
    edges: [], run: { runId: 'r', status: 'idle', lastEventSeq: 1 },
  } as unknown as FlowGraph
  const run = { runId: 'r', status: 'idle', openNodeKeys: [], nodeRuns: [], viewerCanApprove: true }
  return {
    isSample: false, loadGraph: vi.fn(async () => graph), loadRun: vi.fn(async () => run),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async (_p: string, _f: string, v: number) => ({ version: v + 1, keyMap: {}, removedNodeIds: [], removedEdgeIds: [] })),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 2),
    startRun: vi.fn(async () => run), stopRun: vi.fn(async () => run),
    pauseRun: vi.fn(async () => run), resumeRun: vi.fn(async () => run),
    writeCardHold: vi.fn(async () => true), timeMachine: vi.fn(),
    saveNodeTitle: vi.fn(),
  }
}
let center: DesignerCenterSession
afterEach(() => { center?.dispose(); vi.useRealTimers() })
async function open(readArchive: () => Promise<{ archived: boolean }>, source = fixture()) {
  center = createDesignerCenterSession({ source, readArchive, layoutSaveDelayMs: 10, draftSaveDelayMs: 10 })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: F, flowName: 'Archived Flow' })
  await settle()
  return source
}
describe('archived Flow viewing', () => {
  it.each(['pending', 'failed', 'archived'])('keeps a readable tab and blocks every center mutation while %s', async (state) => {
    const pending = deferred<{ archived: boolean }>()
    const source = await open(() => state === 'pending' ? pending.promise : state === 'failed'
      ? Promise.reject(new Error('offline')) : Promise.resolve({ archived: true }))
    expect(center.activeGraphLoad.value?.status).toBe('ready')
    expect(center.state.tabs).toHaveLength(1)
    expect(center.runMarks(F).lock).toBe('archived')
    center.selectNode(F, 'a')
    const target = toInspectorTarget(center.activeSelection.value)!
    expect(target).toMatchObject({ readOnly: 'archived', settingsReadOnly: 'archived', canApprove: false, canEditTitle: false })
    const graph = (center.activeGraphLoad.value as any).graph
    expect(toVueFlowNodes(graph, ['a'], new Map(), { marks: center.runMarks(F), selectMode: true })
      .every(node => !node.draggable && !node.connectable)).toBe(true)
    expect(center.removeNode(F, 'a')).toBe(false)
    expect(center.connectNodes(F, 'a', 'b')).toBe(false)
    expect(center.moveNodes(F, [{ nodeId: 'a', x: 100, y: 100 }])).toBe(false)
    expect(center.undoEdit(F)).toBe(false)
    expect(center.redoEdit(F)).toBe(false)
    expect(center.prepareRun(F)).toBe(false)
    expect(await center.runFlow(F, 'full')).toBe(false)
    expect(await center.pauseRun(F)).toBe(false)
    expect(await center.stopRun(F)).toBe(false)
    expect(await center.resumeRun(F)).toBe(false)
    expect(await center.setCardHold(F, 'a', true)).toBe(false)
    expect(center.askTimeMachine(F, 'a')).toBe(false)
    expect((await center.saveNodeTitle(F, 'a', 'Changed')).ok).toBe(false)
    await center.confirmDraft(F)
    await center.validateDraft(F)
    center.commitViewport(F, { x: 25, y: 35, zoom: 1.4 })
    expect(center.activeView.value?.viewport).toEqual({ x: 25, y: 35, zoom: 1.4 })
    center.closeFlow(F)
    await settle()
    for (const name of ['saveLayout', 'saveDraft', 'confirmDraft', 'validateDraft', 'startRun', 'pauseRun',
      'resumeRun', 'stopRun', 'writeCardHold', 'timeMachine', 'saveNodeTitle'] as const) expect(source[name]).not.toHaveBeenCalled()
  })
  it('restores editing in the existing tab and preserves active Flow behavior', async () => {
    const source = await open(async () => ({ archived: true }))
    center.setFlowArchived(F, false)
    expect(center.isFlowReadOnly(F)).toBe(false)
    expect(center.removeNode(F, 'a')).toBe(true)
    center.closeFlow(F) // flush the legitimate draft save
    await settle()
    expect(source.saveDraft).toHaveBeenCalledTimes(1)
  })
  it('an archive event cancels queued layout/draft writes before close flushes them', async () => {
    vi.useFakeTimers()
    const source = await open(async () => ({ archived: false }))
    center.commitViewport(F, { x: 10, y: 20, zoom: 1.2 })
    expect(center.removeNode(F, 'a')).toBe(true)
    center.setFlowArchived(F, true)
    center.closeFlow(F)
    await vi.advanceTimersByTimeAsync(100)
    await settle()
    expect(source.saveLayout).not.toHaveBeenCalled()
    expect(source.saveDraft).not.toHaveBeenCalled()
  })
  it.each([false, true])('project switching flushes active saves and refuses archived saves (archived=%s)', async archived => {
    vi.useFakeTimers()
    const source = await open(async () => ({ archived: false }))
    expect(center.removeNode(F, 'a')).toBe(true)
    if (archived) center.setFlowArchived(F, true)
    center.setProject('another-project')
    await vi.advanceTimersByTimeAsync(100)
    await settle()
    expect(source.saveDraft).toHaveBeenCalledTimes(archived ? 0 : 1)
  })
  it('an old archive read cannot unlock a newer archive event or another opening', async () => {
    const pending = deferred<{ archived: boolean }>()
    await open(() => pending.promise)
    center.setFlowArchived(F, true)
    pending.resolve({ archived: false })
    await settle()
    expect(center.isFlowReadOnly(F)).toBe(true)
    expect(center.state.tabs).toHaveLength(1)
  })
  it('locks revision settings, memos, gate decisions and Q&A while keeping selection', async () => {
    await open(async () => ({ archived: true }))
    center.selectNode(F, 'a')
    const selection = center.activeSelection.value!
    const target = toInspectorTarget({ ...selection, graph: { ...selection.graph, viewMode: 'revision' } })!
    expect(cardSettingsDisabledReason({ ...target, placing: false, read: true, locked: false, blocked: null })).toBe('reason.read_only_archived')
    expect(cardMemoLockedReason(target)).toBe('locked.archived')
    expect(gateDecisionReason(target, { status: 'idle', nodes: [{ nodeKey: 'a', status: 'open', gate: { can_decide: true } }] } as any, false)).toBe('reason.read_only_archived')
    expect(qnaDisabledReason({ readOnly: target.readOnly } as any)).toBe('reason.read_only_archived')
  })
})
