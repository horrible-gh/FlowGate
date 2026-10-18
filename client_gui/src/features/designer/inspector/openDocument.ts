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
  settleWrittenCardLines,
  writtenCardLines,
  type DocumentWindowMode,
} from '@/features/documentWindow'
import type { InspectorHost } from './inspectorHost'
import { inspectorCardNodeKey, type InspectorCardDocument, type InspectorTarget } from './inspectorTarget'
import { slotLineCard, type SlotLineCard } from './parts/slotStateView'

export type OpenDocumentMode = DocumentWindowMode

/**
 * Opens the document in its document window: `view` (default) or straight to the editor (`edit`). While an
 * upload guards the document an edit request opens (or brings back) the window in the view.
 */
export function openDocument(docId: string, mode: OpenDocumentMode = 'view'): void {
  if (docId) openDocumentWindow(docId, mode === 'edit' && documentIoGuarded(docId) ? 'view' : mode)
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
