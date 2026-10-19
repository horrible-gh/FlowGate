// flowgate.v02.0048 T#4 — ↗ 열기 (D#1 §9.2). Every Inspector feature opens a document through this
// one function, so where a document is shown is decided here only.
// flowgate.v02.0053 T#3 — the document opens in this tab, in its own modeless document window (MD
// Viewer, ✎ editing in the same window; features/documentWindow). The v0.1 new-tab path is gone: no
// second tab, no second sign-in, no v0.1 screen. One window per document — opening it again brings
// the open window back.
// - `view` (↗): the document as it reads.
// - `edit` (✎): straight to the window's text editor.
// flowgate.v02.0056 F1 — a document's 검수·반려 이력 opens the same way, in its own modeless window
// (openDocumentHistory, from the history button of a document line).
// flowgate.v02.0056 F2-2 — the row actions of a card line open their windows here too (openCardLineAction):
// [작성] of a line without a document opens its write window (O3 0009-TR §4-2), [AI 호출] of a line with one the
// document's AI call window (§4-3). The lines written there stand for the card's lines until the Canvas read
// shows them (cardLinesWritten / settleCardLinesWritten, used by the Frame), and a line tells an AI run on its
// document (cardLineRow).
// flowgate.v02.0068 T#1 — a document's [업로드][다운로드] go through the one coordinator of the document window
// feature too: the lines draw its group (DocumentIoGroup, handed to the slot list's `io` slot), an edit request
// for a document an upload guards opens its window in the view (0068 L §2.13), and the Frame tells when the
// Designer is left or the project changes (leaveDocumentIo / forgetDocumentIo).
// flowgate.v02.0072 T#3 — a work plan card's 「작업계획」 line opens the plan in the v0.2 work plan edit window
// (features/workPlanWindow, T#1, reached through the document window surface; its Step ✎ opens the T#2 Step window),
// not in the document window: open and ✎ alike, one window per plan, opening it again brings it back. The window
// reads its Flow's verdict: read only as the Inspector last showed the Flow (the ownership conflict / revision view —
// the Frame notes it on every target it shows, noteWorkPlanOpener), and the Flow's run lock as the Designer knows it
// now (TR0012 rev1: the Frame's reader, provideWorkPlanFlowRunLock), whatever is selected — no card, another card or
// another Flow. A run status not confirmed, or no Designer to tell it, locks the window too. A save of the plan asks
// the Designer to read that Flow's Canvas again.
import { reactive, shallowRef } from 'vue'
import {
  DocumentIoGroup,
  documentIoForget,
  documentIoGuarded,
  documentIoLeave,
  documentAiRunning,
  openDocumentAiWindow,
  openDocumentHistoryWindow,
  openDocumentWindow,
  openSlotWriteWindow,
  openWorkPlanEditWindow,
  settleWrittenCardLines,
  writtenCardLines,
  type DocumentWindowMode,
} from '@/features/documentWindow'
import QnaTab from './features/qna/QnaTab.vue'
import type { InspectorHost } from './inspectorHost'
import { inspectorCardNodeKey, type InspectorCardDocument, type InspectorTarget } from './inspectorTarget'
import { slotLineCard, type SlotLineCard } from './parts/slotStateView'

export type OpenDocumentMode = DocumentWindowMode

/** flowgate.v02.0070 T#4 (B4) — how a document opens; without options it opens as before (following its chain). */
export interface OpenDocumentOptions {
  /**
   * That very id as history (the 「전환 전 문서」 link): the window does not follow the document's supersede chain and is
   * read only (no edit / re-enter / recover).
   */
  readonly exact?: boolean
  /**
   * With `exact`: the target the link was pressed on (its project and Flow). The window then shows that document's
   * questions under its body — the Q&A tab's list and ↗ windows read as history (no asking, answering or AI request).
   */
  readonly target?: InspectorTarget | null
}

/**
 * Opens the document in its document window: `view` (default) or straight to the editor (`edit`). While an
 * upload guards the document an edit request opens (or brings back) the window in the view.
 */
