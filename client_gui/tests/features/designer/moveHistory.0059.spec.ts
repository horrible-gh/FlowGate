/**
 * flowgate.v02.0059 T#2 (0011-CH contract C) — card moves in the Flow's one undo / redo history:
 * - the draft editor: a move entry (`record`) next to the structure entries, stepped through `onMove`
 *   (never a draft save), in the order the actions were made; `tmp_` key remap and revived keys rename it;
 *   a new action empties redo, a fresh read / reset empties both, a refused (conflict) save stops it; an
 *   editor without save keeps moves only.
 * - the Center session: one drop / one group drop / 정렬 / 자동 배치 = one entry; undo / redo put the cards back
 *   through the move path (Graph + layout save queue); mixed with add / connect; a Flow with no editable draft.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDesignerCenterSession, type NodePlacement } from '@/features/designer/center/designerCenterSession'
import { createDraftEditor } from '@/features/designer/center/edit/draftEditor'
import type { DraftModel, DraftNode } from '@/features/designer/center/edit/draftModel'
import type { NodeMove } from '@/features/designer/center/edit/editHistory'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowDraftSaveResult, FlowGraphSource, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'

const draftNode = (key: string, x = 0, y = 320): DraftNode => ({
  key, isNew: key.startsWith('tmp_'), nodeType: 'document', docType: 'D', paletteItemId: null, label: 'D',
  assignee: null, kind: 'design', x, y,
})
const model = (keys: string[]): DraftModel => ({ nodes: keys.map((k) => draftNode(k)), edges: [] })
const move = (nodeId: string, from: [number, number], to: [number, number]): NodeMove => ({
  nodeId, from: { x: from[0], y: from[1] }, to: { x: to[0], y: to[1] },
})

function setupEditor(start = model(['gn_a']), placeAnswer = true) {
  const saves: Array<{ version: number; content: FlowDraftContent; resolve: (r: FlowDraftSaveResult) => void; reject: (e: unknown) => void }> = []
  const placed: NodePlacement[][] = []
  const editor = createDraftEditor(start, 1, {
    save: (v, content) => new Promise((resolve, reject) => saves.push({ version: v, content, resolve, reject })),
    onKeys: () => {},
    onChange: () => {},
    onMove: (placements) => {
      placed.push(placements)
      return placeAnswer
    },
    delayMs: 100,
  })
  return { editor, saves, placed }
}

describe('draft editor — move entries on the same stack', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('a recorded move is undone to `from` and redone to `to` through onMove, and saves no draft', async () => {
    const { editor, saves, placed } = setupEditor()
    expect(editor.canUndo()).toBe(false)
    expect(editor.record([move('gn_a', [0, 320], [400, 100]), move('doc:x', [10, 10], [20, 20])])).toBe(true)
    expect(editor.canUndo()).toBe(true)
    expect(editor.undo()).toBe(true)
    expect(placed.at(-1)).toEqual([{ nodeId: 'gn_a', x: 0, y: 320 }, { nodeId: 'doc:x', x: 10, y: 10 }])
    expect([editor.canUndo(), editor.canRedo()]).toEqual([false, true])
    expect(editor.redo()).toBe(true)
    expect(placed.at(-1)).toEqual([{ nodeId: 'gn_a', x: 400, y: 100 }, { nodeId: 'doc:x', x: 20, y: 20 }])
    expect([editor.canUndo(), editor.canRedo()]).toEqual([true, false])
    await vi.runAllTimersAsync()
    expect(saves).toHaveLength(0)
    expect(editor.status()).toBe('idle')
    expect(editor.record([])).toBe(false)
  })

  it('structure edits and moves step back and forth in the order they were made', () => {
    const { editor, placed } = setupEditor()
    editor.apply({ type: 'add_node', node: draftNode('tmp_b', 500, 40) })
    editor.record([move('tmp_b', [500, 40], [600, 80])])
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })
    const state = () => [editor.model().nodes.map((n) => n.key), editor.model().edges.map((e) => e.key)]
    expect(editor.undo()).toBe(true)                                  // the connect
    expect(state()).toEqual([['gn_a', 'tmp_b'], []])
    expect(editor.undo()).toBe(true)                                  // the move
    expect(placed).toEqual([[{ nodeId: 'tmp_b', x: 500, y: 40 }]])
    expect(editor.undo()).toBe(true)                                  // the add
    expect(state()).toEqual([['gn_a'], []])
    expect(editor.canUndo()).toBe(false)
    expect(editor.redo()).toBe(true)
    expect(state()).toEqual([['gn_a', 'tmp_b'], []])
    expect(editor.redo()).toBe(true)
    expect(placed.at(-1)).toEqual([{ nodeId: 'tmp_b', x: 600, y: 80 }])
    expect(editor.redo()).toBe(true)
    expect(state()).toEqual([['gn_a', 'tmp_b'], ['tmp_e']])
    expect(editor.canRedo()).toBe(false)
  })

  it('a new structure edit or a new move empties redo', () => {
    const { editor } = setupEditor()
    editor.record([move('gn_a', [0, 320], [1, 1])])
    editor.undo()
    expect(editor.canRedo()).toBe(true)
    editor.record([move('gn_a', [0, 320], [2, 2])])
    expect(editor.canRedo()).toBe(false)
    editor.undo()
    editor.apply({ type: 'add_node', node: draftNode('tmp_c') })
    expect(editor.canRedo()).toBe(false)
  })

  it('a save answering the key of a moved new node renames it in the move entries', async () => {
    const { editor, saves, placed } = setupEditor()
    editor.apply({ type: 'add_node', node: draftNode('tmp_b', 500, 40) })
    editor.record([move('tmp_b', [500, 40], [600, 80])])
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b' } })
    await vi.runAllTimersAsync()
    editor.undo()
    expect(placed.at(-1)).toEqual([{ nodeId: 'gn_b', x: 500, y: 40 }])
    editor.redo()
    expect(placed.at(-1)).toEqual([{ nodeId: 'gn_b', x: 600, y: 80 }])
  })

  it('a node revived under a new key takes its moves along (the other history follows the rename)', async () => {
    const { editor, saves, placed } = setupEditor()
    editor.apply({ type: 'add_node', node: draftNode('tmp_b', 500, 40) })
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b' } })
    await vi.runAllTimersAsync()
    editor.record([move('gn_b', [500, 40], [600, 80])])
    editor.undo()                                                     // the move
    editor.undo()                                                     // the add: gn_b is deleted ...
    vi.advanceTimersByTime(100)
    saves[1].resolve({ version: 3, keyMap: {} })                      // ... and that is stored
    await vi.runAllTimersAsync()
    expect(editor.redo()).toBe(true)                                  // gn_b comes back under a new key
    const revived = editor.model().nodes[1].key
    expect(revived).toMatch(/^tmp_/)
    expect(editor.redo()).toBe(true)
    expect(placed.at(-1)).toEqual([{ nodeId: revived, x: 600, y: 80 }])
  })

  it('a move whose cards are all gone is dropped; a reset or a conflict ends the move history too', async () => {
    const gone = setupEditor(model(['gn_a']), false)
    gone.editor.record([move('gone', [0, 0], [5, 5])])
    expect(gone.editor.undo()).toBe(false)
    expect([gone.editor.canUndo(), gone.editor.canRedo()]).toEqual([false, false])

    const { editor, saves } = setupEditor()
    editor.record([move('gn_a', [0, 320], [5, 5])])
    editor.reset(model(['gn_a']), 4)
    expect([editor.canUndo(), editor.canRedo()]).toEqual([false, false])
    editor.apply({ type: 'add_node', node: draftNode('tmp_b') })
    vi.advanceTimersByTime(100)
    saves[0].reject({ code: 'draft_version_conflict' })
    await vi.runAllTimersAsync()
    expect(editor.status()).toBe('conflict')
    expect(editor.record([move('gn_a', [0, 320], [9, 9])])).toBe(false)
    expect(editor.canUndo()).toBe(false)
  })

  it('place() keeps the draft positions where the cards are, so a removed card comes back there', () => {
    const { editor } = setupEditor(model(['gn_a', 'gn_b']))
    editor.place([{ nodeId: 'gn_b', x: 700, y: 90 }, { nodeId: 'doc:none', x: 1, y: 1 }])
    editor.apply({ type: 'remove_node', key: 'gn_b' })
    editor.undo()
    expect(editor.model().nodes.find((n) => n.key === 'gn_b')).toMatchObject({ x: 700, y: 90 })
  })

  it('an editor without save keeps moves only: no structure edit applies', () => {
    const placed: NodePlacement[][] = []
    const editor = createDraftEditor({ nodes: [], edges: [] }, 0, { onKeys: () => {}, onChange: () => {}, onMove: (p) => placed.push(p) > 0 })
    expect(editor.apply({ type: 'add_node', node: draftNode('tmp_x') })).toBe(false)
    expect(editor.record([move('doc:a', [0, 0], [50, 60])])).toBe(true)
    expect(editor.undo()).toBe(true)
    expect(editor.redo()).toBe(true)
    expect(placed).toEqual([[{ nodeId: 'doc:a', x: 0, y: 0 }], [{ nodeId: 'doc:a', x: 50, y: 60 }]])
    expect(editor.busy()).toBe(false)
  })
})

// --- Center session -------------------------------------------------------------------------------

const definition = (docType: string) => ({ nodeType: 'document', docType, paletteItemId: null, label: docType, assignee: null })

const draftGraph = (): FlowGraph => ({
  flowId: 'f1',
  ownership: 'unassigned',
  projection: 'documents',
  nodes: [
    { id: 'doc:r', kind: 'requirement', title: 'R', x: 0, y: 0, movable: true },
    { id: 'gn_a', kind: 'requirement', title: 'A', x: 40, y: 320, movable: true, structureEditable: true, definition: definition('R') },
    { id: 'gn_b', kind: 'design', title: 'B', x: 300, y: 380, movable: true, structureEditable: true, definition: definition('D') },
    { id: 'doc:fixed', kind: 'research', title: 'F', x: 900, y: 0, movable: false },
  ],
  edges: [],
  draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  viewMode: 'draft',
})

const v01Graph = (): FlowGraph => ({
  flowId: 'f1',
  ownership: 'v01',
  projection: 'sequence',
  nodes: [
    { id: 'doc:root', kind: 'requirement', title: 'R', x: 0, y: 0, movable: true },
    { id: 'slot_1', kind: 'design', title: 'D', x: 260, y: 0, movable: true },
  ],
  edges: [{ id: 'seq:1', source: 'doc:root', target: 'slot_1', edgeClass: 'execution' }],
  draft: null,
})

function open(graph: () => FlowGraph = draftGraph) {
  const layouts: FlowLayoutChange[] = []
  let keySeq = 0
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => graph()),
    saveLayout: vi.fn(async (_p, _f, change: FlowLayoutChange) => {
      layouts.push(change)
      return { ignoredNodeIds: [] }
    }),
    saveDraft: vi.fn(async (_p, _f, version, content: FlowDraftContent) => ({
      version: version + 1,
      keyMap: Object.fromEntries([...content.nodes, ...content.edges].filter((x) => x.isNew).map((x) => [x.key, `gn_new${keySeq++}`])),
    })),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 1),
  }
  const spec = { nodeType: 'document', docType: 'T', paletteItemId: null, label: 'T', kind: 'task' }
  const center = createDesignerCenterSession({ source, layoutSaveDelayMs: 50, draftSaveDelayMs: 50, readDrop: () => spec })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
  const shown = () => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph as FlowGraph
  }
  const at = (id: string) => {
    const found = shown().nodes.find((n) => n.id === id)
    return found ? [found.x, found.y] : null
  }
  const drop = (x: number, y: number) => center.dropOnCanvas('f1', { getData: () => 'x' }, x, y)
  return { center, source, layouts, shown, at, drop }
}

/** Every position the layout saves stored, last one per node. */
const storedPositions = (layouts: FlowLayoutChange[]) =>
  Object.fromEntries(layouts.flatMap((c) => c.positions).map((p) => [p.nodeId, [p.x, p.y]]))

