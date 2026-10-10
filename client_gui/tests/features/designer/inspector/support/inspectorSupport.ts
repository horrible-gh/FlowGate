/**
 * flowgate.v02.0048 T#1 — shared Inspector test support (D#1 §12.3). Owned by T#1 (T#3 adds the
 * event hub mock); feature sets use it and keep their own helpers in their own folders.
 *
 * - Selection fixtures per ownership (v01 / graph / unassigned / conflict), shaped as the server
 *   Canvas read gives them after toFlowGraph (bound document, v0.1 slot, Graph node run).
 * - i18n preparation: the app strings plus the registered feature strings.
 * - Fixture registries built through the same pure collector as the real one.
 */
import { defineComponent, h, type Component } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import {
  createInspectorRegistry,
  installInspectorMessages,
  type InspectorRegistration,
  type InspectorRegistry,
} from '@/features/designer/inspector/inspectorRegistry'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'

export const PROJECT_ID = 'flowgate'

/** App strings + every registered feature's strings on the shared i18n instance. */
export function prepareInspectorI18n(registry?: InspectorRegistry): typeof i18n {
  registerAppMessages(i18n)
  installInspectorMessages(i18n.global as unknown as Parameters<typeof installInspectorMessages>[0], registry)
  return i18n
}

// --- Selections ------------------------------------------------------------------------------

/** v0.1 Flow: a reviewed TR document Node with its sequence slot. */
export const V01_TASK_NODE: FlowGraphNode = {
  id: 'doc:flowgate.v02.0048.0011-TR',
  kind: 'task',
  title: 'T#1 결과 레포트',
  state: 'in_progress',
  docType: 'TR',
  x: 0,
  y: 0,
  document: {
    docId: 'flowgate.v02.0048.0011-TR',
    typeCode: 'TR',
    seq: 11,
    title: 'T#1 결과 레포트',
    docReviewStatus: 'pending_review',
    status: 'open',
    supersedes: ['flowgate.v02.0048.0009-TR'],
  },
  slot: {
    itemId: 4821,
    itemSeq: 11,
    order: 4,
    type: 'TR',
    label: 'T#1 결과',
    derivedStatus: 'in_progress',
    protected: false,
    sourceWpCardId: 'c_t1',
  },
  nodeRun: null,
}

/** v0.1 Flow: a sequence slot with no document yet. */
export const V01_EMPTY_SLOT_NODE: FlowGraphNode = {
  id: 'slot_q7m2x1',
  kind: 'design',
  title: '정렬 기본설계',
  state: 'draft',
  docType: 'D',
  x: 260,
  y: 0,
  document: null,
  slot: { itemId: 4820, itemSeq: 5, order: 3, type: 'D', label: '정렬 기본설계', derivedStatus: 'pending', protected: false, sourceWpCardId: null },
}

export const V01_GRAPH: FlowGraph = {
  flowId: 'flowgate.v02.0048',
  nodes: [V01_TASK_NODE, V01_EMPTY_SLOT_NODE],
  edges: [],
  ownership: 'v01',
  draft: null,
  viewMode: null,
}

/** Graph Flow: a document node whose run produced an approved TS result. */
export const GRAPH_TEST_NODE: FlowGraphNode = {
  id: 'gn_ts1',
  kind: 'test',
  title: '테스트 지시',
  state: 'done',
  docType: 'TS',
  x: 0,
  y: 0,
  definition: { nodeType: 'document', docType: 'TS', paletteItemId: 'doc_ts', label: '테스트 지시', assignee: null, rootBound: false },
  document: {
    docId: 'flowgate.v02.0049.0007-TS',
    typeCode: 'TS',
    seq: 7,
    title: '테스트 지시',
    docReviewStatus: 'approved',
    status: 'closed',
    supersedes: [],
  },
  slot: null,
  nodeRun: { status: 'completed', openedAt: '2026-10-09T01:00:00Z', finishedAt: '2026-10-09T01:20:00Z', resultDocId: 'flowgate.v02.0049.0007-TS' },
}

/** Graph Flow: a gate node — no document. */
export const GRAPH_GATE_NODE: FlowGraphNode = {
  id: 'gn_gate',
  kind: 'approval',
  title: '확인 게이트',
  state: 'draft',
  docType: null,
  x: 260,
  y: 0,
  definition: { nodeType: 'gate', docType: null, paletteItemId: 'gate', label: '확인 게이트', assignee: null, rootBound: false },
  document: null,
  slot: null,
  nodeRun: null,
}