export function openDocument(docId: string, mode: OpenDocumentMode = 'view', options?: OpenDocumentOptions): void {
  if (!docId) return
  if (!options?.exact) {
    openDocumentWindow(docId, mode === 'edit' && documentIoGuarded(docId) ? 'view' : mode)
    return
  }
  const target = options.target ?? null
  // The questions of the document itself (only its project / Flow / id are read from the target).
  const aside = target ? { component: QnaTab, props: { target: { ...target, docId }, history: true } } : null
  openDocumentWindow(docId, 'view', { exact: true, aside })
}

/** flowgate.v02.0072 T#3 — what an opener tells the work plan edit window: read only, the Flow's run lock, or nothing. */
export type WorkPlanOpenerLock = 'read_only' | 'run_locked' | null

/** The opener's verdict of a target: read only (conflict / revision view) wins over the run lock (also `unknown`). */
export function workPlanOpenerLock(target: Pick<InspectorTarget, 'readOnly' | 'editLock'>): WorkPlanOpenerLock {
  if (target.readOnly !== null) return 'read_only'
  return target.editLock ? 'run_locked' : null
}

/**
 * flowgate.v02.0072 TR0012 rev1 — a Flow's run lock as the Designer knows it now (the Center session's run marks): `run`
 * (running / paused / stopping), `unknown` (not confirmed: being read, failed, or the Flow is not open) or null.
 */
export type WorkPlanFlowRunLock = (flowId: string) => 'run' | 'unknown' | null

/** Whether the Inspector last showed each Flow read only (reactive: an open window follows it). */
const workPlanReadOnlyFlows = reactive(new Map<string, boolean>())

/** The Frame's reader of the Flows' run locks while it is mounted (reactive: an open window follows it). */
const workPlanRunLockReader = shallowRef<WorkPlanFlowRunLock | null>(null)

/** flowgate.v02.0072 T#3 — the Frame notes the read-only verdict of every target it shows (by its Flow). */
export function noteWorkPlanOpener(target: InspectorTarget | null): void {
  if (!target) return
  const readOnly = target.readOnly !== null
  if (workPlanReadOnlyFlows.get(target.flowId) !== readOnly) workPlanReadOnlyFlows.set(target.flowId, readOnly)
}

/**
 * flowgate.v02.0072 TR0012 rev1 — the Frame tells the work plan windows every Flow's run lock, not only the selected
 * one's. Returns the undo (the Frame's end: the run status is then not known, and the windows stay locked).
 */
export function provideWorkPlanFlowRunLock(reader: WorkPlanFlowRunLock): () => void {
  workPlanRunLockReader.value = reader
  return () => {
    if (workPlanRunLockReader.value === reader) workPlanRunLockReader.value = null
  }
}

/** What an open work plan window of the Flow reads now: read only, else the Flow's run lock (`unknown` locks too). */
export function workPlanFlowLock(flowId: string): WorkPlanOpenerLock {
  if (workPlanReadOnlyFlows.get(flowId) === true) return 'read_only'
  const reader = workPlanRunLockReader.value
  return reader === null || reader(flowId) !== null ? 'run_locked' : null
}

/** Forgets the noted verdicts (another project, tests). */
export function forgetWorkPlanOpeners(): void {
  workPlanReadOnlyFlows.clear()
}

/**
 * flowgate.v02.0072 T#3 — opens (or brings back) the work plan edit window of the target's plan document. A save
 * reads the Flow's Canvas again (the host's refresh-flow); the card's summary also reads the plan again on the
 * server's refresh events.
 */
export function openWorkPlan(target: InspectorTarget, docId: string, host: InspectorHost | null): void {
  if (!docId) return
  noteWorkPlanOpener(target)
  const flowId = target.flowId
  const line = target.cardDocuments.find((item) => item.docId === docId)
  openWorkPlanEditWindow({
    docId,
    projectId: target.projectId,
    groupId: flowId,
    title: line?.title || null,
    external: () => workPlanFlowLock(flowId),
    saved: () => host?.request({ kind: 'refresh-flow', flowId }),
  })
}

