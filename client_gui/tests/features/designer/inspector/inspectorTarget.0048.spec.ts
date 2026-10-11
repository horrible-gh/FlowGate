/**
 * flowgate.v02.0048 T#1 — selection context -> InspectorTarget (D#1 §4.3 / §4.4 / §8.5): card
 * kinds, the read-only reason table (ownership 4 × view 3), documentless Nodes and the fields
 * taken from the bound document, the v0.1 slot and the Graph node run.
 */
import { describe, expect, it } from 'vitest'
import {
  INSPECTOR_CARD_DOC_TYPES,
  INSPECTOR_CARD_KINDS,
  INSPECTOR_DOC_TYPES,
  inspectorCardKind,
  inspectorReadOnly,
  toInspectorTarget,
} from '@/features/designer/inspector/inspectorTarget'
import { GRAPH_GRAPH, SELECTIONS, V01_GRAPH, selectionOf, targetOf } from './support/inspectorSupport'

describe('card kind of a document type', () => {
  it.each([
    ['R', 'requirement'], ['B', 'requirement'],
    ['N', 'research'], ['NR', 'research'],
    ['T', 'task'], ['TR', 'task'], ['T2', 'task'], ['TR2', 'task'],
    ['DS', 'design'], ['D', 'design'], ['P', 'design'], ['L', 'design'], ['DB', 'design'],
    ['TS', 'test'], ['TSR', 'test'], ['V', 'test'],
    ['AC', 'check'],
    ['WP', 'plan'],
    ['M', 'other'], ['CH', 'other'], ['Q', 'other'], ['A', 'other'], ['RJ', 'other'], ['DC', 'other'], ['C', 'other'],
    ['ZZ', 'other'], [null, 'other'], ['', 'other'], [' tr ', 'task'],
  ] as const)('%s -> %s', (docType, kind) => {
    expect(inspectorCardKind(docType)).toBe(kind)
  })

  it('names exactly the eight card kinds, `other` holding no fixed type', () => {
    expect(INSPECTOR_CARD_KINDS).toEqual(['requirement', 'research', 'task', 'design', 'test', 'check', 'plan', 'other'])
    expect(Object.keys(INSPECTOR_CARD_DOC_TYPES)).toEqual(INSPECTOR_CARD_KINDS.filter((kind) => kind !== 'other'))
    expect([...INSPECTOR_DOC_TYPES].sort()).toEqual(
      ['R', 'B', 'N', 'NR', 'T', 'TR', 'T2', 'TR2', 'DS', 'D', 'P', 'L', 'DB', 'TS', 'TSR', 'V', 'AC', 'WP'].sort(),
    )
  })
})

describe('read-only reason (ownership × view)', () => {
  it.each([
    ['unassigned', 'revision', 'revision'],
    ['unassigned', 'draft', null],
    ['unassigned', null, null],
    ['v01', 'revision', 'revision'],
    ['v01', 'draft', null],
    ['v01', null, null],
    ['graph', 'revision', 'revision'],
    ['graph', 'draft', null],
    ['graph', null, null],
    ['conflict', 'revision', 'conflict'],
    ['conflict', 'draft', 'conflict'],
    ['conflict', null, 'conflict'],
  ] as const)('ownership %s, view %s -> %s', (ownership, viewMode, readOnly) => {
    expect(inspectorReadOnly(ownership, viewMode)).toBe(readOnly)
    const graph = { ...GRAPH_GRAPH, ownership, viewMode }
    expect(targetOf(selectionOf(graph, GRAPH_GRAPH.nodes[0].id)).readOnly).toBe(readOnly)
  })
})

