// flowgate.v02.0048 T#1 — what the bottom Inspector works on (D#1 §4.3). One pure translation of
// the Center session's selection context (FlowGraphSelection) into an InspectorTarget: the
// document bound to the selected Node, its card kind, its states and whether the Flow is read
// only. Every Inspector part reads this one shape, so the rules here (card kind, read only) are
// decided once and never again per feature.
// flowgate.v02.0070 T#1 (B1): the card's own title state and whether it may be edited now (the session's titleEdit) —
// the Inspector header edits the title; the title text rules it checks before sending are the Canvas's (nodeTitle.ts).
import { cardApprovalFailureView, cardStateView, type CardApprovalFailureView, type CardStateView } from '../center/graph/cardStateView'
import type {
  FlowGraphCardDocument,
  FlowGraphNodeRun,
  FlowGraphNodeSlot,
  FlowGraphSelection,
  FlowGraphTitleState,
} from '../center/graph/flowGraph'

export type { CardApprovalFailureView, CardStateView }
export { NODE_TITLE_MAX_CHARS, checkNodeTitle, type NodeTitleSaveOutcome } from '../center/graph/nodeTitle'

/** Inspector card kinds (D#1 §4.3) — the one value set registrations may name. */
export const INSPECTOR_CARD_KINDS = ['requirement', 'research', 'task', 'design', 'test', 'check', 'plan', 'other'] as const

export type InspectorCardKind = (typeof INSPECTOR_CARD_KINDS)[number]

/** Document types of each card kind; `other` takes every remaining (open) type. */
export const INSPECTOR_CARD_DOC_TYPES: Readonly<Record<Exclude<InspectorCardKind, 'other'>, readonly string[]>> = {
  requirement: ['R', 'B'],
  research: ['N', 'NR'],
  task: ['T', 'TR', 'T2', 'TR2'],
  design: ['DS', 'D', 'P', 'L', 'DB'],
  test: ['TS', 'TSR', 'V'],
  check: ['AC'],
  plan: ['WP'],
}

/** The document types a registration's `docTypes` may name (every type outside `other`). */
export const INSPECTOR_DOC_TYPES: readonly string[] = Object.values(INSPECTOR_CARD_DOC_TYPES).flat()

/** Flow ownership keys a registration's `ownerships` may name. */
export const INSPECTOR_OWNERSHIPS = ['unassigned', 'v01', 'graph', 'conflict'] as const

/** One document line of a card (FlowGraphCardDocument). */
export type InspectorCardDocument = FlowGraphCardDocument

/** Why the Inspector may not change anything: ownership conflict, or a Graph revision view. */
export type InspectorReadOnly = 'conflict' | 'revision' | null

export interface InspectorTarget {
  readonly projectId: string
  /** Flow id = v0.1 group id. */
  readonly flowId: string
  /** Canvas Node identity (node_key). */
  readonly nodeId: string
  /** Canvas Node kind key, for the header look only (flowNodeKinds.ts). */
  readonly nodeKind: string
  /** The live document bound to the Node, or null. */
  readonly docId: string | null
  /** Document type: the bound document's, else the Graph definition's, else the Node's. */
  readonly docType: string | null
  readonly cardKind: InspectorCardKind
  readonly title: string
  /** Canvas state key (5 kinds, flowNodeKinds.ts) — the state chip. */
  readonly canvasState: string | null
  readonly docReviewStatus: string | null
  readonly docStatus: string | null
  readonly supersedes: readonly string[]
  readonly slot: FlowGraphNodeSlot | null
  readonly nodeRun: FlowGraphNodeRun | null
  /**
   * flowgate.v02.0053 T#5: the card's document lines as the server keeps them (T#1 — made when the card
   * is placed), in its order; empty on a Node without them (a document / sequence Node).
   */
  readonly cardDocuments: readonly InspectorCardDocument[]
  /** Graph definition node type (`document` | `gate` | `custom`), or null. */
  readonly nodeType: string | null
  /**
   * flowgate.v02.0053 T#7: the Graph card's own document type (its definition), whatever document its run bound
   * (a 조사 card stays N with its NR result); null on a Node without a definition.
   */
  readonly cardDocType: string | null
  readonly rootBound: boolean
  readonly ownership: string | null
  readonly viewMode: string | null
  readonly readOnly: InspectorReadOnly
  /**
   * flowgate.v02.0061 T#14: `run` while the Flow's Graph run is running, paused or stopping — its structure and card
   * settings cannot change (the Center session's run lock); TR0031 rev1: `unknown` while the run's status is not
   * confirmed (being read, or the read failed). Absent / null = not locked.
   */
  readonly editLock?: 'run' | 'unknown' | null
  /** flowgate.v02.0070 T#1: the card's own title state as shown (its version), or null / absent (none). */
  readonly titleState?: FlowGraphTitleState | null
  /** flowgate.v02.0070 T#1: the card's title may be edited now (the Canvas's rule, graph/nodeTitle.ts). Absent = no. */
  readonly canEditTitle?: boolean
  /** flowgate.v02.0070 T#1: why it may not — the card runs (an open edit then closes with the lock note). */
  readonly titleLock?: 'running' | null
}