/** flowgate.v02.0068 T#1 — the [업로드][다운로드] group of one document (a document line's `io` slot). */
export { DocumentIoGroup }

/** flowgate.v02.0068 T#1 — the Designer is left: running uploads / downloads end quietly (L §2.11). */
export function leaveDocumentIo(): void {
  documentIoLeave()
}

/** flowgate.v02.0068 T#1 — another project: what the buttons knew about documents is read again (L §2.15). */
export function forgetDocumentIo(): void {
  documentIoForget()
}

/** Opens the document's review history (AI reviews · rejections · AI responses) in its own window. */
export function openDocumentHistory(docId: string): void {
  if (docId) openDocumentHistoryWindow(docId)
}

/** The line of a card a row action was pressed on (what the slot list hands back). */
export interface CardLineActionLine {
  readonly name: string
  readonly id?: string | null
  /** The card line (its slot) behind the row. */
  readonly cardSlot?: string | null
}

/**
 * flowgate.v02.0056 F2-2 — what the card's own line (`cardSlot`) adds to a row of its documents tab (slotLineCard):
 * 문서 없음 with [작성], 비어 있음, the history number and [AI 호출] — drawn running while an AI run works on the
 * document. Only a stored card that is not read only offers the actions.
 */
export function cardLineRow(target: InspectorTarget, row: { readonly id?: string | null; readonly empty?: boolean }, cardSlot: string): SlotLineCard {
  return slotLineCard(row, target.cardDocuments.find((line) => line.slot === cardSlot), {
    changeable: inspectorCardNodeKey(target) !== null && target.readOnly === null,
    running: documentAiRunning,
  })
}

/**
 * flowgate.v02.0056 F2-2 — a row action of a card line. `write`: the line's [작성] window (nothing is made until
 * it is saved); `ai_invoke`: the document's AI call window (nothing is called until [실행]). The Flow's Canvas is
 * read again (the host's refresh-flow) after a write, after a refusal that means the line has a document now, and
 * when the AI run started there ends.
 */
export function openCardLineAction(target: InspectorTarget, line: CardLineActionLine, action: string, host: InspectorHost | null): void {
  const nodeKey = inspectorCardNodeKey(target)
  if (nodeKey === null) return
  const flowId = target.flowId
  const refresh = () => host?.request({ kind: 'refresh-flow', flowId })
  if (action === 'write') {
    const slot = line.cardSlot ?? null
    if (!slot) return
    const card = target.cardDocuments.find((item) => item.slot === slot)
    openSlotWriteWindow({
      flowId: target.flowId,
      nodeKey,
      slot,
      docType: card?.docType ?? null,
      name: line.name,
      cardTitle: target.title,
      nodeRunning: target.nodeRun?.status === 'open',
      written: refresh,
      reread: refresh,
    })
    return
  }
  if (action === 'ai_invoke' && line.id) {
    const docId = line.id
    openDocumentAiWindow({
      docId,
      groupId: target.flowId,
      projectId: target.projectId,
      name: line.name,
      title: target.cardDocuments.find((item) => item.docId === docId)?.title ?? '',
      finished: refresh,
    })
  }
}

/** flowgate.v02.0056 F2-2 — the lines of the target's card written from here and not yet shown by the Canvas read. */
export function cardLinesWritten(target: InspectorTarget | null): readonly InspectorCardDocument[] {
  const nodeKey = inspectorCardNodeKey(target)
  return target && nodeKey ? writtenCardLines(target.flowId, nodeKey) : []
}

/**
 * flowgate.v02.0056 F2-2 — the Canvas read of the target's card as the Inspector shows it: written lines it shows
 * now are dropped, and a line waiting to be opened (a refused [작성]) opens its document.
 */
export function settleCardLinesWritten(target: InspectorTarget | null): void {
  const nodeKey = inspectorCardNodeKey(target)
  if (target && nodeKey) settleWrittenCardLines(target.flowId, nodeKey, target.cardDocuments, (docId) => openDocumentWindow(docId))
}
