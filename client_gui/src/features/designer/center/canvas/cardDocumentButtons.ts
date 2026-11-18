import type { FlowGraphNode } from '../graph/flowGraph'

export interface CardDocumentButton {
  docId: string
  docType: string | null
  title: string
  ownerSlot: string | null
  report: boolean
  /** flowgate.v02test.0044: the server says the document has no body yet (`has_body` false; never M / WP). The button
   * looks disabled but still opens the document, so a person can start writing it from the card. */
  empty?: boolean
}
/** Line types that always have a body (O3 §4-1: M records, WP plans), as the Inspector's slotStateView decides; the
 * Center imports no Inspector part, so it keeps its own copy. */
const ALWAYS_FILLED_TYPES = ['M', 'WP']
function emptyLine(line: { docType: string | null; hasBody?: boolean }): boolean {
  return line.hasBody === false && !ALWAYS_FILLED_TYPES.includes(line.docType?.trim().toUpperCase() ?? '')
}
export interface CardDocumentOpen { nodeId: string; docId: string }

/** Live graph metadata only: shared TS lines yield one button, blank slots yield none. */
export function cardDocumentButtons(node: Pick<FlowGraphNode, 'cardDocuments' | 'document'>): CardDocumentButton[] {
  const docs = new Map<string, CardDocumentButton>()
  for (const line of node.cardDocuments ?? []) {
    if (!line.docId) continue
    const previous = docs.get(line.docId)
    if (previous && line.slot !== 'instruction') continue
    docs.set(line.docId, {
      docId: line.docId, docType: line.docType, title: line.title ?? line.docId,
      ownerSlot: line.slot, report: line.slot === 'report' || ['NR', 'TR', 'TR2', 'TSR'].includes(line.docType ?? ''),
      empty: emptyLine(line),
    })
  }
  if (!docs.size && !node.cardDocuments?.length && node.document?.docId) {
    const doc = node.document
    docs.set(doc.docId, { docId: doc.docId, docType: doc.typeCode, title: doc.title ?? doc.docId,
      ownerSlot: null, report: ['NR', 'TR', 'TR2', 'TSR'].includes(doc.typeCode ?? ''), empty: false })
  }
  return [...docs.values()]
}