const CARD_KIND_BY_DOC_TYPE: ReadonlyMap<string, InspectorCardKind> = new Map(
  Object.entries(INSPECTOR_CARD_DOC_TYPES).flatMap(([kind, types]) => types.map((type) => [type, kind as InspectorCardKind])),
)

/** Card kind of a document type; unknown or missing types are `other`. */
export function inspectorCardKind(docType: string | null | undefined): InspectorCardKind {
  if (!docType) return 'other'
  return CARD_KIND_BY_DOC_TYPE.get(docType.trim().toUpperCase()) ?? 'other'
}

/** Read-only reason: an ownership conflict wins over a Graph revision view (D#1 §4.3 / §8.3). */
export function inspectorReadOnly(ownership: string | null | undefined, viewMode: string | null | undefined): InspectorReadOnly {
  if (ownership === 'conflict') return 'conflict'
  if (viewMode === 'revision') return 'revision'
  return null
}

/**
 * flowgate.v02.0053 T#7 — what the settings tab holds for a Node, by the kinds the server keeps card settings
 * by (T#2 `fields.card_kind`): run (조사 · 작업 · 설계 · Custom), test, requirement, check.
 * flowgate.v02.0072 T#3 — and plan: a Graph work plan (WP) card keeps the plan settings (the AI 사전 지시서 작성
 * switch and the run block, values only).
 */
export const INSPECTOR_SETTINGS_KINDS = ['run', 'test', 'requirement', 'check', 'plan'] as const

export type InspectorSettingsKind = (typeof INSPECTOR_SETTINGS_KINDS)[number]

/** Document card types with run settings (T#2 `fields._RUN_DOC_TYPES`). */
const SETTINGS_RUN_TYPES: readonly string[] = ['N', 'T', 'T2', 'D', 'P', 'L', 'DB']

/**
 * The settings kind of a Node, or null (no settings tab). A Graph card (a definition node) has the kind the
 * server keeps its settings by: a Custom card runs, a gate is a check, a work plan card is a plan, a document card
 * goes by its own (definition) type — not by the result its run bound (N → NR, T → TR), so a done card keeps its
 * form. A v0.1 Flow's work plan Node has no store, so no settings tab.
 * A Node without a definition (a v0.1 Flow's document Node) keeps what 0048 / 0051 gave it: a run or test
 * card as a v0.1 sequence step (its slot), the requirement and check cards drawn without a store.
 */
export function inspectorSettingsKind(
  target: Pick<InspectorTarget, 'nodeType' | 'cardDocType' | 'docType' | 'cardKind' | 'slot'>,
): InspectorSettingsKind | null {
  if (target.nodeType === 'custom') return 'run'
  if (target.nodeType === 'gate') return 'check'
  if (target.nodeType !== null) {
    if (target.nodeType !== 'document') return null
    const type = (target.cardDocType ?? target.docType)?.trim().toUpperCase() ?? ''
    if (type === 'R' || type === 'B') return 'requirement'
    if (type === 'TS') return 'test'
    if (type === 'WP') return 'plan'
    return SETTINGS_RUN_TYPES.includes(type) ? 'run' : null
  }
  if (target.cardKind === 'requirement' || target.cardKind === 'check') return target.cardKind
  if (!target.slot) return null
  if (target.cardKind === 'test') return 'test'
  return target.cardKind === 'research' || target.cardKind === 'task' || target.cardKind === 'design' ? 'run' : null
}

