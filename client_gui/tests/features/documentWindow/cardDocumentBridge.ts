/**
 * flowgate.v02.0127 T#5 — a test bridge from the document window's body mocks to the card document v2 calls.
 * The window specs keep their `contentRead` (stored body per document) and `contentSave` (what a save writes)
 * mocks; the v2 read / revise functions are answered from them, with one revision counter per document, so a
 * spec states the stored file once and the card transport stays the real request / answer shape.
 */
import type { CardDocumentContext } from '@/features/document'

/** The card coordinates an opener hands for a document (flow / node / owner slot are fixed test values). */
export const CARD = (docId: string): CardDocumentContext => ({
  projectId: 'flowgate', flowId: 'flowgate.v02.0053', nodeKey: 'task', ownerSlot: 'instruction', openedDocId: docId,
})

type Fn = (...args: any[]) => any

export function cardDocumentBridge(contentRead: Fn, contentSave: Fn) {
  const revisions = new Map<string, number>()
  /** Every request the window sent, in order (key / base / text), to assert what is resent as it is. */
  const calls: { expectedDocId: string; expectedRevision: number; idempotencyKey: string; content?: string }[] = []
  const revisionOf = (docId: string) => revisions.get(docId) ?? 0
  return {
    revisions,
    calls,
    /** `cardDocumentRead`: the stored body as a v2 snapshot (an unreadable / missing file is not a readable body). */
    async read(ctx: CardDocumentContext) {
      const docId = ctx.openedDocId
      const read = await contentRead(docId)
      const base = { docId, revisionNo: revisionOf(docId), updatedAt: 't', line: {} }
      if (read && (read.missing === true || read.state === 'unreadable')) {
        return { ...base, content: null, bodyState: read.reason ?? 'file_path_missing' }
      }
      return { ...base, content: typeof read?.content === 'string' ? read.content : '', bodyState: 'readable' }
    },
    /** `cardDocumentRevise`: records the save through `contentSave`, answers the P#1 success body for N+1. */
    async revise(ctx: CardDocumentContext, request: { expectedDocId: string; expectedRevision: number; idempotencyKey: string; content?: string }) {
      calls.push({ ...request })
      await contentSave(request.expectedDocId, request.content)
      revisions.set(request.expectedDocId, request.expectedRevision + 1)
      return {
        ok: true, server_ts: 't', doc_id: request.expectedDocId, revision_no: request.expectedRevision + 1,
        updated_at: 'u', replayed: false, line: { slot: ctx.ownerSlot },
      }
    },
  }
}
