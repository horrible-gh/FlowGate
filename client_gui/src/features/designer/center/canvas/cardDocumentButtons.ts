import type { FlowGraphNode } from '../graph/flowGraph'

export interface CardDocumentButton {
  docId: string
  docType: string | null
  title: string
  ownerSlot: string | null
  report: boolean
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
    })
  }
  if (!docs.size && !node.cardDocuments?.length && node.document?.docId) {
    const doc = node.document
    docs.set(doc.docId, { docId: doc.docId, docType: doc.typeCode, title: doc.title ?? doc.docId,
      ownerSlot: null, report: ['NR', 'TR', 'TR2', 'TSR'].includes(doc.typeCode ?? '') })
  }
  return [...docs.values()]
}