/** The key a placed card keeps until its first draft save answers the stored one (the server knows it not). */
export const INSPECTOR_CLIENT_KEY_PREFIX = 'tmp_'

/** The node_key of the stored Graph card behind the target, or null: a Node without a definition or a `tmp_` card. */
export function inspectorCardNodeKey(target: Pick<InspectorTarget, 'nodeType' | 'nodeId'> | null): string | null {
  return target && target.nodeType !== null && !target.nodeId.startsWith(INSPECTOR_CLIENT_KEY_PREFIX) ? target.nodeId : null
}

/**
 * flowgate.v02.0056 F2-2 — the target with the card lines written from the Inspector ([작성], O3 0009-TR §4-2) that
 * the Canvas read does not show yet: a written line takes the place of its slot's line while that line has no
 * document (or the card has no line of the slot); a line the read shows with a document stays as read. The same
 * target when nothing changes.
 */
export function withWrittenCardLines(target: InspectorTarget | null, written: readonly InspectorCardDocument[]): InspectorTarget | null {
  if (!target || !written.length) return target
  let changed = false
  const lines = target.cardDocuments.map((line) => {
    const own = written.find((item) => item.slot === line.slot)
    if (!own || line.docId !== null) return line
    changed = true
    return own
  })
  const added = written.filter((item) => !target.cardDocuments.some((line) => line.slot === item.slot))
  if (!changed && !added.length) return target
  return { ...target, cardDocuments: [...lines, ...added] }
}

/** Selection context -> InspectorTarget; null when nothing is selected. */
export function toInspectorTarget(selection: FlowGraphSelection | null | undefined): InspectorTarget | null {
  if (!selection) return null
  const { graph, node } = selection
  const document = node.document ?? null
  const docType = document?.typeCode ?? node.definition?.docType ?? node.docType ?? null
  const ownership = graph.ownership ?? null
  const viewMode = graph.viewMode ?? null
  return {
    projectId: selection.projectId,
    flowId: graph.flowId,
    nodeId: node.id,
    nodeKind: node.kind,
    docId: document?.docId ?? null,
    docType,
    cardKind: inspectorCardKind(docType),
    title: node.title,
    canvasState: node.state ?? null,
    docReviewStatus: document?.docReviewStatus ?? null,
    docStatus: document?.status ?? null,
    supersedes: document?.supersedes ?? [],
    slot: node.slot ?? null,
    nodeRun: node.nodeRun ?? null,
    cardDocuments: node.cardDocuments ?? [],
    nodeType: node.definition?.nodeType ?? null,
    cardDocType: node.definition?.docType ?? null,
    rootBound: node.definition?.rootBound === true,
    ownership,
    viewMode,
    readOnly: inspectorReadOnly(ownership, viewMode),
    editLock: selection.editLock ?? null,
    titleState: node.titleState ?? null,
    canEditTitle: selection.titleEdit?.editable === true,
    titleLock: selection.titleEdit?.lock ?? null,
  }
}

/**
 * flowgate.v02.0056 F2-1 — the card state of a Graph card with a run (작업 중 · 승인 대기 · 승인됨 …), decided by the
 * one rule the Canvas card uses too (center/graph/cardStateView.ts); null on a gate (✓ 확인 · ✕ 반려 is its own),
 * a card not saved yet, a Node without a definition or without a run.
 */
export function inspectorCardState(target: Pick<InspectorTarget, 'nodeType' | 'nodeId' | 'nodeRun'> | null): CardStateView | null {
  if (!target || target.nodeType === 'gate' || inspectorCardNodeKey(target) === null) return null
  return cardStateView(target.nodeRun)
}

/** flowgate.v02.0056 F2-1 — how a refused [승인] is worded and handled (the Canvas' grouping, cardStateView.ts). */
export function inspectorApprovalFailure(code: string | null, status: number | null, network: boolean): CardApprovalFailureView {
  return cardApprovalFailureView(code, status, network)
}
