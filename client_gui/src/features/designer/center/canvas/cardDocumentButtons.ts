import type { FlowGraphNode } from '../graph/flowGraph'
import { instructionVisible } from '../graph/instructionVisibility'

export interface CardDocumentButton {
  docId: string
  docType: string | null
  title: string
  ownerSlot: string | null
  report: boolean
  /** The test card's 시나리오 button: it opens the same TS document as the instruction button (the scenario lives inside it). */
  scenario?: boolean
  /** the server says the document has no body yet (`has_body` false; never M / WP). The button
   * looks disabled but still opens the document, so a person can start writing it from the card. */
  empty?: boolean
}
/** Line types that always have a body (O3 §4-1: M records, WP plans), as the Inspector's slotStateView decides; the
 * Center imports no Inspector part, so it keeps its own copy. */
const ALWAYS_FILLED_TYPES = ['M', 'WP']
function emptyLine(line: { docType: string | null; hasBody?: boolean }): boolean {
  return line.hasBody === false && !ALWAYS_FILLED_TYPES.includes(line.docType?.trim().toUpperCase() ?? '')
}
/** `scenario` tells the 시나리오 button from the instruction button of the same shared TS document. */
export interface CardDocumentOpen { nodeId: string; docId: string; scenario?: boolean }
/** Stable key of a button: a shared TS document has two (instruction and 시나리오) with one doc id. */
export const cardDocumentButtonKey = (doc: Pick<CardDocumentButton, 'docId' | 'scenario'>): string => doc.scenario ? `${doc.docId}#scenario` : doc.docId

/** Live graph metadata only: a shared TS document yields its instruction button plus a 시나리오 button right after it (flowgate.v02test.0041),
 * blank slots yield none. */
export function cardDocumentButtons(node: Pick<FlowGraphNode, 'cardDocuments' | 'document'>): CardDocumentButton[] {
  const docs = new Map<string, CardDocumentButton>()
  const scenarios = new Map<string, CardDocumentButton>()
  for (const line of node.cardDocuments ?? []) {
    if (!line.docId) continue
    if (line.slot === 'instruction' && !instructionVisible(line.instructionOmitted === true, line)) continue
    if (line.slot === 'scenario') {
      scenarios.set(line.docId, {
        docId: line.docId, docType: line.docType, title: line.title ?? line.docId,
        ownerSlot: line.slot, report: false, scenario: true, empty: emptyLine(line),
      })
      continue
    }
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
  const buttons: CardDocumentButton[] = []
  for (const doc of docs.values()) {
    buttons.push(doc)
    const scenario = doc.ownerSlot === 'instruction' ? scenarios.get(doc.docId) : undefined
    if (scenario) buttons.push(scenario)
  }
  // A hidden instruction must not remove the separate scenario of its shared TS document.
  for (const scenario of scenarios.values()) {
    if (!buttons.some(button => button.scenario && button.docId === scenario.docId)) {
      const reportIndex = buttons.findIndex(button => button.report)
      buttons.splice(reportIndex < 0 ? buttons.length : reportIndex, 0, scenario)
    }
  }
  return buttons
}
