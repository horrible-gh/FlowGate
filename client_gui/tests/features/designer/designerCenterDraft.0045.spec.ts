/**
 * flowgate.v02.0045 T0013 (S3+S4) — Center session Graph draft editing over a fake source:
 * Palette drop -> draft node (the Shell's `readDrop`), the first draft save (version 0), key
 * remapping with the node's position stored only after it has its key, connect / remove / undo,
 * re-reads deferred while a draft save waits, validation, confirmation (S4-1) and its refusal
 * with issues (S4-4). No DOM.
 * TR0020 rev3: no start-draft / switch-view actions — a Graph Flow's latest revision is edited as
 * is (its first save creates the draft), a stored draft is shown without asking, and 실행 shows
 * the run again. TR0020 rev4: a refused save (conflict / error) reads the Flow again by itself —
 * what the removed 다시 불러오기 button did — and the local edits it refused are dropped.
 * Rework (TR0014 r1): a tab closed / project changed while a new node's draft save runs still
 * stores the node's position once the save answers its key, and a reopened tab reads it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'
import { paletteNodeSpec, readPaletteDragData, writePaletteDragData, DEFAULT_PALETTE_ITEMS, paletteItemDefinition } from '@/features/designer/palette/paletteCatalog'

const unassigned = (version = 0, extra: Partial<FlowGraph> = {}): FlowGraph => ({
  flowId: 'f1',
  ownership: 'unassigned',
  nodes: [{ id: 'doc:r', kind: 'requirement', title: 'R', x: 0, y: 0, movable: true }],
  edges: [],
  draft: { version, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  viewMode: version > 0 ? 'draft' : null,
  ...extra,
})

function dataTransfer(itemId: string) {
  const store = new Map<string, string>()
  const transfer = { setData: (k: string, v: string) => store.set(k, v), getData: (k: string) => store.get(k) ?? '', effectAllowed: 'none' }
  const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === itemId)!
  writePaletteDragData(transfer as unknown as DataTransfer, paletteItemDefinition(item, item.id === 'task' ? '구현' : item.id))
  return transfer
}

function setup(graphs: FlowGraph[]) {
  const loads: Array<{ view?: string }> = []
  const drafts: Array<{ version: number; content: FlowDraftContent }> = []
  const layouts: FlowLayoutChange[] = []
  const reads = [...graphs]
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async (_p: string, _f: string, view?: string) => {
      loads.push({ view })
      return reads.length > 1 ? reads.shift()! : reads[0]
    }),
    saveLayout: vi.fn(async (_p, _f, change) => {
      layouts.push(change)
      return { ignoredNodeIds: [] }
    }),
    saveDraft: vi.fn(async (_p, _f, version, content) => {
      drafts.push({ version, content })
      const keyMap: Record<string, string> = {}
      content.nodes.filter((n) => n.isNew).forEach((n, i) => (keyMap[n.key] = `gn_new${drafts.length}${i}`))
      content.edges.filter((e) => e.isNew).forEach((e, i) => (keyMap[e.key] = `ge_new${drafts.length}${i}`))
      return { version: version + 1, keyMap }
    }),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 1),
  }
  const center = createDesignerCenterSession({
    source,
    layoutSaveDelayMs: 50,
    draftSaveDelayMs: 50,
    readDrop: (transfer) => {
      const definition = readPaletteDragData(transfer)
      return definition ? paletteNodeSpec(definition) : null
    },
  })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
  return { center, source, loads, drafts, layouts }
}

/** Holds each draft save until the test releases it (the save runs on release). */
function holdDraftSaves(source: FlowGraphSource) {
  const held: Array<() => void> = []
  const save = source.saveDraft!
  source.saveDraft = vi.fn((p: string, f: string, v: number, c: FlowDraftContent) =>
    new Promise<Awaited<ReturnType<typeof save>>>((resolve, reject) => held.push(() => void save(p, f, v, c).then(resolve, reject))))
  return held
}

const shown = (center: ReturnType<typeof setup>['center']) => {
  const load = center.state.graphs.f1
  if (load?.status !== 'ready') throw new Error('not ready')
  return load.graph as FlowGraph
}

describe('Palette node spec', () => {
  it('maps document cards, the confirm card and custom cards onto Graph node types', () => {
    const def = (id: string) => paletteItemDefinition(DEFAULT_PALETTE_ITEMS.find((i) => i.id === id)!, id)
    expect(paletteNodeSpec(def('task'))).toEqual({ nodeType: 'document', docType: 'T', paletteItemId: 'task', label: 'task', kind: 'task' })
    expect(paletteNodeSpec(def('confirm'))).toMatchObject({ nodeType: 'gate', docType: null, kind: 'approval' })
    expect(paletteNodeSpec({ ...def('task'), itemId: 'custom.1', custom: true, docType: null, nodeKind: 'custom.1' }).nodeType).toBe('custom')
  })
})

