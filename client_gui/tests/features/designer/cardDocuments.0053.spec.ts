/**
 * flowgate.v02.0053 T#5 — a placed card's document lines reach the Inspector at once. The server makes a
 * card's documents in the draft save that adds the card (T#1) and answers them (`card_documents`); the
 * Center does not read its own save's Canvas change again, so the save answer is what puts the lines (and
 * the first line a person writes, as the node's document) on the shown Graph:
 *
 * - toFlowGraph keeps a node's card lines from the Canvas read;
 * - the draft editor reports every stored save (`onSaved`) after renaming its keys;
 * - withCardDocuments puts the answered lines on the draft nodes; withDraft keeps a node's documents across
 *   other edits while its kind stays, and drops them when the node is retyped;
 * - the Center session: drop → save → the selected card's Inspector target has its lines and document.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { createDraftEditor } from '@/features/designer/center/edit/draftEditor'
import { draftFromGraph, withCardDocuments, withDraft } from '@/features/designer/center/edit/draftModel'
import { applyCommand } from '@/features/designer/center/edit/graphEditCommands'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowDraftSaveResult, FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, readPaletteDragData, writePaletteDragData } from '@/features/designer/palette/paletteCatalog'
import type { FlowCanvas } from '@/features/project'

const line = (slot: string, docType: string, docId: string | null, author: 'human' | 'run'): FlowGraphCardDocument => ({
  slot, docType, author, docId, title: `구현 · ${slot}`, status: 'draft', docReviewStatus: null,
})

const TASK_LINES = [line('instruction', 'T', 'f1.0002-T', 'human'), line('report', 'TR', 'f1.0003-TR', 'run')]

const draftNode = (id: string, docType: string, extra: Partial<FlowGraphNode> = {}): FlowGraphNode => ({
  id,
  kind: 'task',
  title: '구현',
  x: 0,
  y: 0,
  movable: true,
  structureEditable: true,
  definition: { nodeType: 'document', docType, paletteItemId: 'task', label: '구현', assignee: null },
  ...extra,
})

describe('the Canvas read keeps the card lines (toFlowGraph)', () => {
  it('a Graph / draft node carries its lines; a node without them carries none', () => {
    const canvas = {
      flowId: 'f1',
      projectId: 'p',
      ownership: { state: 'unassigned', sequenceRootDocId: null, graphRevisionNo: null, assignedAt: null },
      projection: 'graph',
      nodes: [
        { nodeKey: 'gn_t', identitySource: 'graph_node', origin: 'draft_node', kind: 'task', docType: 'T', title: '구현', state: 'draft', position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: true }, document: null, slot: null, cardDocuments: TASK_LINES },
        { nodeKey: 'doc:r', identitySource: 'document', origin: 'document', kind: 'requirement', docType: 'R', title: 'R', state: 'draft', position: { x: 0, y: 0, source: 'auto' }, editable: { move: true, structure: false }, document: null, slot: null },
      ],
      edges: [],
      externalRelations: [],
      viewport: null,
      identity: { sequenceTag: null, rematched: false, retiredNodeKeys: [] },
      collapsed: { applied: [], ignoredKeys: [], hiddenCounts: {} },
      view: { mode: 'draft', revisionNo: null, draftVersion: 1 },
      graph: { draft: null, latestRevisionNo: null, rootBinding: null },
    } as FlowCanvas
    const graph = toFlowGraph(canvas)
    expect(graph.nodes[0].cardDocuments).toEqual(TASK_LINES)
    expect(graph.nodes[1]).not.toHaveProperty('cardDocuments')
  })
})

describe('the draft model (withCardDocuments / withDraft)', () => {
  const graph: FlowGraph = {
    flowId: 'f1',
    nodes: [{ id: 'doc:r', kind: 'requirement', title: 'R', x: 0, y: 0 }, draftNode('gn_t', 'T'), draftNode('gn_g', 'T')],
    edges: [],
  }

  it('the answered lines go on the draft nodes; a node without a document shows its first line a person writes', () => {
    const report = { ...TASK_LINES[1] }
    const shown = withCardDocuments(graph, { gn_t: [report, TASK_LINES[0]] })
    const task = shown.nodes.find((node) => node.id === 'gn_t')!
    expect(task.cardDocuments).toEqual([report, TASK_LINES[0]])
    expect(task.document).toEqual({ docId: 'f1.0002-T', typeCode: 'T', seq: null, title: '구현 · instruction', docReviewStatus: null, status: 'draft', supersedes: [] })
    // A draft node the answer has no lines for has none; a node that is not a draft node is left as it is.
    expect(shown.nodes.find((node) => node.id === 'gn_g')!.cardDocuments).toEqual([])
    expect(shown.nodes.find((node) => node.id === 'gn_g')).not.toHaveProperty('document')
    expect(shown.nodes[0]).toBe(graph.nodes[0])
  })

  it('a node keeps the document it has; without a line a person writes, the first filled line', () => {
    const bound = { docId: 'f1.0001-R', typeCode: 'R', seq: 1, title: 'R', docReviewStatus: null, status: 'open', supersedes: [] }
    const withDocument = { ...graph, nodes: [draftNode('gn_t', 'T', { document: bound })] }
    expect(withCardDocuments(withDocument, { gn_t: TASK_LINES }).nodes[0].document).toBe(bound)
    const runOnly = withCardDocuments(graph, { gn_t: [line('instruction', 'T', null, 'human'), line('report', 'TR', 'f1.0003-TR', 'run')] })
    expect(runOnly.nodes.find((node) => node.id === 'gn_t')!.document?.docId).toBe('f1.0003-TR')
  })

  it('withDraft keeps a node’s documents while its kind stays and drops them when it is retyped', () => {
    const shown = withCardDocuments(graph, { gn_t: TASK_LINES })
    const model = draftFromGraph(shown)
    const other = { key: 'tmp_x', isNew: true, nodeType: 'document', docType: 'N', paletteItemId: 'research', label: '조사', assignee: null, kind: 'research', x: 9, y: 9 }
    const edited = applyCommand(model, { type: 'add_node', node: other })!.model
    const kept = withDraft(shown, edited).nodes.find((node) => node.id === 'gn_t')!
    expect(kept.cardDocuments).toEqual(TASK_LINES)
    expect(kept.document?.docId).toBe('f1.0002-T')
    const retyped = { ...model, nodes: model.nodes.map((node) => (node.key === 'gn_t' ? { ...node, docType: 'N' } : node)) }
    const dropped = withDraft(shown, retyped).nodes.find((node) => node.id === 'gn_t')!
    expect(dropped).not.toHaveProperty('cardDocuments')
    expect(dropped).not.toHaveProperty('document')
  })
})

describe('the draft editor reports every stored save (onSaved)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('after the key renames, with what the server answered; a refused save is not reported', async () => {
    const order: string[] = []
    const saves: Array<{ resolve: (r: FlowDraftSaveResult) => void; reject: (e: unknown) => void }> = []
    const editor = createDraftEditor({ nodes: [], edges: [] }, 0, {
      save: () => new Promise((resolve, reject) => saves.push({ resolve, reject })),
      onKeys: () => order.push('keys'),
      onSaved: (result) => order.push(`saved:${Object.keys(result.cardDocuments ?? {}).join(',')}`),
      onChange: () => {},
      delayMs: 10,
    })
    editor.apply({ type: 'add_node', node: { key: 'tmp_a', isNew: true, nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '구현', assignee: null, kind: 'task', x: 0, y: 0 } })
    vi.advanceTimersByTime(10)
    saves[0].resolve({ version: 1, keyMap: { tmp_a: 'gn_a' }, cardDocuments: { gn_a: TASK_LINES } })
    await vi.runAllTimersAsync()
    expect(order).toEqual(['keys', 'saved:gn_a'])
    editor.apply({ type: 'add_node', node: { key: 'tmp_b', isNew: true, nodeType: 'document', docType: 'N', paletteItemId: 'research', label: '조사', assignee: null, kind: 'research', x: 0, y: 0 } })
    vi.advanceTimersByTime(10)
    saves[1].reject(Object.assign(new Error('x'), { code: 'save_failed' }))
    await vi.runAllTimersAsync()
    expect(order).toEqual(['keys', 'saved:gn_a'])
  })
})

describe('Center session: a placed card has its lines at once (flowgate.v02.0053 T#5)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function dataTransfer(itemId: string) {
    const store = new Map<string, string>()
    const transfer = { setData: (k: string, v: string) => store.set(k, v), getData: (k: string) => store.get(k) ?? '', effectAllowed: 'none' }
    const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === itemId)!
    writePaletteDragData(transfer as unknown as DataTransfer, paletteItemDefinition(item, '구현'))
    return transfer
  }

  it('drop → the save answers the card’s lines → the selected card’s Inspector target has them and its document', async () => {
    const reads: FlowGraph = {
      flowId: 'f1',
      ownership: 'unassigned',
      nodes: [],
      edges: [],
      draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
      viewMode: null,
    }
    const loadGraph = vi.fn(async () => reads)
    const source: FlowGraphSource = {
      isSample: false,
      loadGraph,
      saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
      saveDraft: vi.fn(async (_p: string, _f: string, version: number, content: FlowDraftContent) => {
        const key = content.nodes.find((node) => node.isNew)?.key
        return { version: version + 1, keyMap: key ? { [key]: 'gn_t' } : {}, cardDocuments: { gn_t: TASK_LINES } }
      }),
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
    await vi.runAllTimersAsync()
    expect(center.dropOnCanvas('f1', dataTransfer('task'), 100, 100)).toBe(true)
    const local = (center.state.graphs.f1 as { graph: FlowGraph }).graph.nodes.find((node) => node.id.startsWith('tmp_'))!
    center.selectNode('f1', local.id)
    expect(toInspectorTarget(center.activeSelection.value)?.cardDocuments).toEqual([])
    await vi.runAllTimersAsync()
    const target = toInspectorTarget(center.activeSelection.value)!
    expect(target.nodeId).toBe('gn_t')
    expect(target.cardDocuments).toEqual(TASK_LINES)
    expect(target.docId).toBe('f1.0002-T')
    // The lines came from the save answer: the Flow was read once (when it opened), not again.
    expect(loadGraph).toHaveBeenCalledTimes(1)
    center.dispose?.()
  })
})