export const GRAPH_GRAPH: FlowGraph = {
  flowId: 'flowgate.v02.0049',
  nodes: [GRAPH_TEST_NODE, GRAPH_GATE_NODE],
  edges: [{ id: 'e1', source: GRAPH_TEST_NODE.id, target: GRAPH_GATE_NODE.id, edgeClass: 'execution' }],
  ownership: 'graph',
  viewMode: 'draft',
  draft: { version: 3, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 2 },
}

/** Unassigned Flow: the root requirement only. */
export const UNASSIGNED_REQ_NODE: FlowGraphNode = {
  id: 'doc:flowgate.v02.0050.0001-R',
  kind: 'requirement',
  title: '새 요구사항',
  state: 'in_progress',
  docType: 'R',
  x: 0,
  y: 0,
  document: {
    docId: 'flowgate.v02.0050.0001-R',
    typeCode: 'R',
    seq: 1,
    title: '새 요구사항',
    docReviewStatus: null,
    status: 'draft',
    supersedes: [],
  },
}

export const UNASSIGNED_GRAPH: FlowGraph = {
  flowId: 'flowgate.v02.0050',
  nodes: [UNASSIGNED_REQ_NODE],
  edges: [],
  ownership: 'unassigned',
  viewMode: 'draft',
}

/** Conflict Flow: a rejected design document. */
export const CONFLICT_DESIGN_NODE: FlowGraphNode = {
  id: 'doc:flowgate.v02.0051.0003-D',
  kind: 'design',
  title: '충돌 설계',
  state: 'rejected',
  docType: 'D',
  x: 0,
  y: 0,
  document: {
    docId: 'flowgate.v02.0051.0003-D',
    typeCode: 'D',
    seq: 3,
    title: '충돌 설계',
    docReviewStatus: 'rejected',
    status: 'open',
    supersedes: [],
  },
}

export const CONFLICT_GRAPH: FlowGraph = {
  flowId: 'flowgate.v02.0051',
  nodes: [CONFLICT_DESIGN_NODE],
  edges: [],
  ownership: 'conflict',
  viewMode: 'revision',
}

export function selectionOf(graph: FlowGraph, nodeId: string, projectId = PROJECT_ID): FlowGraphSelection {
  const node = graph.nodes.find((candidate) => candidate.id === nodeId)
  if (!node) throw new Error(`no node ${nodeId} in ${graph.flowId}`)
  return { projectId, graph, node }
}

export const SELECTIONS = {
  v01: selectionOf(V01_GRAPH, V01_TASK_NODE.id),
  v01NoDocument: selectionOf(V01_GRAPH, V01_EMPTY_SLOT_NODE.id),
  graph: selectionOf(GRAPH_GRAPH, GRAPH_TEST_NODE.id),
  graphGate: selectionOf(GRAPH_GRAPH, GRAPH_GATE_NODE.id),
  unassigned: selectionOf(UNASSIGNED_GRAPH, UNASSIGNED_REQ_NODE.id),
  conflict: selectionOf(CONFLICT_GRAPH, CONFLICT_DESIGN_NODE.id),
} as const

export function targetOf(selection: FlowGraphSelection): InspectorTarget {
  const target = toInspectorTarget(selection)
  if (!target) throw new Error('no target')
  return target
}

// --- Fixture registries ------------------------------------------------------------------------

export const EMPTY_MESSAGES = { ko: {}, en: {}, ja: {} } as const

/** A trivial contribution part: shows its key and the target's doc id. */
export function probePart(name: string): Component {
  return defineComponent({
    name: `Probe_${name}`,
    props: { target: { type: Object, required: true } },
    setup: (props) => () => h('div', { 'data-test': `probe-${name}`, 'data-doc': (props.target as InspectorTarget).docId ?? '' }, name),
  })
}

/** A registration with the given fields (key / order required; requiresDocument defaults to true). */
export function registration(fields: Partial<InspectorRegistration> & Pick<InspectorRegistration, 'key' | 'order'>): InspectorRegistration {
  return { requiresDocument: true, messages: EMPTY_MESSAGES, ...fields }
}

/** Glob-shaped modules (`./features/<key>/register.ts` -> { default }) for the pure collector. */
export function registrationModules(entries: readonly InspectorRegistration[], folders?: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(entries.map((entry, i) => [`./features/${folders?.[i] ?? entry.key}/register.ts`, { default: entry }]))
}

export function fixtureRegistry(entries: readonly InspectorRegistration[]): InspectorRegistry {
  return createInspectorRegistry(registrationModules(entries))
}