describe('Center session draft editing', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('a Palette drop creates the first draft and stores the node position once it has its key', async () => {
    const { center, drafts, layouts } = setup([unassigned()])
    await vi.runAllTimersAsync()
    expect(center.dropOnCanvas('f1', dataTransfer('task'), 560, 320)).toBe(true)
    const local = shown(center).nodes.find((n) => n.id.startsWith('tmp_'))!
    expect([local.title, local.x, local.y, local.structureEditable]).toEqual(['구현', 560, 320, true])
    expect(toVueFlowNodes(shown(center), null).find((n) => n.id === local.id)?.connectable).toBe(true)
    center.moveNode('f1', local.id, 600, 330)              // not stored yet: no key
    await vi.advanceTimersByTimeAsync(60)
    expect(drafts).toHaveLength(1)
    expect(drafts[0].version).toBe(0)
    expect(drafts[0].content.nodes).toEqual([{ key: local.id, isNew: true, nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '구현', assignee: null }])
    await vi.runAllTimersAsync()
    expect(shown(center).nodes.map((n) => n.id)).toEqual(['doc:r', 'gn_new10'])
    expect(layouts).toEqual([{ positions: [{ nodeId: 'gn_new10', x: 600, y: 330 }], viewport: null }])
    expect(center.state.edits.f1.status).toBe('idle')
  })

  it('connect, remove and undo edit the draft; a drop without Palette data is ignored', async () => {
    const graph = unassigned(2, {
      nodes: [
        { id: 'gn_a', kind: 'requirement', title: 'R', x: 0, y: 320, movable: true, structureEditable: true,
          definition: { nodeType: 'document', docType: 'R', paletteItemId: null, label: 'R', assignee: null } },
        { id: 'gn_b', kind: 'design', title: 'D', x: 260, y: 320, movable: true, structureEditable: true,
          definition: { nodeType: 'document', docType: 'D', paletteItemId: 'basic_design', label: 'D', assignee: null } },
      ],
    })
    const { center, drafts } = setup([graph])
    await vi.runAllTimersAsync()
    expect(center.dropOnCanvas('f1', { getData: () => '' }, 1, 1)).toBe(false)
    expect(center.checkConnection('f1', 'gn_a', 'doc:r')).toEqual({ ok: false, reason: 'not_editable' })
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(true)
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(false)                 // duplicate
    expect(shown(center).edges.map((e) => [e.source, e.target, e.editable])).toEqual([['gn_a', 'gn_b', true]])
    expect(center.removeNode('f1', 'gn_b')).toBe(true)
    expect(shown(center).edges).toEqual([])
    expect(center.state.edits.f1.canUndo).toBe(true)
    expect(center.undoEdit('f1')).toBe(true)
    expect(shown(center).nodes.map((n) => n.id).sort()).toEqual(['gn_a', 'gn_b'])
    expect(shown(center).edges).toHaveLength(1)
    await vi.runAllTimersAsync()
    expect(drafts.at(-1)!.version).toBe(2)
    expect(drafts.at(-1)!.content.nodes.map((n) => n.key)).toEqual(['gn_a', 'gn_b'])
  })

  it('a refused save reads the Flow again by itself and drops the refused edits (rev4)', async () => {
    for (const code of ['draft_version_conflict', 'request_failed']) {
      const { center, source, loads } = setup([unassigned(1)])
      await vi.runAllTimersAsync()
      source.saveDraft = vi.fn(async () => {
        throw Object.assign(new Error('refused'), { code })
      })
      const before = loads.length
      center.dropOnCanvas('f1', dataTransfer('task'), 10, 10)
      expect(shown(center).nodes).toHaveLength(2)
      await vi.runAllTimersAsync()
      expect(source.saveDraft).toHaveBeenCalledTimes(1)
      expect(loads.length, code).toBe(before + 1)
      expect(center.state.edits.f1.status, code).toBe('idle')
      expect(center.state.edits.f1.errorCode).toBeNull()
      expect(shown(center).nodes.map((n) => n.id)).toEqual(['doc:r'])
      // Editing goes on from the stored draft; the next save is tried again.
      source.saveDraft = vi.fn(async (_p, _f, version) => ({ version: version + 1, keyMap: {} }))
      expect(center.dropOnCanvas('f1', dataTransfer('task'), 10, 10)).toBe(true)
      await vi.runAllTimersAsync()
      expect(source.saveDraft).toHaveBeenCalledWith('p', 'f1', 1, expect.anything())
      expect(center.state.edits.f1.status).toBe('idle')
      expect(loads.length, code).toBe(before + 1)
    }
  })

  it('a re-read asked while a draft save waits runs only after the save', async () => {
    const { center, loads } = setup([unassigned()])
    await vi.runAllTimersAsync()
    center.dropOnCanvas('f1', dataTransfer('task'), 10, 10)
    const before = loads.length
    center.reloadDraft('f1')                                  // a re-read request while busy
    expect(loads.length).toBe(before)
    await vi.runAllTimersAsync()
    expect(loads.length).toBe(before + 1)
  })

  it('validate and confirm run on the saved version; confirmation re-reads the Flow', async () => {
    const { center, source, loads } = setup([unassigned(1), unassigned(1)])
    await vi.runAllTimersAsync()
    await center.validateDraft('f1')
    expect(source.validateDraft).toHaveBeenCalledWith('p', 'f1', 1)
    expect(center.state.edits.f1.check).toEqual({ confirmable: true, issues: [] })
    await center.confirmDraft('f1')
    expect(source.confirmDraft).toHaveBeenCalledWith('p', 'f1', 1, null)
    expect(center.state.edits.f1.notice).toEqual({ kind: 'confirmed', revisionNo: 1 })
    await vi.runAllTimersAsync()
    expect(loads.at(-1)).toEqual({ view: undefined })
  })

  it('a refused confirmation shows its issues; an ownership refusal re-reads', async () => {
    const { center, source, loads } = setup([unassigned(1)])
    await vi.runAllTimersAsync()
    source.confirmDraft = vi.fn(async () => {
      throw Object.assign(new Error('x'), { code: 'graph_validation_failed', issues: [{ code: 'rule_undecided', structure: 'branch', nodeIds: ['gn_a'], edgeIds: ['ge_1'], message: '분기' }] })
    })
    await center.confirmDraft('f1')
    expect(center.state.edits.f1.check?.issues.map((i) => i.nodeIds)).toEqual([['gn_a']])
    source.confirmDraft = vi.fn(async () => {
      throw Object.assign(new Error('x'), { code: 'flow_owned_by_v01' })
    })
    const before = loads.length
    await center.confirmDraft('f1')
    expect(center.state.edits.f1.notice).toEqual({ kind: 'error', code: 'flow_owned_by_v01' })
    await vi.runAllTimersAsync()
    expect(loads.length).toBe(before + 1)
  })

  it('a tab closed while a new node waits for its key still stores the node where it was shown', async () => {
    const { center, source, layouts } = setup([unassigned()])
    const held = holdDraftSaves(source)
    await vi.runAllTimersAsync()
    center.dropOnCanvas('f1', dataTransfer('task'), 560, 320)
    const local = shown(center).nodes.find((n) => n.id.startsWith('tmp_'))!
    center.moveNode('f1', local.id, 600, 330)
    await vi.advanceTimersByTimeAsync(60)
    expect(held).toHaveLength(1)                              // the draft save is on the wire
    center.closeFlow('f1')
    expect(center.state.graphs.f1).toBeUndefined()
    held.shift()!()
    await vi.runAllTimersAsync()
    expect(source.saveLayout).toHaveBeenCalledWith('p', 'f1', { positions: [{ nodeId: 'gn_new10', x: 600, y: 330 }], viewport: null })
    expect(layouts).toEqual([{ positions: [{ nodeId: 'gn_new10', x: 600, y: 330 }], viewport: null }])
    expect(center.state.edits.f1).toBeUndefined()
  })

  it('edits made during the save still go out after the close; a reopened tab reads once positions are stored', async () => {
    const { center, source, drafts, layouts } = setup([unassigned()])
    const held = holdDraftSaves(source)
    await vi.runAllTimersAsync()
    center.dropOnCanvas('f1', dataTransfer('task'), 560, 320)
    await vi.advanceTimersByTimeAsync(60)                     // save 1 (node A) on the wire
    center.dropOnCanvas('f1', dataTransfer('task'), 100, 50)  // node B waits for the next save
    center.closeFlow('f1')
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })   // reopened before the save answered
    await vi.advanceTimersByTimeAsync(0)
    held.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect(held).toHaveLength(1)                              // save 2 (node B) went out after the close
    held.shift()!()
    await vi.runAllTimersAsync()
    expect(drafts.map((d) => [d.version, d.content.nodes.map((n) => n.isNew)])).toEqual([[0, [true]], [1, [false, true]]])
    expect(layouts).toEqual([
      { positions: [{ nodeId: 'gn_new10', x: 560, y: 320 }], viewport: null },
      { positions: [{ nodeId: 'gn_new20', x: 100, y: 50 }], viewport: null },
    ])
    const loadOrder = vi.mocked(source.loadGraph).mock.invocationCallOrder
    const layoutOrder = vi.mocked(source.saveLayout!).mock.invocationCallOrder
    expect(loadOrder.at(-1)!).toBeGreaterThan(layoutOrder.at(-1)!)   // the reopened tab read after the last position save
  })

  it('a project change while the save runs stores the position for the project the save belongs to', async () => {
    const { center, source } = setup([unassigned()])
    const held = holdDraftSaves(source)
    await vi.runAllTimersAsync()
    center.dropOnCanvas('f1', dataTransfer('task'), 40, 70)
    await vi.advanceTimersByTimeAsync(60)
    center.setProject('q')
    held.shift()!()
    await vi.runAllTimersAsync()
    expect(source.saveDraft).toHaveBeenCalledWith('p', 'f1', 0, expect.anything())
    expect(source.saveLayout).toHaveBeenCalledWith('p', 'f1', { positions: [{ nodeId: 'gn_new10', x: 40, y: 70 }], viewport: null })
  })

  const graphNode = { id: 'gn_r', kind: 'requirement', title: 'R', state: 'done', x: 0, y: 0, movable: true, structureEditable: false,
    definition: { nodeType: 'document', docType: 'R', paletteItemId: null, label: 'R', assignee: null } }
  const runView = (extra: Partial<FlowGraph> = {}): FlowGraph => ({
    flowId: 'f1', ownership: 'graph', nodes: [graphNode], edges: [], draft: null, viewMode: 'revision', latestRevisionNo: 2, viewRevisionNo: 2, ...extra,
  })

  it('a Graph Flow showing its latest revision is edited as is: the first save creates the draft from it', async () => {
    const { center, source, loads, drafts } = setup([runView()])
    source.loadRevision = vi.fn()
    await vi.runAllTimersAsync()
    const shown = center.activeGraphLoad.value
    expect(shown?.status === 'ready' && shown.graph.draft).toEqual({ version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 2 })
    expect(shown?.status === 'ready' && shown.graph.nodes[0].state).toBe('done')        // run state kept
    expect(center.dropOnCanvas('f1', dataTransfer('task'), 40, 70)).toBe(true)
    await vi.runAllTimersAsync()
    expect(drafts).toHaveLength(1)
    expect(drafts[0].version).toBe(0)
    expect(drafts[0].content.nodes.map((n) => [n.key, n.isNew])).toEqual([['gn_r', false], [expect.stringMatching(/^tmp_/), true]])
    expect(source.loadRevision).not.toHaveBeenCalled()
    // 실행: confirmed on top of revision 2, then the server's default view (the run) is read.
    await expect(center.prepareRun('f1')).resolves.toBe(true)
    expect(source.confirmDraft).toHaveBeenCalledWith('p', 'f1', 1, 2)
    await vi.runAllTimersAsync()
    expect(loads.at(-1)).toEqual({ view: undefined })
  })

  it('a run pinned to an older revision is not offered for editing', async () => {
    const { center } = setup([runView({ viewRevisionNo: 1 })])
    await vi.runAllTimersAsync()
    expect(center.dropOnCanvas('f1', dataTransfer('task'), 1, 1)).toBe(false)
    const shown = center.activeGraphLoad.value
    expect(shown?.status === 'ready' && shown.graph.draft).toBeNull()
  })

  it('a Graph Flow with a stored draft shows the draft without asking', async () => {
    const stored = { version: 3, editable: false, confirmable: true, blockedReason: null, baseRevisionNo: 2 }
    const draftView: FlowGraph = { ...runView(), nodes: [{ ...graphNode, structureEditable: true }], draft: { ...stored, editable: true }, viewMode: 'draft' }
    const { center, loads } = setup([runView({ draft: stored }), draftView])
    await vi.runAllTimersAsync()
    expect(loads).toEqual([{ view: undefined }, { view: 'draft' }])
    const shown = center.activeGraphLoad.value
    expect(shown?.status === 'ready' && shown.graph.viewMode).toBe('draft')
    expect(center.dropOnCanvas('f1', dataTransfer('task'), 1, 1)).toBe(true)
  })

  it('a draft gone before its read falls back to the server default', async () => {
    const { center, source, loads } = setup([runView()])
    const answers: Array<() => FlowGraph> = [
      () => runView({ draft: { version: 1, editable: false, confirmable: true, blockedReason: null, baseRevisionNo: 2 } }),
      () => { throw new Error('draft_not_found') },
      () => runView(),
    ]
    source.loadGraph = vi.fn(async (_p: string, _f: string, view?: string) => {
      loads.push({ view })
      return answers.shift()!()
    })
    center.retryGraph('f1')
    await vi.runAllTimersAsync()
    expect(loads.slice(-3)).toEqual([{ view: undefined }, { view: 'draft' }, { view: undefined }])
    expect(center.activeGraphLoad.value?.status).toBe('ready')
  })
})