describe('Center session — moves in the Flow undo / redo', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('one card move: undo puts it back, redo moves it again, each saved through the layout queue', async () => {
    const { center, layouts, at } = open()
    await vi.runAllTimersAsync()
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: false })
    center.moveNode('f1', 'gn_b', 500, 100)
    expect(center.state.edits.f1).toMatchObject({ canUndo: true, canRedo: false })
    await vi.runAllTimersAsync()
    expect(storedPositions(layouts)).toEqual({ gn_b: [500, 100] })
    expect(center.undoEdit('f1')).toBe(true)
    expect(at('gn_b')).toEqual([300, 380])
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: true })
    await vi.runAllTimersAsync()
    expect(storedPositions(layouts)).toEqual({ gn_b: [300, 380] })
    expect(center.redoEdit('f1')).toBe(true)
    expect(at('gn_b')).toEqual([500, 100])
    await vi.runAllTimersAsync()
    expect(storedPositions(layouts)).toEqual({ gn_b: [500, 100] })
    expect(center.state.edits.f1).toMatchObject({ canUndo: true, canRedo: false })
  })

  it('a group move is one entry: one undo takes every card back', async () => {
    const { center, at } = open()
    await vi.runAllTimersAsync()
    expect(center.moveNodes('f1', [{ nodeId: 'doc:r', x: 100, y: 50 }, { nodeId: 'gn_a', x: 140, y: 370 }, { nodeId: 'doc:fixed', x: 1, y: 1 }])).toBe(true)
    expect([at('doc:r'), at('gn_a'), at('doc:fixed')]).toEqual([[100, 50], [140, 370], [900, 0]])
    center.undoEdit('f1')
    expect([at('doc:r'), at('gn_a')]).toEqual([[0, 0], [40, 320]])
    expect(center.state.edits.f1.canUndo).toBe(false)
    // A drop that moves nothing (same place / not movable) is no entry.
    expect(center.moveNodes('f1', [{ nodeId: 'gn_a', x: 40, y: 320 }, { nodeId: 'doc:fixed', x: 5, y: 5 }])).toBe(false)
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: true })
  })

  it('add → move → connect: three undos and three redos in order', async () => {
    const { center, shown, at, drop, source } = open()
    await vi.runAllTimersAsync()
    expect(drop(500, 40)).toBe(true)
    const added = shown().nodes.find((n) => n.id.startsWith('tmp_'))!.id
    center.moveNode('f1', added, 600, 80)
    expect(center.connectNodes('f1', 'gn_a', added)).toBe(true)
    const edges = () => shown().edges.map((e) => [e.source, e.target])
    center.undoEdit('f1')
    expect([at(added), edges()]).toEqual([[600, 80], []])
    center.undoEdit('f1')
    expect([at(added), edges()]).toEqual([[500, 40], []])
    center.undoEdit('f1')
    expect(at(added)).toBeNull()
    expect(center.state.edits.f1.canUndo).toBe(false)
    center.redoEdit('f1')
    expect([at(added), edges()]).toEqual([[500, 40], []])
    center.redoEdit('f1')
    expect([at(added), edges()]).toEqual([[600, 80], []])
    center.redoEdit('f1')
    expect([at(added), edges()]).toEqual([[600, 80], [['gn_a', added]]])
    expect(center.state.edits.f1).toMatchObject({ canUndo: true, canRedo: false })
    await vi.runAllTimersAsync()
    expect(source.saveDraft).toHaveBeenCalled()
  })

  it('a new card moved before its save answered: the stored key takes over its moves (undo saves that key)', async () => {
    const { center, shown, at, drop, layouts } = open()
    await vi.runAllTimersAsync()
    drop(500, 40)
    const added = shown().nodes.find((n) => n.id.startsWith('tmp_'))!.id
    center.moveNode('f1', added, 600, 80)
    await vi.runAllTimersAsync()
    expect(at(added)).toBeNull()
    expect(at('gn_new0')).toEqual([600, 80])
    expect(storedPositions(layouts)).toEqual({ gn_new0: [600, 80] })
    center.undoEdit('f1')
    expect(at('gn_new0')).toEqual([500, 40])
    await vi.runAllTimersAsync()
    expect(storedPositions(layouts)).toEqual({ gn_new0: [500, 40] })
  })

  it('a card removed after a move comes back where it was moved to', async () => {
    const { center, at } = open()
    await vi.runAllTimersAsync()
    center.moveNode('f1', 'gn_b', 700, 90)
    center.removeNode('f1', 'gn_b')
    expect(at('gn_b')).toBeNull()
    center.undoEdit('f1')
    expect(at('gn_b')).toEqual([700, 90])
    center.undoEdit('f1')
    expect(at('gn_b')).toEqual([300, 380])
  })

  it('a Flow with no editable draft still undoes / redoes its moves', async () => {
    const { center, at } = open(v01Graph)
    await vi.runAllTimersAsync()
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: false })
    center.moveNode('f1', 'slot_1', 400, 200)
    expect(center.state.edits.f1.canUndo).toBe(true)
    expect(center.undoEdit('f1')).toBe(true)
    expect(at('slot_1')).toEqual([260, 0])
    expect(center.redoEdit('f1')).toBe(true)
    expect(at('slot_1')).toEqual([400, 200])
  })

  it('reading the Flow again (a change elsewhere) and closing it empty the move history', async () => {
    const { center } = open()
    await vi.runAllTimersAsync()
    center.moveNode('f1', 'gn_b', 500, 100)
    center.undoEdit('f1')
    center.moveNode('f1', 'gn_a', 10, 10)
    expect(center.state.edits.f1).toMatchObject({ canUndo: true, canRedo: false })
    center.refreshFlow('f1')
    await vi.runAllTimersAsync()
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: false })
    center.moveNode('f1', 'gn_a', 20, 20)
    center.closeFlow('f1')
    expect(center.undoEdit('f1')).toBe(false)
  })

  it('정렬 of the selected cards and 자동 배치 are one entry each', async () => {
    const { center, at } = open()
    await vi.runAllTimersAsync()
    expect(center.arrangeNodes('f1', 'align-left')).toBe(false)              // nothing selected
    center.selectAll('f1')
    expect(center.arrangeNodes('f1', 'align-top')).toBe(true)
    expect([at('doc:r'), at('gn_a'), at('gn_b'), at('doc:fixed')]).toEqual([[0, 0], [40, 0], [300, 0], [900, 0]])
    center.undoEdit('f1')
    expect([at('doc:r'), at('gn_a'), at('gn_b')]).toEqual([[0, 0], [40, 320], [300, 380]])
    // 자동 배치 by the server's documents projection rules: documents on one row, draft nodes from y=320.
    expect(center.autoLayout('f1')).toBe(true)
    expect([at('doc:r'), at('gn_a'), at('gn_b'), at('doc:fixed')]).toEqual([[0, 0], [0, 320], [0, 460], [900, 0]])
    expect(center.autoLayout('f1')).toBe(false)                              // already laid out: no entry
    expect(center.autoLayout('f1', 1.5)).toBe(true)
    expect([at('gn_a'), at('gn_b')]).toEqual([[0, 480], [0, 690]])
    center.undoEdit('f1')
    expect([at('gn_a'), at('gn_b')]).toEqual([[0, 320], [0, 460]])
    center.undoEdit('f1')
    expect([at('doc:r'), at('gn_a'), at('gn_b')]).toEqual([[0, 0], [40, 320], [300, 380]])
  })
})