describe('toInspectorTarget', () => {
  it('is null without a selection', () => {
    expect(toInspectorTarget(null)).toBeNull()
    expect(toInspectorTarget(undefined)).toBeNull()
  })

  it('v0.1 document Node: document, slot, card kind and states', () => {
    expect(targetOf(SELECTIONS.v01)).toEqual({
      projectId: 'flowgate',
      flowId: 'flowgate.v02.0048',
      nodeId: 'doc:flowgate.v02.0048.0011-TR',
      nodeKind: 'task',
      docId: 'flowgate.v02.0048.0011-TR',
      docType: 'TR',
      cardKind: 'task',
      title: 'T#1 결과 레포트',
      canvasState: 'in_progress',
      docReviewStatus: 'pending_review',
      docStatus: 'open',
      supersedes: ['flowgate.v02.0048.0009-TR'],
      slot: {
        itemId: 4821, itemSeq: 11, order: 4, type: 'TR', label: 'T#1 결과',
        derivedStatus: 'in_progress', protected: false, sourceWpCardId: 'c_t1',
      },
      nodeRun: null,
      cardDocuments: [],
      nodeType: null,
      rootBound: false,
      ownership: 'v01',
      viewMode: null,
      readOnly: null,
    })
  })

  it('Graph document Node: node run, definition and the document doc type', () => {
    const target = targetOf(SELECTIONS.graph)
    expect(target).toMatchObject({
      flowId: 'flowgate.v02.0049',
      docId: 'flowgate.v02.0049.0007-TS',
      docType: 'TS',
      cardKind: 'test',
      docReviewStatus: 'approved',
      nodeType: 'document',
      ownership: 'graph',
      viewMode: 'draft',
      readOnly: null,
      slot: null,
    })
    expect(target.nodeRun).toEqual({
      status: 'completed', openedAt: '2026-10-09T01:00:00Z', finishedAt: '2026-10-09T01:20:00Z', resultDocId: 'flowgate.v02.0049.0007-TS',
    })
  })

  it('a Node without a document has docId null and falls back to the definition / Node doc type', () => {
    const gate = targetOf(SELECTIONS.graphGate)
    expect(gate).toMatchObject({ docId: null, docType: null, cardKind: 'other', nodeType: 'gate', docReviewStatus: null, supersedes: [] })
    const slot = targetOf(SELECTIONS.v01NoDocument)
    expect(slot).toMatchObject({ docId: null, docType: 'D', cardKind: 'design', slot: { itemId: 4820 } })
    // A definition doc type wins over the Node's own when no document is bound.
    const node = { ...V01_GRAPH.nodes[1], docType: 'R', definition: { nodeType: 'document', docType: 'WP', paletteItemId: null, label: 'WP', assignee: null } }
    expect(targetOf({ projectId: 'p', graph: V01_GRAPH, node }).cardKind).toBe('plan')
  })

  it('a bound document type wins over the Node doc type (e.g. after a kind change)', () => {
    const node = { ...SELECTIONS.unassigned.node, docType: 'R', document: { ...SELECTIONS.unassigned.node.document!, typeCode: 'B' } }
    expect(targetOf({ ...SELECTIONS.unassigned, node })).toMatchObject({ docType: 'B', cardKind: 'requirement' })
  })

  it('a conflict Flow is read only whatever the view', () => {
    expect(targetOf(SELECTIONS.conflict)).toMatchObject({ ownership: 'conflict', viewMode: 'revision', readOnly: 'conflict' })
  })

  it('missing optional Graph fields read as null / empty (fixture source shape)', () => {
    const target = targetOf({ projectId: 'p', graph: { flowId: 'f', nodes: [], edges: [] }, node: { id: 'n', kind: 'task', title: 'T', x: 0, y: 0 } })
    expect(target).toMatchObject({
      docId: null, docType: null, cardKind: 'other', canvasState: null, slot: null, nodeRun: null,
      nodeType: null, rootBound: false, ownership: null, viewMode: null, readOnly: null, supersedes: [], cardDocuments: [],
    })
  })

  it('flowgate.v02.0053 T#5: a Graph / draft node hands its card lines over as they are', () => {
    const lines = [
      { slot: 'instruction', docType: 'T', author: 'human', docId: 'f.0002-T', title: '구현 · 지시서', status: 'draft', docReviewStatus: null },
      { slot: 'report', docType: 'TR', author: 'run', docId: 'f.0003-TR', title: '구현 · 레포트', status: 'draft', docReviewStatus: null },
    ]
    const node = { id: 'gn_t', kind: 'task', title: '구현', x: 0, y: 0, cardDocuments: lines }
    expect(targetOf({ projectId: 'p', graph: { flowId: 'f', nodes: [node], edges: [] }, node }).cardDocuments).toEqual(lines)
  })
})
