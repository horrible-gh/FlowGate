// flowgate.v02.0053 T#3 (rework 2) — which document an open window shows. A run result does not write
// into the card's empty report: the server binds the card line to the result document and marks the
// report `superseded_by` it (flowgate/card_documents/lines.py adopt_result), and the card shows the end
// of that chain (lines_by_node → graph_workflow supersede.live_end). The window reads the same chain with
// the document read it already uses, one document per hop, so a report opened before its run shows the
// result (and its next revisions) once the server links them.
// The chain stops — the last document read stays the shown one — at a link that can not be read, a
// cancelled document, a document of another Flow, a cycle or DOCUMENT_CHAIN_MAX_HOPS, where live_end
// gives no live document either.
import type { DocumentRecord } from '@/features/document'

/** Most supersede links followed from the opened document. */
export const DOCUMENT_CHAIN_MAX_HOPS = 8

const CANCELLED_STATUS = 'cancelled'

/**
 * The opened document and the documents its supersede chain reaches, in order; the last one is what the
 * window shows. The opened document's read failure is thrown as it is; a later link's ends the chain.
 */
export async function readDocumentChain(
  docId: string,
  read: (docId: string) => Promise<DocumentRecord>,
): Promise<DocumentRecord[]> {
  const first = await read(docId)
  const chain = [first]
  const seen = new Set([first.docId])
  let current = first
  while (current.supersededBy && chain.length <= DOCUMENT_CHAIN_MAX_HOPS && !seen.has(current.supersededBy)) {
    let next: DocumentRecord
    try {
      next = await read(current.supersededBy)
    } catch {
      break
    }
    if (next.status === CANCELLED_STATUS || next.groupId !== first.groupId) break
    chain.push(next)
    seen.add(next.docId)
    current = next
  }
  return chain
}
